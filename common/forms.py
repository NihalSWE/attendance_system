"""Shared input conventions for domain forms."""
import datetime
import re
import zoneinfo

from django import forms
from django.core.exceptions import ValidationError

from common.tenant import get_current_company_id


def normalize_bd_phone(value):
    value = (value or "").strip()
    if not value or value == "+88":
        return ""
    if not re.fullmatch(r"\+?[0-9 ()-]+", value):
        raise ValidationError("Enter a phone number using digits only.")
    digits = re.sub(r"[^0-9]", "", value)
    if digits.startswith("0088"):
        digits = digits[2:]
    if not digits.startswith("88"):
        digits = "88" + digits
    if not 8 <= len(digits) <= 15:
        raise ValidationError("Enter a complete phone number (8–15 digits including 88).")
    return digits


class BangladeshPhoneInput(forms.TextInput):
    input_type = "tel"

    def __init__(self, attrs=None):
        super().__init__({"data-phone-prefix": "88", "inputmode": "tel", "autocomplete": "tel", **(attrs or {})})

    def format_value(self, value):
        if not value:
            return "+88"
        try:
            return "+" + (normalize_bd_phone(value) or "88")
        except ValidationError:
            return value


def company_timezone():
    """The active company's timezone, or UTC when there is no company.

    Read here rather than passed in by every view, because a form is always
    built inside a tenant context and the answer is the same for all of them.
    """
    # Resolved on each call rather than captured once, because a field is
    # built at class-definition time — at import, long before any request.
    # Every caller here runs inside the request/response cycle, where
    # TenantMiddleware has set the company. Rendering one of these widgets
    # outside a request silently falls back to UTC, so a test that re-renders
    # a bound field by hand must wrap it in use_company().
    company_id = get_current_company_id()
    if company_id is not None:
        from tenants.models import Company

        name = (
            Company.objects.filter(pk=company_id)
            .values_list("timezone", flat=True)
            .first()
        )
        if name:
            try:
                return zoneinfo.ZoneInfo(name)
            except zoneinfo.ZoneInfoNotFoundError:
                pass
    return datetime.timezone.utc


def date_widget(placeholder=""):
    """A date box driven by the project calendar (datepicker.js).

    ``type="date"`` stays on the input: it still holds and posts the value, and
    is the no-JavaScript fallback. ``data-datepicker`` is what swaps the
    browser's own calendar for ours.
    """
    return forms.DateInput(
        format="%Y-%m-%d",
        attrs={
            "type": "date",
            "data-datepicker": "",
            "data-placeholder": placeholder,
        },
    )


def time_widget():
    """A plain text box for HH:MM.

    Not ``type="time"``: the browser draws its own clock for that, which the
    design does not allow (UI_AND_ONBOARDING_CONVENTIONS.md).
    """
    return forms.TextInput(
        attrs={
            "placeholder": "HH:MM",
            "maxlength": 5,
            "inputmode": "numeric",
            "autocomplete": "off",
        }
    )


TIME_INPUT_FORMATS = ["%H:%M", "%H.%M", "%H:%M:%S"]


class CompanyDateTimeWidget(forms.MultiWidget):
    """The project calendar plus an HH:MM box, standing in for one datetime.

    ``<input type="datetime-local">`` is the browser's own calendar *and* its
    own clock in one control, so it cannot be used here at all. Two inputs are
    the only way to keep both halves ours.
    """

    def __init__(self, attrs=None, placeholder=""):
        super().__init__([date_widget(placeholder), time_widget()], attrs)

    def decompress(self, value):
        """Split a stored datetime into the two boxes, in company time.

        Values are stored in UTC, so an edit form that skipped this would show
        the wrong clock time to everyone outside UTC.
        """
        if not value:
            return [None, None]
        if isinstance(value, datetime.datetime):
            if value.tzinfo is not None:
                value = value.astimezone(company_timezone())
            return [value.date(), value.strftime("%H:%M")]
        return [value, None]


class CompanyDateTimeField(forms.MultiValueField):
    """One datetime, entered as a date and an HH:MM time, read as company time.

    A blank time means midnight. That is the same reading the rest of the
    system gives a bare date, and it keeps "just pick the day" working for the
    effective-dated rows where the exact minute rarely matters.
    """

    widget = CompanyDateTimeWidget

    def __init__(self, *, placeholder="", require_all_fields=False, **kwargs):
        fields = (
            forms.DateField(),
            forms.TimeField(input_formats=TIME_INPUT_FORMATS, required=False),
        )
        kwargs.setdefault("widget", CompanyDateTimeWidget(placeholder=placeholder))
        super().__init__(
            fields=fields, require_all_fields=require_all_fields, **kwargs
        )
        # MultiValueField hands `required` down to every subfield, which would
        # demand a time as well. The time is genuinely optional.
        self.fields[0].required = self.required
        self.fields[1].required = False

    def compress(self, data_list):
        if not data_list:
            return None
        day, moment = data_list[0], data_list[1]
        if day is None:
            if moment is not None:
                raise ValidationError(
                    "Enter a date as well as a time.", code="incomplete"
                )
            return None
        return datetime.datetime.combine(
            day, moment or datetime.time.min, tzinfo=company_timezone()
        )


class StyledFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            # A checkbox is not a text field. Giving it class="input" made it
            # inherit `width: 100%`, which inside the flex row that renders it
            # collapsed the control to zero width — a 13px sliver with no box
            # at all. It gets the checkbox component class instead, so the
            # shared `.check` styles apply wherever the template puts it.
            if isinstance(widget, (forms.CheckboxInput, forms.RadioSelect,
                                   forms.CheckboxSelectMultiple)):
                widget.attrs["class"] = "check__input"
                continue
            widget.attrs["class"] = "input"
            # Control choice follows the DATA, not the widget:
            #   database-backed (ModelChoice / ModelMultipleChoice) -> Select2,
            #     because those lists are dynamic and may need search/paging;
            #   static choice lists -> our own custom select (customselect.js),
            #     which replaces the native control so the open dropdown is ours
            #     and not the operating system's.
            # `.select` stays on both as the no-JavaScript fallback.
            if isinstance(field, (forms.ModelChoiceField, forms.ModelMultipleChoiceField)):
                widget.attrs["class"] += " select js-select2"
            elif isinstance(widget, forms.Select) and not isinstance(
                widget, (forms.RadioSelect, forms.CheckboxSelectMultiple)
            ):
                widget.attrs["class"] += " select js-select"
            if isinstance(widget, forms.Textarea):
                widget.attrs.update({"class": "textarea", "rows": 3})
            if isinstance(field, (forms.IntegerField, forms.DecimalField, forms.FloatField)):
                widget.attrs["inputmode"] = "numeric" if isinstance(field, forms.IntegerField) else "decimal"


def apply_service_errors(form, exc):
    """Put a service's ValidationError on the form fields it names.

    Services validate independently of forms, because a form is not a security
    boundary. When a service refuses, its message should still appear next to
    the field the user has to change, and anything not tied to a visible field
    goes to the top of the form.
    """
    if hasattr(exc, "error_dict"):
        for field, errors in exc.error_dict.items():
            target = field if field in form.fields else None
            for error in errors:
                form.add_error(target, error)
    else:
        for message in exc.messages:
            form.add_error(None, message)
