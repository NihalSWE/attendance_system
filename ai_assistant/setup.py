"""Keeping the AI assistant's settings and recipients (owner or company admin)."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.forms import EmailField

from access_control.branch_access import can
from ai_assistant.claude import encrypt
from ai_assistant.intake import require_settings_keeper
from ai_assistant.models import AiRecipient, AiSettings
from auditlog.services import record_company_event
from common.tenant import use_company
from inventory.doc_common import _writable

FIELDS = ("consent_received", "consent_date", "consent_note", "enabled", "model", "effort", "daily_cost_cap",
          "draft_user", "inbox_on", "imap_host", "imap_port", "imap_username", "imap_folder")
SHOWN = FIELDS[:-4] + ("inbox_on", "imap_host", "imap_username")


def _snapshot(row):
    """What the audit log keeps: never a key or password, only whether one is saved."""
    snap = {field: str(getattr(row, field + "_id" if field == "draft_user" else field)) for field in SHOWN}
    snap.update(key_saved=bool(row.api_key_encrypted), inbox_password_saved=bool(row.imap_password_encrypted))
    return snap


@transaction.atomic
def save_settings(*, actor, company_id, values, api_key="", imap_password=""):
    membership = require_settings_keeper(actor, company_id)
    values = _writable(values, FIELDS)
    with use_company(company_id):
        row = AiSettings.objects.select_for_update().first() or AiSettings(company=membership.company,
                                                                           created_by=actor)
        before = _snapshot(row) if row.pk else {}
        for field, value in values.items():
            setattr(row, field, value)
        if api_key.strip():
            row.api_key_encrypted = encrypt(api_key.strip())
        if imap_password:
            row.imap_password_encrypted = encrypt(imap_password)
        if row.consent_received and not row.consent_date:
            raise ValidationError({"consent_date": "Give the date of the client's written consent."})
        if row.consent_received and not before.get("consent_received") == "True":
            row.consent_recorded_by = actor
        if row.enabled:
            if not row.consent_received:
                raise ValidationError({"enabled": "Record the client's written consent first."})
            if not row.api_key_encrypted:
                raise ValidationError({"api_key": "Save the Anthropic API key first."})
            if row.draft_user_id is None:
                raise ValidationError({"draft_user": "Choose the login AI drafts are entered as."})
            if not can(row.draft_user, company_id, "sales.order"):
                raise ValidationError({"draft_user": "That login cannot enter sales orders."})
        if row.inbox_on and not (row.imap_host and row.imap_username and row.imap_password_encrypted):
            raise ValidationError({"inbox_on": "Give the inbox server, username and password to read it."})
        row.updated_by = actor
        row.full_clean()
        row.save()
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="ai.settings.updated", obj=row, before=before, after=_snapshot(row))
    return row


@transaction.atomic
def add_recipient(*, actor, company_id, values):
    membership = require_settings_keeper(actor, company_id)
    values = _writable(values, ("name", "designation", "email", "gets_orders", "gets_daily", "gets_weekly"))
    EmailField().clean(values.get("email"))
    with use_company(company_id):
        if AiRecipient.objects.filter(email__iexact=values["email"]).exists():
            raise ValidationError({"email": "That address is on the list already."})
        row = AiRecipient(company=membership.company, created_by=actor, updated_by=actor, **values)
        row.full_clean()
        row.save()
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="ai.recipient.added", obj=row, before={}, after={"email": row.email})
    return row


@transaction.atomic
def remove_recipient(*, actor, company_id, recipient_id):
    membership = require_settings_keeper(actor, company_id)
    with use_company(company_id):
        row = AiRecipient.objects.filter(pk=recipient_id).first()
        if row is None:
            raise ValidationError("That recipient was not found.")
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="ai.recipient.removed", obj=row, before={"email": row.email}, after={})
        row.delete()
