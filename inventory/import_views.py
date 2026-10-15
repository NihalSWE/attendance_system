"""Import pages: upload a filled template, see the preview, confirm.

What was read waits in the reader's own session between the preview and
Confirm (as the employee import does), and Confirm checks it all again.
"""

import datetime
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from base_template.tables import render
from common.forms import apply_service_errors
from inventory import forms, imports
from inventory.views import company_or_bail

SESSION_KEY = "inventory_import"
TEMPLATES_DIR = Path(settings.BASE_DIR) / "docs" / "inventory" / "templates"
BACK = {"stores": "inventory:stores", "items": "inventory:items", "suppliers": "inventory:suppliers",
        "customers": "inventory:customers", "opening": "inventory:opening"}


def _kind(kind):
    if kind not in imports.SPECS:
        raise Http404("Unknown import.")
    return kind


@login_required
def import_page(request, kind):
    kind = _kind(kind)
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    imports._require_permission(kind, request.user, company_id)
    form_class = forms.OpeningImportForm if kind == "opening" else forms.ImportForm
    form = form_class(request.POST or None, request.FILES or None)
    preview = None
    if request.method == "POST" and form.is_valid():
        work_date = form.cleaned_data.get("work_date")
        try:
            data = imports.read(kind, form.cleaned_data["upload"])
            rows, extra = imports.check(kind, request.user, company_id, data, work_date)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            request.session[SESSION_KEY] = {
                "kind": kind, "company_id": company_id, "data": data,
                "work_date": work_date.isoformat() if work_date else "",
            }
            preview = {"rows": rows, "summary": imports.summary(rows),
                       "categories": extra.get("categories", [])}
    return render(request, "inventory/import.html", {
        "kind": kind, "title": imports.TITLES[kind], "form": form, "preview": preview,
        "back": reverse(BACK[kind]), "template_file": imports.TEMPLATE_FILES[kind],
    })


@login_required
@require_POST
def import_confirm(request, kind):
    kind = _kind(kind)
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    stored = request.session.get(SESSION_KEY)
    if not stored or stored.get("kind") != kind or stored.get("company_id") != company_id:
        messages.error(request, "Upload the file again: the preview has expired.")
        return redirect("inventory:import", kind=kind)
    work_date = (datetime.date.fromisoformat(stored["work_date"]) if stored.get("work_date")
                 else None)
    try:
        result = imports.commit(kind, actor=request.user, company_id=company_id,
                                data=stored["data"], work_date=work_date)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("inventory:import", kind=kind)
    request.session.pop(SESSION_KEY, None)
    if kind == "opening":
        messages.success(request, f"{result.number} made from the file as a draft. "
                                  "Check it, then post it.")
        return redirect("inventory:opening_detail", pk=result.pk)
    messages.success(request, f"Imported {result} row{'s' if result != 1 else ''}.")
    return redirect(BACK[kind])


@login_required
def template_download(request, kind):
    kind = _kind(kind)
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    imports._require_permission(kind, request.user, company_id)
    path = TEMPLATES_DIR / imports.TEMPLATE_FILES[kind]
    if not path.exists():
        raise Http404("Template not found.")
    return FileResponse(path.open("rb"), as_attachment=True, filename=path.name)
