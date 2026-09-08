"""Repeatable device scenarios, in the ZKTeco ATTLOG wire format.

Every scenario is deterministic: given the same anchor date it produces byte-
identical payloads, so a run can be replayed and compared. That is what makes
"replaying the same fixture twice produces no duplicate attendance effect" a
testable claim rather than an opinion.

These scenarios model the situations in DEVICE_ATTENDANCE_POLICY.md. They are
**simulator** fixtures: passing them says the server behaves correctly for
this input, never that the physical SenseFace 2A produces this input.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

# ZKTeco verify codes (see devices/adapters/zkteco_adms.py).
FINGERPRINT = "1"
FACE = "15"

# ZKTeco punch-state codes. Informational; pairing never trusts them.
CHECK_IN = "0"
CHECK_OUT = "1"


@dataclass
class Batch:
    """One POST the device would make, as a list of ATTLOG rows."""

    serial: str
    rows: list = field(default_factory=list)
    stamp: str = "0"
    label: str = ""

    def body(self):
        return "".join(f"{r}\n" for r in self.rows)


@dataclass
class Scenario:
    name: str
    description: str
    batches: list
    expectation: str


def _row(device_user_id, moment, status=CHECK_IN, verify=FINGERPRINT):
    stamp = moment.strftime("%Y-%m-%d %H:%M:%S")
    return f"{device_user_id}\t{stamp}\t{status}\t{verify}\t0\t\t"


def _day(anchor, hour, minute=0):
    return datetime(anchor.year, anchor.month, anchor.day, hour, minute)


def single_day(anchor, serial, device_user_id="1"):
    """One employee, one in and one out on the same device."""
    return Scenario(
        name="single_day",
        description="One employee checks in at 09:00 and out at 18:00.",
        batches=[
            Batch(
                serial=serial,
                stamp="1001",
                label="morning",
                rows=[_row(device_user_id, _day(anchor, 9, 0), CHECK_IN)],
            ),
            Batch(
                serial=serial,
                stamp="1002",
                label="evening",
                rows=[_row(device_user_id, _day(anchor, 18, 0), CHECK_OUT, FACE)],
            ),
        ],
        expectation="Two authorized punches for one employee, both dedupe_status=unique.",
    )


def replay(anchor, serial, device_user_id="1"):
    """The same batch delivered twice — a retransmission."""
    batch = Batch(
        serial=serial,
        stamp="2001",
        label="original",
        rows=[
            _row(device_user_id, _day(anchor, 9, 0), CHECK_IN),
            _row(device_user_id, _day(anchor, 18, 0), CHECK_OUT),
        ],
    )
    resend = Batch(
        serial=serial, stamp=batch.stamp, label="identical resend", rows=list(batch.rows)
    )
    return Scenario(
        name="replay",
        description="The identical batch is delivered a second time.",
        batches=[batch, resend],
        expectation=(
            "One DeviceMessage and two PunchEvents. The resend is refused by the "
            "idempotency key and is still acknowledged, so the device advances."
        ),
    )


def resend_new_stamp(anchor, serial, device_user_id="1"):
    """The same punches re-sent under a different stamp.

    This is the harder case: the message is genuinely new, but the punches
    inside it are retransmissions.
    """
    rows = [
        _row(device_user_id, _day(anchor, 9, 0), CHECK_IN),
        _row(device_user_id, _day(anchor, 18, 0), CHECK_OUT),
    ]
    return Scenario(
        name="resend_new_stamp",
        description="Identical punches arrive again under a new stamp.",
        batches=[
            Batch(serial=serial, stamp="3001", label="first", rows=list(rows)),
            Batch(serial=serial, stamp="3002", label="resend", rows=list(rows)),
        ],
        expectation=(
            "Two DeviceMessages and four PunchEvents. The second pair is kept as "
            "evidence, marked confirmed_duplicate, linked to the original and "
            "excluded, so the attendance effect is not doubled."
        ),
    )


def rapid_repeat(anchor, serial, device_user_id="1"):
    """Two scans a few seconds apart: ambiguous, not provably duplicate."""
    first = _day(anchor, 9, 0)
    return Scenario(
        name="rapid_repeat",
        description="The same person scans twice, five seconds apart.",
        batches=[
            Batch(
                serial=serial,
                stamp="4001",
                rows=[
                    _row(device_user_id, first, CHECK_IN),
                    _row(device_user_id, first + timedelta(seconds=5), CHECK_IN),
                ],
            )
        ],
        expectation=(
            "Both punches kept. The second is probable_duplicate for review: "
            "closeness in time is not proof of duplication."
        ),
    )


def offline_backlog(anchor, serial, device_user_id="1", count=24):
    """Two hours of punches buffered during an outage, drained at once.

    This is the shape the server must handle when the internet drops: nothing
    arrives for hours, then the whole backlog appears in one or a few batches
    with timestamps far behind the receipt time.
    """
    start = _day(anchor, 8, 0)
    rows = [
        _row(
            str(int(device_user_id) + (i % 3)),
            start + timedelta(minutes=5 * i),
            CHECK_IN if i % 2 == 0 else CHECK_OUT,
        )
        for i in range(count)
    ]
    # Delivered as the device would after reconnecting: several batches.
    batches = [
        Batch(serial=serial, stamp=f"500{n + 1}", label=f"drain {n + 1}", rows=chunk)
        for n, chunk in enumerate([rows[i:i + 8] for i in range(0, len(rows), 8)])
    ]
    return Scenario(
        name="offline_backlog",
        description=f"{count} punches buffered over two hours, drained after reconnect.",
        batches=batches,
        expectation=(
            f"All {count} punches stored exactly once, each judged by the policy "
            "in force when it happened rather than the policy now."
        ),
    )


def unknown_user(anchor, serial):
    """A device user number nobody is enrolled under."""
    return Scenario(
        name="unknown_user",
        description="A punch from an unenrolled device user number.",
        batches=[
            Batch(
                serial=serial,
                stamp="6001",
                rows=[_row("9999", _day(anchor, 9, 0), CHECK_IN)],
            )
        ],
        expectation=(
            "The punch is stored and marked unknown_employee, never dropped, and "
            "appears in the unresolved queue."
        ),
    )


def malformed(anchor, serial, device_user_id="1"):
    """A batch containing a row the parser cannot interpret."""
    return Scenario(
        name="malformed",
        description="A batch with one unreadable timestamp among valid rows.",
        batches=[
            Batch(
                serial=serial,
                stamp="7001",
                rows=[
                    _row(device_user_id, _day(anchor, 9, 0), CHECK_IN),
                    f"{device_user_id}\tnot-a-timestamp\t0\t1\t0\t\t",
                    _row(device_user_id, _day(anchor, 18, 0), CHECK_OUT),
                ],
            )
        ],
        expectation=(
            "Two punches extracted; the raw unreadable line is still preserved in "
            "the message, which is marked partially_failed for review."
        ),
    )


BUILDERS = {
    "single_day": single_day,
    "replay": replay,
    "resend_new_stamp": resend_new_stamp,
    "rapid_repeat": rapid_repeat,
    "offline_backlog": offline_backlog,
    "unknown_user": unknown_user,
    "malformed": malformed,
}


def build(name, *, anchor, serial, **kwargs):
    try:
        builder = BUILDERS[name]
    except KeyError:
        raise ValueError(
            f"Unknown scenario {name!r}. Available: {', '.join(sorted(BUILDERS))}"
        ) from None
    return builder(anchor, serial, **kwargs)


def scenario_names():
    return sorted(BUILDERS)
