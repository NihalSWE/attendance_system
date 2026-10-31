"""What making a product costs, before it is made (2026-10-27,
docs/inventory/PRODUCTION_PLAN.md §5, §7.4; §15 phase 5).

- **Cost rates**: what an hour of people or of a machine costs (Line worker
  75, Oven 300). A change counts from then on: a finished production keeps
  what it cost.
- **Cost lines** on a product: besides its materials, each batch costs people
  × hours × rate, machine hours × rate, a fixed amount, or a % of the
  materials.
- **The cost sheet**: materials (the recipe × each material's average cost,
  wastage included) + the cost lines = one batch; divided by what one batch
  makes = one unit; × a pack's size = one pack. A semi-finished product on
  the recipe costs what is in stock of it, else what making it costs.

Every write needs "Set up BOMs (recipes)" and the Production module.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from auditlog.services import record_company_event
from common.tenant import use_company
from inventory import access
from inventory.models import MADE_KINDS, Item, ItemCost, ItemKind, ItemUom
from production.models import CostLine, CostRate

CODE = "production.bom"
ZERO = Decimal("0")
CENT = Decimal("0.01")
RATE_FIELDS = ("name", "kind", "rate")
LINE_FIELDS = ("how", "label", "rate", "people", "hours", "amount", "percent")


def _require(actor, company_id):
    membership = access.require(actor, company_id, CODE)
    if not access.production_on(company_id):
        raise PermissionDenied("Production is not enabled for this company.")
    return membership


def _plain(value):
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return getattr(value, "pk", value)


def _snapshot(row, fields):
    return {name: _plain(getattr(row, name)) for name in fields}


def _only(values, allowed):
    extra = set(values) - set(allowed)
    if extra:
        raise ValidationError(f"Unsupported field: {', '.join(sorted(extra))}")
    return {key: value.strip() if isinstance(value, str) else value for key, value in values.items()}


# --- rates --------------------------------------------------------------------

@transaction.atomic
def add_rate(*, actor, company_id, values):
    membership = _require(actor, company_id)
    values = _only(values, RATE_FIELDS)
    with use_company(company_id):
        if CostRate.objects.filter(name__iexact=values.get("name", "")).exists():
            raise ValidationError({"name": "A rate with this name exists already."})
        rate = CostRate(company=membership.company, created_by=actor, updated_by=actor, **values)
        rate.full_clean()
        rate.save()
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="production.cost_rate.created", obj=rate,
                             after=_snapshot(rate, RATE_FIELDS))
    return rate


def _rate(company_id, pk):
    with use_company(company_id):
        rate = CostRate.objects.filter(pk=pk).first()
    if rate is None:
        raise PermissionDenied("Rate not found in this company.")
    return rate


@transaction.atomic
def update_rate(*, actor, company_id, pk, values):
    """A new rate counts for productions after it; finished ones keep theirs."""
    membership = _require(actor, company_id)
    rate = _rate(company_id, pk)
    values = _only(values, RATE_FIELDS)
    with use_company(company_id):
        if CostRate.objects.filter(name__iexact=values.get("name", rate.name)).exclude(pk=pk).exists():
            raise ValidationError({"name": "A rate with this name exists already."})
        if values.get("kind", rate.kind) != rate.kind and rate.lines.exists():
            raise ValidationError({"kind": "Products' costs use this rate: it stays a "
                                           f"{rate.get_kind_display().lower()} rate."})
        before = _snapshot(rate, RATE_FIELDS)
        for name, value in values.items():
            setattr(rate, name, value)
        rate.updated_by = actor
        rate.full_clean()
        rate.save()
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="production.cost_rate.updated", obj=rate, before=before,
                             after=_snapshot(rate, RATE_FIELDS))
    return rate


@transaction.atomic
def set_rate_status(*, actor, company_id, pk, active):
    membership = _require(actor, company_id)
    rate = _rate(company_id, pk)
    with use_company(company_id):
        rate.status, rate.updated_by = ("active" if active else "inactive"), actor
        rate.save(update_fields=["status", "updated_by", "updated_at"])
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="production.cost_rate.status", obj=rate, after={"status": rate.status})
    return rate


# --- a product's cost lines ---------------------------------------------------

def _made(item):
    if item.kind not in MADE_KINDS or item.sourcing == Item.Sourcing.BUY:
        raise ValidationError(f"{item.code} is not made here: it has no cost of making.")


@transaction.atomic
def add_line(*, actor, company_id, item, values):
    membership = _require(actor, company_id)
    _made(item)
    values = _only(values, LINE_FIELDS)
    rate = values.get("rate")
    if rate is not None and (rate.company_id != company_id or rate.status != "active"):
        raise ValidationError({"rate": "Choose an active rate of this company."})
    with use_company(company_id):
        line = CostLine(company=membership.company, item=item, created_by=actor, updated_by=actor, **values)
        line.full_clean()
        line.save()
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="production.cost_line.added", obj=item,
                             after=_snapshot(line, LINE_FIELDS))
    return line


@transaction.atomic
def remove_line(*, actor, company_id, item, line_id):
    membership = _require(actor, company_id)
    with use_company(company_id):
        line = CostLine.objects.filter(item=item, pk=line_id).first()
        if line is None:
            raise ValidationError("That cost is not on this product.")
        before = _snapshot(line, LINE_FIELDS)
        line.delete()
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="production.cost_line.removed", obj=item, before=before)


# --- the expected cost --------------------------------------------------------

@dataclass
class Row:
    """One line of the cost sheet."""

    name: str
    how: str
    amount: Decimal
    line: object = None
    missing: bool = False


@dataclass
class Sheet:
    """What one batch of a product costs, and one unit and one pack of it."""

    item: Item
    bom: object
    makes: Decimal                      # one batch, in the product's unit
    materials: list = field(default_factory=list)
    costs: list = field(default_factory=list)

    @property
    def materials_total(self):
        return sum((row.amount for row in self.materials), ZERO)

    @property
    def costs_total(self):
        return sum((row.amount for row in self.costs), ZERO)

    @property
    def total(self):
        return self.materials_total + self.costs_total

    @property
    def per_unit(self):
        return (self.total / self.makes).quantize(Decimal("0.0001")) if self.makes else None

    @property
    def missing(self):
        """Materials with no cost known yet: the sheet is short by them."""
        return [row.name for row in self.materials if row.missing]

    @property
    def margin(self):
        if not self.item.sale_price or self.per_unit is None:
            return None
        return (self.item.sale_price - self.per_unit).quantize(CENT)

    @property
    def margin_pct(self):
        margin = self.margin
        return None if margin is None else (margin / self.item.sale_price * 100).quantize(Decimal("0.1"))

    def per_pack(self, packs):
        """``[(pack, cost of one)]``."""
        if self.per_unit is None:
            return []
        return [(pack, (self.per_unit * pack.factor).quantize(CENT)) for pack in packs]


def unit_cost(company_id, item, seen=frozenset()):
    """What one unit of ``item`` costs: its average cost in stock; for a
    semi-finished product with none in stock, what making one costs; else its
    standard cost. None when nothing says."""
    costs = ItemCost.all_objects.filter(company_id=company_id, item=item)
    company = costs.filter(warehouse=None).values_list("avg_cost", flat=True).first()
    if company:
        return company
    # Costs kept per store: their average, weighted by what each holds.
    rows = list(costs.exclude(warehouse=None).filter(qty_basis__gt=0).values_list("avg_cost", "qty_basis"))
    if rows:
        weight = sum(qty for _cost, qty in rows)
        return (sum(cost * qty for cost, qty in rows) / weight).quantize(Decimal("0.0001"))
    if item.kind in MADE_KINDS and item.sourcing != Item.Sourcing.BUY and item.pk not in seen:
        made = sheet(company_id, item, seen=seen | {item.pk})
        if made is not None and made.per_unit and not made.missing:
            return made.per_unit
    return item.standard_cost or None


def _amount(value):
    return value.quantize(CENT)


def sheet(company_id, item, seen=frozenset()):
    """The cost sheet of one batch of a made product; None without a recipe."""
    from production import recipes

    bom = recipes.current(company_id, item)
    if bom is None:
        return None
    seen = seen | {item.pk}
    with use_company(company_id):
        lines = list(bom.lines.select_related("item__base_uom", "uom"))
        cost_lines = list(CostLine.objects.filter(item=item).select_related("rate"))
    found = Sheet(item=item, bom=bom, makes=bom.batch_size_base)
    for line in lines:
        need = line.qty_base * (1 + line.scrap_pct / 100)
        cost = unit_cost(company_id, line.item, seen)
        how = f"{need.normalize():,f} {line.item.base_uom.code}"
        how += f" × {cost.quantize(CENT):,f}" if cost is not None else " · no cost yet"
        found.materials.append(Row(line.item.name, how, _amount(need * (cost or ZERO)), line, cost is None))
    for line in cost_lines:
        if line.how == CostLine.How.LABOUR:
            amount = line.people * line.hours * line.rate.rate
            how = f"{line.people.normalize():f} × {line.hours.normalize():f} h × {line.rate.rate.normalize():f}"
        elif line.how == CostLine.How.MACHINE:
            amount = line.hours * line.rate.rate
            how = f"{line.hours.normalize():f} h × {line.rate.rate.normalize():f}"
        elif line.how == CostLine.How.FIXED:
            amount, how = line.amount, "per batch"
        else:
            amount = found.materials_total * line.percent / 100
            how = f"{line.percent.normalize():f} % of materials"
        found.costs.append(Row(line.name, how, _amount(amount), line))
    return found


def expected_unit_cost(company_id, item):
    """One unit's expected cost, or None (no recipe, or a material with no cost)."""
    found = sheet(company_id, item)
    if found is None or found.missing:
        return None
    return found.per_unit


def packs(company_id, item):
    return list(ItemUom.all_objects.filter(company_id=company_id, item=item).select_related("uom"))
