"""Ask: a question typed in the top search bar, answered from our own data
(2026-11-02).

``/ask/?q=…`` reads the question with one AI call (``ask.interpret``) and
redirects to the same page with the understood search in the address; that
page runs the search (``ask.searches``) with the viewer's own access, shows a
one-line answer, chips saying how the question was understood and the rows.
When the AI cannot be asked or cannot read the question, the page says so
and falls back to the normal employee name search.
"""

import calendar
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from access_control.page_access import may_open
from ai_assistant.claude import AiUnavailable, encrypt, settings_for
from ai_assistant.models import AiCall, AiSettings
from ask import interpret, searches
from ask.forms import AskSettingsForm
from auditlog.services import record_company_event
from base_template.tables import paginate_rows, render
from common.forms import apply_service_errors
from common.middleware import SELF_SERVICE_ROLES
from common.tenant import use_company
from organization.models import Branch, Department
from organization.services import STRUCTURE_ROLES, require_company_membership


def _company_or_bail(request):
    if request.user.is_superuser:
        return None, redirect("platform:company_list")
    if not request.company_id:
        return None, render(request, "base_template/no_company.html")
    return request.company_id, None


def _may_search_names(request):
    """Whether the normal employee name search opens for this login."""
    membership = require_company_membership(request.user, request.company_id)
    return membership.role not in SELF_SERVICE_ROLES or may_open(request.user, request.company_id, "employee_list")


def _fall_back(request, question, why):
    """Say why Ask could not answer, then the normal employee name search -
    or, for a login without it, this page with the reason."""
    if _may_search_names(request):
        if why:
            messages.warning(request, f"{why} Showing the employee search instead.")
        return redirect(f"{reverse('employee_list')}?{urlencode({'q': question})}")
    return render(request, "ask/ask.html", {"question": question, "why": why or "Ask could not answer that."})


@login_required
@require_http_methods(["GET"])
def ask(request):
    company_id, bail = _company_or_bail(request)
    if bail:
        return bail
    question = request.GET.get("q", "").strip()[:300]
    today = timezone.localdate()
    if "s" not in request.GET:
        if not question:
            return render(request, "ask/ask.html", {"question": ""})
        try:
            reading = interpret.interpret(company_id, question, today)
        except AiUnavailable as exc:
            return _fall_back(request, question, f"Ask is not available: {'; '.join(exc.messages)}")
        except ValidationError as exc:
            return _fall_back(request, question, f"Ask could not read that question ({'; '.join(exc.messages)})")
        if reading.is_name:
            return _fall_back(request, question, "")
        if reading.ask is None:
            return _fall_back(request, question, f"Ask could not answer that: {reading.reason}")
        for kind, name in (("branch", reading.branch_name), ("department", reading.department_name)):
            if name:
                messages.info(request, f"No {kind} is called “{name}”: showing every {kind}.")
        return redirect(f"{reverse('ask:ask')}?{reading.ask.query()}")

    found = interpret.Ask.from_query(request.GET, today)
    result = searches.run(request.user, company_id, found)
    page = paginate_rows(request, result.rows, columns=len(result.columns)) if result.columns else None
    with use_company(company_id):
        branches = list(Branch.objects.order_by("name").values_list("pk", "name"))
        departments = sorted(set(Department.objects.values_list("name", flat=True)), key=str.casefold)
    return render(request, "ask/ask.html", {
        "question": question, "found": found, "result": result, "page": page,
        "chips": _chips(found, dict(branches), limited=require_company_membership(
            request.user, company_id).role in SELF_SERVICE_ROLES), "branches": branches, "departments": departments,
        "searches": interpret.SEARCHES, "statuses": interpret.STATUSES, "overtime_states": interpret.OVERTIME,
        "group_choices": interpret.GROUP_BY, "months": [(n, calendar.month_name[n]) for n in range(1, 13)],
        "years": range(today.year + 1, today.year - 5, -1),
    })


