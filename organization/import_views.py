"""Upload a file of employees, look at what it would do, then confirm.

Three steps, three views, plus the demo file. The middle one writes nothing: it
reads the file, checks every row and the branch, and keeps them in the
reader's session so the confirm step has something to write without asking
for the file twice. The session is the reader's own input, so
``import_services.commit`` checks everything again before writing any of it.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from access_control.branch_access import ALL_BRANCHES
from common.forms import apply_service_errors
from common.tenant import use_company
from organization import import_services
from organization.import_forms import EmployeeImportForm
from organization.views import _company_or_redirect

#: Where the checked rows wait between the preview and the confirm.
SESSION_KEY = "employee_import"

#: How many good rows the preview lists. Every *bad* row is always listed -
#: that is what the preview is for.
GOOD_ROWS_SHOWN = 100


def _forget(request):
    request.session.pop(SESSION_KEY, None)


def _form(request, company_id, data=None, files=None):
    """The upload form, narrowed to where this person may import."""
    _membership, branch_ids = import_services.import_scope(request.user, company_id)
    branches = import_services.import_branches(request.user, company_id)
    # A branch manager with one branch sees it locked; the company, and a
    # manager with several branches, choose from a dropdown.
    locked = branch_ids is not ALL_BRANCHES and branches.count() == 1
    return EmployeeImportForm(
        data, files, branches=branches, locked=locked,
        default_branch=import_services.default_branch(request.user, company_id),
    )


@login_required
@require_http_methods(["GET", "POST"])
def employee_import(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail

    with use_company(company_id):
        preview = None
        if request.method == "POST":
            form = _form(request, company_id, request.POST, request.FILES)
            if form.is_valid():
                data = form.cleaned_data
                try:
                    # Empty means the default branch; check_branch resolves it.
                    chosen = data["branch"].pk if data["branch"] else None
                    branch = import_services.check_branch(request.user, company_id, chosen)
                    rows = import_services.read_file(data["upload"])
                    rows = import_services.check(request.user, company_id, rows)
                except ValidationError as exc:
                    _forget(request)
                    apply_service_errors(form, exc)
                else:
                    counts = import_services.summarise(rows)
                    request.session[SESSION_KEY] = {
                        "company_id": company_id,
                        "file_name": data["upload"].name,
                        "branch_id": branch.pk,
                        "rows": rows,
                    }
                    new = [row for row in rows if not row["errors"] and not row["existing"]]
                    preview = {
                        "counts": counts,
                        "file_name": data["upload"].name,
                        "branch": branch,
                        "bad": [row for row in rows if row["errors"]],
                        "good": new[:GOOD_ROWS_SHOWN],
                        "good_hidden": max(0, counts["good"] - GOOD_ROWS_SHOWN),
                        # Already employees: skipped, left as they are...
                        "existing": import_services.already_here(rows),
                        # ...or, when the file names them differently, renamed.
                        "renames": import_services.renames(rows),
                    }
        else:
            _forget(request)
            form = _form(request, company_id)

        return render(request, "organization/employee_import.html", {
            "form": form,
            "preview": preview,
            "headings": import_services.HEADINGS,
            "demo_rows": import_services.DEMO_ROWS,
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

    try:
        created = import_services.commit(
            actor=request.user, company_id=company_id,
            rows=stored["rows"], branch_id=stored["branch_id"],
        )
    except ValidationError as exc:
        _forget(request)
        for message in exc.messages:
            messages.error(request, message)
        return redirect("organization:employee_import")

    _forget(request)
    count = len(created)
    skipped = sum(1 for row in stored["rows"] if row.get("existing") and not row.get("rename"))
    renamed = sum(1 for row in stored["rows"] if row.get("existing") and row.get("rename"))
    file_name = stored.get("file_name") or "the file"
    messages.success(
        request,
        (f"{count} employee{'s' if count != 1 else ''} imported from {file_name}. "
         "Set each one's department, designation and salary on Edit employee — "
         "until then they are in Unassigned and salary skips them."
         if count else f"Nobody new in {file_name}.")
        + (f" {renamed} name{'s' if renamed != 1 else ''} updated from the file."
           if renamed else "")
        + (f" {skipped} already in the software {'was' if skipped == 1 else 'were'} "
           "skipped and left unchanged." if skipped else ""),
    )
    return redirect("employee_list")


@login_required
@require_http_methods(["GET"])
def employee_import_demo(request):
    """The demo file, CSV or (``?format=xlsx``) Excel: the format the import
    accepts, with made-up people."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    import_services.import_scope(request.user, company_id)
    if request.GET.get("format") == "xlsx":
        response = HttpResponse(
            import_services.demo_xlsx(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = 'attachment; filename="employee-import-demo.xlsx"'
        return response
    response = HttpResponse(import_services.demo_csv(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="employee-import-demo.csv"'
    return response
