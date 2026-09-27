"""The profile's other sections (Ajay, 2026-09-27): Approver information,
Education history, Employee documents, the Roster and Pending approvals.

Who may: education and documents are changed by whoever may edit the
employee (``employees.edit`` in their branch) and seen by whoever may see them
(``employees.view``) - the photo's rule. A document is stored under a random
name and served only by ``employee_document``, never at a public address.

Approver information is read from the rules that decide things, not stored:
who decides their leave follows ``leaves.workflow.reviewable`` (their branch
manager, the head of their department, anyone given "Approve leave" there;
the company for a branch manager's own leave or a branch without one).
"""

import datetime
import uuid

from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from access_control.models import EmployeePermissionOverride
from accounts.models import CompanyMembership
from auditlog.services import record_company_event
from common.forms import StyledFormMixin
from common.tenant import use_company
from employees.models import Employee, EmployeeDocument, EmployeeEducation
from leaves.documents import DocumentField
from organization.employee_edit_services import get_employee_for_edit

Role = CompanyMembership.Role


# --------------------------------------------------------------------------
# Education history
# --------------------------------------------------------------------------

EDUCATION_FIELDS = ("qualification", "institution", "subject", "result", "passing_year", "note")


class EducationForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = EmployeeEducation
        fields = EDUCATION_FIELDS
        labels = {
            "qualification": "Degree or certificate",
            "subject": "Subject or group",
            "result": "Result or grade",
            "passing_year": "Year passed",
        }
        help_texts = {"qualification": "For example SSC, HSC, BSc in Computer Science."}

    def clean_passing_year(self):
        year = self.cleaned_data.get("passing_year")
        if year is not None and not 1940 <= year <= timezone.now().year + 1:
            raise ValidationError("Give the year it was passed, for example 2019.")
        return year


def _editable(actor, company_id, employee_id):
    membership, employee, _a, _c = get_employee_for_edit(
        actor=actor, company_id=company_id, employee_id=employee_id, code="employees.edit")
    return membership, employee


def _snapshot(row, fields):
    return {f: str(getattr(row, f) or "") for f in fields}


@transaction.atomic
def save_education(*, actor, company_id, employee_id, values, row_id=None):
    """Add a qualification, or change one (``row_id``)."""
    membership, employee = _editable(actor, company_id, employee_id)
    with use_company(company_id):
        if row_id is None:
            row = EmployeeEducation(employee=employee, created_by=actor)
            row.company_id = company_id
            before = {}
        else:
            row = EmployeeEducation.objects.filter(pk=row_id, employee=employee).first()
            if row is None:
                raise PermissionDenied("That qualification is not on this profile.")
            before = _snapshot(row, EDUCATION_FIELDS)
        for field in EDUCATION_FIELDS:
            setattr(row, field, values.get(field) if field == "passing_year"
                    else values.get(field) or "")
        row.updated_by = actor
        row.full_clean()
        row.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="employee.education_changed" if before else "employee.education_added",
            obj=employee, before=before, after=_snapshot(row, EDUCATION_FIELDS),
        )
    return row


@transaction.atomic
def remove_education(*, actor, company_id, employee_id, row_id):
    membership, employee = _editable(actor, company_id, employee_id)
    with use_company(company_id):
        row = EmployeeEducation.objects.filter(pk=row_id, employee=employee).first()
        if row is None:
            raise PermissionDenied("That qualification is not on this profile.")
        before = _snapshot(row, EDUCATION_FIELDS)
        row.delete()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="employee.education_removed", obj=employee, before=before, after={},
        )


# --------------------------------------------------------------------------
# Employee documents
# --------------------------------------------------------------------------


class DocumentForm(StyledFormMixin, forms.Form):
    kind = forms.ChoiceField(choices=EmployeeDocument.Kind.choices, label="What it is")
    title = forms.CharField(max_length=150, label="Name",
                            help_text="For example: National ID card, HSC certificate.")
    file = DocumentField(required=True, label="File",
                         help_text="PDF, JPG, PNG or WEBP, up to 5 MB. Only people who may see "
                                   "this employee can open it.")
    note = forms.CharField(max_length=255, required=False, label="Note")


@transaction.atomic
def add_document(*, actor, company_id, employee_id, values):
    membership, employee = _editable(actor, company_id, employee_id)
    upload = values["file"]
    extension = {"jpeg": "jpg"}.get(upload.kind, upload.kind)
    with use_company(company_id):
        row = EmployeeDocument(employee=employee, kind=values["kind"],
                               title=values["title"], note=values.get("note") or "",
                               file_name=upload.name[:255], created_by=actor, updated_by=actor)
        row.company_id = company_id
        row.file.save(f"{uuid.uuid4().hex}.{extension}", ContentFile(upload.read()), save=False)
        try:
            row.full_clean()
            row.save()
        except ValidationError:
            row.file.storage.delete(row.file.name)
            raise
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="employee.document_added", obj=employee, before={},
            after={"title": row.title, "kind": row.kind, "file": row.file_name},
        )
    return row


