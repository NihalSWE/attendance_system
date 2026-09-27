"""Setting up leave policies (Phase E, 2026-09-27): the owner or company
administrator, as with leave types.

A policy's versions are its history. The first version may start on any date
(its entitlement then reaches back, docs/LEAVE_FULL_DESIGN.md "what changes"
3); every later one starts today or later. A version that has started is
never edited or removed - a change is a new version. Changing a version that
has not started, or the company default, drops the automatic ledger entries
from that date for the people it applies to; they are posted again.
"""

import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from auditlog.services import record_company_event
from common.choices import ActiveStatus
from common.tenant import use_company
from leaves.models import (
    EmployeeLeavePolicy,
    LeavePolicy,
    LeavePolicyRule,
    LeavePolicyVersion,
    LeaveType,
)
from leaves.policies import POLICY_ROLES, _member, forget_from

POLICY_FIELDS = ("code", "name", "description", "is_default")
RULE_FIELDS = ("days_per_year", "accrual", "carry_forward_days", "carry_forward_expires_months",
               "allow_half_day", "allow_hourly", "allow_negative")


def require_policy_manager(actor, company_id):
    return _member(actor, company_id, POLICY_ROLES, "Setting up leave policies")


def _people(policy):
    """Who a change to this policy reaches: everyone if it is the default."""
    if policy.is_default:
        return None
    return list(EmployeeLeavePolicy.objects.filter(policy=policy).values_list(
        "employee_id", flat=True).distinct())


def _forget(day, policy):
    people = _people(policy)
    if people is None:
        forget_from(day)
    elif people:
        from employees.models import Employee

        forget_from(day, Employee.objects.filter(pk__in=people))


def _snapshot(policy):
    return {"code": policy.code, "name": policy.name, "default": policy.is_default,
            "status": policy.status}


@transaction.atomic
def save_policy(*, actor, company_id, values, policy_id=None):
    membership = require_policy_manager(actor, company_id)
    unknown = set(values) - set(POLICY_FIELDS)
    if unknown:
        raise ValidationError(f"Unsupported field: {', '.join(sorted(unknown))}")
    with use_company(company_id):
        if policy_id is None:
            policy = LeavePolicy(created_by=actor)
            policy.company_id = company_id
            before = {}
        else:
            policy = LeavePolicy.objects.filter(pk=policy_id).first()
            if policy is None:
                raise PermissionDenied("Leave policy not found in this company.")
            before = _snapshot(policy)
        policy.code = (values.get("code") or "").strip().upper()
        policy.name = (values.get("name") or "").strip()
        policy.description = (values.get("description") or "").strip()
        was_default = policy.is_default
        policy.is_default = bool(values.get("is_default"))
        if LeavePolicy.objects.filter(code=policy.code).exclude(pk=policy.pk).exists():
            raise ValidationError({"code": f"{policy.code} is already used."})
        if policy.is_default and not was_default:
            for other in LeavePolicy.objects.filter(is_default=True).exclude(pk=policy.pk):
                other.is_default = False
                other.save(update_fields=["is_default", "updated_at"])
        policy.updated_by = actor
        policy.full_clean()
        policy.save()
        if policy.is_default != was_default:
            # Everyone without a policy of their own changes policy, from today.
            forget_from(timezone.localdate())
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="leave.policy_saved", obj=policy, before=before,
                             after=_snapshot(policy))
    return policy


@transaction.atomic
def set_policy_status(*, actor, company_id, policy_id, status):
    membership = require_policy_manager(actor, company_id)
    with use_company(company_id):
        policy = LeavePolicy.objects.filter(pk=policy_id).first()
        if policy is None:
            raise PermissionDenied("Leave policy not found in this company.")
        if status not in ActiveStatus.values:
            raise ValidationError({"status": "Choose active or inactive."})
        if status != ActiveStatus.ACTIVE and policy.is_default:
            raise ValidationError("The company default cannot be turned off. Make another "
                                  "policy the default first.")
        before = _snapshot(policy)
        policy.status = status
        policy.updated_by = actor
        policy.save(update_fields=["status", "updated_by", "updated_at"])
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="leave.policy_saved", obj=policy, before=before,
                             after=_snapshot(policy))
    return policy


def _clean_rules(rules):
    """``{leave_type: {field: value}}`` -> the same, checked."""
    cleaned = {}
    for leave_type, values in rules.items():
        values = {name: values.get(name) for name in RULE_FIELDS}
        if values["days_per_year"] is None:
            raise ValidationError(f"{leave_type.name}: give the days per year.")
        if values["carry_forward_expires_months"] and not values["carry_forward_days"]:
            raise ValidationError(f"{leave_type.name}: carried days can only expire if some "
                                  "are carried forward.")
        values["accrual"] = values["accrual"] or LeavePolicyRule.Accrual.YEARLY
        for flag in ("allow_half_day", "allow_hourly", "allow_negative"):
            values[flag] = bool(values[flag])
        cleaned[leave_type] = values
    if not cleaned:
        raise ValidationError("Give at least one leave type a rule.")
    return cleaned


