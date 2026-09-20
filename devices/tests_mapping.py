"""Mapping employees to device users, and putting them on devices.

Device payloads follow the office SenseFace 2A (ZAM70-NF24HA-Ver3.0.15) as
captured 2026-09-19; template strings are made up.
"""

import datetime
from decimal import Decimal

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import CompanyMembership
from common.tenant import use_company
from devices.models import (
    BiometricDevice,
    DeviceEnrollment,
    DeviceModel,
    DeviceOutboxCommand,
    DeviceVendor,
)
from devices.services import commands, mapping
from devices.services.device_roster import build_roster
from employees.services import create_employee
from organization.catalogue import adopt_department, adopt_designation
from organization.models import Branch
from tenants.services import onboard_company

User = get_user_model()
KEY = Fernet.generate_key().decode()
SN = "NYU7251601501"
USERS = (
    "user uid=13\tcardno=3231436\tpin=445962\tpassword=\tgroup=1\tstarttime=0\tendtime=0"
    "\tname=Ajay\tprivilege=14\tdisable=0\tverify=0\n"
    "user uid=14\tcardno=8868366\tpin=445900\tpassword=\tgroup=1\tstarttime=0\tendtime=0"
    "\tname=Moin\tprivilege=0\tdisable=0\tverify=0\n"
    "user uid=15\tcardno=\tpin=777\tpassword=\tgroup=1\tstarttime=0\tendtime=0"
    "\tname=Nobody\tprivilege=0\tdisable=0\tverify=0\n"
)
BIODATA = (
    "biodata pin=445962\tno=6\tindex=0\tvalid=1\tduress=0\ttype=1\tmajorver=13\tminorver=0"
    "\tformat=0\ttmp=apUBEBgEFAKEFINGER==\n"
    "biodata pin=445962\tno=0\tindex=0\tvalid=1\tduress=0\ttype=9\tmajorver=40\tminorver=1"
    "\tformat=0\ttmp=apUBFjYCFAKEFACE==\n"
)


@override_settings(BIOMETRIC_TEMPLATE_KEY=KEY)
class MappingCase(TestCase):
    def setUp(self):
        vendor = DeviceVendor.objects.get_or_create(
            code="zkteco", defaults={"name": "ZKTeco", "adapter_key": "zkteco_adms_push"})[0]
        self.model = DeviceModel.objects.get_or_create(
            vendor=vendor, model_code="senseface-2a",
            defaults={"name": "SenseFace 2A", "protocol": DeviceModel.Protocol.ADMS_PUSH})[0]
        self.company = onboard_company(code="AMZ", slug="amz", name="Amazon")
        with use_company(self.company):
            self.hq = Branch.objects.get(is_default=True)
            self.unit = Branch.objects.create(company=self.company, code="CTG", name="Chittagong",
                                              timezone="Asia/Dhaka", country_code="BD")
            self.department = adopt_department(self.hq, "SW", "Software")
            self.designation = adopt_designation(self.department, "DEV", "Developer")
            self.unit_department = adopt_department(self.unit, "SW", "Software")
            self.unit_designation = adopt_designation(self.unit_department, "DEV", "Developer")
            self.device = self.device_at(self.hq, SN, "Main Entrance")
            self.second = self.device_at(self.hq, "NYU0000000002", "Back Door")
            self.unit_device = self.device_at(self.unit, "NYU0000000003", "CTG Gate")
        self.admin = self.member("admin@amz.test", "company_admin")
        self.manager = self.member("manager@amz.test", "manager", branches=[self.hq])
        self.ajay = self.employee("Ajay", "445962")
        self.moin = self.employee("Moin", "445900")
        self.new = self.employee("Newcomer", "445999")
        self.lettered = self.employee("Lettered", "E-7")
        self.far = self.employee("Far", "445800", branch=self.unit)

    def device_at(self, branch, serial, name):
        return BiometricDevice.objects.create(
            branch=branch, device_model=self.model, name=name, serial_number=serial,
            timezone="Asia/Dhaka", status=BiometricDevice.Status.ACTIVE,
            settings={"announced": {"pushver": "3.1.2", "device_type": "acc"}})

    def member(self, email, role, branches=()):
        user = User.objects.create_user(email=email, password="pw-12345678")
        membership = CompanyMembership.all_objects.create(
            company=self.company, user=user, role=role, status="active")
        with use_company(self.company):
            membership.allowed_branches.set(branches)
        return user

    def employee(self, name, code, branch=None):
        branch = branch or self.hq
        return create_employee(
            company=self.company, first_name=name, employee_code=code, branch=branch,
            department=self.department if branch == self.hq else self.unit_department,
            designation=self.designation if branch == self.hq else self.unit_designation,
            effective_from=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
            pay_basis="monthly", base_rate=Decimal("20000"),
        )["employee"]

    def upload(self, body, table="user", serial=SN, cmdid="1"):
        extra = f"&cmdid={cmdid}" if cmdid else ""
        return self.client.post(
            f"/iclock/cdata?SN={serial}&type=tabledata&tablename={table}&count=3{extra}",
            data=body, content_type="text/plain")

    def outbox(self, device):
        return list(DeviceOutboxCommand.all_objects.filter(device=device).order_by("command_id")
                    .values_list("key", flat=True))


