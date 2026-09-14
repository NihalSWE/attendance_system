"""Root-only operations. Writes and audit entries share one transaction."""
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from functools import wraps

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import CompanyMembership
from auditlog.services import record_platform_event
from common.services import create_validated
from common.tenant import use_company
from tenants.models import Company, CompanyFeature, Feature
from tenants.services import onboard_company
from common.identifiers import prepare_company_identifiers
from common.forms import normalize_bd_phone

User = get_user_model()
COMPANY_FIELDS = ("code", "slug", "name", "legal_name", "timezone", "currency", "country_code", "email", "phone", "address")
ADMIN_ROLES = (CompanyMembership.Role.OWNER, CompanyMembership.Role.COMPANY_ADMIN)


def require_platform_owner(actor):
    if not actor or not actor.is_authenticated or not actor.is_active or not actor.is_superuser:
        raise PermissionDenied("Platform owner access is required.")


def company_snapshot(company):
    snapshot = {field: getattr(company, field) for field in (*COMPANY_FIELDS, "status", "suspension_reason")}
    snapshot.update({field: getattr(company, field).isoformat() if getattr(company, field) else None for field in ("activated_at", "suspended_at")})
    snapshot["suspended_by_id"] = company.suspended_by_id
    return snapshot


def platform_company_context(operation):
    """Explicit temporary scope for root writes, never a session tenant bypass."""
    @wraps(operation)
    def wrapped(*, actor, company_id, **kwargs):
        require_platform_owner(actor)
        with use_company(company_id):
            return operation(actor=actor, company_id=company_id, **kwargs)
    return wrapped


def validate_company_values(values):
    values = dict(values)
    if set(values) - set(COMPANY_FIELDS):
        raise ValidationError("Unsupported company field.")
    if "phone" in values:
        try:
            values["phone"] = normalize_bd_phone(values["phone"])
        except ValidationError as exc:
            raise ValidationError({"phone": exc.messages})
    if "timezone" in values:
        try:
            ZoneInfo(values["timezone"])
        except (ZoneInfoNotFoundError, ValueError):
            raise ValidationError({"timezone": "Enter a valid IANA timezone, such as Asia/Dhaka."})
    for field, length in (("currency", 3), ("country_code", 2)):
        if field in values:
            values[field] = values[field].strip().upper()
            if (field == "currency" or values[field]) and (len(values[field]) != length or not values[field].isascii() or not values[field].isalpha()):
                raise ValidationError({field: f"Enter a {length}-letter code."})
    return values


@transaction.atomic
def create_platform_company(*, actor, values):
    require_platform_owner(actor)
    values = validate_company_values(values)
    values.pop("code", None)
    values.pop("slug", None)
    candidate = Company(**values)
    prepare_company_identifiers(candidate)
    candidate.full_clean()
    values = {field: getattr(candidate, field) for field in COMPANY_FIELDS}
    company = onboard_company(
        **{k: values[k] for k in ("code", "slug", "name", "timezone", "currency", "country_code")},
        created_by=actor, require_new=True, default_leave_types=True,
    )
    for field, value in values.items():
        setattr(company, field, value)
    company.updated_by = actor
    company.full_clean()
    company.save()
    ensure_feature_catalogue(actor=actor)
    record_platform_event(actor=actor, company=company, action="company.created", obj=company, after=company_snapshot(company))
    return company


def ensure_feature_catalogue(*, actor):
    """Onboarding supplies the minimal catalogue without demo data or grants."""
    require_platform_owner(actor)
    for order, (code, name) in enumerate((("attendance", "Attendance"), ("leave", "Leave"), ("payroll", "Payroll"))):
        Feature.objects.get_or_create(code=code, defaults={"name": name, "sort_order": order})


@transaction.atomic
def update_platform_company(*, actor, company_id, values):
    require_platform_owner(actor)
    company = Company.objects.select_for_update().get(pk=company_id)
    values = validate_company_values(values)
    for field in ("code", "slug"):
        if field in values and values[field] != getattr(company, field):
            raise ValidationError({field: "This generated identifier is read-only."})
    before = company_snapshot(company)
    # Changes after onboarding need the future dated-policy workflow.
    from employees.models import Employee
    if Employee.all_objects.filter(company=company).exists():
        for field in ("currency", "timezone"):
            if field in values and values[field] != getattr(company, field):
                raise ValidationError({field: "Changes after employees exist require a dated policy change."})
    for field, value in values.items():
        setattr(company, field, value)
    company.updated_by = actor
    company.full_clean()
    company.save()
    record_platform_event(actor=actor, company=company, action="company.updated", obj=company, before=before, after=company_snapshot(company))
    return company


@transaction.atomic
def change_company_status(*, actor, company_id, status, reason):
    require_platform_owner(actor)
    company = Company.objects.select_for_update().get(pk=company_id)
    if status not in Company.Status.values:
        raise ValidationError({"status": "Choose a valid status."})
    if not reason.strip():
        raise ValidationError({"reason": "Explain this status change."})
    if company.status == status:
        return company
    before = company_snapshot(company)
    company.status, company.updated_by = status, actor
    now = timezone.now()
    if status == Company.Status.ACTIVE:
        company.activated_at = now
    if status == Company.Status.SUSPENDED:
        company.suspended_at, company.suspended_by = now, actor
        company.suspension_reason = reason
    company.full_clean()
    company.save()
    after = company_snapshot(company)
    after["reason"] = reason
    record_platform_event(actor=actor, company=company, action="company.status_changed", obj=company, before=before, after=after)
    return company


