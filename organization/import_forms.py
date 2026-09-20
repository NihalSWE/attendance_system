"""The upload form for the bulk employee import.

Only the file and the two pay defaults live here; everything the rows are
checked against is in ``organization.import_services``, because a crafted POST
straight to the confirm step must be checked the same way.
"""

from django import forms

from common.forms import StyledFormMixin
from employees.models import EmployeeCompensation


class EmployeeImportForm(StyledFormMixin, forms.Form):
    upload = forms.FileField(
        label="Spreadsheet",
        help_text="A .csv or .xlsx file with the template's column headings.",
        widget=forms.ClearableFileInput(attrs={"accept": ".csv,.xlsx,.xlsm,text/csv"}),
    )
    default_pay_basis = forms.ChoiceField(
        label="Pay basis for rows that leave it empty",
        required=False,
        choices=[("", "No default — every row must say")]
        + list(EmployeeCompensation.PayBasis.choices),
    )
    default_base_rate = forms.DecimalField(
        label="Base rate for rows that leave it empty",
        required=False, max_digits=18, decimal_places=2, min_value=0.01,
        help_text="Per month, day or hour, matching the pay basis above.",
    )

    def clean(self):
        cleaned = super().clean()
        # Everyone in one import is usually on the same footing, so one of the
        # two without the other is almost certainly a slip.
        basis = cleaned.get("default_pay_basis")
        rate = cleaned.get("default_base_rate")
        if basis and rate is None:
            self.add_error("default_base_rate",
                           "Give the rate as well, or leave the pay basis empty.")
        if rate is not None and not basis:
            self.add_error("default_pay_basis",
                           "Say what that rate is per, or leave the rate empty.")
        return cleaned