def _chips(found, branch_names, limited=False):
    """How the question was understood, as ``[{label, remove}]``: ``remove`` is
    the address without that filter (None where nothing can be removed).
    ``limited``: a branch login, whose "every branch" is its own branches."""
    chips = [{"label": interpret.SEARCHES[found.search], "remove": None}]
    if found.status:
        chips.append({"label": interpret.STATUSES[found.status], "remove": found.query(status="")})
    if found.overtime:
        chips.append({"label": interpret.OVERTIME[found.overtime], "remove": found.query(overtime="")})
    first, last = found.period
    if first:
        chips.append({"label": searches.day(first) if first == last else f"{searches.day(first)} – {searches.day(last)}",
                      "remove": None})
    if found.month_name and found.search != "leave_balance":
        chips.append({"label": found.month_name, "remove": None})
    if found.search == "leave_balance":
        chips.append({"label": str(found.year), "remove": None})
    if found.employee or found.employee_id:
        chips.append({"label": found.employee or "One person", "remove": found.query(employee="", employee_id=None)})
    if found.branch_id and found.branch_id in branch_names:
        chips.append({"label": branch_names[found.branch_id], "remove": found.query(branch_id=None)})
    else:
        chips.append({"label": "Your branches" if limited else "All branches", "remove": None})
    if found.department:
        chips.append({"label": found.department, "remove": found.query(department="")})
    if found.group_by:
        chips.append({"label": interpret.GROUP_BY[found.group_by], "remove": None})
    return chips


# --- settings (owner and company administrator) ---------------------------------------

FIELDS = ("ask_enabled", "ask_consent_received", "ask_consent_date", "ask_model", "daily_cost_cap")


def _keeper(actor, company_id):
    membership = require_company_membership(actor, company_id)
    if membership.role not in STRUCTURE_ROLES:
        raise PermissionDenied("Ask is set up by the owner or company administrator.")
    return membership


def _snapshot(row):
    """What the audit log keeps: never the key, only whether one is saved."""
    return {**{name: str(getattr(row, name)) for name in FIELDS}, "key_saved": bool(row.api_key_encrypted)}


@transaction.atomic
def save_settings(*, actor, company_id, values, api_key=""):
    membership = _keeper(actor, company_id)
    with use_company(company_id):
        row = AiSettings.objects.select_for_update().first() or AiSettings(company=membership.company,
                                                                           created_by=actor)
        before = _snapshot(row) if row.pk else {}
        for name in FIELDS:
            if name in values:
                setattr(row, name, values[name])
        if api_key.strip():
            row.api_key_encrypted = encrypt(api_key.strip())
        if row.ask_consent_received and not row.ask_consent_date:
            raise ValidationError({"ask_consent_date": "Give the date of the client's written consent."})
        if row.ask_enabled and not row.ask_consent_received:
            raise ValidationError({"ask_enabled": "Record the client's written consent first."})
        if row.ask_enabled and not row.api_key_encrypted:
            raise ValidationError({"api_key": "Save the Anthropic API key first."})
        row.updated_by = actor
        row.full_clean()
        row.save()
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="ai.ask_settings.updated", obj=row, before=before, after=_snapshot(row))
    return row


@login_required
@require_http_methods(["GET", "POST"])
def settings_page(request):
    company_id, bail = _company_or_bail(request)
    if bail:
        return bail
    _keeper(request.user, company_id)
    own = settings_for(company_id)
    form = AskSettingsForm(request.POST or None, instance=own)
    if request.method == "POST" and form.is_valid():
        try:
            save_settings(actor=request.user, company_id=company_id, values=form.cleaned_data,
                          api_key=form.cleaned_data.get("api_key", ""))
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "Ask settings saved.")
            return redirect("ask:settings")
    with use_company(company_id):
        calls = list(AiCall.objects.filter(kind=AiCall.Kind.ASK).order_by("-created_at")[:10])
    from ai_assistant.claude import spent_today

    return render(request, "ask/settings.html", {"form": form, "own": own, "calls": calls,
                                                 "spent": spent_today(company_id)})
