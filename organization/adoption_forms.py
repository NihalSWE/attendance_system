"""Forms for a company's own departments and designations.

A form here is a convenience layer, never the security boundary:
``organization.adoption_services`` re-validates every value and re-checks row
scope before writing.
"""

from django import forms
from django.core.exceptions import NON_FIELD_ERRORS

from common.choices import ActiveStatus
from common.forms import StyledFormMixin
from employees.models import Employee
from organization.models import Branch, Department, Designation


class DepartmentAdoptionForm(StyledFormMixin, forms.ModelForm):
    """Create or edit one of the company's departments in a branch."""

    # branch and head point at tenant-owned models. Declared explicitly with an
    # empty queryset because ModelForm's metaclass would otherwise call their
    # default manager at import time, which is tenant-scoped and raises when no
    # company context exists yet. The view supplies the real, scoped querysets.
    branch = forms.ModelChoiceField(queryset=Branch.all_objects.none(), label="Branch")
    head = forms.ModelChoiceField(
        queryset=Employee.all_objects.none(), required=False, label="Head"
    )

    class Meta:
        model = Department
        fields = ("branch", "code", "name", "head", "description", "status")
        widgets = {"description": forms.TextInput()}
        help_texts = {
            "branch": "A branch uses each department code once.",
            "code": "A short code, unique within the branch (e.g. SW, HR).",
            "head": "Optional. The employee who administers people in this department.",
        }

    def __init__(self, *args, branches=None, employees=None, **kwargs):
        super().__init__(*args, **kwargs)
        if branches is not None:
            self.fields["branch"].queryset = branches
        if employees is not None:
            self.fields["head"].queryset = employees
        self.fields["head"].empty_label = "No head appointed yet"
        self.fields["branch"].empty_label = "Select a branch"

        if self.instance.pk:
            # The branch is fixed once created: changing it would move every
            # employee filed under this department to another branch silently.
            self.fields["branch"].disabled = True
            self.fields["branch"].help_text = (
                "Fixed once created. Add a separate department to use this "
                "code in another branch."
            )

    def _post_clean(self):
        """Move a duplicate-code error onto the code field.

        ``(branch, code)`` and ``(branch, name)`` are UniqueConstraints, which
        Django validates during ``_post_clean`` as a page-level error. Relocate
        it to the field the reader must change.
        """
        super()._post_clean()
        non_field = self._errors.get(NON_FIELD_ERRORS)
        if not non_field:
            return
        branch = self.cleaned_data.get("branch")
        if branch is None:
            return
        remaining = [m for m in non_field if "already exists" not in m.lower()]
        if len(remaining) == len(non_field):
            return
        if remaining:
            self._errors[NON_FIELD_ERRORS] = self.error_class(remaining)
        else:
            del self._errors[NON_FIELD_ERRORS]
        self.add_error(
            "code",
            f"{branch.name} already has a department with this code or name. "
            "Edit that entry instead of adding it again.",
        )


class DesignationForm(StyledFormMixin, forms.ModelForm):
    """Create or edit one designation (job title) inside a department."""

    department = forms.ModelChoiceField(
        queryset=Department.all_objects.none(), label="Department"
    )
    parent = forms.ModelChoiceField(
        queryset=Designation.all_objects.none(), required=False, label="Reports to",
        help_text="Optional. A senior title in the same department.",
    )

    class Meta:
        model = Designation
        fields = ("department", "code", "name", "parent", "status")
        help_texts = {"code": "A short code, unique within the department."}

    def __init__(self, *args, departments=None, designations=None, **kwargs):
        super().__init__(*args, **kwargs)
        if departments is not None:
            self.fields["department"].queryset = departments
        if designations is not None:
            self.fields["parent"].queryset = designations
        self.fields["department"].empty_label = "Select a department"
        self.fields["parent"].empty_label = "No parent"
        if self.instance.pk:
            # The department is fixed once created: moving a title would reshape
            # who holds it. Add a new one under another department instead.
            self.fields["department"].disabled = True
            self.fields["department"].help_text = "Fixed once created."
            # A title cannot be its own parent.
            self.fields["parent"].queryset = self.fields["parent"].queryset.exclude(
                pk=self.instance.pk
            )


class AdoptionStatusForm(StyledFormMixin, forms.Form):
    """Activate or deactivate a department or designation. There is no delete."""

    status = forms.ChoiceField(choices=ActiveStatus.choices, label="Status")


class CopyAdoptionsForm(StyledFormMixin, forms.Form):
    """Copy one branch's departments and their designations into another."""

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
