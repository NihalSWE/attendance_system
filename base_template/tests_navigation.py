"""Sidebar destinations, section anchors and existing access boundaries (A14)."""

from types import SimpleNamespace
from urllib.parse import urlsplit

from django.test import SimpleTestCase
from django.urls import resolve, reverse

from accounts.models import CompanyMembership, User
from base_template.navigation import company_menus
from common.tenant import use_company
from scheduling.tests_screens import CalendarBase


class NavigationSelectionTests(SimpleTestCase):
    def test_detail_and_edit_pages_select_their_own_menu(self):
        cases = (
            ("organization:employee_edit", "employees", "All employees"),
            ("attendance:attendance_day", "attendance", "Calendar"),
            ("payroll:penalty_rule_change", "salary", "Penalty rules"),
            ("payroll:overtime_decide", "salary", "Overtime"),
            ("scheduling:weekly_off_start", "shifts", "Weekly off days"),
            ("devices:device_users", "devices", "All devices"),
        )
        for view, group, label in cases:
            with self.subTest(view=view):
                request = SimpleNamespace(resolver_match=SimpleNamespace(view_name=view))
                menus = company_menus(request, can_manage=True, can_manage_devices=True)
                self.assertEqual([m["key"] for m in menus if m["active"]], [group])
                self.assertEqual([link["label"] for m in menus for link in m["links"] if link["active"]], [label])


class CompanyNavigationTests(CalendarBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def test_every_destination_loads_and_every_fragment_exists(self):
        home = self.client.get(reverse("dashboard"))
        menus = home.context["company_menus"]
        self.assertEqual([m["label"] for m in menus],
                         ["Employees", "Attendance", "Leave", "Salary", "Shifts", "Organisation", "Devices"])
        responses = {}
        for menu in menus:
            for link in menu["links"]:
                with self.subTest(link=link["label"]):
                    destination = urlsplit(link["url"])
                    resolve(destination.path)
                    if destination.path not in responses:
                        responses[destination.path] = self.client.get(destination.path)
                    response = responses[destination.path]
                    self.assertEqual(response.status_code, 200)
                    if destination.fragment:
                        self.assertContains(response, f'id="{destination.fragment}"', count=1)

    def test_existing_page_actions_are_preserved_outside_sidebar(self):
        checks = (
            ("payroll:payroll_home", "payroll:salary_settings"),
            ("scheduling:schedule_overview", "scheduling:attendance_settings_edit"),
            ("scheduling:schedule_overview", "scheduling:weekly_off_create"),
            ("scheduling:schedule_overview", "scheduling:department_shift_set"),
            ("leaves:leave_list", "leaves:leave_type_list"),
        )
        for page, action in checks:
            with self.subTest(page=page, action=action):
                content = self.client.get(reverse(page)).content.decode().split('</aside>', 1)[1]
                self.assertIn(f'href="{reverse(action)}"', content)

    def test_hr_does_not_get_admin_settings_or_device_links(self):
        self.client.force_login(self.hr)
        page = self.client.get(reverse("scheduling:schedule_overview"))
        sidebar = page.content.decode().split('</aside>', 1)[0]
        self.assertNotIn('data-menu="devices"', sidebar)
        for view in ("payroll:salary_settings", "organization:employee_create",
                     "scheduling:attendance_settings_edit"):
            self.assertNotIn(f'href="{reverse(view)}', sidebar)
            self.assertEqual(self.client.get(reverse(view)).status_code, 403)
        # HR records leave (A10a), so that link stays in HR's menu.
        self.assertIn(f'href="{reverse("leaves:leave_record")}"', sidebar)
        self.assertIn('data-menu="attendance"', sidebar)

    def test_branch_scoped_admin_has_no_device_menu(self):
        membership = CompanyMembership.all_objects.get(user=self.admin, company=self.company)
        with use_company(self.company):
            membership.allowed_branches.add(self.hq)
        page = self.client.get(reverse("scheduling:schedule_overview"))
        self.assertNotContains(page, 'data-menu="devices"')
        self.assertEqual(self.client.get(reverse("devices:device_list")).status_code, 403)

    def test_company_selection_uses_current_membership_permissions(self):
        # The user administers one company and only reads another.
        CompanyMembership.all_objects.create(
            user=self.admin, company=self.other, role="hr", status="active",
        )
        self.client.post(reverse("switch_company"), {"company_id": self.other.pk})
        page = self.client.get(reverse("scheduling:schedule_overview"))
        self.assertEqual(page.context["active_company"], self.other)
        self.assertNotContains(page, 'data-menu="devices"')
        self.assertFalse(any(link["manage"] for m in page.context["company_menus"] for link in m["links"]))

    def test_employee_and_manager_keep_their_own_navigation(self):
        membership = CompanyMembership.all_objects.get(user=self.hr, company=self.company)
        self.client.force_login(self.hr)
        for role in ("employee", "manager"):
            with self.subTest(role=role):
                membership.role = role
                membership.save(update_fields=["role"])
                page = self.client.get(reverse("me:home"))
                self.assertContains(page, 'id="self-service-sidebar"')
                self.assertContains(page, 'aria-controls="self-service-sidebar"')
                self.assertNotContains(page, 'data-menu="salary"')

    def test_platform_keeps_separate_navigation(self):
        root = User.objects.create_superuser(email="root@navigation.test", password="test-only")
        self.client.force_login(root)
        page = self.client.get(reverse("platform:company_list"))
        self.assertContains(page, 'id="platform-sidebar"')
        self.assertContains(page, 'aria-controls="platform-sidebar"')
        self.assertNotContains(page, 'data-menu="salary"')
