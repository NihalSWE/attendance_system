"""Create employee records from the users a device reports.

Commissioning a terminal that already has staff enrolled on it means typing
every person in twice — once on the device, once here. This turns the roster
the device already sent us into draft Employee rows plus the DeviceEnrollment
that maps them, so punches resolve immediately and HR fills in the real
details afterwards.

Two things this deliberately does not do:

- It never guesses a person's identity. A device user with no name becomes a
  clearly-labelled placeholder ("Device user 445961"), not an invented name.
  The row is a stub to be completed, and it says so.
- It never grants attendance permission silently. Created enrollments are
  recognition-only (``assigned_device_authorized=False``), so a synced user's
  punches are preserved and marked ``unauthorized_device`` until a human
  authorises them. Enrollment is not authorization
  (DEVICE_ATTENDANCE_POLICY.md).

Boundary note: Employee and EmployeeAssignment belong to the employees app,
which Ajay owns. This writes draft rows into them, and the employee_code
prefix below makes every such row identifiable for review or bulk correction.
"""

from django.db import transaction
from django.utils import timezone

from devices.models import DeviceEnrollment
from devices.services.device_roster import build_roster

# Every employee created this way carries this prefix in its assignment code,
# so drafts can be found, reviewed and corrected as a set.
DRAFT_CODE_PREFIX = "DEV"


class SyncNotPossible(Exception):
    """Raised when the company has no organisation structure to place people in."""


def _placement(device):
    """Pick the branch/department/designation a draft employee is filed under.

    The device's own branch is used, because that is where the person
    physically scans. The company must already have added a department in that
    branch and assigned a designation to it: inventing organisation structure
    is HR's decision, not a side-effect of plugging in a terminal. Note these
    are the company's own rows, not the root lists — a device can only ever be
    filed against its own company's structure.
    """
    from organization.models import Department, Designation

    branch = device.branch
    department = (
        Department.objects.filter(branch=branch).order_by("pk").first()
    )
    if department is None:
        raise SyncNotPossible(
            f"{branch.name} has no department yet. Create one under "
            "Departments first, then sync — new employees must be filed "
            "somewhere real."
        )
    designation = (
        Designation.objects.filter(department=department)
        .order_by("pk")
        .first()
    )
    if designation is None:
        raise SyncNotPossible(
            f"{department.name} has no designation yet. Create one under "
            "Designations first, then sync."
        )
    return branch, department, designation


def _next_code(company_id, pin):
    """A unique, obviously-provisional assignment code for a synced person."""
    return f"{DRAFT_CODE_PREFIX}-{pin}"


def sync_device_users(*, device, actor=None, pins=None):
    """Create draft employees + enrollments for unmapped device users.

    ``pins`` limits the sync to specific device user ids; None means every
    unmapped user on the roster. Returns (created, skipped, errors).
    """
    from employees.models import Employee, EmployeeAssignment

    roster = build_roster(device)
    targets = [
        row for row in roster
        if not row["is_mapped"] and (pins is None or row["pin"] in pins)
    ]
    if not targets:
        return [], [], []

    branch, department, designation = _placement(device)
    now = timezone.now()
    created, skipped, errors = [], [], []

    for row in targets:
        pin = row["pin"]
        try:
            with transaction.atomic():
                # Re-check inside the transaction: another administrator may
                # have mapped this user while this page was open.
                if DeviceEnrollment.objects.filter(
                    device=device, device_user_id=pin
                ).exists():
                    skipped.append(pin)
                    continue

                device_name = (row["name"] or "").strip()
                employee = Employee(
                    # A blank name on the device must not become a blank
                    # employee: the placeholder says what this row is.
                    first_name=device_name or f"Device user {pin}",
                    last_name="",
                    employment_status=Employee.EmploymentStatus.ACTIVE,
                    metadata={
                        "created_from_device_sync": True,
                        "source_device_serial": device.serial_number,
                        "source_device_user_id": pin,
                        "needs_hr_review": True,
                    },
                )
                employee.company_id = device.company_id
                employee.full_clean()
                employee.save()

                assignment = EmployeeAssignment(
                    employee=employee,
                    employee_code=_next_code(device.company_id, pin),
                    branch=branch,
                    department=department,
                    designation=designation,
                    effective_from=now,
                    change_reason=(
                        f"Draft created by device sync from {device.name} "
                        f"(device user {pin}). Details pending HR review."
                    ),
                )
                assignment.company_id = device.company_id
                assignment.full_clean()
                assignment.save()

                enrollment = DeviceEnrollment(
                    device=device,
                    employee=employee,
                    device_user_id=pin,
                    card_number=row.get("card_number") or "",
                    effective_from=now,
                    attendance_enabled=True,
                    # Recognition only. A human decides whether these punches
                    # count; syncing must never hand out permission.
                    assigned_device_authorized=False,
                    enrollment_status=DeviceEnrollment.EnrollmentStatus.SYNCED,
                    created_by=actor if actor and actor.is_authenticated else None,
                )
                enrollment.company_id = device.company_id
                enrollment.full_clean()
                enrollment.save()

                created.append({"pin": pin, "employee": employee})
        except Exception as exc:  # noqa: BLE001 - surfaced to the operator
            errors.append({"pin": pin, "error": str(exc)})

    return created, skipped, errors
