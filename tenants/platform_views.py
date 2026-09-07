"""Platform HTTP adapters; all mutations delegate to authorized services."""
from functools import wraps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from accounts.models import CompanyMembership
from access_control.services import is_feature_enabled
from auditlog.models import AuditLog
from tenants.forms import AdministratorForm, CompanyFeatureForm, CompanyForm, CompanyStatusForm
from tenants.models import Company, Feature
from tenants import platform_services as services


def platform_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        services.require_platform_owner(request.user)
        return view(request, *args, **kwargs)
    return wrapped


def _integer(value, default, minimum=0, maximum=1000000):
    try:
        return max(minimum, min(int(value), maximum))
    except (ValueError, TypeError):
        return default


@platform_required
@require_http_methods(["GET"])
def company_list(request):
    query = request.GET.get("search[value]", request.GET.get("q", "")).strip()[:200]
    companies = Company.objects.annotate(employee_count=Count("employees_employee_set", distinct=True))
    total = companies.count()
    if query:
        companies = companies.filter(Q(name__icontains=query) | Q(code__icontains=query) | Q(status__icontains=query))
    order = {"0": "name", "1": "code", "2": "status", "3": "employee_count"}.get(request.GET.get("order[0][column]"), "name")
    if request.GET.get("order[0][dir]") == "desc":
        order = "-" + order
    companies = companies.order_by(order, "pk")
    if request.GET.get("format") == "data":
        start = _integer(request.GET.get("start"), 0)
        length = _integer(request.GET.get("length"), 25, 1, 100)
        return JsonResponse({"draw": _integer(request.GET.get("draw"), 0), "recordsTotal": total,
            "recordsFiltered": companies.count(), "data": [
                {"name": c.name, "code": c.code, "status": c.get_status_display(), "employee_count": c.employee_count,
                 "url": reverse("platform:company_detail", args=[c.public_id])} for c in companies[start:start + length]
            ]})
    page = Paginator(companies, 25).get_page(request.GET.get("page"))
    return render(request, "tenants/platform/company_list.html", {"page": page, "query": query, "total": total})


@platform_required
@require_http_methods(["GET"])
def company_detail(request, public_id):
    company = get_object_or_404(Company, public_id=public_id)
    members = CompanyMembership.all_objects.filter(company=company).select_related("user").order_by("user__email")
    return render(request, "tenants/platform/company_detail.html", {
        "company": company, "members": members,
        "administrator": members.filter(role__in=services.ADMIN_ROLES).exclude(status="ended").first(),
        "features": [{"feature": f, "enabled": is_feature_enabled(company, f)} for f in Feature.objects.filter(is_active=True)],
        "events": AuditLog.objects.filter(company=company).select_related("actor_user")[:10],
    })


def _form_page(request, *, form, title, submit_label, action, company=None, explanation=""):
    if request.method == "POST" and form.is_valid():
        try:
            result = action(form.cleaned_data)
        except ValidationError as exc:
            if hasattr(exc, "message_dict"):
                for field, errors in exc.message_dict.items():
                    form.add_error(field if field in form.fields else None, errors)
            else:
                form.add_error(None, exc)
        except IntegrityError:
            form.add_error(None, "A conflicting record was saved. Check the values and try again.")
        else:
            messages.success(request, "Changes saved.")
            return redirect("platform:company_detail", public_id=(company or result).public_id)
    return render(request, "tenants/platform/form.html", {"form": form, "title": title, "submit_label": submit_label,
        "company": company, "explanation": explanation})


@platform_required
@require_http_methods(["GET", "POST"])
def company_create(request):
    form = CompanyForm(request.POST or None)
    return _form_page(request, form=form, title="Create company", submit_label="Create company",
        explanation="A Head Office branch and attendance settings will be created automatically. The company starts in Trial status.",
        action=lambda data: services.create_platform_company(actor=request.user, values=data))


@platform_required
@require_http_methods(["GET", "POST"])
def company_edit(request, public_id):
    company = get_object_or_404(Company, public_id=public_id)
    return _form_page(request, form=CompanyForm(request.POST or None, instance=company), company=company,
        title="Edit company", submit_label="Save company", action=lambda data: services.update_platform_company(actor=request.user, company_id=company.pk, values=data))


@platform_required
@require_http_methods(["GET", "POST"])
def company_status(request, public_id):
    company = get_object_or_404(Company, public_id=public_id)
    return _form_page(request, form=CompanyStatusForm(request.POST or None, initial={"status": company.status}), company=company,
        title="Change company status", submit_label="Save status",
        explanation="Suspended and inactive companies lose company-panel access on their next request. Existing records are preserved.",
        action=lambda data: services.change_company_status(actor=request.user, company_id=company.pk, **data))


@platform_required
@require_http_methods(["GET", "POST"])
def administrator_create(request, public_id):
    company = get_object_or_404(Company, public_id=public_id)
    def submit(data):
        values = {k: data[k] for k in ("email", "first_name", "last_name", "password")}
        return services.grant_company_administrator(actor=request.user, company_id=company.pk, **values)
    return _form_page(request, form=AdministratorForm(request.POST or None, company=company), company=company,
        title="Add company administrator", submit_label="Create administrator", action=submit,
        explanation="Create the single master login for this company. No role selection is needed. No email is sent; share the credentials securely.")


@platform_required
@require_http_methods(["GET", "POST"])
def membership_edit(request, public_id, membership_id):
    company = get_object_or_404(Company, public_id=public_id)
    member = get_object_or_404(CompanyMembership.all_objects, pk=membership_id, company=company, role__in=services.ADMIN_ROLES)
    initial = {field: getattr(member.user, field) for field in ("email", "first_name", "last_name")}
    initial["status"] = member.status
    def submit(data):
        data.pop("password_confirm", None)
        return services.edit_company_administrator(actor=request.user, company_id=company.pk, membership_id=member.pk, **data)
    return _form_page(request, form=AdministratorForm(request.POST or None, member=member, initial=initial), company=company,
        title="Edit company administrator", submit_label="Save administrator",
        explanation="Update this company's master login. Leave the password blank to retain it.", action=submit)



@platform_required
@require_http_methods(["GET", "POST"])
def company_feature(request, public_id):
    company = get_object_or_404(Company, public_id=public_id)
    def submit(data):
        return services.set_company_feature(actor=request.user, company_id=company.pk, feature_id=data["feature"].pk, effect=data["effect"], reason=data["reason"])
    return _form_page(request, form=CompanyFeatureForm(request.POST or None), company=company,
        title="Change feature access", submit_label="Save feature access", action=submit,
        explanation="Access is recorded from now forward. Enabling a feature does not implement its pages or grant employees permission to its actions.")