def remove_document(*, actor, company_id, employee_id, row_id):
    """Take a document off the profile, and its file off the disk once the
    removal is recorded."""
    with transaction.atomic():
        membership, employee = _editable(actor, company_id, employee_id)
        with use_company(company_id):
            row = EmployeeDocument.objects.filter(pk=row_id, employee=employee).first()
            if row is None:
                raise PermissionDenied("That document is not on this profile.")
            storage, name = row.file.storage, row.file.name
            before = {"title": row.title, "kind": row.kind, "file": row.file_name}
            row.delete()
            record_company_event(
                actor=actor, membership=membership, company=membership.company,
                action="employee.document_removed", obj=employee, before=before, after={},
            )
    if name:
        storage.delete(name)


def document_for(*, actor, company_id, employee_id, row_id):
    """The document, when ``actor`` may see this employee."""
    _m, employee, _a, _c = get_employee_for_edit(
        actor=actor, company_id=company_id, employee_id=employee_id, code="employees.view")
    with use_company(company_id):
        row = EmployeeDocument.objects.filter(pk=row_id, employee=employee).first()
    if row is None:
        raise PermissionDenied("That document is not on this profile.")
    return row


def records(company_id, employee):
    """Education and documents, for the profile."""
    with use_company(company_id):
        return {
            "education": list(EmployeeEducation.objects.filter(employee=employee)
                              .order_by("-passing_year", "-pk")),
            "documents": list(EmployeeDocument.objects.filter(employee=employee)
                              .select_related("created_by").order_by("-created_at")),
        }


# --------------------------------------------------------------------------
# Approver information
# --------------------------------------------------------------------------


def _names(company_id, user_ids, *, leave_out=None):
    user_ids = [pk for pk in dict.fromkeys(user_ids) if pk != leave_out]
    with use_company(company_id):
        named = {e.user_id: e.full_name for e in Employee.objects.filter(user_id__in=user_ids)}
    emails = dict(CompanyMembership.all_objects.filter(user_id__in=user_ids)
                  .values_list("user_id", "user__email"))
    return [named.get(pk) or emails.get(pk, "") for pk in user_ids]


def _granted(company_id, code, branch_id, at):
    """Employees given ``code`` by hand in this branch (their logins)."""
    with use_company(company_id):
        rows = (EmployeePermissionOverride.objects
                .filter(permission__code=code, effect=EmployeePermissionOverride.Effect.GRANT,
                        status=EmployeePermissionOverride.Status.ACTIVE,
                        effective_from__lte=at, allowed_branches=branch_id,
                        employee__user__isnull=False)
                .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=at)))
        return list(rows.values_list("employee__user_id", flat=True))


def approvers(company_id, employee, assignment):
    """Who looks after this person's requests, in words for the profile."""
    if assignment is None:
        return None
    at = timezone.now()
    active = CompanyMembership.all_objects.filter(
        company_id=company_id, status="active", ended_at__isnull=True, user__is_active=True)
    own = employee.user_id
    with use_company(company_id):
        managers = list(
            active.filter(role=Role.MANAGER, allowed_branches=assignment.branch_id)
            .filter(Q(allowed_departments__isnull=True)
                    | Q(allowed_departments=assignment.department_id))
            .values_list("user_id", flat=True).distinct())
        head = assignment.department.head if assignment.department.head_id else None
    company = list(active.filter(role__in=(Role.OWNER, Role.COMPANY_ADMIN))
                   .values_list("user_id", flat=True))
    hr = list(active.filter(role=Role.HR).values_list("user_id", flat=True))
    is_branch_manager = own is not None and active.filter(user_id=own, role=Role.MANAGER).exists()
    leave = _names(company_id, managers + _granted(company_id, "leave.approve",
                                                   assignment.branch_id, at), leave_out=own)
    if head is not None and head.pk != employee.pk and head.full_name not in leave:
        leave.append(head.full_name)
    attendance = _names(company_id, managers + hr + _granted(
        company_id, "attendance.fix", assignment.branch_id, at), leave_out=own)
    company_names = _names(company_id, company, leave_out=own)
    return {
        "line_manager": assignment.manager,
        "department_head": head if head is not None and head.pk != employee.pk else None,
        "branch_managers": _names(company_id, managers, leave_out=own),
        # A branch manager's own leave, and a branch with no manager, go to the company.
        "leave": company_names if is_branch_manager or not managers else leave,
        "leave_by_company": is_branch_manager or not managers,
        "attendance": attendance or company_names,
        "company": company_names,
    }


