"""Forms that save without reloading the page (2026-10-27, plan §4.3).

A form marked ``data-fetch`` is sent by base_template/js/fetch_forms.js with the
header ``X-Requested-With: fetch``. The view answers with one of:

- ``saved(message, redirect=None)``: ``{"ok": true, "message", "redirect"}``;
  the page shows the message just above the Save button, then goes to
  ``redirect`` (or stays).
- ``refused(form)`` or ``refused(message=...)``: ``{"ok": false, "message",
  "errors": {box name: [reasons]}, "form_errors": [reasons]}`` with status 400;
  the page outlines each named box in red with its reason, lists the rest
  above the Save button, and keeps everything that was typed.

The same view still answers a plain post (no JavaScript) the usual way: ask
``wants_json(request)`` first.
"""

from django.http import JsonResponse

HEADER_VALUE = "fetch"
FIX_THE_RED = "Not saved: fix the boxes marked in red."


# Django's own words, said as what to do.
PLAIN = {
    "This field is required.": "Fill this in.",
    "Enter a number.": "Write a number.",
    "Enter a whole number.": "Write a whole number.",
    "Enter a valid date.": "Pick a date.",
}


def _plain(reason):
    return PLAIN.get(reason, reason)


def wants_json(request):
    return request.headers.get("X-Requested-With") == HEADER_VALUE


def saved(message, redirect=None):
    return JsonResponse({"ok": True, "message": message, "redirect": redirect})


def refused(form=None, message=None, status=400):
    """The form's reasons, by the name each box has on the page."""
    errors, form_errors = {}, []
    if form is not None:
        for name, reasons in form.errors.items():
            if name == "__all__":
                form_errors += [str(reason) for reason in reasons]
            else:
                errors[form.add_prefix(name)] = [_plain(str(reason)) for reason in reasons]
    if message is None:
        message = form_errors.pop(0) if form_errors and not errors else FIX_THE_RED
    return JsonResponse({"ok": False, "message": message, "errors": errors,
                         "form_errors": form_errors}, status=status)
