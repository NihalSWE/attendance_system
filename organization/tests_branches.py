"""Branch write flow: authorization, scope, default handling, audit."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from accounts.models import CompanyMembership, User
from auditlog.models import AuditLog
from common.tenant import use_company
from organization.models import Branch
from organization.services import (
    create_branch,
    set_branch_status,
    update_branch,
    visible_branches,
)
from tenants.services import onboard_company


class BranchServiceTests(TestCase):
    def setUp(self):
        self.company = onboard_company(code="ACME", slug="acme", name="Acme Ltd")
        self.other = onboard_company(code="OTHER", slug="other", name="Other Ltd")

        self.admin = User.objects.create_user(email="admin@acme.test", password="pw")
        self.hr = User.objects.create_user(email="hr@acme.test", password="pw")
        self.scoped = User.objects.create_user(email="scoped@acme.test", password="pw")
        self.outsider = User.objects.create_user(email="out@other.test", password="pw")

        self.membership = CompanyMembership.all_objects.create(
            company=self.company, user=self.admin,
            role=CompanyMembership.Role.COMPANY_ADMIN,
            status=CompanyMembership.Status.ACTIVE,
        )
        CompanyMembership.all_objects.create(
            company=self.company, user=self.hr,
            role=CompanyMembership.Role.HR,
            status=CompanyMembership.Status.ACTIVE,
        )
        CompanyMembership.all_objects.create(
            company=self.other, user=self.outsider,
            role=CompanyMembership.Role.COMPANY_ADMIN,
            status=CompanyMembership.Status.ACTIVE,
        )
        with use_company(self.company):
            self.hq = Branch.objects.get(is_default=True)

    def _create(self, code="CTG", name="Chittagong", **extra):
        values = {
            "code": code, "name": name, "address": "", "city": "", "postal_code": "",
            "country_code": "", "timezone": "Asia/Dhaka", "email": "", "phone": "",
            "is_default": False, "status": "active",
            "opened_on": None, "closed_on": None,
        }
        values.update(extra)
        return create_branch(actor=self.admin, company_id=self.company.pk, values=values)

    # --- authorization ----------------------------------------------------

    def test_company_admin_can_create_a_branch(self):
        branch = self._create()
        self.assertEqual(branch.company_id, self.company.pk)
        self.assertEqual(branch.code, "CTG")

    def test_hr_role_cannot_create_a_branch(self):
        with self.assertRaises(PermissionDenied):
            create_branch(actor=self.hr, company_id=self.company.pk, values={
                "code": "X", "name": "X", "status": "active", "is_default": False,
            })

    def test_member_of_another_company_is_refused(self):
        with self.assertRaises(PermissionDenied):
            create_branch(actor=self.outsider, company_id=self.company.pk, values={
                "code": "X", "name": "X", "status": "active", "is_default": False,
            })

    def test_cannot_edit_a_branch_in_another_company(self):
        with use_company(self.other):
            foreign = Branch.objects.get(is_default=True)
        with self.assertRaises(PermissionDenied):
            update_branch(actor=self.admin, company_id=self.company.pk,
                          branch_id=foreign.pk, values={"name": "Hijacked"})

    def test_unsupported_field_is_rejected(self):
        # A crafted POST must not reach a column the form does not expose.
        with self.assertRaises(ValidationError):
            create_branch(actor=self.admin, company_id=self.company.pk, values={
                "code": "Z", "name": "Z", "company_id": self.other.pk,
            })

    # --- branch-level row scope -------------------------------------------

    def test_scoped_member_sees_only_their_branches(self):
        # Visibility scoping applies to any member, not only administrators.
        ctg = self._create()
        scoped_membership = CompanyMembership.all_objects.get(user=self.hr)
        scoped_membership.allowed_branches.add(ctg)
        with use_company(self.company):
            visible = set(visible_branches(scoped_membership).values_list("pk", flat=True))
        self.assertEqual(visible, {ctg.pk})
        self.assertNotIn(self.hq.pk, visible)

    def test_scoped_member_cannot_edit_a_branch_outside_scope(self):
        # Only one non-ended owner/company_admin may exist per company, so the
        # incoming administrator replaces the outgoing one (a handover).
        ctg = self._create()
        self.membership.status = CompanyMembership.Status.ENDED
        self.membership.save(update_fields=["status"])
        scoped_membership = CompanyMembership.all_objects.create(
            company=self.company, user=self.scoped,
            role=CompanyMembership.Role.COMPANY_ADMIN,
            status=CompanyMembership.Status.ACTIVE,
        )
        scoped_membership.allowed_branches.add(ctg)
        with self.assertRaises(PermissionDenied):
            update_branch(actor=self.scoped, company_id=self.company.pk,
                          branch_id=self.hq.pk, values={"name": "Nope"})

    def test_empty_scope_means_unrestricted_not_no_access(self):
        with use_company(self.company):
            visible = set(visible_branches(self.membership).values_list("pk", flat=True))
        self.assertIn(self.hq.pk, visible)

    # --- default branch ----------------------------------------------------

    def test_promoting_a_new_default_demotes_the_previous_one(self):
        new_default = self._create(is_default=True)
        with use_company(self.company):
            self.hq.refresh_from_db()
            self.assertFalse(self.hq.is_default)
            self.assertTrue(Branch.objects.get(pk=new_default.pk).is_default)
            self.assertEqual(Branch.objects.filter(is_default=True).count(), 1)

    def test_default_branch_cannot_be_retired(self):
        with self.assertRaises(ValidationError):
            set_branch_status(actor=self.admin, company_id=self.company.pk,
                              branch_id=self.hq.pk, status="inactive")

    def test_non_default_branch_can_be_retired_without_deletion(self):
        ctg = self._create()
        set_branch_status(actor=self.admin, company_id=self.company.pk,
                          branch_id=ctg.pk, status="inactive", reason="Closed")
        with use_company(self.company):
            ctg.refresh_from_db()
        self.assertEqual(ctg.status, "inactive")
        self.assertEqual(Branch.all_objects.filter(pk=ctg.pk).count(), 1)

    def test_device_scope_override_can_be_set_and_cleared(self):
        # This field is on the form, so the service whitelist must accept it.
        branch = self._create(device_attendance_scope_override="branch_devices")
        self.assertEqual(branch.device_attendance_scope_override, "branch_devices")
        update_branch(actor=self.admin, company_id=self.company.pk,
                      branch_id=branch.pk,
                      values={"device_attendance_scope_override": None})
        with use_company(self.company):
            branch.refresh_from_db()
        self.assertIsNone(branch.device_attendance_scope_override)

    def test_duplicate_code_in_the_same_company_is_rejected(self):
        self._create(code="CTG")
        with self.assertRaises(ValidationError):
            self._create(code="CTG", name="Duplicate")

    # --- audit --------------------------------------------------------------

    def test_create_writes_an_audit_row_with_the_company_actor(self):
        branch = self._create()
        entry = AuditLog.objects.filter(action="branch.created",
                                        object_id=str(branch.pk)).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.actor_user, self.admin)
        self.assertEqual(entry.actor_type, AuditLog.ActorType.USER)
        self.assertEqual(entry.company_id, self.company.pk)

    def test_update_audit_records_before_and_after(self):
        branch = self._create(name="Chittagong")
        update_branch(actor=self.admin, company_id=self.company.pk,
                      branch_id=branch.pk, values={"name": "Chattogram"})
        entry = AuditLog.objects.filter(action="branch.updated",
                                        object_id=str(branch.pk)).first()
        self.assertEqual(entry.before_data["name"], "Chittagong")
        self.assertEqual(entry.after_data["name"], "Chattogram")

    def test_failed_write_leaves_no_branch_and_no_audit(self):
        before_audit = AuditLog.objects.count()
        with self.assertRaises(ValidationError):
            self._create(code="", name="")
        with use_company(self.company):
            self.assertEqual(Branch.objects.filter(name="").count(), 0)
        self.assertEqual(AuditLog.objects.count(), before_audit)
