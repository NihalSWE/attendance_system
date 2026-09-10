"""Forms for adopting a catalogue department into a company branch.

A form here is a convenience layer, never the security boundary:
``organization.adoption_services`` re-validates every value and re-checks row
scope before writing. What the form does add is *not offering* an invalid
choice in the first place — a job title from another department would be
rejected by ``CompanyDesignation.clean()``, and making the user discover that
after submitting is a worse experience than never listing it.
"""

from django import forms
from django.core.exceptions import NON_FIELD_ERRORS

from common.choices import ActiveStatus
from employees.models import Employee
from common.forms import StyledFormMixin
from organization.models import (
    Branch,
    CompanyDepartment,
    Department,
    Designation,
)


class DepartmentAdoptionForm(StyledFormMixin, forms.ModelForm):
    """Adopt a catalogue department into one branch, with its job titles.

    ``designations`` is a multiselect narrowed to the chosen department. The
    narrowing happens twice on purpose: on GET from the bound instance or a
    ``?department=`` hint, and again on POST from the submitted department, so
    validation cannot be widened by editing the page.
    """

    # branch and head point at tenant-owned models. Declared explicitly with an
    # empty queryset because ModelForm's metaclass would otherwise call their
    # default manager at import time, which is tenant-scoped and raises when no
    # company context exists yet. The view supplies the real, scoped querysets.
    branch = forms.ModelChoiceField(
        queryset=Branch.all_objects.none(), label="Branch"
    )
    head = forms.ModelChoiceField(
        queryset=Employee.all_objects.none(), required=False, label="Head"
    )

    designations = forms.ModelMultipleChoiceField(
        queryset=Designation.objects.none(),
        required=False,
        label="Job titles",
        help_text=(
            "Only titles filed under the chosen department are listed. Pick the "
            "ones this branch actually uses; you can add more later."
        ),
    )

    class Meta:
        model = CompanyDepartment
        fields = ("branch", "department", "head", "description", "status")
        widgets = {"description": forms.TextInput()}
        help_texts = {
            "branch": "A branch adopts each department once.",
            "department": (
                "Chosen from the platform catalogue. The name is read through "
                "from there, so it cannot drift from the canonical spelling."
            ),
            "head": "Optional. The employee who administers people in this department.",
        }

    def __init__(self, *args, branches=None, employees=None, **kwargs):
        super().__init__(*args, **kwargs)

        # Branch choices come from the caller, already scoped to what this
        # member may see. The form never widens them.
        if branches is not None:
            self.fields["branch"].queryset = branches
        if employees is not None:
            self.fields["head"].queryset = employees
        self.fields["head"].required = False
        self.fields["head"].empty_label = "No head appointed yet"

        self.fields["department"].queryset = Department.objects.filter(
            status=ActiveStatus.ACTIVE
        ).order_by("name")
        self.fields["department"].empty_label = "Select a department"

        if self.instance.pk:
            # Branch and catalogue department are fixed once adopted: changing
            # either would silently move every employee filed under this row.
            for name in ("branch", "department"):
                self.fields[name].disabled = True
                self.fields[name].help_text = (
                    "Fixed once adopted. Create a separate adoption to use this "
                    "department in another branch."
                )

        department = self._selected_department()
        if department is not None:
            self.fields["designations"].queryset = Designation.objects.filter(
                department=department, status=ActiveStatus.ACTIVE
            ).order_by("name")
        if self.instance.pk and not self.is_bound:
            self.fields["designations"].initial = Designation.objects.filter(
                company_links__company_department=self.instance,
                company_links__status=ActiveStatus.ACTIVE,
            )

    def _selected_department(self):
        """The department in play, whether posted, bound or hinted at."""
        if self.is_bound:
            raw = self.data.get(self.add_prefix("department"))
            if self.instance.pk:
                return self.instance.department
            if raw:
                return Department.objects.filter(pk=raw).first()
            return None
        if self.instance.pk:
            return self.instance.department
        initial = self.initial.get("department")
        if initial:
            return Department.objects.filter(pk=initial).first()
        return None

    def _post_clean(self):
        """Move the repeat-adoption error onto the department field.

        ``(branch, department)`` is enforced by a UniqueConstraint, and Django
        validates constraints during ``_post_clean``, reporting the failure as
        a page-level error. That makes the reader hunt for which input is
        wrong, so it is relocated to the field they actually need to change.
        Only that one message moves; any other non-field error is left alone.
        """
        super()._post_clean()
        non_field = self._errors.get(NON_FIELD_ERRORS)
        if not non_field or "department" not in self.fields:
            return

        branch = self.cleaned_data.get("branch")
        department = self.cleaned_data.get("department")
        if not branch or not department:
            return

        remaining = [
            message for message in non_field
            if "already exists" not in message.lower()
        ]
        if len(remaining) == len(non_field):
            return

        if remaining:
            self._errors[NON_FIELD_ERRORS] = self.error_class(remaining)
        else:
            del self._errors[NON_FIELD_ERRORS]
        self.add_error(
            "department",
            f"{branch.name} has already adopted {department.name}. Edit that "
            "entry instead of adding it a second time.",
        )

    def clean(self):
        cleaned = super().clean()
        department = (
            self.instance.department if self.instance.pk else cleaned.get("department")
        )
        titles = cleaned.get("designations") or []
        if department is not None and titles:
            wrong = [t for t in titles if t.department_id != department.pk]
            if wrong:
                self.add_error(
                    "designations",
                    "These job titles belong to a different department: "
                    + ", ".join(sorted(t.name for t in wrong)),
                )
        return cleaned


class AdoptionStatusForm(StyledFormMixin, forms.Form):
    """Activate or deactivate an adopted department. There is no delete."""

    status = forms.ChoiceField(choices=ActiveStatus.choices, label="Status")