class MapOneTests(MappingCase):
    def test_person_on_the_device_is_mapped_without_writing(self):
        self.upload(USERS)
        with use_company(self.company):
            outcome = mapping.map_employee(actor=self.admin, device=self.device, employee=self.moin)
        enrollment = outcome.enrollment
        self.assertEqual((enrollment.device_user_id, enrollment.card_number), ("445900", "8868366"))
        self.assertTrue(enrollment.attendance_enabled and enrollment.assigned_device_authorized)
        # On the device but with no fingerprint/face there: nothing to copy.
        self.assertTrue(outcome.uploaded is False or self.outbox(self.device))

    def test_newcomer_is_sent_to_the_device(self):
        self.upload(USERS)
        with use_company(self.company):
            outcome = mapping.map_employee(actor=self.admin, device=self.device, employee=self.new)
        self.assertTrue(outcome.uploaded)
        self.assertEqual(self.outbox(self.device), ["push_user:445999", "push_access:445999"])
        self.assertEqual(outcome.enrollment.enrollment_status, DeviceEnrollment.EnrollmentStatus.QUEUED)

    def test_copy_to_a_second_device_carries_card_role_finger_and_face(self):
        self.upload(USERS)
        self.upload(BIODATA, table="biodata", cmdid="2")
        with use_company(self.company):
            outcome = mapping.map_employee(actor=self.admin, device=self.second, employee=self.ajay)
        self.assertTrue(outcome.uploaded and outcome.fingerprint and outcome.face)
        self.assertEqual(self.outbox(self.second), [
            "push_user:445962", "push_access:445962",
            "push_template:445962:1:6:0", "push_template:445962:9:0:0"])
        user_row = DeviceOutboxCommand.all_objects.get(device=self.second, key="push_user:445962")
        self.assertEqual(user_row.body, "DATA UPDATE user Pin=445962\tName=Ajay\tCardNo=3231436"
                                        "\tPrivilege=14\tGrp=1")
        finger = DeviceOutboxCommand.all_objects.get(device=self.second, key="push_template:445962:1:6:0")
        self.assertEqual(finger.body, "")  # the template waits encrypted
        self.assertNotIn(b"FAKEFINGER", bytes(finger.body_encrypted))

    def test_refusals(self):
        with use_company(self.company):
            with self.assertRaisesMessage(mapping.MappingError, "must be digits"):
                mapping.map_employee(actor=self.admin, device=self.device, employee=self.lettered)
            with self.assertRaisesMessage(mapping.MappingError, "works at Chittagong"):
                mapping.map_employee(actor=self.admin, device=self.device, employee=self.far)
            mapping.map_employee(actor=self.admin, device=self.device, employee=self.moin)
            with self.assertRaisesMessage(mapping.MappingError, "already mapped"):
                mapping.map_employee(actor=self.admin, device=self.device, employee=self.moin)

    def test_branch_manager_maps_only_their_branch(self):
        from django.core.exceptions import PermissionDenied

        with use_company(self.company):
            mapping.map_employee(actor=self.manager, device=self.device, employee=self.moin)
            with self.assertRaises(PermissionDenied):
                mapping.map_employee(actor=self.manager, device=self.unit_device, employee=self.far)

    def test_start_day_is_company_midnight(self):
        with use_company(self.company):
            outcome = mapping.map_employee(actor=self.admin, device=self.device, employee=self.moin,
                                           start_day=datetime.date(2026, 9, 1))
        start = outcome.enrollment.effective_from.astimezone(datetime.timezone(datetime.timedelta(hours=6)))
        self.assertEqual((start.date(), start.hour), (datetime.date(2026, 9, 1), 0))


