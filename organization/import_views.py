"""Upload a spreadsheet of employees, look at what it would do, then confirm.

Three steps, three views. The middle one writes nothing: it reads the file,
checks every row and keeps the checked rows in the reader's session so the
confirm step has something to write without asking for the file twice. The
session is the reader's own input, so ``import_services.commit`` checks the
rows again from scratch before writing any of them.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from common.forms import apply_service_errors
from organization import import_services
from organization.import_forms import EmployeeImportForm
from organization.views import _company_or_redirect

#: Where the checked rows wait between the preview and the confirm.
SESSION_KEY = "employee_import"

#: How many good rows the preview lists. Every *bad* row is always listed —
#: that is what the preview is for — but a clean 600-row file does not need
#: 600 rows of confirmation to be believable.
GOOD_ROWS_SHOWN = 100


def _forget(request):
    request.session.pop(SESSION_KEY, None)


@login_required
@require_http_methods(["GET", "POST"])
def employee_import(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    # Refuses anyone who may not create employees anywhere.
    import_services.import_scope(request.user, company_id)

    preview = None
    if request.method == "POST":
        form = EmployeeImportForm(request.POST, request.FILES)
        if form.is_valid():
            basis = form.cleaned_data["default_pay_basis"]
            rate = form.cleaned_data["default_base_rate"]
            try:
                rows = import_services.read_file(form.cleaned_data["upload"])
                rows = import_services.check(
                    request.user, company_id, rows,
                    default_pay_basis=basis, default_base_rate=rate,
                )
            except ValidationError as exc:
                _forget(request)
                apply_service_errors(form, exc)
            else:
                counts = import_services.summarise(rows)
                request.session[SESSION_KEY] = {
                    "company_id": company_id,
                    "file_name": form.cleaned_data["upload"].name,
                    "default_pay_basis": basis,
                    "default_base_rate": str(rate) if rate is not None else "",
                    "rows": rows,
                }
                preview = {
                    "counts": counts,
                    "file_name": form.cleaned_data["upload"].name,
                    "bad": [row for row in rows if row["errors"]],
                    "good": [row for row in rows if not row["errors"]][:GOOD_ROWS_SHOWN],
                    "good_hidden": max(0, counts["good"] - GOOD_ROWS_SHOWN),
                }
    else:
        _forget(request)
        form = EmployeeImportForm()

    return render(request, "organization/employee_import.html", {
        "form": form,
        "preview": preview,
        "headings": import_services.HEADINGS,
        "notes": import_services.NOTES,
        "max_rows": import_services.MAX_ROWS,
    })


@login_required
@require_POST
def employee_import_confirm(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail

    stored = request.session.get(SESSION_KEY)
    if not stored or stored.get("company_id") != company_id:
        messages.error(request, "That import is no longer waiting. Upload the file again.")
        return redirect("organization:employee_import")

    rate = stored.get("default_base_rate") or None
    try:
        created = import_services.commit(
            actor=request.user, company_id=company_id, rows=stored["rows"],
            default_pay_basis=stored.get("default_pay_basis", ""),
            default_base_rate=rate,
        )
    except ValidationError as exc:
        _forget(request)
        for message in getattr(exc, "messages", [str(exc)]):
            messages.error(request, message)
        return redirect("organization:employee_import")

    _forget(request)
    messages.success(
        request,
        f"{len(created)} employee{'s' if len(created) != 1 else ''} imported from "
        f"{stored.get('file_name') or 'the file'}.",
    )
    return redirect("employee_list")


@login_required
@require_http_methods(["GET"])
def employee_import_template(request):
    """Download the blank template, as .xlsx or as .csv."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    import_services.import_scope(request.user, company_id)

    if request.GET.get("format") == "csv":
        response = HttpResponse(
            import_services.template_csv(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="employee-import-template.csv"'
        return response
    response = HttpResponse(
        import_services.template_xlsx(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="employee-import-template.xlsx"'
    return response
