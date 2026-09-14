"""Company salary settings: which rules apply to a month, and changing them.

The rules are a dated ``PayrollPolicyVersion``. A change never edits a version
in force; it saves a new one from the 1st of a month, and the month before
keeps its rules, so any month's salary can be recalculated the way it was.

A company that has never saved its settings runs on ``STANDARD_RULES`` —
exactly the 2026-09-12 formula — so nothing changes until someone changes it.
"""

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from auditlog.services import record_company_event
from common.services import create_validated
from common.tenant import use_company
from organization.services import require_structure_manager
from payroll.models import PayrollPolicyVersion, PayrollSettings

Version = PayrollPolicyVersion
RULES_CODE = "SALARY"
INCOMPLETE_PAY = {"pay_full": Decimal("1"), "pay_half": Decimal("0.5"), "unpaid": Decimal("0")}
ROUNDING = {
    Version.RoundingMode.HALF_UP: ROUND_HALF_UP,
    Version.RoundingMode.UP: ROUND_CEILING,
    Version.RoundingMode.DOWN: ROUND_FLOOR,
}

# The rule fields the settings page edits. Everything else on the version
# keeps its default until its feature is built.
RULE_FIELDS = (
    "monthly_proration_method",
    "monthly_divisor",
    "absence_deduction_method",
    "money_rounding_increment",
    "money_rounding_mode",
    "allow_negative_net_pay",
    "maximum_period_deduction_percent",
    "overtime_multiplier",
    "holiday_overtime_multiplier",
    "minimum_overtime_minutes",
    "overtime_rounding_minutes",
)
CONFIG_FIELDS = tuple(Version.CONFIG_DEFAULTS)
GENERAL_FIELDS = ("currency", "default_pay_day")


def plain(value):
    """A Decimal without trailing zeros or exponent: 30.000000 -> "30"."""
    return format(Decimal(value).normalize(), "f")


@dataclass(frozen=True)
class SalaryRules:
    """The rules one salary calculation uses, read from a version."""

    version: object
    per_day_method: str
    divisor: Decimal
    absence_method: str
    half_day_pay: Decimal       # share of a day's pay a half day earns, 0..1
    incomplete_pay: Decimal     # share of a day's pay a day without check-out earns
    daily_paid_days_off: bool
    hourly_paid_days_off: bool
    allow_negative: bool
    rounding_increment: Decimal
    rounding_mode: str
    max_penalty_percent: object = None  # Decimal, or None for no limit
    pays_overtime: bool = True
    overtime_multiplier: Decimal = Decimal("2")
    day_off_multiplier: Decimal = Decimal("2")
    overtime_minimum: int = 0
    overtime_step: int = 0

    @classmethod
    def from_version(cls, version):
        source = version or Version()
        return cls(
            pays_overtime=source.overtime_method != Version.OvertimeMethod.NONE,
            overtime_multiplier=Decimal(source.overtime_multiplier),
            day_off_multiplier=Decimal(source.holiday_overtime_multiplier),
            overtime_minimum=source.minimum_overtime_minutes,
            overtime_step=source.overtime_rounding_minutes,
            version=version,
            per_day_method=source.monthly_proration_method,
            divisor=Decimal(source.monthly_divisor),
            absence_method=source.absence_deduction_method,
            half_day_pay=Decimal(str(source.config("half_day_pay_percent"))) / Decimal("100"),
            incomplete_pay=INCOMPLETE_PAY[source.config("incomplete_day_treatment")],
            daily_paid_days_off=bool(source.config("daily_paid_days_off")),
            hourly_paid_days_off=bool(source.config("hourly_paid_days_off")),
            allow_negative=source.allow_negative_net_pay,
            rounding_increment=Decimal(source.money_rounding_increment),
            rounding_mode=source.money_rounding_mode,
            max_penalty_percent=source.maximum_period_deduction_percent,
        )

    def payable_overtime(self, minutes):
        """Approved minutes of one day, after the minimum and the rounding.

        A day under the minimum pays none; otherwise the minutes are rounded
        down to whole blocks (30 → 95 min pays 90).
        """
        minutes = int(minutes or 0)
        if not self.pays_overtime or minutes <= 0 or minutes < self.overtime_minimum:
            return 0
        if self.overtime_step:
            minutes -= minutes % self.overtime_step
        return minutes

    def round_net(self, value):
        steps = (Decimal(value) / self.rounding_increment).quantize(
            Decimal("1"), rounding=ROUNDING[self.rounding_mode]
        )
        return (steps * self.rounding_increment).quantize(Decimal("0.01"))

    def describe(self):
        """What a run records about the rules it used."""
        return {
            "version_id": self.version.pk if self.version else None,
            "version_number": self.version.version_number if self.version else None,
            "name": "Company rules" if self.version else "Standard rules",
            "per_day_method": self.per_day_method,
            "divisor": plain(self.divisor),
            "absence_method": self.absence_method,
            "half_day_pay": str(self.half_day_pay),
            "incomplete_pay": str(self.incomplete_pay),
            "daily_paid_days_off": self.daily_paid_days_off,
            "hourly_paid_days_off": self.hourly_paid_days_off,
            "allow_negative": self.allow_negative,
            "rounding": f"{self.rounding_mode} {plain(self.rounding_increment)}",
            "max_penalty_percent": (
                plain(self.max_penalty_percent) if self.max_penalty_percent is not None else None
            ),
            "overtime": {
                "paid": self.pays_overtime,
                "multiplier": plain(self.overtime_multiplier),
                "day_off_multiplier": plain(self.day_off_multiplier),
                "minimum_minutes": self.overtime_minimum,
                "rounding_minutes": self.overtime_step,
            },
        }


