"""Company onboarding services."""

from django.db import transaction

from common.services import create_validated
from common.tenant import use_company
from organization.models import Branch
from scheduling.models import CompanyAttendanceSettings
from tenants.models import Company


@transaction.atomic
def onboard_company(
    *,
    code,
    slug,
    name,
    timezone="Asia/Dhaka",
    currency="BDT",
    country_code="BD",
    default_branch_code="HQ",
    default_branch_name="Head Office",
    shift_mode=CompanyAttendanceSettings.ShiftMode.DEPARTMENT_SHIFTS,
    effective_from=None,
    created_by=None,
    require_new=False,
):
    """Create a company with its default branch and attendance settings.

    Atomic: the whole block is one transaction, so a failure part-way through
    leaves no half-built company (no company without a branch, no branch without
    settings). Idempotent on ``code``: calling it again returns the existing
    company and does not create duplicates, which matters because onboarding may
    be retried after a timeout.

    Settings default to department-shift mode because single-shift mode requires
    naming a shift, and no shift exists yet at onboarding time.
    """
    from django.utils import timezone as django_timezone

    effective_from = effective_from or django_timezone.now()

    company, created = Company.objects.get_or_create(
        code=code,
        defaults={
            "slug": slug,
            "name": name,
            "timezone": timezone,
            "currency": currency,
            "country_code": country_code,
            "created_by": created_by,
        },
    )
    if not created:
        if require_new:
            from django.core.exceptions import ValidationError
            raise ValidationError({"code": "A company with this code already exists."})
        return company

    with use_company(company):
        create_validated(
            Branch,
            company=company,
            code=default_branch_code,
            name=default_branch_name,
            timezone=timezone,
            country_code=country_code,
            is_default=True,
            created_by=created_by,
        )
        create_validated(
            CompanyAttendanceSettings,
            company=company,
            shift_mode=shift_mode,
            effective_from=effective_from,
            created_by=created_by,
        )

    return company
