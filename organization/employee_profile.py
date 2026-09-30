"""The employee profile (Nihal/Ajay, 2026-09-26): what the history page (N6)
grew into - the photo, personal information, this month's attendance, absent
and late days, their leave, and the things done to one person, each in a
modal or its own page.

Who may: whoever may edit this employee (``employees.edit`` in their branch -
the owner, company admin, their branch manager, or anyone given it), the same
rule Edit employee uses. The person themselves does not edit here.

The summaries call the report builders (``reports.builders``) for this one
person, so the profile and the Reports can never disagree.
"""

import datetime
import uuid
import zoneinfo

from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import QueryDict

from auditlog.services import record_company_event
from common.forms import StyledFormMixin, date_widget
from common.tenant import use_company
from employees.models import Employee
from organization.employee_edit_services import get_employee_for_edit

#: What the Personal card holds. The name, work email, phone and joining date
#: stay on Edit employee (renaming someone there also renames them on the
#: terminals).
PERSONAL_FIELDS = (
    "preferred_name", "date_of_birth", "gender", "blood_group", "marital_status",
    "national_id", "passport_number", "personal_email", "address",
    "emergency_contact_name", "emergency_contact_phone", "emergency_contact_relation",
    "confirmation_date",
)

GENDERS = [("", "—"), ("female", "Female"), ("male", "Male"), ("other", "Other")]
BLOOD_GROUPS = [("", "—")] + [(g, g) for g in ("A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-")]
MARITAL = [("", "—"), ("single", "Single"), ("married", "Married"), ("widowed", "Widowed"),
           ("divorced", "Divorced")]

CHOICES = {"gender": GENDERS, "blood_group": BLOOD_GROUPS, "marital_status": MARITAL}

PHOTO_SUFFIXES = {"png": "png", "jpg": "jpeg", "jpeg": "jpeg", "webp": "webp"}
MAX_PHOTO_BYTES = 3 * 1024 * 1024


class PersonalForm(StyledFormMixin, forms.ModelForm):
    gender = forms.ChoiceField(choices=GENDERS, required=False, label="Gender")
    blood_group = forms.ChoiceField(choices=BLOOD_GROUPS, required=False, label="Blood group")
    marital_status = forms.ChoiceField(choices=MARITAL, required=False, label="Marital status")

    class Meta:
        model = Employee
        fields = PERSONAL_FIELDS
        labels = {
            "preferred_name": "Preferred name", "date_of_birth": "Date of birth",
            "national_id": "National ID", "passport_number": "Passport number",
            "personal_email": "Personal email", "emergency_contact_name": "Emergency contact",
            "emergency_contact_phone": "Their phone", "emergency_contact_relation": "Relation",
            "confirmation_date": "Confirmed on",
        }
        widgets = {
            "date_of_birth": date_widget("Date of birth"),
            "confirmation_date": date_widget("Confirmed on"),
            "address": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The form opens on what they hold (Nihal, 2026-09-29). A gender, blood
        # group or marital status saved in another spelling ("Male", "a+") is
        # matched to its choice; one that is not a choice at all is offered as
        # it is - otherwise the box shows "—" and saving would wipe it.
        for name in CHOICES:
            held = (getattr(self.instance, name, "") or "").strip()
            if not held or name not in self.fields:
                continue
            field = self.fields[name]
            match = next((value for value, _label in field.choices
                          if value and value.lower() == held.lower()), None)
            if match is None:
                field.choices = [*field.choices, (held, held)]
                match = held
            if not self.is_bound:
                self.initial[name] = match

    def clean_date_of_birth(self):
        born = self.cleaned_data.get("date_of_birth")
        if born and born > datetime.date.today():
            raise ValidationError("A date of birth cannot be in the future.")
        return born


class PhotoForm(StyledFormMixin, forms.Form):
    photo = forms.FileField(label="Photo", required=False)
    remove = forms.BooleanField(label="Remove the photo", required=False)

    def clean_photo(self):
        upload = self.cleaned_data.get("photo")
        if not upload:
            return upload
        suffix = upload.name.rsplit(".", 1)[-1].lower() if "." in upload.name else ""
        if suffix not in PHOTO_SUFFIXES:
            raise ValidationError("Use a PNG, JPG or WEBP picture.")
        if upload.size > MAX_PHOTO_BYTES:
            raise ValidationError("That picture is over 3 MB. Use a smaller one.")
        # A real picture, not something renamed: Pillow must be able to read it.
        from PIL import Image, UnidentifiedImageError

        try:
            with Image.open(upload) as picture:
                picture.verify()
                kind = (picture.format or "").lower()
        except (UnidentifiedImageError, OSError, SyntaxError) as exc:
            raise ValidationError("That file is not a picture we can read.") from exc
        if kind not in ("png", "jpeg", "webp"):
            raise ValidationError("Use a PNG, JPG or WEBP picture.")
        upload.seek(0)
        upload.picture_format = kind
        return upload


def editable(actor, company_id, employee_id):
    """``(membership, employee)`` when ``actor`` may change this person here."""
    membership, employee, _assignment, _pay = get_employee_for_edit(
        actor=actor, company_id=company_id, employee_id=employee_id, code="employees.edit")
    return membership, employee


@transaction.atomic
def save_personal(*, actor, company_id, employee_id, form):
    membership, employee = editable(actor, company_id, employee_id)
    with use_company(company_id):
        before = {f: str(getattr(employee, f) or "") for f in PERSONAL_FIELDS}
        for field in PERSONAL_FIELDS:
            setattr(employee, field, form.cleaned_data.get(field) or (
                None if field in ("date_of_birth", "confirmation_date") else ""))
        employee.updated_by = actor
        employee.full_clean()
        employee.save()
        after = {f: str(getattr(employee, f) or "") for f in PERSONAL_FIELDS}
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="employee.personal_updated", obj=employee,
            before={f: v for f, v in before.items() if v != after[f]},
            after={f: v for f, v in after.items() if v != before[f]},
        )
    return employee


