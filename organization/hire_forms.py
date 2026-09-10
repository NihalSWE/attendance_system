"""Form for hiring an employee into a branch, department and job title.

The domain logic lives in ``employees.services.hire_employee`` and is already
tested; this only collects and narrows the input. Branch, department and job
title are dependent: each narrows the next, so a combination that the model
would reject is never offered.

``employee_code`` is deliberately manual. It is reusable by someone else once
the previous holder's interval ends, which an auto-generated code could not
express, and an exclusion constraint enforces the overlap rule in the
database. A violation is translated into a field error by the view.
"""

from datetime import datetime, time
from zoneinfo import ZoneInfo

from django import forms

from common.choices import ActiveStatus
from common.forms import StyledFormMixin
from employees.models import Employee, EmployeeCompensation
from organization.models import Branch, CompanyDepartment, CompanyDesignation


class HireEmployeeForm(StyledFormMixin, forms.Form):
    """Collect one person's identity, placement and starting pay."""

    first_name = forms.CharField(max_length=150, label="First name")
    last_name = forms.CharField(max_length=150, required=False, label="Last name")
    employee_code = forms.CharField(
        max_length=64,
        label="Employee code",
        help_text=(
            "Your own reference for this person. It can be reused by someone "
            "else after this person's placement ends, but not while it is open."
        ),
    )

    # Tenant-owned querysets are supplied by the view. They start empty because
    # ModelChoiceField would otherwise call the tenant-scoped default manager at
    # import time, before any company context exists.
    branch = forms.ModelChoiceField(
        queryset=Branch.all_objects.none(), label="Branch"
    )
    department = forms.ModelChoiceField(
        queryset=CompanyDepartment.all_objects.none(),
        label="Department",
        help_text="Only departments this branch has adopted are listed.",
    )
    designation = forms.ModelChoiceField(
        queryset=CompanyDesignation.all_objects.none(),
        label="Job title",
        help_text="Only job titles belonging to the chosen department are listed.",
    )
    manager = forms.ModelChoiceField(
        queryset=Employee.all_objects.none(), required=False, label="Manager"
    )

    effective_from = forms.DateField(
        label="Start date",
        widget=forms.DateInput(attrs={
            "type": "date",
            "data-datepicker": "",
            "data-placeholder": "Select start date",
        }),
        help_text="The date this placement and pay take effect.",
    )
    pay_basis = forms.ChoiceField(
        choices=EmployeeCompensation.PayBasis.choices, label="Pay basis"
    )
    base_rate = forms.DecimalField(
        max_digits=18, decimal_places=2, min_value=0.01, label="Base rate",
        help_text="Per month, day or hour, matching the pay basis.",
    )

    def __init__(self, *args, company=None, branches=None, employees=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.company = company

        if branches is not None:
            self.fields["branch"].queryset = branches
        if employees is not None:
            self.fields["manager"].queryset = employees
        self.fields["manager"].empty_label = "No manager"
        self.fields["branch"].empty_label = "Select a branch"
        self.fields["department"].empty_label = "Select a department"
        self.fields["designation"].empty_label = "Select a job title"

        # Narrow the dependent fields from whatever is already chosen, so a
        # re-rendered form after an error still validates the same way.
        branch = self._chosen("branch", Branch)
        self.fields["department"].queryset = self._departments_for(branch)

        department = self._chosen("department", CompanyDepartment)
        self.fields["designation"].queryset = self._titles_for(department)

    def _chosen(self, field, model):
        raw = self.data.get(self.add_prefix(field)) if self.is_bound else self.initial.get(field)
        if not raw:
            return None
        return model.objects.filter(pk=raw).first()

    @staticmethod
    def _departments_for(branch):
        if branch is None:
            return CompanyDepartment.objects.none()
        return (
            CompanyDepartment.objects.filter(
                branch=branch, status=ActiveStatus.ACTIVE
            )
            .select_related("department")
            .order_by("department__name")
        )

    @staticmethod
    def _titles_for(department):
        if department is None:
            return CompanyDesignation.objects.none()
        return (
            CompanyDesignation.objects.filter(
                company_department=department, status=ActiveStatus.ACTIVE
            )
            .select_related("designation")
            .order_by("designation__name")
        )

    def clean_effective_from(self):
        """Turn the picked date into an aware instant at local midnight.

        ``effective_from`` is a DateTimeField on the dated rows, and the
        exclusion constraints compare instants. Handing it a naive value would
        store an ambiguous one and warn; the company's own timezone is the
        right frame, since that is where the working day starts.
        """
        value = self.cleaned_data["effective_from"]
        zone = ZoneInfo(getattr(self.company, "timezone", None) or "UTC")
        return datetime.combine(value, time.min, tzinfo=zone)

    def clean(self):
        cleaned = super().clean()
        branch = cleaned.get("branch")
        department = cleaned.get("department")
        designation = cleaned.get("designation")

        # Re-check the chain rather than trusting the narrowed querysets: the
        # form is a convenience, and a crafted POST reaches this method too.
        if branch and department and department.branch_id != branch.pk:
            self.add_error(
                "department", "That department is not adopted by this branch."
            )
        if department and designation and designation.company_department_id != department.pk:
            self.add_error(
                "designation", "That job title does not belong to this department."
            )
        return cleaned
