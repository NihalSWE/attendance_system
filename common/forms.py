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
            # Every <select> becomes a real Select2 - single, multiple, and
            # fixed choice lists alike. A native select can style its closed box
            # but its open dropdown is drawn by the operating system, which is
            # exactly the default appearance we are replacing. `.select` stays as
            # the no-JavaScript fallback.
            if isinstance(widget, forms.SelectMultiple):
                widget.attrs["class"] += " select js-select2 js-select2--multiple"
            elif isinstance(widget, forms.Select) and not isinstance(
                widget, (forms.RadioSelect, forms.CheckboxSelectMultiple)
            ):
                widget.attrs["class"] += " select js-select2"
            if isinstance(widget, forms.Textarea):
                widget.attrs.update({"class": "textarea", "rows": 3})
            if isinstance(field, (forms.IntegerField, forms.DecimalField, forms.FloatField)):
                widget.attrs["inputmode"] = "numeric" if isinstance(field, forms.IntegerField) else "decimal"