def _write_rules(version, rules):
    for leave_type, values in rules.items():
        rule = LeavePolicyRule(version=version, leave_type=leave_type, **values)
        rule.company_id = version.company_id
        rule.full_clean()
        rule.save()


def _rules_snapshot(rules):
    return {leave_type.code: {k: str(v) for k, v in values.items()}
            for leave_type, values in rules.items()}


@transaction.atomic
def add_version(*, actor, company_id, policy_id, effective_from, note, rules, today=None):
    """A new version of the rules from a date."""
    membership = require_policy_manager(actor, company_id)
    today = today or timezone.localdate()
    rules = _clean_rules(rules)
    with use_company(company_id):
        policy = LeavePolicy.objects.filter(pk=policy_id).first()
        if policy is None:
            raise PermissionDenied("Leave policy not found in this company.")
        versions = LeavePolicyVersion.objects.filter(policy=policy)
        if versions.exists() and effective_from < today:
            raise ValidationError({"effective_from": (
                "A new version starts today or later. What was given before stays as it was.")})
        if versions.filter(effective_from=effective_from).exists():
            raise ValidationError({"effective_from": "A version already starts on that day."})
        number = (versions.aggregate(top=Max("number"))["top"] or 0) + 1
        version = LeavePolicyVersion(policy=policy, number=number, effective_from=effective_from,
                                     note=(note or "").strip()[:255], created_by=actor,
                                     updated_by=actor)
        version.company_id = company_id
        version.full_clean()
        version.save()
        _write_rules(version, rules)
        _forget(effective_from, policy)
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="leave.policy_version_added", obj=policy,
                             after={"version": number, "from": effective_from.isoformat(),
                                    "rules": _rules_snapshot(rules)})
    return version


def _unstarted(company_id, version_id, today):
    version = LeavePolicyVersion.objects.select_related("policy").filter(pk=version_id).first()
    if version is None:
        raise PermissionDenied("Leave policy version not found in this company.")
    if version.effective_from <= today:
        raise ValidationError("This version has started, so it stays as it is. Add a new "
                              "version from a later date instead.")
    return version


@transaction.atomic
def edit_version(*, actor, company_id, version_id, effective_from, note, rules, today=None):
    """Change a version that has not started yet."""
    membership = require_policy_manager(actor, company_id)
    today = today or timezone.localdate()
    rules = _clean_rules(rules)
    with use_company(company_id):
        version = _unstarted(company_id, version_id, today)
        if effective_from <= today:
            raise ValidationError({"effective_from": "It has to start after today."})
        if LeavePolicyVersion.objects.filter(policy=version.policy, effective_from=effective_from
                                             ).exclude(pk=version.pk).exists():
            raise ValidationError({"effective_from": "A version already starts on that day."})
        earliest = min(version.effective_from, effective_from)
        version.effective_from = effective_from
        version.note = (note or "").strip()[:255]
        version.updated_by = actor
        version.full_clean()
        version.save()
        version.rules.all().delete()
        _write_rules(version, rules)
        _forget(earliest, version.policy)
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="leave.policy_version_changed", obj=version.policy,
                             after={"version": version.number, "from": effective_from.isoformat(),
                                    "rules": _rules_snapshot(rules)})
    return version


@transaction.atomic
def delete_version(*, actor, company_id, version_id, today=None):
    membership = require_policy_manager(actor, company_id)
    today = today or timezone.localdate()
    with use_company(company_id):
        version = _unstarted(company_id, version_id, today)
        policy, number, start = version.policy, version.number, version.effective_from
        version.delete()
        _forget(start, policy)
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="leave.policy_version_removed", obj=policy,
                             before={"version": number, "from": start.isoformat()})
    return policy


def rules_initial(policy):
    """The latest version's rules, to start a new version from."""
    latest = policy.versions.order_by("-effective_from").first()
    if latest is None:
        return {}
    return {rule.leave_type_id: {name: getattr(rule, name) for name in RULE_FIELDS}
            for rule in latest.rules.all()}


def active_leave_types():
    return LeaveType.objects.filter(status=ActiveStatus.ACTIVE).order_by("name")


def next_year_start(today=None):
    today = today or timezone.localdate()
    return datetime.date(today.year + 1, 1, 1)
