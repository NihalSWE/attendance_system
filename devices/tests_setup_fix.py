"""Attendance → Fix setup (2026-10-26): people added from a device, in the
Unassigned department with no shift and missing from devices, fixed in bulk."""

import datetime
from decimal import Decimal

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from common.tenant import use_company
from devices.models import DeviceEnrollment, PunchEvent, SetupFixJob
from devices.services import mapping, setup_fix
from devices.tests_mapping import KEY, SN, MappingCase
from employees.models import EmployeeAssignment
from scheduling.models import DepartmentShift, Shift


@override_settings(BIOMETRIC_TEMPLATE_KEY=KEY)
class SetupFixTests(MappingCase):
    def setUp(self):
        super().setUp()
        with use_company(self.company):
            shift = Shift.objects.create(
                company=self.company, code="DAY", name="Day", start_time=datetime.time(9),
                end_time=datetime.time(18), scheduled_minutes=540)
            DepartmentShift.objects.create(company=self.company, department=self.department,
                                           shift=shift, is_default=True,
                                           effective_from=datetime.date(2026, 1, 1))
            self.loose_department, self.loose_designation = mapping.unassigned_placement(self.hq)
            # Added from a device: Unassigned, no shift.
            self.loose = self.employee("Parvez", "130016")
            EmployeeAssignment.objects.filter(employee=self.loose).update(
                department=self.loose_department, designation=self.loose_designation)
        self.client.force_login(self.admin)

    def problems(self):
        return {p.employee.first_name: p.problems for p in setup_fix.people(self.company)}

    def test_the_list_says_what_is_wrong(self):
        found = self.problems()
        self.assertEqual(found["Parvez"], [setup_fix.UNASSIGNED, setup_fix.NO_SHIFT, setup_fix.DEVICES])
        # Linked on one of the branch's two devices: missing from the other.
        with use_company(self.company):
            mapping.map_employee(actor=self.admin, device=self.device, employee=self.ajay)
        person = next(p for p in setup_fix.people(self.company) if p.employee == self.ajay)
        self.assertEqual((person.on, person.of, person.missing), (1, 2, ["Back Door"]))
        page = self.client.get(reverse("attendance:setup_fix"))
        self.assertContains(page, "Parvez")
        self.assertContains(page, "Not on: Back Door, Main Entrance")

    def test_set_department_corrects_the_placement_from_its_first_day(self):
        first = EmployeeAssignment.all_objects.get(employee=self.loose).effective_from
        response = self.client.post(reverse("attendance:setup_fix"), {
            "section": "placement", "who": "ticked", "ids": [self.loose.pk],
            "branch": self.hq.pk, "department": self.department.pk,
            "designation": self.designation.pk, "start": setup_fix.month_starts(self.company)[0][0]})
        self.assertRedirects(response, reverse("attendance:setup_fix"), fetch_redirect_response=False)
        rows = list(EmployeeAssignment.all_objects.filter(employee=self.loose))
        self.assertEqual(len(rows), 1)      # corrected, not a transfer
        self.assertEqual((rows[0].department, rows[0].effective_from), (self.department, first))
        self.assertEqual(self.problems()["Parvez"], [setup_fix.DEVICES])
        job = SetupFixJob.all_objects.get()
        self.assertEqual((job.kind, job.status, job.done_count), ("recalculate", "done", 1))

    def test_nobody_ticked_comes_back_in_its_modal(self):
        page = self.client.post(reverse("attendance:setup_fix"), {
            "section": "send", "who": "ticked"})
        self.assertContains(page, 'data-open-on-load="send-dialog"')
        self.assertContains(page, "Nobody is ticked")

    def test_a_designation_of_another_department_is_refused(self):
        page = self.client.post(reverse("attendance:setup_fix"), {
            "section": "placement", "who": "all", "branch": self.hq.pk,
            "department": self.department.pk, "designation": self.loose_designation.pk,
            "start": setup_fix.month_starts(self.company)[0][0]})
        self.assertContains(page, 'data-open-on-load="placement-dialog"')
        self.assertEqual(EmployeeAssignment.all_objects.get(employee=self.loose).department,
                         self.loose_department)

    def test_send_puts_them_on_every_device_of_their_branch(self):
        self.client.post(reverse("attendance:setup_fix") + "?problem=" + setup_fix.DEVICES, {
            "section": "send", "who": "ticked", "ids": [self.loose.pk]})
        devices = set(DeviceEnrollment.all_objects.filter(employee=self.loose)
                      .values_list("device__name", flat=True))
        self.assertEqual(devices, {"Main Entrance", "Back Door"})
        self.assertNotIn(setup_fix.DEVICES, self.problems()["Parvez"])

    def test_recheck_counts_the_scans_refused_before_the_link(self):
        start = setup_fix.month_starts(self.company)[0][0]
        day = max(start, timezone.localdate() - datetime.timedelta(days=3))
        self.client.post(f"/iclock/cdata?SN={SN}&table=ATTLOG&Stamp=77",
                         data=f"130016\t{day:%Y-%m-%d} 09:00:00\t0\t15\t0\t\t\n",
                         content_type="text/plain")
        punch = PunchEvent.all_objects.get(device_user_id="130016")
        self.assertEqual(punch.authorization_status, PunchEvent.AuthorizationStatus.UNKNOWN_EMPLOYEE)
        # Linked later, from before the scan, the way "Add as employees" did -
        # without judging the old scans again.
        DeviceEnrollment.all_objects.create(
            company=self.company, device=self.device, employee=self.loose, device_user_id="130016",
            effective_from=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
            enrollment_status=DeviceEnrollment.EnrollmentStatus.SYNCED, assigned_device_authorized=True)
        self.client.post(reverse("attendance:setup_fix"), {"section": "recheck",
                                                           "start": start.isoformat()})
        punch.refresh_from_db()
        self.assertEqual(punch.authorization_status, PunchEvent.AuthorizationStatus.AUTHORIZED)
        job = SetupFixJob.all_objects.get(kind="recheck")
        self.assertIn("1 now count", job.message)

    def test_only_salary_months_not_yet_posted_are_offered(self):
        starts = setup_fix.month_starts(self.company)
        self.assertTrue(starts)
        self.assertIn("salary month", starts[0][1])

    def test_a_branch_manager_cannot_open_it(self):
        self.client.force_login(self.manager)
        self.assertNotEqual(self.client.get(reverse("attendance:setup_fix")).status_code, 200)

    def test_the_job_runs_outside_a_request_as_the_thread_does(self):
        with override_settings(SETUP_FIX_IN_BACKGROUND=True), \
                self.captureOnCommitCallbacks(execute=False):
            job, _ = setup_fix.start(actor=self.admin, company=self.company,
                                     kind=SetupFixJob.Kind.SEND, employee_ids=[self.loose.pk])
        setup_fix.run_job(job.pk)      # no company context, as in the thread
        job.refresh_from_db()
        self.assertEqual((job.status, job.done_count, job.failures), ("done", 1, []))

    def test_unassigned_is_not_a_department_to_fix_into(self):
        page = self.client.post(reverse("attendance:setup_fix"), {
            "section": "placement", "who": "all", "branch": self.hq.pk,
            "department": self.loose_department.pk, "designation": self.loose_designation.pk,
            "start": setup_fix.month_starts(self.company)[0][0]})
        self.assertContains(page, "Choose their real department")

    def test_missing_days_start_from_the_chosen_day_and_count_the_refused_scans(self):
        from attendance.models import AttendanceRecord
        from employees.services import create_employee

        # The scan must fall before yesterday (when the person was added), so the
        # month chosen must start before yesterday: on a salary month's second
        # day its first day is yesterday itself, and the month before is used.
        yesterday = timezone.localdate() - datetime.timedelta(days=1)
        start = next(first for first, _label in setup_fix.month_starts(self.company) if first < yesterday)
        scan_day = max(start, timezone.localdate() - datetime.timedelta(days=5))
        added = mapping.day_start(self.company, yesterday)
        # Added yesterday from the device: placement and link from yesterday.
        late = create_employee(
            company=self.company, first_name="Late", employee_code="130200", branch=self.hq,
            department=self.department, designation=self.designation, effective_from=added,
            pay_basis="monthly", base_rate=Decimal("20000"))["employee"]
        with use_company(self.company):
            DeviceEnrollment.objects.create(
                company=self.company, device=self.device, employee=late, device_user_id="130200",
                effective_from=added, assigned_device_authorized=True,
                enrollment_status=DeviceEnrollment.EnrollmentStatus.SYNCED)
        self.client.post(f"/iclock/cdata?SN={SN}&table=ATTLOG&Stamp=78",
                         data=f"130200\t{scan_day:%Y-%m-%d} 09:00:00\t0\t15\t0\t\t\n",
                         content_type="text/plain")
        punch = PunchEvent.all_objects.get(device_user_id="130200")
        # Refused: before the link started (or before any link at all).
        self.assertNotEqual(punch.authorization_status, PunchEvent.AuthorizationStatus.AUTHORIZED)

        self.client.post(reverse("attendance:setup_fix"), {
            "section": "missing_days", "who": "people", "people": [late.pk],
            "start": start.isoformat()})

        start_at = mapping.day_start(self.company, start)
        self.assertEqual(EmployeeAssignment.all_objects.get(employee=late).effective_from, start_at)
        self.assertEqual(DeviceEnrollment.all_objects.get(employee=late).effective_from, start_at)
        late.refresh_from_db()
        self.assertEqual(late.joining_date, start)
        punch.refresh_from_db()
        self.assertEqual(punch.authorization_status, PunchEvent.AuthorizationStatus.AUTHORIZED)
        record = AttendanceRecord.all_objects.get(employee=late, work_date=scan_day)
        self.assertNotEqual(record.attendance_status, AttendanceRecord.AttendanceStatus.ABSENT)
        job = SetupFixJob.all_objects.get(kind="missing_days")
        self.assertEqual(job.status, "done")
        row = job.results[0]
        self.assertTrue(any(text.startswith("Placement from") for text in row["moved"]))
        self.assertIsNotNone(row["absent"])

    def test_missing_days_needs_someone_chosen(self):
        page = self.client.post(reverse("attendance:setup_fix"), {
            "section": "missing_days", "who": "people",
            "start": setup_fix.month_starts(self.company)[0][0].isoformat()})
        self.assertContains(page, 'data-open-on-load="missing-dialog"')
        self.assertContains(page, "Choose at least one person.")