class BulkAndAutoTests(MappingCase):
    def test_bulk_map_a_branch_to_all_its_devices(self):
        self.upload(USERS)
        with use_company(self.company):
            result = mapping.map_branch(actor=self.admin, branch=self.hq)
        # Ajay, Moin, Newcomer on both HQ devices; Lettered fails on both.
        self.assertEqual(len(result.mapped), 6)
        self.assertEqual(len(result.failed), 2)
        with use_company(self.company):
            again = mapping.map_branch(actor=self.admin, branch=self.hq)
        self.assertEqual((len(again.mapped), len(again.skipped)), (0, 6))

    def test_map_automatically_by_employee_id(self):
        self.upload(USERS)
        with use_company(self.company):
            result = mapping.map_automatically(actor=self.admin, device=self.device)
        self.assertEqual(sorted(o.enrollment.device_user_id for o in result.mapped), ["445900", "445962"])
        self.assertEqual([pin for pin, _, _ in result.skipped], ["777"])
        self.assertEqual(self.outbox(self.device), [])  # nothing written: they are on it


class RemovedFromDeviceTests(MappingCase):
    def test_user_missing_from_the_latest_full_list_is_marked(self):
        self.upload(USERS, cmdid="1")
        # Moin deleted on the terminal; the next full answer lacks him.
        self.upload("\n".join(line for line in USERS.splitlines() if "pin=445900" not in line) + "\n",
                    cmdid="5")
        with use_company(self.company):
            roster = {row["pin"]: row for row in build_roster(self.device)}
        self.assertTrue(roster["445900"]["removed_from_device"])
        self.assertFalse(roster["445962"]["removed_from_device"])

    def test_single_user_upload_after_the_list_is_not_removed(self):
        self.upload(USERS, cmdid="1")
        self.upload("user uid=20\tcardno=\tpin=445999\tpassword=\tgroup=1\tstarttime=0\tendtime=0"
                    "\tname=Newcomer\tprivilege=0\tdisable=0\tverify=0\n", cmdid=None)
        with use_company(self.company):
            roster = {row["pin"]: row for row in build_roster(self.device)}
        self.assertFalse(roster["445999"]["removed_from_device"])

    def test_without_a_full_list_nothing_is_marked(self):
        self.upload(USERS, cmdid=None)
        with use_company(self.company):
            self.assertFalse(any(r["removed_from_device"] for r in build_roster(self.device)))


class OutboxTests(MappingCase):
    def test_outbox_is_handed_over_a_few_per_check_in_and_answers_recorded(self):
        with use_company(self.company):
            for employee in (self.new, self.moin, self.ajay):
                mapping.map_employee(actor=self.admin, device=self.second, employee=employee)
        self.assertEqual(len(self.outbox(self.second)), 6)
        body, issued = commands.take_pending_commands(self.second)
        self.assertEqual(len(issued), commands.COMMANDS_PER_POLL)
        self.assertEqual(body.count("\n"), commands.COMMANDS_PER_POLL)
        commands.note_results(self.second, "\n".join(
            f"ID={e['id']}&Return=0&CMD=DATA UPDATE" for e in issued))
        _, rest = commands.take_pending_commands(self.second)
        self.assertEqual(len(rest), 1)
        done = DeviceOutboxCommand.all_objects.filter(device=self.second, status="done").count()
        self.assertEqual(done, commands.COMMANDS_PER_POLL)
        self.assertEqual(commands.waiting_count(self.second), 0)
        self.assertTrue(all(r["ok"] for r in commands.recent_results(self.second)))

    def test_same_write_queued_twice_is_sent_once(self):
        with use_company(self.company):
            commands.push_to_device(self.second, "445999", name="A")
            commands.push_to_device(self.second, "445999", name="B")
        self.assertEqual(commands.waiting_count(self.second), 2)
        body, _ = commands.take_pending_commands(self.second)
        self.assertIn("Name=B", body)
        self.assertNotIn("Name=A", body)


