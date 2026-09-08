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

def record_company_event(*, actor, membership, company, action, obj, before=None, after=None):
    """Audit for a company administrator's own action.

    Distinct from record_platform_event: the actor is a company member, not the
    platform owner, so actor_type is USER and the membership is recorded. Call
    inside the same transaction as the business write so a failed audit rolls
    the change back.
    """
    return AuditLog.objects.create(
        actor_user=actor,
        actor_membership=membership,
        actor_type=AuditLog.ActorType.USER,
        company=company,
        action=action,
        object_app=obj._meta.app_label,
        object_model=obj._meta.model_name,
        object_id=str(obj.pk),
        object_public_id=str(getattr(obj, "public_id", "")),
        object_display=str(obj)[:255],
        before_data=before or {},
        after_data=after or {},
    )
