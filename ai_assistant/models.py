"""The AI sales order assistant (docs/inventory/00-PLAN.md §8, Phase 8).

Orders arrive as text - an email to the order inbox, or text a person pastes
(WhatsApp, a phone note). Claude reads each one into a structured order; our
own code matches the customer, items and units, checks stock and credit, and
makes a **draft** sales order. A person always confirms it. The AI never sets
prices, confirms orders or moves stock.
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from common.models import ActorTracked, TenantOwned

ZERO = Decimal("0")


def _user(**extra):
    return models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="+", **extra)


class AiSettings(TenantOwned, ActorTracked):
    """One per company, kept by the owner or company admin. Off until the
    client's written consent to send order text to Anthropic is recorded."""

    class Model(models.TextChoices):
        OPUS = "claude-opus-5-5", "Claude Opus 5.5 (most accurate; default)"
        SONNET = "claude-sonnet-5-5", "Claude Sonnet 5.5 (cheaper)"
        HAIKU = "claude-haiku-5-5", "Claude Haiku 5.5 (cheapest)"

    class Effort(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium (default)"
        HIGH = "high", "High"

    enabled = models.BooleanField("AI order assistant on", default=False)
    consent_received = models.BooleanField(
        "The client's written consent is received", default=False,
        help_text="Consent to send order text and report figures to the AI provider (Anthropic). "
                  "Needed before the assistant can be switched on.")
    consent_date = models.DateField(null=True, blank=True)
    consent_note = models.CharField("Where the consent is kept", max_length=255, blank=True)
    consent_recorded_by = _user()
    api_key_encrypted = models.TextField(blank=True)
    model = models.CharField(max_length=32, choices=Model.choices, default=Model.OPUS)
    effort = models.CharField("Effort", max_length=8, choices=Effort.choices, default=Effort.MEDIUM)
    daily_cost_cap = models.DecimalField(
        "Daily spending limit (US$)", max_digits=8, decimal_places=2, default=Decimal("5.00"),
        validators=[MinValueValidator(Decimal("0.10")), MaxValueValidator(Decimal("1000"))],
        help_text="Once the day's AI calls reach this, new orders wait for a person.")
    draft_user = _user(verbose_name="Draft orders are entered as",
                       help_text="A login with 'Enter sales orders'. Whoever confirms must be someone else.")
    # The order inbox (IMAP).
    inbox_on = models.BooleanField("Read the order inbox", default=False)
    imap_host = models.CharField("Inbox server (IMAP)", max_length=255, blank=True)
    imap_port = models.PositiveIntegerField(default=993)
    imap_username = models.CharField(max_length=255, blank=True)
    imap_password_encrypted = models.TextField(blank=True)
    imap_folder = models.CharField(max_length=128, default="INBOX")
    last_inbox_check = models.DateTimeField(null=True, blank=True)
    last_inbox_error = models.CharField(max_length=500, blank=True)

    class Meta:
        db_table = "ai_settings"
        constraints = [models.UniqueConstraint(fields=["company"], name="uniq_ai_settings_per_company")]

    def __str__(self):
        return "AI assistant settings"

    @property
    def has_key(self):
        return bool(self.api_key_encrypted)

    @property
    def ready(self):
        return self.enabled and self.consent_received and self.has_key and self.draft_user_id is not None


class AiRecipient(TenantOwned, ActorTracked):
    """A manager who receives order summaries and reports (the client's five)."""

    name = models.CharField(max_length=128)
    designation = models.CharField(max_length=128, blank=True)
    email = models.EmailField()
    gets_orders = models.BooleanField("New confirmed orders", default=True)
    gets_daily = models.BooleanField("Daily report", default=True)
    gets_weekly = models.BooleanField("Weekly report", default=True)
    active = models.BooleanField(default=True)

    class Meta:
        db_table = "ai_recipient"
        ordering = ("name",)
        constraints = [models.UniqueConstraint(fields=["company", "email"], name="uniq_ai_recipient_email")]

    def __str__(self):
        return f"{self.name} <{self.email}>"


class ItemAlias(TenantOwned, ActorTracked):
    """What customers call an item ("aamer juice", "চিনি 50 কেজি বস্তা"),
    learned when a reviewer matches a line by hand."""

    item = models.ForeignKey("inventory.Item", on_delete=models.CASCADE, related_name="ai_aliases")
    alias = models.CharField(max_length=255)
    normalized = models.CharField(max_length=255, editable=False)

    class Meta:
        db_table = "ai_item_alias"
        ordering = ("alias",)
        constraints = [models.UniqueConstraint(fields=["company", "normalized"], name="uniq_ai_item_alias")]

    def __str__(self):
        return self.alias


class InboundMessage(TenantOwned):
    """One order as it arrived - kept whatever happens next."""

    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        PASTE = "paste", "Pasted (WhatsApp, phone...)"
        API = "api", "API"

    class Status(models.TextChoices):
        NEW = "new", "New"
        NEEDS_REVIEW = "needs_review", "Needs review"
        CONVERTED = "converted", "Confirmed as an order"
        IGNORED = "ignored", "Ignored"
        FAILED = "failed", "AI could not read it"
        WAITING = "waiting", "Waiting (AI off or limit reached)"

    channel = models.CharField(max_length=8, choices=Channel.choices)
    sender = models.CharField(max_length=255, blank=True)
    subject = models.CharField(max_length=500, blank=True)
    body = models.TextField()
    message_id = models.CharField(max_length=500, blank=True, help_text="The email's Message-ID, to skip repeats.")
    received_at = models.DateTimeField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW)
    extraction = models.JSONField(null=True, blank=True)
    problems = models.JSONField(default=list, blank=True)
    unmatched = models.JSONField(default=list, blank=True, help_text="Lines no item was found for.")
    customer = models.ForeignKey("sales.Customer", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    sales_order = models.ForeignKey("sales.SalesOrder", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    note = models.CharField(max_length=500, blank=True)
    entered_by = _user()
    reviewed_by = _user()
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "ai_inbound_message"
        ordering = ("-received_at", "-pk")
        indexes = [models.Index(fields=["company", "status"], name="ai_msg_status"),
                   models.Index(fields=["company", "message_id"], name="ai_msg_message_id")]

    def __str__(self):
        return self.subject or f"Message {self.pk}"


class AiCall(TenantOwned):
    """Every call to the AI: what it was for, what came back, and what it cost."""

    class Kind(models.TextChoices):
        EXTRACT = "extract", "Read an order"
        SUMMARY = "summary", "Report summary"

    kind = models.CharField(max_length=8, choices=Kind.choices)
    message = models.ForeignKey(InboundMessage, null=True, blank=True, on_delete=models.SET_NULL, related_name="calls")
    model = models.CharField(max_length=64)
    served_by = models.CharField(max_length=64, blank=True, help_text="The model that answered (a fallback, if one ran).")
    request_id = models.CharField(max_length=128, blank=True)
    stop_reason = models.CharField(max_length=32, blank=True)
    output = models.TextField(blank=True)
    error = models.CharField(max_length=500, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cache_read_tokens = models.PositiveIntegerField(default=0)
    cache_write_tokens = models.PositiveIntegerField(default=0)
    cost = models.DecimalField("Estimated cost (US$)", max_digits=10, decimal_places=6, default=ZERO)

    class Meta:
        db_table = "ai_call"
        ordering = ("-created_at", "-pk")

    @property
    def ok(self):
        return not self.error


class AiReportRun(TenantOwned):
    """A daily or weekly management report that went out (or failed to)."""

    class Kind(models.TextChoices):
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"

    kind = models.CharField(max_length=8, choices=Kind.choices)
    period_start = models.DateField()
    period_end = models.DateField()
    summary = models.TextField(blank=True)
    sent_to = models.TextField(blank=True)
    status = models.CharField(max_length=8, default="sent")
    error = models.CharField(max_length=500, blank=True)

    class Meta:
        db_table = "ai_report_run"
        ordering = ("-created_at",)