class ScreenTests(MappingCase):
    def test_employees_list_shows_badges_and_map_controls(self):
        self.upload(USERS)
        self.upload(BIODATA, table="biodata", cmdid="2")
        with use_company(self.company):
            mapping.map_employee(actor=self.admin, device=self.device, employee=self.ajay)
        self.client.force_login(self.admin)
        page = self.client.get(reverse("employee_list")).content.decode()
        self.assertIn('title="Linked on Main Entrance as user 445962"', page)
        self.assertIn("Finger · Face saved", page)
        self.assertIn("Not on any device", page)
        self.assertIn("Bulk map to devices", page)
        self.assertIn('data-map-employee="%d"' % self.moin.pk, page)

    def test_map_dialog_posts_and_returns(self):
        self.upload(USERS)
        self.client.force_login(self.admin)
        response = self.client.post(reverse("devices:employee_map"), {
            "employee": self.moin.pk, "device": self.device.pk,
            "start_day": timezone.localdate().isoformat(),
            "attendance_enabled": "on", "assigned": "on", "next": reverse("employee_list"),
        }, follow=True)
        self.assertContains(response, "Moin mapped on Main Entrance as Employee ID 445900")

    def test_bulk_map_posts(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("devices:employee_bulk_map"), {
            "branch": self.hq.pk, "attendance_enabled": "on", "assigned": "on",
        }, follow=True)
        self.assertContains(response, "Head Office: mapped 6")

    def test_manager_uses_map_but_not_on_another_branch(self):
        self.client.force_login(self.manager)
        response = self.client.post(reverse("devices:employee_map"), {
            "employee": self.far.pk, "device": self.unit_device.pk,
        }, follow=True)
        self.assertContains(response, "may not map")
        self.assertFalse(DeviceEnrollment.all_objects.filter(employee=self.far).exists())

    def test_device_users_map_automatically(self):
        self.upload(USERS)
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("devices:device_map_automatically", args=[self.device.public_id]), follow=True)
        self.assertContains(response, "Mapped 2 device user(s) by Employee ID")
        self.assertContains(response, "777 (no employee has this Employee ID)")


class TransferTests(MappingCase):
    def setUp(self):
        super().setUp()
        self.upload(USERS)
        self.upload(BIODATA, table="biodata", cmdid="2")

    def test_ticked_users_copy_with_card_role_finger_face(self):
        with use_company(self.company):
            result = mapping.transfer_users(actor=self.admin, source=self.device, target=self.second,
                                            pins=["445962", "445900"])
        self.assertEqual((result.sent, result.fingerprints, result.faces), (["445962", "445900"], 1, 1))
        self.assertEqual(self.outbox(self.second), [
            "push_user:445962", "push_access:445962", "push_template:445962:1:6:0",
            "push_template:445962:9:0:0", "push_user:445900", "push_access:445900"])
        moin = DeviceOutboxCommand.all_objects.get(device=self.second, key="push_user:445900")
        self.assertEqual(moin.body, "DATA UPDATE user Pin=445900\tName=Moin\tCardNo=8868366\tPrivilege=0\tGrp=1")

    def test_mapped_user_is_mapped_on_the_target_too(self):
        with use_company(self.company):
            mapping.map_employee(actor=self.admin, device=self.device, employee=self.ajay)
            result = mapping.transfer_users(actor=self.admin, source=self.device, target=self.second,
                                            pins=["445962"])
            self.assertEqual(result.mapped, [self.ajay])
            self.assertIsNotNone(mapping.live_enrollment(self.second, self.ajay))

    def test_only_to_the_same_model(self):
        other_model = DeviceModel.objects.get_or_create(
            vendor=self.model.vendor, model_code="senseface-3a",
            defaults={"name": "SenseFace 3A", "protocol": DeviceModel.Protocol.ADMS_PUSH})[0]
        with use_company(self.company):
            other = BiometricDevice.objects.create(
                branch=self.hq, device_model=other_model, name="3A", serial_number="VGU1",
                timezone="Asia/Dhaka", status=BiometricDevice.Status.ACTIVE)
            self.assertNotIn(other, mapping.transfer_targets(self.device))
            with self.assertRaisesMessage(mapping.MappingError, "same model"):
                mapping.transfer_users(actor=self.admin, source=self.device, target=other, pins=["445962"])

    def test_unknown_number_is_reported(self):
        with use_company(self.company):
            result = mapping.transfer_users(actor=self.admin, source=self.device, target=self.second,
                                            pins=["123"])
        self.assertEqual(result.failed, [("123", "not on the source device")])

    def test_screen_ticked_and_everyone(self):
        self.client.force_login(self.admin)
        url = reverse("devices:device_users", args=[self.device.public_id])
        page = self.client.get(url).content.decode()
        self.assertIn("data-pick-all", page)
        self.assertIn('value="445962" form="transfer-form"', page)
        self.assertIn("Back Door", page)
        response = self.client.post(
            reverse("devices:device_users_transfer", args=[self.device.public_id]),
            {"target": self.second.pk, "pin": ["445900"]}, follow=True)
        self.assertContains(response, "Copying 1 user(s) to Back Door")
        response = self.client.post(
            reverse("devices:device_users_transfer", args=[self.device.public_id]),
            {"target": self.second.pk, "all": "1"}, follow=True)
        self.assertContains(response, "Copying 3 user(s) to Back Door")


