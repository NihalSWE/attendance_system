"""Effective-permission resolution tests."""

from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from access_control.models import (
    AccessPermission,
    DepartmentPermission,
    DesignationPermission,
    EmployeePermissionOverride,
)
from access_control.services import (
    get_effective_permissions,
    has_permission,
    is_feature_enabled,
)
from common.tenant import use_company
from employees.services import create_employee
from organization.models import (
    Branch,
    Department,
    Designation,
)
from tenants.models import CompanyFeature, Feature
from tenants.services import onboard_company


def dt(y, m, d):
    return datetime(y, m, d, tzinfo=dt_timezone.utc)


NOW = dt(2024, 6, 1)


class PermissionResolutionTests(TestCase):
    def setUp(self):
        self.leave_feature = Feature.objects.create(code="leave", name="Leave")
        self.approve = AccessPermission.objects.create(
            feature=self.leave_feature, code="leave.approve",
            name="Approve leave", action="approve",
        )
        self.company = onboard_company(code="ACME", slug="acme", name="Acme Ltd")
        with use_company(self.company):
            self.branch = Branch.objects.get()
            self.department = Department.objects.create(
                branch=self.branch, code="SW", name="Software"
            )
            self.manager_title = Designation.objects.create(
                department=self.department, code="MGR", name="Manager"
            )
            self.dev_title = Designation.objects.create(
                department=self.department, code="DEV", name="Developer"
            )
            CompanyFeature.objects.create(
                company=self.company, feature=self.leave_feature, effect="enable"
            )
        self.manager = self._create_employee("Meera", "E1", self.manager_title)
        self.developer = self._create_employee("Dev", "E2", self.dev_title)

    def _create_employee(self, name, code, designation):
        return create_employee(
            company=self.company, first_name=name, employee_code=code,
            branch=self.branch, department=self.department, designation=designation,
            effective_from=dt(2024, 1, 1),
            pay_basis="monthly", base_rate=Decimal("30000"),
        )["employee"]

    def _designation_rule(self, designation, level, start=None):
        with use_company(self.company):
            return DesignationPermission.objects.create(
                company=self.company, designation=designation,
                permission=self.approve, access_level=level,
                effective_from=start or dt(2024, 1, 1),
            )

    def _department_rule(self, level, department=None, start=None):
        with use_company(self.company):
            return DepartmentPermission.objects.create(
                company=self.company,
                company_department=department or self.department,
                permission=self.approve, access_level=level,
                effective_from=start or dt(2024, 1, 1),
            )

    # --- default deny -------------------------------------------------------

    def test_nothing_is_permitted_by_default(self):
        self.assertFalse(has_permission(self.manager, "leave.approve", NOW))

    def test_enabling_a_feature_does_not_grant_employees_its_actions(self):
        # The company HAS the leave feature enabled (see setUp) yet nobody can
        # approve until a designation rule or override says so.
        self.assertTrue(is_feature_enabled(self.company, self.leave_feature, NOW))
        self.assertFalse(has_permission(self.manager, "leave.approve", NOW))

    def test_unknown_permission_code_is_denied(self):
        self.assertFalse(has_permission(self.manager, "does.not.exist", NOW))

    # --- designation defaults -----------------------------------------------

    def test_designation_allowed_grants_permission(self):
        self._designation_rule(self.manager_title, "allowed")
        self.assertTrue(has_permission(self.manager, "leave.approve", NOW))
        # The developer has a different title and is unaffected.
        self.assertFalse(has_permission(self.developer, "leave.approve", NOW))

    def test_designation_denied_blocks_permission(self):
        self._designation_rule(self.manager_title, "denied")
        self.assertFalse(has_permission(self.manager, "leave.approve", NOW))

    def test_default_access_level_is_not_a_grant(self):
        self._designation_rule(self.manager_title, "default")
        self.assertFalse(has_permission(self.manager, "leave.approve", NOW))

    # --- feature gating -------------------------------------------------------

    def test_disabling_the_feature_removes_the_permission(self):
        self._designation_rule(self.manager_title, "allowed")
        self.assertTrue(has_permission(self.manager, "leave.approve", NOW))
        with use_company(self.company):
            CompanyFeature.objects.create(
                company=self.company, feature=self.leave_feature, effect="disable"
            )
        # An explicit disable beats an existing enable.
        self.assertFalse(has_permission(self.manager, "leave.approve", NOW))

    def test_company_without_the_feature_is_denied(self):
        other = onboard_company(code="B", slug="b", name="Company B")
        with use_company(other):
            branch = Branch.objects.get()
            # Company B has its own department and designation.
            dept = Department.objects.create(
                branch=branch, code="SW", name="Software"
            )
            title = Designation.objects.create(
                department=dept, code="MGR", name="Manager"
            )
        employee = create_employee(
            company=other, first_name="Zed", employee_code="Z1", branch=branch,
            department=dept, designation=title, effective_from=dt(2024, 1, 1),
            pay_basis="monthly", base_rate=Decimal("100"),
        )["employee"]
        with use_company(other):
            DesignationPermission.objects.create(
                company=other, designation=title, permission=self.approve,
                access_level="allowed", effective_from=dt(2024, 1, 1),
            )
        # Rule says allowed, but Company B never enabled the leave feature.
        self.assertFalse(has_permission(employee, "leave.approve", NOW))

    # --- individual overrides --------------------------------------------------

    def test_override_grant_beats_missing_designation_rule(self):
        with use_company(self.company):
            EmployeePermissionOverride.objects.create(
                company=self.company, employee=self.developer,
                permission=self.approve, effect="grant",
                effective_from=dt(2024, 1, 1),
            )
        self.assertTrue(has_permission(self.developer, "leave.approve", NOW))

    def test_override_revoke_beats_designation_allowed(self):
        self._designation_rule(self.manager_title, "allowed")
        with use_company(self.company):
            EmployeePermissionOverride.objects.create(
                company=self.company, employee=self.manager,
                permission=self.approve, effect="revoke",
                effective_from=dt(2024, 1, 1),
            )
        self.assertFalse(has_permission(self.manager, "leave.approve", NOW))

    # --- dated answers -----------------------------------------------------------

    def test_permission_is_answered_as_of_a_date(self):
        # Granted only from 1 May: asking about March must say no.
        self._designation_rule(self.manager_title, "allowed", start=dt(2024, 5, 1))
        self.assertFalse(has_permission(self.manager, "leave.approve", dt(2024, 3, 1)))
        self.assertTrue(has_permission(self.manager, "leave.approve", dt(2024, 6, 1)))

    # --- department ceiling ---------------------------------------------------

    def test_department_denial_blocks_a_designation_grant(self):
        """The old parent-chain rule, moved up a level to the department."""
        self._department_rule("denied")
        with use_company(self.company):
            title_rule = DesignationPermission(
                company=self.company, designation=self.dev_title,
                permission=self.approve, access_level="allowed",
                effective_from=dt(2024, 1, 1),
            )
            with self.assertRaises(ValidationError):
                title_rule.full_clean()

    def test_designation_grant_accepted_when_the_department_allows(self):
        self._department_rule("allowed")
        with use_company(self.company):
            title_rule = DesignationPermission(
                company=self.company, designation=self.dev_title,
                permission=self.approve, access_level="allowed",
                effective_from=dt(2024, 1, 1),
            )
            title_rule.full_clean()  # must not raise

    def test_department_denial_blocks_an_individual_grant(self):
        """A department head cannot hand out what the department is denied."""
        self._department_rule("denied")
        with use_company(self.company):
            override = EmployeePermissionOverride(
                company=self.company, employee=self.developer,
                permission=self.approve, effect="grant",
                effective_from=dt(2024, 1, 1),
            )
            with self.assertRaises(ValidationError):
                override.full_clean()

    def test_department_denial_beats_an_existing_designation_allow(self):
        """Resolution, not just validation: the ceiling wins at read time too."""
        self._designation_rule(self.manager_title, "allowed")
        self.assertTrue(has_permission(self.manager, "leave.approve", NOW))
        self._department_rule("denied")
        self.assertFalse(has_permission(self.manager, "leave.approve", NOW))

    def test_department_allowance_is_the_floor_for_everyone_in_it(self):
        self._department_rule("allowed")
        # Neither employee carries a title rule of their own.
        self.assertTrue(has_permission(self.manager, "leave.approve", NOW))
        self.assertTrue(has_permission(self.developer, "leave.approve", NOW))

    def test_designation_denial_overrides_the_department_floor(self):
        self._department_rule("allowed")
        self._designation_rule(self.dev_title, "denied")
        self.assertTrue(has_permission(self.manager, "leave.approve", NOW))
        self.assertFalse(has_permission(self.developer, "leave.approve", NOW))

    def test_department_rules_do_not_leak_into_another_company(self):
        self._department_rule("allowed")
        other = onboard_company(code="C", slug="c", name="Company C")
        with use_company(other):
            branch = Branch.objects.get()
            dept = Department.objects.create(
                branch=branch, code="SW", name="Software"
            )
            title = Designation.objects.create(
                department=dept, code="MGR", name="Manager"
            )
            CompanyFeature.objects.create(
                company=other, feature=self.leave_feature, effect="enable"
            )
        outsider = create_employee(
            company=other, first_name="Nadia", employee_code="C1", branch=branch,
            department=dept, designation=title, effective_from=dt(2024, 1, 1),
            pay_basis="monthly", base_rate=Decimal("100"),
        )["employee"]
        # Another company's own department: nothing carries over.
        self.assertFalse(has_permission(outsider, "leave.approve", NOW))

    # --- effective permission set -------------------------------------------------

    def test_effective_permissions_lists_only_held_codes(self):
        self._designation_rule(self.manager_title, "allowed")
        self.assertEqual(
            get_effective_permissions(self.manager, NOW), {"leave.approve"}
        )
        self.assertEqual(get_effective_permissions(self.developer, NOW), set())
