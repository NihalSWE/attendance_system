"""AI assistant forms. Convenience only: the services check everything again."""

from django import forms
from django.contrib.auth import get_user_model

from accounts.models import CompanyMembership
from ai_assistant.models import AiRecipient, AiSettings
from common.forms import StyledFormMixin, date_widget
from inventory.forms import CompanyForm
from inventory.models import Item, Uom
from sales.models import Customer


class PasteForm(StyledFormMixin, forms.Form):
    sender = forms.CharField(label="From (name, phone or email)", max_length=255, required=False)
    body = forms.CharField(label="The order as written", widget=forms.Textarea(attrs={"rows": 8}),
                           max_length=20000,
                           help_text="Paste the WhatsApp message, email or phone note, in Bangla or English.")


class ReasonForm(StyledFormMixin, forms.Form):
    reason = forms.CharField(max_length=255, label="Why")


class CustomerForm(StyledFormMixin, forms.Form):
    customer = forms.ModelChoiceField(queryset=Customer.all_objects.none(), label="Customer")

    def __init__(self, *args, company_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["customer"].queryset = Customer.all_objects.filter(
            company_id=company_id, status="active").order_by("name")


class MatchForm(StyledFormMixin, forms.Form):
    index = forms.IntegerField(widget=forms.HiddenInput)
    item = forms.ModelChoiceField(queryset=Item.all_objects.none(), label="It is")
    qty = forms.DecimalField(label="Quantity", max_digits=18, decimal_places=4, min_value=0.0001)
    uom = forms.ModelChoiceField(queryset=Uom.all_objects.none(), label="Unit")
    remember = forms.BooleanField(label="Remember these words for this item", required=False, initial=True)

    def __init__(self, *args, company_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item"].queryset = Item.all_objects.filter(company_id=company_id, status="active").order_by("code")
        self.fields["item"].label_from_instance = lambda i: f"{i.code} · {i.name}"
        self.fields["uom"].queryset = Uom.all_objects.filter(company_id=company_id, status="active").order_by("code")


class SettingsForm(StyledFormMixin, forms.ModelForm):
    api_key = forms.CharField(label="Anthropic API key", required=False, widget=forms.PasswordInput,
                              help_text="Write-only: a saved key is never shown. Leave empty to keep it.")
    imap_password = forms.CharField(label="Inbox password", required=False, widget=forms.PasswordInput,
                                    help_text="Write-only. Leave empty to keep the saved one.")
    draft_user = forms.ModelChoiceField(queryset=get_user_model().objects.none(), required=False,
                                        label="Draft orders are entered as")

    class Meta:
        model = AiSettings
        fields = ("consent_received", "consent_date", "consent_note", "enabled", "model", "effort",
                  "daily_cost_cap", "draft_user", "inbox_on", "imap_host", "imap_port", "imap_username",
                  "imap_folder")
        widgets = {"consent_date": date_widget()}

    def __init__(self, *args, company_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        people = CompanyMembership.all_objects.filter(company_id=company_id, status="active").values("user_id")
        self.fields["draft_user"].queryset = get_user_model().objects.filter(pk__in=people).order_by("email")
        self.fields["draft_user"].label_from_instance = lambda u: u.get_full_name() or u.email
        self.fields["draft_user"].help_text = ("A login with 'Enter sales orders'. Whoever confirms must be "
                                              "someone else.")

    def _post_clean(self):
        pass


class RecipientForm(CompanyForm):
    class Meta:
        model = AiRecipient
        fields = ("name", "designation", "email", "gets_orders", "gets_daily", "gets_weekly")

    def _post_clean(self):
        pass