class ScenarioTests(MappingCase):
    """The ways a company gets people and devices together (Ajay, 2026-09-19)."""

    def test_device_first_import_users_as_employees(self):
        # People enrolled at the terminal; the company has no employees for them.
        from employees.models import EmployeeAssignment

        self.upload(USERS)
        self.upload(BIODATA, table="biodata", cmdid="2")
        with use_company(self.company):
            result = mapping.import_users(actor=self.admin, device=self.device, pins=["777"])
            [employee] = result.created
            assignment = mapping.current_assignment(employee)
            self.assertEqual((employee.first_name, assignment.employee_code), ("Nobody", "777"))
            self.assertEqual(assignment.department.name, "Unassigned")
            self.assertIsNotNone(mapping.live_enrollment(self.device, employee))
            # Existing employees with that Employee ID are linked, not duplicated.
            again = mapping.import_users(actor=self.admin, device=self.device)
            self.assertEqual(sorted(e.first_name for e in again.mapped), ["Ajay", "Moin"])
            self.assertEqual(again.created, [])
            self.assertEqual(EmployeeAssignment.all_objects.filter(employee_code="777").count(), 1)
        self.assertEqual(self.outbox(self.device), [])  # nothing written to the device

    def test_software_first_send_selected_employees(self):
        # Bulk-made employees, no templates yet: they go with ID and name only.
        with use_company(self.company):
            result = mapping.send_employees(actor=self.admin, employees=[self.new, self.far])
        sent = {(e.first_name, d.name) for e, d in result.sent}
        self.assertEqual(sent, {("Newcomer", "Main Entrance"), ("Newcomer", "Back Door"),
                                ("Far", "CTG Gate")})
        self.assertEqual(self.outbox(self.device), ["push_user:445999", "push_access:445999"])

    def test_replacement_device_is_loaded_with_saved_templates(self):
        # Templates saved from the old device; a new device of the same model arrives.
        self.upload(USERS)
        self.upload(BIODATA, table="biodata", cmdid="2")
        with use_company(self.company):
            new_device = self.device_at(self.hq, "NYU0000000009", "Replacement")
            result = mapping.load_device(actor=self.admin, device=new_device)
        self.assertEqual({e.first_name for e, _ in result.sent}, {"Ajay", "Moin", "Newcomer"})
        self.assertEqual([e.first_name for e, _, _ in result.failed], ["Lettered"])
        keys = self.outbox(new_device)
        self.assertIn("push_template:445962:1:6:0", keys)
        self.assertIn("push_template:445962:9:0:0", keys)
        ajay = DeviceOutboxCommand.all_objects.get(device=new_device, key="push_user:445962")
        self.assertIn("CardNo=3231436\tPrivilege=14", ajay.body)

    def test_screens(self):
        self.upload(USERS)
        self.client.force_login(self.admin)
        with use_company(self.company):
            new_device = self.device_at(self.hq, "NYU0000000009", "Replacement")
        page = self.client.get(reverse("devices:device_users", args=[new_device.public_id])).content.decode()
        self.assertIn("Load 4 employees onto this device", page)
        response = self.client.post(reverse("devices:device_load", args=[new_device.public_id]), follow=True)
        self.assertContains(response, "Loading 3 employee(s) onto Replacement")
        page = self.client.get(reverse("devices:device_users", args=[self.device.public_id])).content.decode()
        self.assertIn("Add as employees", page)
        self.assertIn('name="pin" value="777" form="transfer-form"', page)
        response = self.client.post(reverse("devices:device_users_import", args=[self.device.public_id]),
                                    {"pin": ["777"]}, follow=True)
        self.assertContains(response, "Added 1 employee(s) from Main Entrance")
        employees = self.client.get(reverse("employee_list")).content.decode()
        self.assertIn('name="employee" value="%d" form="send-form"' % self.moin.pk, employees)
        response = self.client.post(reverse("devices:employees_send"), {"all": "1"}, follow=True)
        self.assertContains(response, "Sending")


