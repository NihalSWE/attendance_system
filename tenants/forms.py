"""Platform forms using the shared project input conventions."""
from django import forms
from accounts.models import CompanyMembership
from common.forms import BD_MOBILE_HELP, BangladeshPhoneInput, StyledFormMixin, normalize_bd_mobile
from tenants.models import Company, CompanyFeature, Feature
from tenants.platform_services import validate_company_values


class CompanyForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Company
        # No legal name (Nihal, 2026-09-28): the name is enough.
        fields = ("name", "email", "phone", "address")
        widgets = {"address": forms.TextInput(), "phone": BangladeshPhoneInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["phone"].label = "Phone / mobile number"
        self.fields["phone"].help_text = BD_MOBILE_HELP
        if self.instance.pk:
            for name in ("code", "slug"):
                self.fields[name] = forms.CharField(
                    initial=getattr(self.instance, name), disabled=True, required=False,
                    widget=forms.TextInput(attrs={"class": "input", "readonly": True}),
                    help_text="Generated automatically and retained when the name changes.",
                )

    def clean_phone(self):
        return normalize_bd_mobile(self.cleaned_data.get("phone"))

    def clean(self):
        return validate_company_values(super().clean())


#: What a password must be, in one line (Django's own list printed its HTML).
PASSWORD_HELP = "At least 8 characters, not only numbers, and not a common password."


class CompanyCreateForm(StyledFormMixin, forms.Form):
    """Create company: the company and its administrator's login on one form
    (Nihal, 2026-09-28). The email is the company's and the login's."""

    name = forms.CharField(max_length=255, label="Name")
    email = forms.EmailField(label="Email", widget=forms.EmailInput(attrs={"autocomplete": "username"}),
                             help_text="The company's email, and the administrator signs in with it.")
    phone = forms.CharField(required=False, label="Phone / mobile number",
                            help_text=BD_MOBILE_HELP, widget=BangladeshPhoneInput())
    address = forms.CharField(required=False, label="Address", widget=forms.TextInput())
    password = forms.CharField(label="Password", help_text=PASSWORD_HELP,
                               widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password_confirm = forms.CharField(label="Confirm password",
                                       widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    status = forms.ChoiceField(label="Status", initial=Company.Status.TRIAL,
                               choices=[(Company.Status.TRIAL, "Trial"),
                                        (Company.Status.ACTIVE, "Active")])

    def clean_phone(self):
        return normalize_bd_mobile(self.cleaned_data.get("phone"))

    def clean(self):
        data = super().clean()
        if data.get("password") and data.get("password") != data.get("password_confirm"):
            self.add_error("password_confirm", "Passwords do not match.")
        return data


class CompanyStatusForm(StyledFormMixin, forms.Form):
    status = forms.ChoiceField(choices=Company.Status.choices)
    reason = forms.CharField(widget=forms.Textarea, max_length=2000)


class AdministratorForm(StyledFormMixin, forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "username"}))
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}), help_text=PASSWORD_HELP)
    password_confirm = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}), label="Confirm password")

    def __init__(self, *args, company=None, member=None, **kwargs):
        super().__init__(*args, **kwargs)
        if member:
            self.fields["password"].required = False
            self.fields["password_confirm"].required = False
            self.fields["password"].help_text = "Leave blank to keep the current password."
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
