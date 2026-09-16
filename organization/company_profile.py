"""The company's own profile: who they are, and the logo the panel shows.

Kept deliberately small (Ajay, 2026-09-16): a name, a contact person, the ways
to reach them, and a logo. More fields can follow. Only the owner and company
administrator may change it, and the write shares one transaction with its
audit row like every other company write.
"""

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction

from auditlog.services import record_company_event
from common.forms import BangladeshPhoneInput, StyledFormMixin
from organization.services import require_structure_manager
from tenants.models import Company

#: What the page may change. Anything else on Company (status, code, feature
#: access) stays with the platform operator.
PROFILE_FIELDS = (
    "name", "legal_name", "contact_person", "email", "phone", "address", "logo",
)

#: A browser can be told what to offer, and the service checks it again.
LOGO_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".svg")
MAX_LOGO_BYTES = 2 * 1024 * 1024


class CompanyProfileForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Company
        fields = PROFILE_FIELDS
        labels = {
            "name": "Company name",
            "legal_name": "Registered name",
            "contact_person": "Contact person",
            "email": "Email",
            "phone": "Phone",
            "address": "Address",
            "logo": "Logo",
        }
        help_texts = {
            "name": "Shown across the panel and on payslips.",
            "logo": (
                "PNG, JPG, WEBP or SVG, up to 2 MB. Any shape works — it is "
                "scaled to fit the sidebar."
            ),
        }
        widgets = {
            "address": forms.TextInput(),
            "phone": BangladeshPhoneInput(),
            # Rendered by hand in the template (preview, file button, and
            # the "remove" box in our own checkbox style).
            "logo": forms.ClearableFileInput(attrs={"accept": ",".join(LOGO_SUFFIXES)}),
        }

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise ValidationError("A company name is required.")
        return name

    def clean_logo(self):
        return check_logo(self.cleaned_data.get("logo"))


def check_logo(logo):
    """The logo is a file from a browser: check what it is before storing it."""
    name = getattr(logo, "name", "") or ""
    # An unchanged logo arrives as the stored FieldFile, not a new upload.
    if not logo or not hasattr(logo, "size"):
        return logo
    if not name.lower().endswith(LOGO_SUFFIXES):
        raise ValidationError(
            "Use a PNG, JPG, WEBP or SVG image."
        )
    if logo.size > MAX_LOGO_BYTES:
        raise ValidationError("That image is larger than 2 MB. Use a smaller one.")
    return logo


def _snapshot(company):
    return {field: str(getattr(company, field) or "") for field in PROFILE_FIELDS}


@transaction.atomic
def save_profile(*, actor, company_id, form):
    """Store the submitted profile. Owner and company administrator only."""
    membership = require_structure_manager(actor, company_id)
    company = Company.objects.get(pk=company_id)
    before = _snapshot(company)
    for field in PROFILE_FIELDS:
        if field in form.cleaned_data:
            setattr(company, field, form.cleaned_data[field])
    check_logo(company.logo)
    company.full_clean(exclude=["slug", "code"])
    company.save()
    record_company_event(
        actor=actor, membership=membership, company=company,
        action="company.profile_updated", obj=company,
        before=before, after=_snapshot(company),
    )
    return company