# --------------------------------------------------------------------------
# Roster: the days ahead
# --------------------------------------------------------------------------

ROSTER_DAYS = 14


def roster(company_id, employee, assignment, today, days=ROSTER_DAYS):
    """Each of the next ``days`` days: the shift, a day off or holiday, leave."""
    from leaves.models import LeaveDay
    from leaves.services import LIVE_LEAVE_DAYS
    from scheduling.calendar import HOLIDAY, WEEKLY_OFF, WorkCalendar

    if assignment is None:
        return []
    last = today + datetime.timedelta(days=days - 1)
    with use_company(company_id):
        calendar = WorkCalendar(company_id, today, last)
        on_leave = {
            row.work_date: row for row in LeaveDay.objects.select_related(
                "request_segment__leave_type")
            .filter(employee=employee, work_date__gte=today, work_date__lte=last,
                    status__in=LIVE_LEAVE_DAYS)
        }
        rows = []
        day = today
        while day <= last:
            info = calendar.day(assignment.branch_id, day)
            shift = calendar.shift_for(assignment.department_id, day, employee_id=employee.pk)
            leave = on_leave.get(day)
            rows.append({
                "day": day,
                "shift": shift if info.kind not in (HOLIDAY, WEEKLY_OFF) else None,
                "off": info.kind in (HOLIDAY, WEEKLY_OFF),
                "off_label": ("Holiday" if info.kind == HOLIDAY else "Weekly off"),
                "off_name": info.label if info.kind in (HOLIDAY, WEEKLY_OFF) else "",
                "own": calendar.employee_shift(employee.pk, day) is not None,
                "leave": leave.request_segment.leave_type.name if leave else "",
            })
            day += datetime.timedelta(days=1)
    return rows


# --------------------------------------------------------------------------
# Pending approvals
# --------------------------------------------------------------------------

#: How far back a waiting item is looked for.
PENDING_DAYS = 62


def pending(company_id, employee, today, *, leave=True, attendance=True, overtime=True):
    """What is waiting for somebody's decision about this person."""
    from attendance.models import AttendanceRecord, MissedScanRequest, ReviewStatus
    from leaves.models import LeaveRequest

    since = today - datetime.timedelta(days=PENDING_DAYS)
    result = {"leave": [], "missed": [], "review": [], "overtime": []}
    with use_company(company_id):
        if leave:
            waiting = list(
                LeaveRequest.objects.prefetch_related("segments__leave_type")
                .filter(employee=employee, status__in=(LeaveRequest.Status.PENDING,
                                                       LeaveRequest.Status.SUBMITTED))
                .order_by("-pk"))
            for request in waiting:
                # A request's type and dates are on its segments.
                parts = sorted(request.segments.all(), key=lambda part: part.start_date)
                request.type_name = ", ".join(dict.fromkeys(p.leave_type.name for p in parts))
                request.first_day = parts[0].start_date if parts else None
                request.last_day = max((p.end_date for p in parts), default=None)
            result["leave"] = waiting
        if attendance:
            result["missed"] = list(MissedScanRequest.objects.filter(
                employee=employee, status=MissedScanRequest.Status.PENDING).order_by("work_date"))
            result["review"] = list(AttendanceRecord.objects.filter(
                employee=employee, review_status=ReviewStatus.NEEDS_REVIEW,
                work_date__gte=since, work_date__lte=today).order_by("work_date"))
    if overtime:
        result["overtime"] = _overtime_waiting(company_id, employee, since, today)
    result["total"] = sum(len(v) for v in result.values())
    return result


def _overtime_waiting(company_id, employee, since, today):
    """Days whose overtime waits for a decision (nobody scanned out)."""
    from attendance.models import AttendanceRecord
    from payroll import overtime
    from payroll.models import OvertimeDecision
    from payroll.policy import rules_for

    with use_company(company_id):
        records = list(
            AttendanceRecord.objects.prefetch_related("sessions").select_related("shift")
            .filter(employee=employee, work_date__gte=since, work_date__lte=today,
                    is_open=False).filter(overtime.CANDIDATES).distinct().order_by("work_date"))
        decided = set(OvertimeDecision.objects.filter(
            employee=employee, work_date__gte=since).values_list("work_date", flat=True))
    rules = {}
    waiting = []
    for record in records:
        if record.work_date in decided:
            continue
        month = record.work_date.replace(day=1)
        if month not in rules:
            rules[month] = rules_for(company_id, month)
        claim = overtime.claim_for(record)
        if claim.exists and overtime.state_of(claim, None, rules[month]) == overtime.WAITING:
            waiting.append(record)
    return waiting