@transaction.atomic
def save_photo(*, actor, company_id, employee_id, upload=None, remove=False):
    """Replace or remove the photo. Stored under a random name, never the
    uploaded one, and served only through the profile's own view."""
    membership, employee = editable(actor, company_id, employee_id)
    return store_photo(actor=actor, membership=membership, company_id=company_id,
                       employee=employee, upload=upload, remove=remove)


@transaction.atomic
def store_photo(*, actor, membership, company_id, employee, upload=None, remove=False):
    """The photo written, once whoever it is may change it (above, or the
    employee on their own profile, ``employee_self``)."""
    with use_company(company_id):
        old = employee.photo.name if employee.photo else ""
        if upload is not None:
            extension = "jpg" if upload.picture_format == "jpeg" else upload.picture_format
            employee.photo.save(f"{uuid.uuid4().hex}.{extension}",
                                ContentFile(upload.read()), save=False)
        elif remove:
            employee.photo = None
        else:
            raise ValidationError("Choose a picture, or tick Remove the photo.")
        employee.updated_by = actor
        employee.save(update_fields=["photo", "updated_by", "updated_at"])
        if old and old != (employee.photo.name if employee.photo else ""):
            employee.photo.storage.delete(old)
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="employee.photo_changed", obj=employee,
            after={"photo": "removed" if remove and upload is None else "replaced"},
        )
    return employee


@transaction.atomic
def set_report_visibility(*, actor, company_id, employee_id, hidden):
    """Leave someone out of the Reports (their attendance still counts)."""
    membership, employee = editable(actor, company_id, employee_id)
    with use_company(company_id):
        if employee.hide_from_reports == hidden:
            return employee
        employee.hide_from_reports = hidden
        employee.updated_by = actor
        employee.save(update_fields=["hide_from_reports", "updated_by", "updated_at"])
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="employee.report_visibility", obj=employee,
            before={"hidden": not hidden}, after={"hidden": hidden},
        )
    return employee


# --- the summaries, from the report builders ----------------------------------


#: The monthly grid's total columns, in its order (reports.builders._grid).
GRID_TOTALS = ["Present", "Late", "Half day", "Absent", "Leave", "Off", "Worked",
               "Late (min)", "Early out (min)", "Times out", "Time out", "Overtime"]
PROFILE_TOTALS = ["Present", "Late", "Half day", "Absent", "Leave", "Off", "Worked",
                  "Times out", "Time out"]


def _month_totals(values):
    found = dict(zip(GRID_TOTALS, values)) if values else {}
    return {label: found.get(label, "0:00" if label in ("Worked", "Time out") else 0)
            for label in PROFILE_TOTALS}


def month_summary(*, company, scope, employee, year, month):
    """This person's month - the Monthly attendance, absent and late reports
    for them alone: one row each (or none), read the reports' own way."""
    from reports import builders, filters

    query = QueryDict(mutable=True)
    query.update({"year": str(year), "month": str(month), "employee": str(employee.pk)})
    f = filters.read(query, filters.MONTH)
    zone = zoneinfo.ZoneInfo(company.timezone or "UTC")
    ctx = builders.Context(company_id=company.pk, company=company, zone=zone, scope=scope,
                           filters=f, include_hidden=True)
    grid = builders.monthly_attendance(ctx)
    absent = builders.monthly_absent(ctx)
    late = builders.monthly_late(ctx)
    row = grid.rows[0] if grid.rows else None
    days = f.days
    return {
        "label": f.label,
        "year": year, "month": month,
        # The grid's one line: a code per day, then the totals.
        "days": [(day, row[2 + i] if row else "") for i, day in enumerate(days)],
        # The grid's totals, and how often and how long they were out of the
        # office (2026-09-30); its late and early-out minutes and overtime are
        # shown elsewhere on the profile.
        "totals": _month_totals(row[2 + len(days):] if row else None),
        "absent_dates": absent.rows[0][5] if absent.rows else "",
        "late_dates": late.rows[0][7] if late.rows else "",
        "late_minutes": late.rows[0][5] if late.rows else 0,
        "legend": grid.legend,
    }


def year_leave(*, actor, company, employee, year):
    """This person's leave in a year, by the Leave report; None if the viewer
    may not see leave."""
    from reports import builders, filters
    from reports.access import LEAVE, report_scope

    try:
        _membership, scope = report_scope(actor, company.pk, LEAVE)
    except PermissionDenied:
        return None
    query = QueryDict(mutable=True)
    query.update({"date_from": f"{year}-01-01", "date_to": f"{year}-12-31",
                  "employee": str(employee.pk), "leave_status": "all"})
    f = filters.read(query, filters.RANGE, max_days=366, extras=("leave_status",))
    zone = zoneinfo.ZoneInfo(company.timezone or "UTC")
    return builders.leave(builders.Context(company_id=company.pk, company=company, zone=zone,
                                           scope=scope, filters=f, include_hidden=True))
