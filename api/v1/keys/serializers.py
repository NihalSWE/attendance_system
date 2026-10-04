from rest_framework import serializers

from api.core.scopes import SCOPES
from api.core.serializers import StrictSerializer

SCOPE_CHOICES = [(name, name) for name in SCOPES]


class KeyCreateSerializer(StrictSerializer):
    name = serializers.CharField(max_length=120,
                                 help_text='What the key is for, e.g. "IGL ERP".')
    scopes = serializers.MultipleChoiceField(
        choices=SCOPE_CHOICES,
        help_text="Exactly what the key may do - give only what the machine needs. "
                  "GET /api/v1/api-keys/scopes explains each.")
    allowed_ips = serializers.ListField(
        child=serializers.CharField(max_length=45, help_text="One IP address, e.g. 203.0.113.7."),
        required=False, default=list,
        help_text="If given, the key works only from these addresses. Empty: from anywhere.")
    expires_at = serializers.DateTimeField(
        required=False, allow_null=True, default=None,
        help_text="If given, the key stops working at this moment. Empty: no expiry.")


class KeyChangeSerializer(StrictSerializer):
    name = serializers.CharField(max_length=120, required=False,
                                 help_text="A new name.")
    scopes = serializers.MultipleChoiceField(choices=SCOPE_CHOICES, required=False,
                                             help_text="The new set of scopes (replaces the old).")
    allowed_ips = serializers.ListField(
        child=serializers.CharField(max_length=45, help_text="One IP address."),
        required=False, help_text="The new allow-list (replaces the old). Empty list: anywhere.")
    expires_at = serializers.DateTimeField(required=False, allow_null=True,
                                           help_text="A new expiry; null: no expiry.")


class RotateSerializer(StrictSerializer):
    grace_hours = serializers.IntegerField(
        min_value=0, max_value=24, required=False, default=1,
        help_text="How long the old secret keeps working, so the machine can switch "
                  "without a gap. 0: it stops at once.")


class KeySerializer(serializers.Serializer):
    id = serializers.CharField(source="public_id",
                               help_text="The key id (ak_…) - sent as X-Key-Id.")
    name = serializers.CharField(help_text="What the key is for.")
    scopes = serializers.ListField(child=serializers.CharField(help_text="One scope."),
                                   help_text="What the key may do.")
    allowed_ips = serializers.ListField(child=serializers.CharField(help_text="One address."),
                                        help_text="Where it may be used from; empty: anywhere.")
    expires_at = serializers.DateTimeField(allow_null=True, help_text="When it stops; null: never.")
    created_at = serializers.DateTimeField(help_text="When it was created.")
    created_by = serializers.SerializerMethodField(help_text="Who created it (email).")
    last_used_at = serializers.DateTimeField(allow_null=True, help_text="When it was last used.")
    last_used_ip = serializers.CharField(allow_null=True, help_text="Where it was last used from.")
    revoked = serializers.SerializerMethodField(help_text="True once revoked.")
    old_secret_valid_until = serializers.DateTimeField(
        source="previous_valid_until", allow_null=True,
        help_text="After a rotation: until when the old secret still works.")

    def get_created_by(self, key) -> str | None:
        return key.created_by.email if key.created_by else None

    def get_revoked(self, key) -> bool:
        return key.revoked_at is not None


class KeyWithSecretSerializer(KeySerializer):
    secret = serializers.CharField(
        help_text="The secret (sk_…) - shown only now. Store it on the machine in secure "
                  "storage; it signs every request and is never sent.")


class ScopeSerializer(serializers.Serializer):
    name = serializers.CharField(help_text="The scope, e.g. employees:read.")
    description = serializers.CharField(help_text="What it allows.")
