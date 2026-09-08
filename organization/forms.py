"""Organization forms using the shared project input conventions.

A form is a convenience layer, not a security boundary: organization.services
re-validates every submitted value and re-checks row scope before writing.
"""

from django import forms

from common.choices import ActiveStatus
from common.forms import BangladeshPhoneInput, StyledFormMixin
from organization.models import Branch


class BranchForm(StyledFormMixin, forms.ModelForm):
    """Create/edit a branch.

    `company` is never a form field — it comes from the request's tenant context,
    so a crafted POST cannot move a branch into another company.
    """

    class Meta:
        model = Branch
        fields = (
            "code", "name", "city", "address", "postal_code",
            "timezone", "email", "phone",
            "is_default", "status", "opened_on", "closed_on",
        )
        widgets = {
            # Single-line address, per the project input conventions.
            "address": forms.TextInput(),
            "phone": BangladeshPhoneInput(),
            # data-datepicker swaps in our own calendar (datepicker.js);
            # the native input still holds and posts the value.
            "opened_on": forms.DateInput(
                attrs={"type": "date", "data-datepicker": "",
                       "data-placeholder": "Select opening date"}),
            "closed_on": forms.DateInput(
                attrs={"type": "date", "data-datepicker": "",
                       "data-placeholder": "Not closed"}),
        }
        labels = {
            "code": "Branch code",
            "is_default": "Use as the company's default branch",
        }
        help_texts = {
            "code": "Short identifier, unique within this company. Cannot be reused "
                    "by another active branch.",
            "closed_on": "Set only when the branch has actually closed.",
        }

    def __init__(self, *args, company=None, **kwargs):
        self.company = company
        super().__init__(*args, **kwargs)

        # Fixed choice lists use styled native selects (project convention);
        # only database-backed choices use Select2.
        self.fields["status"].choices = ActiveStatus.choices
        # The default branch cannot un-default itself; another branch must be
        # promoted instead, so the constraint is never momentarily violated.
        if self.instance.pk and self.instance.is_default:
            self.fields["is_default"].disabled = True
            self.fields["is_default"].help_text = (
                "This is the default branch. Promote another branch to change it."
            )

    def clean_code(self):
        return (self.cleaned_data.get("code") or "").strip().upper()

    def clean(self):
        data = super().clean()
        opened, closed = data.get("opened_on"), data.get("closed_on")
        if opened and closed and closed < opened:
            self.add_error("closed_on", "Closing date cannot be before the opening date.")
        return data


class BranchStatusForm(forms.Form):
    """Retire or reactivate a branch, with a reason recorded in the audit log."""

    status = forms.ChoiceField(choices=ActiveStatus.choices)
    reason = forms.CharField(
        widget=forms.Textarea(attrs={"class": "textarea", "rows": 3}),
        max_length=2000,
        required=False,
        help_text="Recorded in the audit trail.",
    )
