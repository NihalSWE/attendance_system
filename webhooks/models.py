"""The ERP webhook (Nihal, 2026-10-01): attendance pushed to a company's own
system - its ERP, payroll or HR software - as it happens.

A company sets its receiver once (Organisation → ERP webhook): the address,
the shared secret and how its system names things. From then on each
employee's check-in, and their check-out once their day is finished, is sent
there, signed and retried until it is received. Nothing is pulled; the ERP
needs no polling. The format follows the IGL ERP's attendance webhook, so
that ERP - and any other built to the same guide - takes it as it is.
"""

import uuid

from django.db import models

from common.models import ActorTracked, TenantOwned


class WebhookSettings(TenantOwned, ActorTracked):
    """Where and how one company's attendance is sent. One per company."""

    class Auth(models.TextChoices):
        HEADER = "header", "X-Webhook-Secret header"
        BEARER = "bearer", "Authorization: Bearer header"

    class Mode(models.TextChoices):
        ARRIVE_AND_LEAVE = "arrive_leave", "When they come in, and when their day is finished"
        EVERY_SCAN = "every_scan", "After every scan (first in and latest out so far)"

    is_active = models.BooleanField(default=False)
    url = models.URLField(max_length=500)
    # GET with the same headers, sending nothing: "Test connection". Blank:
    # the address followed by /ping, as the IGL ERP has it.
    ping_url = models.URLField(max_length=500, blank=True)
    auth = models.CharField(max_length=16, choices=Auth.choices, default=Auth.HEADER)
    secret_encrypted = models.TextField(blank=True)
    signing_secret_encrypted = models.TextField(blank=True)
    # The name the receiver reads the employee by; the value is their Employee ID.
    employee_key = models.CharField(max_length=40, default="au_user_id")
    mode = models.CharField(max_length=16, choices=Mode.choices, default=Mode.ARRIVE_AND_LEAVE)
    # Several events in one request, {"events": [...]}; off: one per request.
    batch = models.BooleanField(default=True)
    # Days before this are never sent: switching on does not replay history.
    send_from = models.DateField(null=True, blank=True)
    last_tested_at = models.DateTimeField(null=True, blank=True)
    last_test_ok = models.BooleanField(null=True, blank=True)
    last_test_message = models.CharField(max_length=500, blank=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "webhooks_settings"
        verbose_name_plural = "webhook settings"
        constraints = [
            models.UniqueConstraint(fields=["company"], name="uniq_webhook_settings_per_company"),
        ]

    def __str__(self):
        return f"{self.company_id} → {self.url}"


class WebhookEvent(TenantOwned):
    """One check-in or check-out waiting to be sent, or sent: the outbox and
    its history. Kept after sending, so the page shows what went and what the
    receiver said."""

    class Kind(models.TextChoices):
        CHECK_IN = "check_in", "Check-in"
        CHECK_OUT = "check_out", "Check-out"
        UPDATE = "update", "Changed"
        # Sent by hand from the page, with times typed in (2026-10-01): to try
        # the connection with real data before a device is connected.
        TEST = "test", "Test (sent by hand)"

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting to be sent"
        SENT = "sent", "Received"
        FAILED = "failed", "Not received - will try again"
        SKIPPED = "skipped", "Refused by the receiver"
        GAVE_UP = "gave_up", "Given up"

    event_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT,
                                 related_name="webhook_events")
    work_date = models.DateField()
    kind = models.CharField(max_length=16, choices=Kind.choices)
    payload = models.JSONField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField()
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_status_code = models.PositiveIntegerField(null=True, blank=True)
    last_message = models.CharField(max_length=500, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "webhooks_event"
        indexes = [
            models.Index(fields=["company", "status", "next_attempt_at"]),
            models.Index(fields=["company", "employee", "work_date"]),
        ]

    def __str__(self):
        return f"{self.kind} {self.employee_id} {self.work_date} ({self.status})"


class WebhookDayState(TenantOwned):
    """What has been queued for one employee-day, so a day is sent once per
    change - its check-in, then its check-out - however often it is worked
    out again."""

    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT,
                                 related_name="+")
    work_date = models.DateField()
    check_in = models.DateTimeField(null=True, blank=True)
    check_out = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "webhooks_day_state"
        constraints = [
            models.UniqueConstraint(fields=["employee", "work_date"],
                                    name="uniq_webhook_day_state"),
        ]
