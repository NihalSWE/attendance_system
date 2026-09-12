"""Forms for adding a root department to a company branch.

A form here is a convenience layer, never the security boundary:
``organization.adoption_services`` re-validates every value and re-checks row
scope before writing.

Designations are **not** filtered by department. Root keeps the two lists
independent, and which designations a department uses is this company's own
decision — so every active designation is offered for every department.
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
    """Add a root department to one branch, and assign designations to it.

    ``designations`` offers every active designation, unfiltered: the two root
    lists are independent, so this company is free to say that Manager belongs
    under its Sales department and under its Production department too.
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
        label="Designations",
        help_text=(
            "Every designation on the platform is available — you decide which "
            "ones this department uses. You can add more later."
        ),
    )

    class Meta:
        model = CompanyDepartment
        fields = ("branch", "department", "head", "description", "status")
        widgets = {"description": forms.TextInput()}
        help_texts = {
            "branch": "A branch uses each department once.",
            "department": (
                "Chosen from the platform list. The name is read through from "
                "there, so it cannot drift from the canonical spelling."
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
                    "Fixed once added. Add it separately to use this "
                    "department in another branch."
                )

        self.fields["designations"].queryset = Designation.objects.filter(
            status=ActiveStatus.ACTIVE
        ).order_by("name")
        if self.instance.pk and not self.is_bound:
            self.fields["designations"].initial = Designation.objects.filter(
                company_links__company_department=self.instance,
                company_links__status=ActiveStatus.ACTIVE,
            )

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
            f"{branch.name} already has {department.name}. Edit that entry "
            "instead of adding it a second time.",
        )


class AdoptionStatusForm(StyledFormMixin, forms.Form):
    """Activate or deactivate one of the company's departments.

    There is no delete.
    """

    status = forms.ChoiceField(choices=ActiveStatus.choices, label="Status")


class CopyAdoptionsForm(StyledFormMixin, forms.Form):
    """Copy one branch's departments and their designations into another.

    A department belongs to one branch by design, so a new branch starts
    empty. This is a convenience over that, not a change to it: it creates
    real rows for the target branch rather than sharing the source's.
    """

    source_branch = forms.ModelChoiceField(
        queryset=Branch.all_objects.none(),
        label="Copy from",
        help_text="Its active departments and their designations are copied.",
    )
    target_branch = forms.ModelChoiceField(
        queryset=Branch.all_objects.none(),
        label="Copy into",
        help_text=(
            "Departments this branch already has are left untouched, so this "
            "is safe to run again after adding one more to the source."
        ),
    )

    def __init__(self, *args, branches=None, **kwargs):
        super().__init__(*args, **kwargs)
        if branches is not None:
            self.fields["source_branch"].queryset = branches
            self.fields["target_branch"].queryset = branches
        self.fields["source_branch"].empty_label = "Select a branch"
        self.fields["target_branch"].empty_label = "Select a branch"

    def clean(self):
        cleaned = super().clean()
        source = cleaned.get("source_branch")
        target = cleaned.get("target_branch")
        if source and target and source.pk == target.pk:
            self.add_error("target_branch", "Choose a different branch to copy into.")
        return cleaned
