"""Logins for employees: create, change the role, reset the password, disable.

A login is a ``User`` plus a ``CompanyMembership`` with the role Employee or
Branch manager (``manager`` scoped to ``allowed_branches``), linked from
``Employee.user``. Owner / company admin only, audited; a password never
appears in an audit row.

Safety rules:

- A login is always a **new** account. An email that already has a login is
  refused, so nobody can attach (and so reach) someone else's account.
- A password can only be reset for an account that belongs to this company
  alone — never for a person who is also a member elsewhere, and never for a
  superuser or staff account.
- Disabling suspends the membership in this company, not the account, so the
  same person's access to another company is not touched.
"""

import functools

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import CompanyMembership
from auditlog.services import record_company_event
from common.tenant import use_company
from employees.models import Employee
from organization.services import assert_branch_in_scope, require_structure_manager

User = get_user_model()
Role = CompanyMembership.Role
LOGIN_ROLES = (Role.EMPLOYEE, Role.MANAGER)
ROLE_LABELS = {Role.EMPLOYEE: "Employee", Role.MANAGER: "Branch manager"}


def _employee(membership, company_id, employee_id):
    with use_company(company_id):
        employee = Employee.objects.select_related("user").filter(pk=employee_id).first()
    if employee is None:
        raise PermissionDenied("Employee not found in this company.")
    return employee


def login_for(company_id, employee):
    """The employee's membership in this company, or None."""
    if employee.user_id is None:
        return None
    return (
        CompanyMembership.all_objects.filter(company_id=company_id, user_id=employee.user_id)
        .prefetch_related("allowed_branches").first()
    )


def _check_role(membership, role, branches):
    if role not in LOGIN_ROLES:
        raise ValidationError({"role": "Choose Employee or Branch manager."})
    branches = list(branches or [])
    if role == Role.MANAGER and not branches:
        raise ValidationError({"branches": "Choose the branch or branches they manage."})
    if role == Role.EMPLOYEE:
        branches = []
    with use_company(membership.company_id):
        for branch in branches:
            assert_branch_in_scope(membership, branch)
    return branches


def _check_password(password, confirm, user):
    if password != confirm:
        raise ValidationError({"password_confirm": "The two passwords are not the same."})
    try:
        validate_password(password, user=user)
    except ValidationError as exc:
        raise ValidationError({"password": exc.messages})


def _in_company(fn):
    """Run the whole service in the company's tenant context: a login's
    branches are tenant rows, read when they are set and when audited."""

    @functools.wraps(fn)
    def wrapped(*args, company_id, **kwargs):
        with use_company(company_id):
            return fn(*args, company_id=company_id, **kwargs)

    return wrapped


def _snapshot(member):
    return {
        "user_id": member.user_id,
        "role": member.role,
        "status": member.status,
        "branches": sorted(member.allowed_branches.values_list("pk", flat=True)),
    }


@_in_company
@transaction.atomic
def give_login(*, actor, company_id, employee_id, values):
    """A new login for an employee who has none."""
    membership = require_structure_manager(actor, company_id)
    employee = _employee(membership, company_id, employee_id)
    if employee.user_id is not None:
        raise ValidationError("This employee already has a login.")
    email = User.objects.normalize_email((values.get("email") or "").strip()).lower()
    if not email:
        raise ValidationError({"email": "Enter the email they will sign in with."})
    if User.objects.filter(email__iexact=email).exists():
        raise ValidationError({
            "email": "This email already has a login. Use a different email for this employee."
        })
    branches = _check_role(membership, values.get("role"), values.get("branches"))
    user = User(email=email, first_name=employee.first_name, last_name=employee.last_name)
    _check_password(values.get("password") or "", values.get("password_confirm") or "", user)
    user.set_password(values["password"])
    user.full_clean(exclude=["password"])
    user.save()

    member = CompanyMembership.all_objects.create(
        company=membership.company, user=user, role=values["role"],
        status=CompanyMembership.Status.ACTIVE, joined_at=timezone.now(),
        invited_by=actor, created_by=actor, updated_by=actor,
    )
    member.allowed_branches.set(branches)
    with use_company(company_id):
        employee.user = user
        employee.updated_by = actor
        employee.save(update_fields=["user", "updated_by", "updated_at"])
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="employee.login_created", obj=employee,
            after={"email": email, **_snapshot(member)},
        )
    return member


def _existing(actor, company_id, employee_id):
    membership = require_structure_manager(actor, company_id)
    employee = _employee(membership, company_id, employee_id)
    member = login_for(company_id, employee)
    if member is None:
        raise ValidationError("This employee has no login yet.")
    if member.role not in LOGIN_ROLES:
        # The company's owner/administrator account is managed on the
        # platform's company page, not from an employee record.
        raise PermissionDenied("This login is the company administrator's; change it on the company page.")
    return membership, employee, member


@_in_company
@transaction.atomic
def change_login_role(*, actor, company_id, employee_id, values):
    membership, employee, member = _existing(actor, company_id, employee_id)
    branches = _check_role(membership, values.get("role"), values.get("branches"))
    before = _snapshot(member)
    member.role = values["role"]
    member.updated_by = actor
    member.save(update_fields=["role", "updated_by", "updated_at"])
    member.allowed_branches.set(branches)
    with use_company(company_id):
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="employee.login_role_changed", obj=employee,
            before=before, after=_snapshot(member),
        )
    return member


@_in_company
@transaction.atomic
def reset_login_password(*, actor, company_id, employee_id, values):
    membership, employee, member = _existing(actor, company_id, employee_id)
    user = member.user
    if user.is_superuser or user.is_staff:
        raise PermissionDenied("This account's password cannot be changed here.")
    if CompanyMembership.all_objects.filter(user=user).exclude(company_id=company_id).exists():
        raise PermissionDenied(
            "This person also belongs to another company, so only they can change their password."
        )
    _check_password(values.get("password") or "", values.get("password_confirm") or "", user)
    user.set_password(values["password"])
    user.save(update_fields=["password", "updated_at"])
    with use_company(company_id):
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="employee.login_password_reset", obj=employee,
            after={"user_id": user.pk},
        )
    return member


@_in_company
@transaction.atomic
def set_login_active(*, actor, company_id, employee_id, active):
    """Disable (suspend) or enable the login in this company."""
    membership, employee, member = _existing(actor, company_id, employee_id)
    target = CompanyMembership.Status.ACTIVE if active else CompanyMembership.Status.SUSPENDED
    if member.status == target:
        raise ValidationError("The login is already " + ("enabled." if active else "disabled."))
    before = _snapshot(member)
    member.status = target
    member.updated_by = actor
    member.save(update_fields=["status", "updated_by", "updated_at"])
    with use_company(company_id):
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="employee.login_enabled" if active else "employee.login_disabled",
            obj=employee, before=before, after=_snapshot(member),
        )
    return member