STANDARD_RULES = SalaryRules.from_version(None)


def version_for(company_id, on):
    """The active rules version in force on a date, or None for the standard rules."""
    with use_company(company_id):
        return (
            Version.objects.filter(
                code=RULES_CODE, status=Version.Status.ACTIVE, effective_from__lte=on
            )
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=on))
            .order_by("-effective_from")
            .first()
        )


def rules_for(company_id, on):
    return SalaryRules.from_version(version_for(company_id, on))


def salary_settings_page(*, actor, company_id, today):
    """Everything the settings page shows. Owner / company admin only."""
    membership = require_structure_manager(actor, company_id)
    with use_company(company_id):
        settings = PayrollSettings.objects.filter().first()
        versions = list(
            Version.objects.select_related("activated_by").filter(code=RULES_CODE)
            .order_by("-effective_from", "-version_number")
        )
    current = version_for(company_id, today)
    upcoming = [
        v for v in versions
        if v.status == Version.Status.ACTIVE and v.effective_from > today
    ]
    return {
        "membership": membership,
        "settings": settings,
        "currency": settings.currency if settings else membership.company.currency,
        "current": current,
        "rules": SalaryRules.from_version(current),
        "upcoming": upcoming,
        "versions": versions,
    }


def _snapshot(version):
    if version is None:
        return {"rules": "standard"}
    return {
        "version_number": version.version_number,
        "effective_from": version.effective_from.isoformat(),
        **{field: str(getattr(version, field)) for field in RULE_FIELDS},
        "calculation_config": version.calculation_config,
    }


@transaction.atomic
def change_salary_rules(*, actor, company_id, values):
    """Save new salary rules from the 1st of a month.

    - A version already in force then is closed the day before.
    - A version that starts on that same month is replaced (retired): saving
      twice for one month is a correction, not two pieces of history.
    - A version that starts later refuses the change, so history cannot be
      rewritten under a future change already saved.
    """
    membership = require_structure_manager(actor, company_id)
    unsupported = set(values) - {"effective_from", *RULE_FIELDS, *CONFIG_FIELDS}
    if unsupported:
        raise ValidationError(f"Unsupported field: {', '.join(sorted(unsupported))}")
    starts = values["effective_from"]
    if starts.day != 1:
        raise ValidationError({"effective_from": "Salary rules start on the 1st of a month."})

    with use_company(company_id):
        active = list(
            Version.objects.select_for_update()
            .filter(code=RULES_CODE, status=Version.Status.ACTIVE)
            .order_by("effective_from")
        )
        later = [v for v in active if v.effective_from > starts]
        if later:
            raise ValidationError({
                "effective_from": (
                    f"Rules from {later[-1].effective_from:%B %Y} are already saved. "
                    f"Choose {later[-1].effective_from:%B %Y} or a later month."
                )
            })
        before = None
        for version in active:
            if version.effective_from == starts:
                before = version
                version.status = Version.Status.RETIRED
                version.updated_by = actor
                version.save(update_fields=["status", "updated_by", "updated_at"])
            elif version.effective_to is None or version.effective_to > starts:
                before = version
                version.effective_to = starts
                version.updated_by = actor
                version.save(update_fields=["effective_to", "updated_by", "updated_at"])

        number = (
            Version.objects.filter(code=RULES_CODE).aggregate(top=Max("version_number"))["top"] or 0
        ) + 1
        config = {field: values[field] for field in CONFIG_FIELDS if field in values}
        if "half_day_pay_percent" in config:
            config["half_day_pay_percent"] = plain(config["half_day_pay_percent"])
        version = create_validated(
            Version,
            company=membership.company,
            created_by=actor,
            updated_by=actor,
            code=RULES_CODE,
            name="Salary rules",
            version_number=number,
            effective_from=starts,
            status=Version.Status.ACTIVE,
            activated_by=actor,
            activated_at=timezone.now(),
            calculation_config={**Version.CONFIG_DEFAULTS, **config},
            **{field: values[field] for field in RULE_FIELDS if field in values},
        )

        settings = _settings_row(membership, actor)
        settings.default_policy_version = version
        settings.monthly_divisor = version.monthly_divisor
        settings.allow_negative_net_pay = version.allow_negative_net_pay
        settings.settings_version += 1
        settings.updated_by = actor
        settings.full_clean()
        settings.save()

        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="payroll.rules_changed", obj=version,
            before=_snapshot(before), after=_snapshot(version),
        )
    return version


def _settings_row(membership, actor):
    settings = PayrollSettings.objects.select_for_update().first()
    if settings is None:
        settings = create_validated(
            PayrollSettings,
            company=membership.company,
            created_by=actor,
            updated_by=actor,
            currency=membership.company.currency,
        )
    return settings


@transaction.atomic
def update_general_settings(*, actor, company_id, values):
    """Currency and pay day: plain settings, not dated rules."""
    membership = require_structure_manager(actor, company_id)
    unsupported = set(values) - set(GENERAL_FIELDS)
    if unsupported:
        raise ValidationError(f"Unsupported field: {', '.join(sorted(unsupported))}")
    with use_company(company_id):
        settings = _settings_row(membership, actor)
        before = {field: getattr(settings, field) for field in GENERAL_FIELDS}
        settings.currency = values["currency"].strip().upper()
        settings.default_pay_day = values.get("default_pay_day")
        settings.settings_version += 1
        settings.updated_by = actor
        settings.full_clean()
        settings.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="payroll.settings_updated", obj=settings,
            before=before, after={field: getattr(settings, field) for field in GENERAL_FIELDS},
        )
    return settings
