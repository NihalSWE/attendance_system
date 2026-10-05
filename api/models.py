"""The API's own tables. Business data stays in the domain apps."""

from django.core.serializers.json import DjangoJSONEncoder
from django.db import models


class IdempotencyRecord(models.Model):
    """The first answer to a request sent with an ``Idempotency-Key``.

    The same caller sending the same key again within 24 hours gets this
    answer back instead of the action running twice - so an app on a poor
    connection can retry safely. A key reused for a different request is
    refused (``idempotency_key_reused``).
    """

    caller = models.CharField(max_length=80)          # "user:12", later "key:ak_…"
    key = models.CharField(max_length=128)
    request_hash = models.CharField(max_length=64)    # method, path and body
    status_code = models.PositiveSmallIntegerField()
    response_body = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "api_idempotency_record"
        constraints = [models.UniqueConstraint(fields=["caller", "key"],
                                               name="uniq_api_idempotency_caller_key")]
        indexes = [models.Index(fields=["created_at"])]

    def __str__(self):
        return f"{self.caller} {self.key} -> {self.status_code}"


class ApiSession(models.Model):
    """One login: a person on one device (docs/api/00-PLAN.md, 2.3).

    The access token (10 minutes) and refresh token (30 days) are stored only
    as SHA-256 digests. The refresh token changes on every use; the one before
    is kept so that if it is ever presented again - someone copied it - the
    whole session ends. Mobile and desktop sessions also get a signing secret
    (stored encrypted) and must sign every request with it.
    """

    class ClientType(models.TextChoices):
        MOBILE = "mobile", "Mobile app"
        DESKTOP = "desktop", "Desktop app"
        WEB = "web", "Web frontend (cookies)"

    class State(models.TextChoices):
        TWO_STEP = "two_step", "Waiting for the two-step code"
        ACTIVE = "active", "Active"
        ENDED = "ended", "Ended"

    public_id = models.CharField(max_length=40, unique=True)            # "ses_…"
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE,
                             related_name="api_sessions")
    client_type = models.CharField(max_length=10, choices=ClientType.choices)
    device_name = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)
    # Must set up two-step login before anything else (owners and admins).
    needs_two_step_setup = models.BooleanField(default=False)
    access_hash = models.CharField(max_length=64, blank=True, db_index=True)
    access_expires_at = models.DateTimeField(null=True, blank=True)
    refresh_hash = models.CharField(max_length=64, blank=True, db_index=True)
    refresh_expires_at = models.DateTimeField(null=True, blank=True)
    previous_refresh_hash = models.CharField(max_length=64, blank=True, db_index=True)
    rotated_at = models.DateTimeField(null=True, blank=True)
    challenge_hash = models.CharField(max_length=64, blank=True, db_index=True)
    challenge_expires_at = models.DateTimeField(null=True, blank=True)
    # A two-step code sent by email to this session (api/core/email_codes.py):
    # only its hash, until when it works, when it was sent, wrong tries.
    email_code_hash = models.CharField(max_length=64, blank=True)
    email_code_expires_at = models.DateTimeField(null=True, blank=True)
    email_code_sent_at = models.DateTimeField(null=True, blank=True)
    email_code_tries = models.PositiveSmallIntegerField(default=0)
    signing_secret_encrypted = models.TextField(blank=True)
    # A fingerprint of the password when the session began: a new password
    # (here or in the panels) ends every older session.
    password_fingerprint = models.CharField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_reason = models.CharField(max_length=120, blank=True)

    class Meta:
        db_table = "api_session"
        indexes = [models.Index(fields=["user", "state"])]

    def __str__(self):
        return f"{self.public_id} {self.user_id} {self.client_type} {self.state}"


class ApiKey(models.Model):
    """A company's key for a machine - an ERP, another server, another make of
    device (docs/api/00-PLAN.md, 2.4). Every request is signed with its
    secret. It can never do more than the person who created it, and only
    what its scopes allow."""

    public_id = models.CharField(max_length=40, unique=True)            # "ak_…"
    company = models.ForeignKey("tenants.Company", on_delete=models.CASCADE,
                                related_name="api_keys")
    name = models.CharField(max_length=120)
    secret_encrypted = models.TextField()
    # After a rotation the old secret still works until this moment.
    previous_secret_encrypted = models.TextField(blank=True)
    previous_valid_until = models.DateTimeField(null=True, blank=True)
    scopes = models.JSONField(default=list)
    allowed_ips = models.JSONField(default=list, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_used_ip = models.GenericIPAddressField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "api_key"

    def __str__(self):
        return f"{self.public_id} {self.name}"


class UsedNonce(models.Model):
    """A signed request's nonce, kept 10 minutes: the same one again is a replay."""

    owner = models.CharField(max_length=40)          # the session or key public id
    nonce = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "api_used_nonce"
        constraints = [models.UniqueConstraint(fields=["owner", "nonce"],
                                               name="uniq_api_nonce_owner")]


class TwoStep(models.Model):
    """A person's two-step login: the way they chose - the authenticator app
    (recommended) or codes by email only (for someone who does not want an
    app) - and the recovery codes. App users can also get a code by email at
    login when the app cannot be used (api/core/email_codes.py)."""

    class Method(models.TextChoices):
        APP = "app", "Authenticator app"
        EMAIL = "email", "Code by email"

    user = models.OneToOneField("accounts.User", on_delete=models.CASCADE,
                                related_name="api_two_step")
    method = models.CharField(max_length=10, choices=Method.choices, default=Method.APP)
    secret_encrypted = models.TextField(blank=True)             # the app's; empty for email
    # Changing the way, or the app to a new phone: the new one waits here
    # until a code of it confirms it; the old way works until then.
    pending_method = models.CharField(max_length=10, choices=Method.choices, blank=True)
    pending_secret_encrypted = models.TextField(blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    recovery_hashes = models.JSONField(default=list, blank=True)     # unused codes
    last_used_step = models.BigIntegerField(default=0)                # a code works once

    class Meta:
        db_table = "api_two_step"


class LoginAttempt(models.Model):
    """Every login try, for the lockout and the audit trail."""

    email = models.CharField(max_length=254, db_index=True)
    ip = models.GenericIPAddressField(null=True, blank=True, db_index=True)
    succeeded = models.BooleanField(default=False)
    reason = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "api_login_attempt"


class PasswordReset(models.Model):
    """A single-use password reset, valid 30 minutes."""

    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="+")
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "api_password_reset"
