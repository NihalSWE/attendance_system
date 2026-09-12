"""Create demo device punches for a company's employees, for one month.

    python manage.py seed_demo_punches --company DEMO-NWT --month 2026-09

For development only. The real device sends punches to whichever server its
tunnel points at, so a developer's localhost has none. This fills that gap by
registering a clearly labelled *simulated* device, enrolling the company's
employees on it, and sending punches through the same parser and ingestion
pipeline a real device uses, so attendance and salary read them exactly as
they would read real ones.

The punches follow a repeatable pattern per employee: mostly on time, some
late, a few half days, a few absences and one missing OUT. Weekly off days,
holidays and recorded leave get no punches. Running it twice sends the same
batches, which the ingestion pipeline recognises as replays.
"""

import datetime
import zoneinfo
from datetime import timezone as dt_timezone

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from common.tenant import use_company
from devices.adapters import get_adapter
from devices.models import BiometricDevice, DeviceEnrollment, DeviceModel, PunchEvent
from devices.services.ingestion import ingest
from employees.models import Employee, EmployeeAssignment
from leaves.models import LeaveDay
from organization.models import Branch
from scheduling.calendar import WORKING, WorkCalendar
from tenants.models import Company

CHECK_IN, CHECK_OUT, FINGERPRINT = "0", "1", "1"


def _row(device_user_id, moment, status):
    return f"{device_user_id}\t{moment:%Y-%m-%d %H:%M:%S}\t{status}\t{FINGERPRINT}\t0\t\t"


def day_pattern(employee_index, day_index):
    """(minutes after shift start for IN, for OUT), or None for absent.

    Repeatable, so a run on two machines gives the same month.
    """
    key = (employee_index * 7 + day_index * 3) % 23
    if key == 0:
        return None                   # absent
    if key == 5:
        return (2, None)              # IN only: missing OUT
    if key in (3, 17):
        return (0, 270)               # half day: leaves after 4.5 hours
    if key in (1, 9, 14):
        return (25, 545)              # late by 25 minutes
    return (-5 + key % 8, 540 + key % 20)   # on time


class Command(BaseCommand):
    help = "Development only: send a month of demo punches through the real ingestion path."

    def add_arguments(self, parser):
        parser.add_argument("--company", required=True, help="Company code, e.g. DEMO-NWT.")
        parser.add_argument("--month", help="YYYY-MM. Defaults to the current month.")
        parser.add_argument("--force", action="store_true", help="Allow when DEBUG is off.")

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError("Refusing to create demo punches with DEBUG off. Use --force.")

        company = Company.objects.filter(code=options["company"]).first()
        if company is None:
            raise CommandError(f"No company with code {options['company']!r}.")

        today = timezone.localdate()
        if options["month"]:
            year, month = (int(part) for part in options["month"].split("-"))
        else:
            year, month = today.year, today.month
        first = datetime.date(year, month, 1)
        if first > today:
            raise CommandError("That month has not started yet.")
        last = (first.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)
        last = min(last, today)

        calendar = WorkCalendar(company.pk, first, last)
        if not calendar.has_any_shift:
            raise CommandError(
                "This company has no shifts set up. Add department shifts or a "
                "company shift under Shifts first."
            )

        with use_company(company):
            branch = Branch.objects.get(is_default=True)
            device_model = DeviceModel.objects.select_related("vendor").first()
            if device_model is None:
                raise CommandError("No device model in the catalogue.")
            serial = f"DEMO-SIM-{company.code}"
            device, created = BiometricDevice.objects.get_or_create(
                serial_number=serial,
                defaults={
                    "branch": branch,
                    "device_model": device_model,
                    "name": "Simulated demo device (not real hardware)",
                    "timezone": branch.timezone or company.timezone or "Asia/Dhaka",
                    "status": BiometricDevice.Status.ACTIVE,
                },
            )
            self.stdout.write(f"{'Registered' if created else 'Using'} simulated device {serial}.")

            since = datetime.datetime(2000, 1, 1, tzinfo=dt_timezone.utc)
            employees = list(
                Employee.objects.filter(
                    pk__in=EmployeeAssignment.objects.exclude(status="cancelled").values("employee_id")
                ).order_by("pk")
            )
            for employee in employees:
                DeviceEnrollment.objects.get_or_create(
                    device=device,
                    device_user_id=str(employee.pk),
                    defaults={
                        "employee": employee,
                        "effective_from": since,
                        "assigned_device_authorized": True,
                    },
                )
            # Each employee punches to their own department's shift.
            department_of = {}
            for assignment in (
                EmployeeAssignment.objects.exclude(status="cancelled").order_by("effective_from")
            ):
                department_of[assignment.employee_id] = assignment.department_id
            on_leave = set(
                LeaveDay.objects.filter(
                    work_date__gte=first, work_date__lte=last,
                    status__in=["reserved", "approved", "consumed"],
                ).values_list("employee_id", "work_date")
            )

        adapter = get_adapter(device.device_model.vendor.adapter_key)
        sent_days = punches = 0
        day, day_index = first, 0
        while day <= last:
            rows = []
            for index, employee in enumerate(employees):
                if employee.joining_date and day < employee.joining_date:
                    continue
                if calendar.day(branch.pk, day).kind != WORKING or (employee.pk, day) in on_leave:
                    continue
                pattern = day_pattern(index, day_index)
                shift = calendar.shift_for(department_of.get(employee.pk), day)
                if pattern is None or shift is None:
                    continue
                start = datetime.datetime.combine(day, shift.start_time)
                in_after, out_after = pattern
                rows.append(_row(employee.pk, start + datetime.timedelta(minutes=in_after), CHECK_IN))
                if out_after is not None:
                    rows.append(_row(employee.pk, start + datetime.timedelta(minutes=out_after), CHECK_OUT))
            if rows:
                body = "\n".join(rows) + "\n"
                query = {"SN": device.serial_number, "table": "ATTLOG", "Stamp": f"demo{day:%Y%m%d}"}
                parsed = adapter.parse(
                    path_name="cdata_upload", query=query, body_text=body,
                    serial_number=device.serial_number,
                )
                ingest(
                    device=device, parsed=parsed, raw_body=body, source_ip="127.0.0.1",
                    headers={"User-Agent": "seed_demo_punches"}, content_type="text/plain",
                )
                sent_days += 1
                punches += len(rows)
            day += datetime.timedelta(days=1)
            day_index += 1

        with use_company(company):
            authorized = PunchEvent.objects.filter(
                device=device, authorization_status=PunchEvent.AuthorizationStatus.AUTHORIZED
            ).count()
            total = PunchEvent.objects.filter(device=device).count()
        self.stdout.write(self.style.SUCCESS(
            f"Sent {punches} punch rows over {sent_days} day(s) for {len(employees)} employee(s). "
            f"The device now holds {total} punches, {authorized} authorized. "
            "These are SIMULATED punches, not hardware evidence."
        ))
