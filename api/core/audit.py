"""Security events in the existing audit log (docs/api/00-PLAN.md, 2.7).

Logins, failed logins, lockouts, two-step changes, sessions ended, keys
created / rotated / revoked. Never a password, token, secret or signature.
"""

from auditlog.models import AuditLog

from api.core.network import client_ip


def record(request, action, *, user=None, company_id=None, obj=None, data=None):
    AuditLog.objects.create(
        actor_user=user if user is not None and getattr(user, "pk", None) else None,
        actor_type=AuditLog.ActorType.USER,
        company_id=company_id,
        action=action,
        object_app=obj._meta.app_label if obj is not None else "api",
        object_model=obj._meta.model_name if obj is not None else "login",
        object_id=str(obj.pk) if obj is not None else "",
        object_public_id=str(getattr(obj, "public_id", "") or "") if obj is not None else "",
        object_display=str(obj)[:255] if obj is not None else action,
        ip_address=client_ip(request) if request is not None else None,
        user_agent=(request.META.get("HTTP_USER_AGENT", "")[:500] if request is not None else ""),
        metadata={"via": "api", **(data or {})},
    )
