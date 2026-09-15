"""The end-employment form (plan step N6). Rules live in the service."""

from django import forms

from common.forms import StyledFormMixin, date_widget
from organization.employee_detail_services import ENDING_STATUSES


class EndEmploymentForm(StyledFormMixin, forms.Form):
    last_day = forms.DateField(
        label="Last working day",
        widget=date_widget("Choose a date"),
        help_text="The last day they worked. Nothing is counted for them after it.",
    )
    status = forms.ChoiceField(label="Why they left", choices=ENDING_STATUSES)
    reason = forms.CharField(
        label="Note for the record",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "e.g. Resigned to study abroad; notice served"}),
    )
    disable_login = forms.BooleanField(
        label="Disable their login", required=False, initial=True,
        help_text="They can no longer sign in to this company. It can be enabled again on Edit employee.",
    )
    end_device_enrollments = forms.BooleanField(
        label="End their device enrollments", required=False, initial=True,
        help_text=(
            "Their user numbers stop counting for them after the last day. They "
            "stay on the terminals themselves until removed on the device's users page."
        ),
    )
