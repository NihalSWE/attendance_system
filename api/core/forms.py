"""Checking input with the panels' own forms.

An endpoint that mirrors a panel page validates through that page's form, so
the API accepts and refuses exactly what the panel does (the same cleaning,
the same messages). The API's serializer only describes the fields for the
documentation and refuses unknown ones; the form decides.

API names differ where the API says what a value is (``branch_id`` where the
form says ``branch``): ``names`` maps form field -> API field, both ways.
"""

import contextlib
import datetime

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Model

from api.core.errors import ApiError


def _api_fields(errors, names):
    fields = {}
    for key, messages in errors.items():
        fields[names.get(key, key)] = [str(m) for m in messages]
    general = fields.pop("__all__", None)
    return fields, general


def refuse(errors, names=None):
    """Raise the API's validation error from form or service errors."""
    fields, general = _api_fields(errors, names or {})
    raise ApiError("validation_error", general[0] if general and not fields else None,
                   fields or None)


@contextlib.contextmanager
def service_errors(names=None):
    """A service's refusal, with the field names the API uses."""
    try:
        yield
    except DjangoValidationError as exc:
        if hasattr(exc, "message_dict"):
            refuse(exc.message_dict, names)
        raise ApiError("validation_error", " ".join(exc.messages)) from exc


def _plain(value):
    if isinstance(value, Model):
        return value.pk
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if value is None:
        return ""
    return value


def form_data(form_class, instance, changes, names=None, **form_kwargs):
    """The form's data for a change: what is stored now, with ``changes`` over
    it (a PATCH sends only what changes; a form needs every field)."""
    names = names or {}
    to_form = {api: form for form, api in names.items()}
    blank = form_class(instance=instance, **form_kwargs)
    data = {}
    for name, field in blank.fields.items():
        value = blank.initial.get(name, field.initial)
        if isinstance(value, (list, tuple)):
            data[name] = [_plain(v) for v in value]
        else:
            data[name] = _plain(value)
    for key, value in changes.items():
        data[to_form.get(key, key)] = "" if value is None else value
    return data


def checked(form_class, data, names=None, **form_kwargs):
    """A bound, valid form - or the API's validation error."""
    form = form_class(data=data, **form_kwargs)
    if not form.is_valid():
        refuse(form.errors, names)
    return form