class RefillAfterRemovalTests(MappingCase):
    def test_removed_users_come_back_with_card_role_and_templates(self):
        # Everyone but Ajay deleted on the terminal, list refreshed, then all sent back.
        self.upload(USERS, cmdid="1")
        self.upload(BIODATA.replace("445962", "445900"), table="biodata", cmdid="2")
        with use_company(self.company):
            mapping.map_automatically(actor=self.admin, device=self.device)
        self.upload("\n".join(l for l in USERS.splitlines() if "pin=445962" in l) + "\n", cmdid="9")
        with use_company(self.company):
            result = mapping.send_employees(actor=self.admin, employees=[self.moin])
        self.assertEqual(len(result.sent), 2)  # both devices of the branch
        moin = DeviceOutboxCommand.all_objects.get(device=self.device, key="push_user:445900")
        self.assertIn("CardNo=8868366", moin.body)
        self.assertIn("push_template:445900:1:6:0", self.outbox(self.device))
        self.assertIn("push_template:445900:9:0:0", self.outbox(self.device))


class RemoveFromDeviceTests(MappingCase):
    """Bulk and single removal, and the device's last super admin (Ajay, 2026-09-20)."""

    def setUp(self):
        super().setUp()
        self.upload(USERS)  # 445962 Ajay (Privilege 14), 445900 Moin, 777 Nobody

    def queued(self):
        return [key for key in self.outbox(self.device) if key.startswith("delete_user:")]

    def test_selected_users_are_removed_but_the_last_super_admin_is_kept(self):
        with use_company(self.company):
            result = mapping.remove_users(actor=self.admin, device=self.device,
                                          pins=["445900", "777", "445962"])
        self.assertEqual(result.removed, ["445900", "777"])
        self.assertEqual([pin for pin, _ in result.skipped], ["445962"])
        self.assertIn("only super admin", result.skipped[0][1])
        self.assertEqual(self.queued(), ["delete_user:445900", "delete_user:777"])
        body = DeviceOutboxCommand.all_objects.get(device=self.device, key="delete_user:445900").body
        self.assertEqual(body, "DATA DELETE user Pin=445900")

    def test_a_second_super_admin_makes_the_first_removable(self):
        self.upload(USERS.replace("pin=445900\tpassword=\tgroup=1\tstarttime=0\tendtime=0"
                                  "\tname=Moin\tprivilege=0",
                                  "pin=445900\tpassword=\tgroup=1\tstarttime=0\tendtime=0"
                                  "\tname=Moin\tprivilege=14"), cmdid="2")
        with use_company(self.company):
            result = mapping.remove_users(actor=self.admin, device=self.device, pins=["445962"])
        self.assertEqual((result.removed, result.skipped), (["445962"], []))

    def test_unknown_number_and_a_2x_device(self):
        with use_company(self.company):
            result = mapping.remove_users(actor=self.admin, device=self.device, pins=["123"])
            self.assertEqual(result.skipped, [("123", "not on this device")])
            att2 = self.device_at(self.hq, "NYU0000000008", "3A-ish")
            att2.settings = {"announced": {"pushver": "2.4.1", "device_type": "att"}}
            att2.save()
            with self.assertRaisesMessage(mapping.MappingError, "not measured"):
                mapping.remove_users(actor=self.admin, device=att2, pins=["1"])

    def test_the_row_button_and_the_bar_both_use_the_guard(self):
        self.client.force_login(self.admin)
        url = reverse("devices:device_user_delete", args=[self.device.public_id])
        response = self.client.post(url, {"device_user_id": "445962"}, follow=True)
        self.assertContains(response, "only super admin")
        self.assertEqual(self.queued(), [])
        response = self.client.post(url, {"device_user_id": "777"}, follow=True)
        self.assertContains(response, "Queued removal of device user 777")
        response = self.client.post(
            reverse("devices:device_users_remove", args=[self.device.public_id]),
            {"pin": ["445900"]}, follow=True)
        self.assertContains(response, "Removing 1 user(s) from Main Entrance")
        self.assertEqual(self.queued(), ["delete_user:777", "delete_user:445900"])

    def test_remove_everyone_keeps_the_admin(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("devices:device_users_remove", args=[self.device.public_id]),
            {"all": "1"}, follow=True)
        self.assertContains(response, "Removing 2 user(s)")
        self.assertContains(response, "445962 kept")
        self.assertEqual(self.queued(), ["delete_user:445900", "delete_user:777"])

    def test_the_bar_offers_removal(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("devices:device_users", args=[self.device.public_id])).content.decode()
        self.assertIn("Remove from device", page)
        self.assertIn('data-confirm-tone="danger"', page)


