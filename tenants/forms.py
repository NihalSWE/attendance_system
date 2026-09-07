"""Platform forms using the shared project input conventions."""
from django import forms
from django.contrib.auth.password_validation import password_validators_help_text_html
from accounts.models import CompanyMembership
from common.forms import BangladeshPhoneInput, StyledFormMixin
from tenants.models import Company, CompanyFeature, Feature
from tenants.platform_services import validate_company_values


class CompanyForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Company
        fields = ("name", "legal_name", "email", "phone", "address")
        widgets = {"address": forms.TextInput(), "phone": BangladeshPhoneInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            for name in ("code", "slug"):
                self.fields[name] = forms.CharField(
                    initial=getattr(self.instance, name), disabled=True, required=False,
                    widget=forms.TextInput(attrs={"class": "input", "readonly": True}),
                    help_text="Generated automatically and retained when the name changes.",
                )

    def clean(self):
        return validate_company_values(super().clean())


class CompanyStatusForm(StyledFormMixin, forms.Form):
    status = forms.ChoiceField(choices=Company.Status.choices)
    reason = forms.CharField(widget=forms.Textarea, max_length=2000)


class AdministratorForm(StyledFormMixin, forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "username"}))
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}), help_text=password_validators_help_text_html())
    password_confirm = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}), label="Confirm password")

    def __init__(self, *args, company=None, member=None, **kwargs):
        super().__init__(*args, **kwargs)
        if member:
            self.fields["password"].required = False
            self.fields["password_confirm"].required = False
            self.fields["password"].help_text = "Leave blank to keep the current password. " + str(password_validators_help_text_html())
            self.fields["status"] = forms.ChoiceField(
                choices=CompanyMembership.Status.choices,
                widget=forms.Select(attrs={"class": "input select"}),
            )

    def clean(self):
        data = super().clean()
        if data.get("password") != data.get("password_confirm"):
            self.add_error("password_confirm", "Passwords do not match.")
        return data


class CompanyFeatureForm(StyledFormMixin, forms.Form):
    feature = forms.ModelChoiceField(queryset=Feature.objects.filter(is_active=True))
    effect = forms.ChoiceField(choices=CompanyFeature.Effect.choices)
    reason = forms.CharField(widget=forms.Textarea, max_length=2000)
