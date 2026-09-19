"""Forms for editing an employee. Three independent sections on one page.

Field names are distinct across the three forms (``placement_from``,
``salary_from`` ...) so they can share a page without prefixes; the placement
form keeps the plain ``branch`` / ``department`` / ``designation`` ids that the
dependent-select script (employee_form.js) looks for.
"""

from datetime import datetime, time
from zoneinfo import ZoneInfo

from django import forms

from common.forms import StyledFormMixin
from employees.models import Employee, EmployeeCompensation
from organization.employee_forms import EmployeeCreateForm
from organization.models import Branch, Department, Designation


def _date(placeholder):
    return forms.DateInput(
        format="%Y-%m-%d",
        attrs={"type": "date", "data-datepicker": "", "data-placeholder": placeholder},
    )


def _start_of(value, company):
    zone = ZoneInfo(getattr(company, "timezone", None) or "UTC")
    return datetime.combine(value, time.min, tzinfo=zone)


class EmployeeDetailsForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Employee
        fields = ("first_name", "last_name", "work_email", "phone", "joining_date")
        labels = {"work_email": "Work email", "joining_date": "Joining date"}
        help_texts = {
            "joining_date": "Days before this are not counted in attendance or salary.",
        }
        widgets = {"joining_date": _date("Select joining date")}


class PlacementForm(StyledFormMixin, forms.Form):
    """Branch, department, designation and code, from a date."""

    branch = forms.ModelChoiceField(queryset=Branch.all_objects.none(), label="Branch")
    department = forms.ModelChoiceField(
        queryset=Department.all_objects.none(), label="Department",
        help_text="Only departments added to this branch are listed.",
    )
    designation = forms.ModelChoiceField(
        queryset=Designation.all_objects.none(), label="Designation",
        help_text="Only designations assigned to the chosen department are listed.",
    )
    employee_code = forms.CharField(max_length=64, label="Employee code")
    placement_from = forms.DateField(
        label="From",
        help_text=(
            "A later date keeps the old placement as history. The date the current "
            "placement started corrects it instead."
        ),
        widget=_date("Select date"),
    )
    placement_reason = forms.CharField(label="Reason", required=False)

    # Reuse the create form's narrowing so the same combinations are offered.
    _chosen = EmployeeCreateForm._chosen
    _departments_for = staticmethod(EmployeeCreateForm._departments_for)
    _designations_for = staticmethod(EmployeeCreateForm._designations_for)

    def __init__(self, *args, company=None, branches=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.company = company
        if branches is not None:
            self.fields["branch"].queryset = branches
        self.fields["branch"].empty_label = "Select a branch"
        self.fields["department"].empty_label = "Select a department"
        self.fields["designation"].empty_label = "Select a designation"
        branch = self._chosen("branch", Branch)
        self.fields["department"].queryset = self._departments_for(branch)
        department = self._chosen("department", Department)
        self.fields["designation"].queryset = self._designations_for(department)

    def clean(self):
        cleaned = super().clean()
        branch, department = cleaned.get("branch"), cleaned.get("department")
        designation = cleaned.get("designation")
        if branch and department and department.branch_id != branch.pk:
            self.add_error("department", "That department is not added to this branch.")
        if department and designation and designation.department_id != department.pk:
            self.add_error("designation", "That designation is not assigned to this department.")
        if cleaned.get("placement_from"):
            cleaned["effective_at"] = _start_of(cleaned["placement_from"], self.company)
        return cleaned

    def service_values(self):
        data = self.cleaned_data
        return {
            "branch": data["branch"], "department": data["department"],
            "designation": data["designation"], "employee_code": data["employee_code"],
            "effective_at": data["effective_at"], "reason": data.get("placement_reason", ""),
        }


class SalaryForm(StyledFormMixin, forms.Form):
    pay_basis = forms.ChoiceField(
        choices=EmployeeCompensation.PayBasis.choices, label="Pay basis"
    )
    base_rate = forms.DecimalField(
        max_digits=18, decimal_places=2, min_value=0.01, label="Rate",
        help_text="Per month, day or hour, matching the pay basis.",
    )
    salary_from = forms.DateField(
        label="From",
        help_text=(
            "A later date keeps the old salary as history. The date the current "
            "salary started corrects it instead."
        ),
        widget=_date("Select date"),
    )
    salary_reason = forms.CharField(label="Reason", required=False)

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.company = company

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("salary_from"):
            cleaned["effective_at"] = _start_of(cleaned["salary_from"], self.company)
        return cleaned

    def service_values(self):
        data = self.cleaned_data
        return {
            "pay_basis": data["pay_basis"], "base_rate": data["base_rate"],
            "effective_at": data["effective_at"], "reason": data.get("salary_reason", ""),
        }


# --------------------------------------------------------------------------
# Login (A6). Field names carry a login_ prefix so they do not clash with the
# placement form's branch field on the same page.
# --------------------------------------------------------------------------

LOGIN_ROLE_CHOICES = (("employee", "Employee"), ("manager", "Branch manager"))


class _LoginRoleFields(forms.Form):
    login_role = forms.ChoiceField(
        label="Access", choices=LOGIN_ROLE_CHOICES,
        help_text="Employee: their own pages. Branch manager: also their branch's people (coming with leave approval).",
    )
    login_branches = forms.ModelMultipleChoiceField(
        queryset=Branch.all_objects.none(), required=False, label="Branches they manage",
        help_text="Only for a branch manager.",
    )

    def __init__(self, *args, branches=None, **kwargs):
        super().__init__(*args, **kwargs)
        if branches is not None:
            self.fields["login_branches"].queryset = branches
        # Only a branch manager has branches (dependent.js).
        self.fields["login_branches"].widget.attrs["data-show-when"] = "login_role:manager"
        self.fields["login_branches"].help_text = ""

    def role_values(self):
        return {
            "role": self.cleaned_data["login_role"],
            "branches": list(self.cleaned_data.get("login_branches") or []),
        }


class GiveLoginForm(StyledFormMixin, _LoginRoleFields):
    login_email = forms.EmailField(label="Email they sign in with")
    login_password = forms.CharField(
        label="Password", strip=False, widget=forms.PasswordInput(render_value=False),
        help_text="Give it to them; they can change it after signing in.",
    )
    login_password_confirm = forms.CharField(
        label="Password again", strip=False, widget=forms.PasswordInput(render_value=False),
    )

    field_order = ("login_email", "login_password", "login_password_confirm", "login_role", "login_branches")

    def service_values(self):
        return {
            "email": self.cleaned_data["login_email"],
            "password": self.cleaned_data["login_password"],
            "password_confirm": self.cleaned_data["login_password_confirm"],
            **self.role_values(),
        }


class LoginRoleForm(StyledFormMixin, _LoginRoleFields):
    pass


class LoginPasswordForm(StyledFormMixin, forms.Form):
    login_new_password = forms.CharField(
        label="New password", strip=False, widget=forms.PasswordInput(render_value=False),
    )
    login_new_password_confirm = forms.CharField(
        label="New password again", strip=False, widget=forms.PasswordInput(render_value=False),
    )

    def service_values(self):
        return {
            "password": self.cleaned_data["login_new_password"],
            "password_confirm": self.cleaned_data["login_new_password_confirm"],
        }
