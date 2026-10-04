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
