"""A12 part 1: branch access rules, granting and removing."""

import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from access_control.branch_access import (
    ALL_BRANCHES,
    BRANCH_PERMISSIONS,
    branches_for,
    can,
    grant_access,
    revoke_access,
    scope_queryset,
)
from access_control.models import AccessPermission
from accounts.models import CompanyMembership, User
from auditlog.models import AuditLog
from common.tenant import use_company
from employees.services import create_employee
from organization.catalogue import adopt_department, adopt_designation
from organization.models import Branch
from tenants.services import onboard_company


class BranchAccessTests(TestCase):
    def setUp(self):
        self.company = onboard_company(code="BRA", slug="bra", name="Branch Ltd")
        with use_company(self.company):
            self.hq = Branch.objects.get(is_default=True)
            self.unit = Branch.objects.create(
                company=self.company, code="CTG", name="Chittagong",
                timezone="Asia/Dhaka", country_code="BD",
            )
            self.department = adopt_department(self.hq, "SW", "Software")
            self.designation = adopt_designation(self.department, "DEV", "Developer")
        self.owner = self.member("owner@bra.test", "company_admin")
        self.manager = self.member("manager@bra.test", "manager", branches=[self.hq])
        self.hr = self.member("hr@bra.test", "hr")
        self.clerk_user, self.clerk = self.staff("clerk@bra.test", "E1")
        self.other_user, self.other = self.staff("other@bra.test", "E2")

    def member(self, email, role, branches=()):
        user = User.objects.create_user(email=email)
        membership = CompanyMembership.all_objects.create(
            company=self.company, user=user, role=role, status="active"
        )
        with use_company(self.company):
            membership.allowed_branches.set(branches)
        return user

    def staff(self, email, code):
        user = self.member(email, "employee")
        employee = create_employee(
            company=self.company, first_name=email.split("@")[0], employee_code=code,
            branch=self.hq, department=self.department, designation=self.designation,
            effective_from=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
            pay_basis="monthly", base_rate=Decimal("20000"),
        )["employee"]
        with use_company(self.company):
            employee.user = user
            employee.save(update_fields=["user"])
        return user, employee

    def grant(self, actor, employee, code, *branches):
        return grant_access(actor=actor, company_id=self.company.pk, employee_id=employee.pk,
                            code=code, branch_ids=[b.pk for b in branches])

    def test_granting_creates_the_permission_row_once(self):
        self.assertFalse(AccessPermission.objects.filter(code="salary.view").exists())
        self.grant(self.owner, self.clerk, "salary.view", self.hq)
        self.grant(self.owner, self.other, "salary.view", self.unit)
        self.assertEqual(AccessPermission.objects.filter(code="salary.view").count(), 1)
        # 11 from A12, plus attendance.view and attendance.fix (N10).
        self.assertEqual(len(BRANCH_PERMISSIONS), 13)

    def test_owner_everywhere_manager_own_branches_hr_company_wide_leave(self):
        company = self.company.pk
        self.assertIs(branches_for(self.owner, company, "salary.prepare"), ALL_BRANCHES)
        self.assertTrue(can(self.manager, company, "salary.prepare", self.hq.pk))
        self.assertFalse(can(self.manager, company, "salary.prepare", self.unit.pk))
        self.assertTrue(can(self.hr, company, "leave.record", self.unit.pk))
        self.assertFalse(can(self.hr, company, "salary.view"))
        self.assertFalse(can(self.clerk_user, company, "employees.view"))
        with use_company(self.company):
            visible = scope_queryset(Branch.objects.all(), self.manager, company, "employees.view", field="pk")
            self.assertEqual(list(visible), [self.hq])
        with self.assertRaises(ValueError):
            can(self.owner, company, "salary.finalise")

    def test_manager_grants_and_removes_access_in_own_branch_only(self):
        company = self.company.pk
        self.grant(self.manager, self.clerk, "salary.view", self.hq)
        self.assertTrue(can(self.clerk_user, company, "salary.view", self.hq.pk))
        self.assertFalse(can(self.clerk_user, company, "salary.view", self.unit.pk))
        with self.assertRaises(PermissionDenied):
            self.grant(self.manager, self.clerk, "salary.view", self.unit)
        revoke_access(actor=self.manager, company_id=company, employee_id=self.clerk.pk,
                      code="salary.view", branch_ids=[self.hq.pk])
        self.assertFalse(can(self.clerk_user, company, "salary.view"))
        with self.assertRaises(ValidationError):
            revoke_access(actor=self.manager, company_id=company, employee_id=self.clerk.pk,
                          code="salary.view", branch_ids=[self.hq.pk])
        self.assertEqual(
            AuditLog.objects.filter(action__in=["access.granted", "access.revoked"]).count(), 2
        )

    def test_grantee_can_hand_on_only_what_they_hold(self):
        company = self.company.pk
        self.grant(self.owner, self.clerk, "access.grant", self.hq)
        self.grant(self.owner, self.clerk, "leave.view", self.hq)
        self.grant(self.clerk_user, self.other, "leave.view", self.hq)
        self.assertTrue(can(self.other_user, company, "leave.view", self.hq.pk))
        with self.assertRaises(PermissionDenied):
            self.grant(self.clerk_user, self.other, "salary.view", self.hq)
        with self.assertRaises(PermissionDenied):
            self.grant(self.clerk_user, self.clerk, "leave.view", self.hq)
        with self.assertRaises(PermissionDenied):
            self.grant(self.hr, self.other, "leave.view", self.hq)

    def test_grants_add_branches_and_removal_keeps_the_rest(self):
        company = self.company.pk
        self.grant(self.owner, self.clerk, "overtime.decide", self.hq)
        self.grant(self.owner, self.clerk, "overtime.decide", self.unit)
        self.assertEqual(branches_for(self.clerk_user, company, "overtime.decide"), {self.hq.pk, self.unit.pk})
        remaining = revoke_access(actor=self.owner, company_id=company, employee_id=self.clerk.pk,
                                  code="overtime.decide", branch_ids=[self.hq.pk])
        self.assertEqual(remaining, [self.unit.pk])
        self.assertEqual(branches_for(self.clerk_user, company, "overtime.decide"), {self.unit.pk})