class JobProgressTests(MappingCase):
    def test_counts_bar_and_time_left(self):
        from devices.services.commands import job_progress, note_results, take_pending_commands

        self.upload(USERS)
        with use_company(self.company):
            mapping.send_employees(actor=self.admin, employees=[self.new])  # 2 commands per device
            job = job_progress(self.device)
            self.assertEqual((job["running"], job["waiting"], job["done"], job["percent"]),
                             (True, 2, 0, 0))
            # 10s check-in, 5 per poll -> a few seconds for two commands.
            self.assertGreater(job["seconds_left"], 0)
            _, issued = take_pending_commands(self.device)
            note_results(self.device, f"ID={issued[0]['id']}&Return=0&CMD=DATA UPDATE\n"
                                      f"ID={issued[1]['id']}&Return=-1&CMD=DATA UPDATE")
            job = job_progress(self.device)
            self.assertEqual((job["running"], job["done"], job["refused"], job["percent"]),
                             (False, 1, 1, 100))
            self.assertEqual(job["refused_users"], ["445999"])

    def test_the_page_shows_the_card_and_the_endpoint_answers(self):
        self.client.force_login(self.admin)
        with use_company(self.company):
            mapping.send_employees(actor=self.admin, employees=[self.new])
        page = self.client.get(reverse("devices:device_users", args=[self.device.public_id])).content.decode()
        self.assertIn("Sending to Main Entrance", page)
        self.assertIn('data-job-bar', page)
        answer = self.client.get(reverse("devices:device_job_progress", args=[self.device.public_id]))
        self.assertEqual(answer.json()["waiting"], 2)


class SavedSummaryTests(MappingCase):
    def test_the_page_says_what_is_saved_and_in_which_format(self):
        self.upload(USERS)
        self.upload(BIODATA, table="biodata", cmdid="2")
        self.client.force_login(self.admin)
        page = self.client.get(reverse("devices:device_users", args=[self.device.public_id])).content.decode()
        self.assertIn("Saved on this server", page)
        self.assertIn("<strong>1</strong> fingerprint", page)
        self.assertIn("<strong>1</strong> face", page)
        self.assertIn("1 fingerprint · type 1 · v13.0", page)
        self.assertIn("1 face · type 9 · v40.1", page)

    def test_nothing_saved_yet_shows_no_line(self):
        self.upload(USERS)
        self.client.force_login(self.admin)
        page = self.client.get(reverse("devices:device_users", args=[self.device.public_id])).content.decode()
        self.assertNotIn("Saved on this server", page)
