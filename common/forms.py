"""Shared input conventions for domain forms."""
import re
from django import forms
from django.core.exceptions import ValidationError


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


class StyledFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
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
