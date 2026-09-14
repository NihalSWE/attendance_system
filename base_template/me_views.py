"""My account: the pages an Employee or Branch manager login lands on.

The first of the employee's own pages. My attendance, leave and payslips join
them with the employee panel (plan step A7). Everyone signed in to a company
may open these; they only ever show the signed-in person's own record.
"""

from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.views import PasswordChangeView
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils import timezone

from accounts.models import CompanyMembership
from common.forms import StyledFormMixin
from common.tenant import use_company
from employees.models import Employee, EmployeeAssignment
from organization.employee_login import ROLE_LABELS
from scheduling.calendar import WorkCalendar


def _membership(request):
    return (
        CompanyMembership.all_objects.select_related("company")
        .prefetch_related("allowed_branches")
        .filter(company_id=request.company_id, user=request.user,
                status=CompanyMembership.Status.ACTIVE)
        .first()
    )


@login_required
def my_account(request):
    if request.user.is_superuser:
        return redirect("platform:company_list")
    if not request.company_id:
        return render(request, "base_template/no_company.html")
    membership = _membership(request)
    today = timezone.localdate()
    employee = placement = shift = None
    with use_company(request.company_id):
        employee = Employee.objects.filter(user=request.user).first()
        if employee is not None:
            placement = (
                EmployeeAssignment.objects.select_related(
                    "branch", "department__department", "designation__designation"
                )
                .filter(employee=employee, effective_to__isnull=True)
                .exclude(status__in=["cancelled", "draft"])
                .first()
            )
            calendar = WorkCalendar(request.company_id, today, today)
            shift = calendar.shift_for(
                placement.department_id if placement else None, today, employee_id=employee.pk
            )
    return render(request, "base_template/me/home.html", {
        "membership": membership,
        "role_label": ROLE_LABELS.get(membership.role, membership.get_role_display()) if membership else "",
        "managed_branches": list(membership.allowed_branches.all()) if membership else [],
        "employee": employee,
        "placement": placement,
        "shift": shift,
        "joined": (
            membership.joined_at.astimezone(ZoneInfo(membership.company.timezone or "UTC"))
            if membership and membership.joined_at else None
        ),
    })


class MyPasswordForm(StyledFormMixin, PasswordChangeForm):
    """Django's own change-password form, in the project's field style."""


class MyPasswordView(PasswordChangeView):
    form_class = MyPasswordForm
    template_name = "base_template/me/password.html"
    success_url = reverse_lazy("me:home")

    def form_valid(self, form):
        messages.success(self.request, "Password changed.")
        return super().form_valid(form)
