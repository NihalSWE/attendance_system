"""Explicit snapshots keep passwords and arbitrary form input out of logs."""
from auditlog.models import AuditLog


def record_platform_event(*, actor, company, action, obj, before=None, after=None):
    display = str(obj)
    if obj._meta.model_name == "companymembership":
        display = f"{obj.user.email} ({obj.get_role_display()})"
    elif obj._meta.model_name == "companyfeature":
        display = f"{obj.feature.name}: {obj.get_effect_display()}"
    return AuditLog.objects.create(
        actor_user=actor, actor_type=AuditLog.ActorType.ROOT_ADMIN,
        company=company, action=action, object_app=obj._meta.app_label,
        object_model=obj._meta.model_name, object_id=str(obj.pk),
        object_public_id=str(getattr(obj, "public_id", "")),
        object_display=display[:255], before_data=before or {}, after_data=after or {},
    )