@transaction.atomic
@platform_company_context
def grant_company_administrator(*, actor, company_id, email, first_name="", last_name="", password=""):
    require_platform_owner(actor)
    company = Company.objects.select_for_update().get(pk=company_id)
    if CompanyMembership.all_objects.filter(company=company, role__in=ADMIN_ROLES).exclude(status="ended").exists():
        raise ValidationError("This company already has an administrator. Edit that administrator instead.")
    email = User.objects.normalize_email(email.strip()).lower()
    if User.objects.filter(email__iexact=email).exists():
        raise ValidationError({"email": "This email already belongs to an account. Use a separate company administrator email."})
    user = User(email=email, first_name=first_name, last_name=last_name)
    validate_password(password, user)
    user.set_password(password)
    user.full_clean()
    user.save()
    record_platform_event(actor=actor, company=company, action="user.created", obj=user, after={"email": user.email})
    role = CompanyMembership.Role.COMPANY_ADMIN
    member = create_validated(CompanyMembership, company=company, user=user, role=role,
        status=CompanyMembership.Status.ACTIVE, joined_at=timezone.now(), invited_by=actor, created_by=actor, updated_by=actor)
    record_platform_event(actor=actor, company=company, action="membership.granted", obj=member, after={"user_id": user.pk, "role": role, "status": member.status})
    return member


@transaction.atomic
@platform_company_context
def edit_company_administrator(*, actor, company_id, membership_id, email, first_name, last_name, status, password=""):
    company = Company.objects.select_for_update().get(pk=company_id)
    member = CompanyMembership.all_objects.select_for_update().get(pk=membership_id, company=company, role__in=ADMIN_ROLES)
    user = User.objects.select_for_update().get(pk=member.user_id)
    if user.is_superuser:
        raise ValidationError("A platform account cannot be a company administrator.")
    if CompanyMembership.all_objects.filter(user=user).exclude(company=company).exclude(status="ended").exists():
        raise ValidationError("Resolve this account's other company memberships before changing its credentials.")
    before = {"email": user.email, "first_name": user.first_name, "last_name": user.last_name}
    user.email = User.objects.normalize_email(email.strip()).lower()
    if User.objects.filter(email__iexact=user.email).exclude(pk=user.pk).exists():
        raise ValidationError({"email": "This email already belongs to an account."})
    user.first_name, user.last_name = first_name, last_name
    if password:
        validate_password(password, user)
        user.set_password(password)
    user.full_clean()
    user.save()
    update_company_membership(actor=actor, company_id=company_id, membership_id=member.pk, role=member.role, status=status)
    record_platform_event(actor=actor, company=company, action="administrator.updated", obj=user, before=before,
        after={"email": user.email, "first_name": user.first_name, "last_name": user.last_name, "password_changed": bool(password)})
    return member


@transaction.atomic
@platform_company_context
def update_company_membership(*, actor, company_id, membership_id, role, status):
    require_platform_owner(actor)
    company = Company.objects.select_for_update().get(pk=company_id)
    member = CompanyMembership.all_objects.select_for_update().get(pk=membership_id, company=company)
    if member.user.is_superuser:
        raise ValidationError("Platform accounts cannot be managed as company administrators here.")
    if role not in CompanyMembership.Role.values or status not in CompanyMembership.Status.values:
        raise ValidationError("Choose a valid membership role and status.")
    before = {"role": member.role, "status": member.status}
    member.role, member.status, member.updated_by = role, status, actor
    member.ended_at = timezone.now() if status == CompanyMembership.Status.ENDED else None
    if status == CompanyMembership.Status.ACTIVE and member.joined_at is None:
        member.joined_at = timezone.now()
    member.full_clean()
    member.save()
    record_platform_event(actor=actor, company=company, action="membership.updated", obj=member, before=before, after={"role": role, "status": status})
    return member


@transaction.atomic
@platform_company_context
def set_company_feature(*, actor, company_id, feature_id, effect, reason):
    require_platform_owner(actor)
    company = Company.objects.select_for_update().get(pk=company_id)
    feature = Feature.objects.get(pk=feature_id, is_active=True)
    if effect not in CompanyFeature.Effect.values:
        raise ValidationError({"effect": "Choose enable or disable."})
    if not reason.strip():
        raise ValidationError({"reason": "Explain the feature change."})
    now = timezone.now()
    rows = list(CompanyFeature.all_objects.select_for_update().filter(company=company, feature=feature, is_active=True).filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now)))
    if any(row.starts_at and row.starts_at > now for row in rows):
        raise ValidationError("A future feature change is scheduled. Resolve it before changing current access.")
    before = {"effects": sorted({row.effect for row in rows})}
    if before["effects"] == [effect]:
        return rows[0]
    for row in rows:
        row.ends_at, row.updated_by = now, actor
        row.full_clean()
        row.save()
    grant = create_validated(CompanyFeature, company=company, feature=feature, effect=effect, starts_at=now,
        reason=reason, granted_by=actor, created_by=actor, updated_by=actor)
    record_platform_event(actor=actor, company=company, action="company.feature_changed", obj=grant,
        before=before, after={"feature": feature.code, "effect": effect, "reason": reason})
    return grant
