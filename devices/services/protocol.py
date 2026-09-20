"""Which command dialect a ZKTeco push device speaks.

Two are in use (A16, 2026-09-15):

- ``PUSH3`` — PushSDK 3.x table commands, ``DATA QUERY tablename=user``. The
  SenseFace 2A (``DeviceType=acc``, pushver 3.x) answers these, and uploads
  its users as ``tabledata`` rows (``user pin=…``).
- ``ATT2`` — the attendance push protocol 2.x, ``DATA QUERY USERINFO PIN=…``.
  The SenseFace 3A announces ``pushver=2.4.1``, ``DeviceType=att``; it answered
  the table form with ``Return=-1004`` and reports each user it enrols or edits
  in its operation log as a ``USER PIN=…`` line (captured 2026-09-14).

What a device announces at its handshake is kept in ``settings["announced"]``.
A device that has not handshaken since that started being recorded is
recognised by the 2.x operation-log lines it has sent (the 2A never sends
them). The administrator's "Push protocol" choice changes how the handshake is
answered, not which commands the device has shown it understands.
"""

from django.db.models import Q

from devices.adapters.zkteco_adms import uses_push3
from devices.models import BiometricDevice, DeviceMessage

PUSH3 = "push3"
ATT2 = "att2"

LABELS = {
    PUSH3: "PushSDK 3.x",
    ATT2: "Attendance push 2.x",
}

# Requests each dialect has a verified command for. On ATT2 the user list
# brings the fingerprint records with it, and attendance history can be asked
# for; biodata and options have no 2.x form verified on hardware yet.
SUPPORTED = {
    PUSH3: {"query_users", "query_biodata", "query_options"},
    ATT2: {"query_users", "query_attlog"},
}


def note_announcement(device, *, pushver="", device_type=""):
    """Remember what the device said at its handshake (only when it changed)."""
    settings = dict(device.settings or {})
    announced = dict(settings.get("announced") or {})
    fresh = {key: value for key, value in (("pushver", pushver), ("device_type", device_type)) if value}
    if not fresh or all(announced.get(key) == value for key, value in fresh.items()):
        return
    announced.update(fresh)
    settings["announced"] = announced
    BiometricDevice.all_objects.filter(pk=device.pk).update(settings=settings)
    device.settings = settings


#: What a model speaks when nothing else says so. A device announces at its
#: handshake and then stays registered — it never announces again — so a server
#: that has not heard it (a rebuilt database, or a device registered before
#: announcements were recorded) has only the catalogue to go on. Measured: the
#: SenseFace 3A refuses the 3.x table form with Return=-1004 and answers the
#: 2.x form with Return=0 (A16, 2026-09-15); the 2A is the other way round.
#: Seen live on 2026-09-20: after the server's database was rebuilt, "Refresh
#: user list" on the client's 3A was refused, because the fallback was 3.x.
MODEL_DIALECTS = {"senseface-3a": ATT2}

#: The administrator's own choice, Edit device -> Push protocol.
FORCED = {"2": ATT2, "3": PUSH3}


def dialect(device):
    settings = device.settings or {}
    forced = FORCED.get(str(settings.get("push_protocol") or "auto"))
    if forced:
        return forced
    announced = settings.get("announced") or {}
    if announced.get("pushver"):
        return PUSH3 if uses_push3(announced["pushver"]) else ATT2
    sent_2x = DeviceMessage.all_objects.filter(device=device).filter(
        Q(raw_payload_text__startswith="OPLOG ")
        | Q(raw_payload_text__startswith="USER PIN=")
        | Q(raw_payload_text__contains="\nUSER PIN=")
    ).exists()
    if sent_2x:
        return ATT2
    model_code = getattr(getattr(device, "device_model", None), "model_code", "")
    return MODEL_DIALECTS.get(model_code, PUSH3)


def supports(device, command_key):
    return command_key in SUPPORTED[dialect(device)]
