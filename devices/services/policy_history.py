"""Reconstructing what a policy field said at a past instant.

An offline punch must be judged by the policy in force when it happened, not
today's (DEVICE_ATTENDANCE_POLICY.md; DEVICE_INTEGRATION_HANDOFF.md rule 7).
The scope and enablement fields are *mutable current state*, so reading them
directly would silently apply today's — possibly broader — permission to a
punch from last week.

There is no scope-version table by design, so history is reconstructed from the
append-only AuditLog plus current state. Two facts make that safe:

- ``updated_at`` (auto_now on every TenantOwned row) proves whether the row has
  been touched at all since the punch. If it has not, current state *is* the
  historical state, and no audit row is needed.
- If the row was touched after the punch, the value at that instant is the
  ``before_data`` of the earliest recorded change after it.

If the row changed but the audit trail cannot supply the old value, the answer
is ``PolicyUnresolved`` — never a fallback to today's value.

**The contract this relies on:** an audit record's ``before_data`` carries every
field that record changed. Given that, a change record which does not mention a
field proves that field was not part of it. What remains genuinely unsafe is a
row modified with *no* audit record at all, and that is what becomes
unresolved — so correctness does not depend on every screen in the project
remembering to audit, only on audited changes being complete.
"""

from datetime import timedelta

from auditlog.models import AuditLog

# Sentinel distinguishing "recorded as null" from "not recorded at all".
_MISSING = object()

# auto_now_add and auto_now each call timezone.now() separately, so a row that
# has never been edited still has created_at and updated_at a few microseconds
# apart. Used only when evaluating as of a row's own creation, so that this
# microsecond gap does not read as an edit.
UNMODIFIED_TOLERANCE = timedelta(seconds=1)


class PolicyUnresolved(Exception):
    """The historical value of a policy field cannot be established."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def value_at(obj, field, instant):
    """Return ``obj.field`` as it stood at ``instant``.

    Raises PolicyUnresolved when the row changed after ``instant`` and the
    audit trail does not record what the field held before that change.
    """
    current = getattr(obj, field)

    updated_at = getattr(obj, "updated_at", None)
    if updated_at is None:
        return current

    # A punch that predates the row entirely is judged by that row's initial
    # configuration — the "including the initial configuration" case in
    # DEVICE_ATTENDANCE_POLICY.md. This covers a backlog punch from earlier on
    # the day a company was first set up. Evaluating as of creation reconstructs
    # back through any audited change made since.
    created_at = getattr(obj, "created_at", None)
    effective_instant = instant
    if created_at is not None and instant < created_at:
        effective_instant = created_at + UNMODIFIED_TOLERANCE

    if updated_at <= effective_instant:
        # Untouched since then: current state is historical state.
        return current

    changes = list(
        AuditLog.objects.filter(
            object_app=obj._meta.app_label,
            object_model=obj._meta.model_name,
            object_id=str(obj.pk),
            occurred_at__gt=effective_instant,
        ).order_by("occurred_at", "pk")
    )
    if not changes:
        raise PolicyUnresolved(
            f"{obj._meta.label}.{field} was modified after the event "
            f"({updated_at.isoformat()}) but no audit record explains the change."
        )

    for change in changes:
        # Chronological order, so the earliest change touching this field holds
        # the value that was in force at the instant.
        before = change.before_data or {}
        if field in before:
            return before[field]
        after = change.after_data or {}
        if field in after:
            raise PolicyUnresolved(
                f"{obj._meta.label}.{field} changed at "
                f"{change.occurred_at.isoformat()} but the audit record does "
                "not contain its previous value."
            )

    # Every modification since the instant is accounted for, and none of them
    # touched this field, so it still holds the value it had then.
    return current


def value_at_or_unresolved(obj, field, instant):
    """Like value_at, but returns ``(value, reason)`` instead of raising."""
    try:
        return value_at(obj, field, instant), ""
    except PolicyUnresolved as exc:
        return _MISSING, exc.reason


def is_missing(value):
    return value is _MISSING
