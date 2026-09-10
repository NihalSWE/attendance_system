"""Adopting a catalogue department into a branch: services and screens.

The load-bearing tests here are the refusals — a title from another
department, a second adoption of the same department into one branch, and
removing a title employees still hold. Each of those is a data-integrity rule
that a form alone would not enforce.
"""

from datetime import datetime, time, timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import CompanyMembership
from auditlog.models import AuditLog
from common.tenant import use_company
from employees.models import Employee, EmployeeAssignment
from organization import adoption_services as services
from organization.catalogue import ensure_department, ensure_designation
from organization.models import (
    Branch,
    CompanyDepartment,
    CompanyDesignation,
)
from scheduling.models import CompanyAttendanceSettings, Shift
from tenants.models import Company

User = get_user_model()


def dt(year, month, day):
    return datetime(year, month, day, tzinfo=dt_timezone.utc)


class AdoptionTestBase(TestCase):
    def setUp(self):
        self.client = Client()
        # Root catalogue, curated by the platform operator.
        self.software = ensure_department("SW", "Software")
        self.hr = ensure_department("HR", "Human Resources")
        self.developer = ensure_designation(self.software, "DEV", "Developer")
        self.senior = ensure_designation(self.software, "SDV", "Senior Developer")
        self.hr_manager = ensure_designation(self.hr, "HRM", "HR Manager")

        self.company, self.owner, self.branch = self._company("A", "owner-a@example.test")
        self.other_company, self.other_owner, self.other_branch = self._company(
            "B", "owner-b@example.test"
        )

    def _company(self, code, email):
        company = Company.objects.create(
            code=code, slug=code.lower(), name=f"Company {code}"
        )
        user = User.objects.create_user(email=email, password="pw-12345678")
        CompanyMembership.all_objects.create(
            company=company, user=user, role="company_admin", status="active"
        )
        with use_company(company):
            branch = Branch.objects.create(code="HQ", name=f"{code} HQ", is_default=True)
            shift = Shift.objects.create(
                code="GEN", name="General", start_time=time(9, 0),
                end_time=time(18, 0), scheduled_minutes=540,
            )
            CompanyAttendanceSettings.objects.create(
                company_shift=shift, effective_from=dt(2026, 1, 1)
            )
        return company, user, branch


class AdoptionServiceTests(AdoptionTestBase):
    def test_adopting_creates_the_department_and_its_titles(self):
        adoption = services.adopt_department(
            actor=self.owner,
            company_id=self.company.pk,
            values={
                "branch": self.branch,
                "department": self.software,
                "status": "active",
                "designations": [self.developer, self.senior],
            },
        )
        with use_company(self.company):
            self.assertEqual(adoption.designations.count(), 2)
            # The name is read through from the catalogue, never copied.
            self.assertEqual(adoption.name, "Software")

    def test_adoption_is_audited(self):
        adoption = services.adopt_department(
            actor=self.owner,
            company_id=self.company.pk,
            values={"branch": self.branch, "department": self.software,
                    "status": "active", "designations": [self.developer]},
        )
        entry = AuditLog.objects.filter(
            action="company_department.adopted", object_id=str(adoption.pk)
        ).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.after_data["titles"], ["Developer"])

    def test_a_branch_cannot_adopt_the_same_department_twice(self):
        values = {"branch": self.branch, "department": self.software, "status": "active"}
        services.adopt_department(
            actor=self.owner, company_id=self.company.pk, values=dict(values)
        )
        with self.assertRaises(ValidationError) as ctx:
            services.adopt_department(
                actor=self.owner, company_id=self.company.pk, values=dict(values)
            )
        self.assertIn("department", ctx.exception.message_dict)

    def test_a_title_from_another_department_is_refused(self):
        with self.assertRaises(ValidationError) as ctx:
            services.adopt_department(
                actor=self.owner,
                company_id=self.company.pk,
                values={
                    "branch": self.branch,
                    "department": self.software,
                    "status": "active",
                    # HR Manager is filed under Human Resources, not Software.
                    "designations": [self.developer, self.hr_manager],
                },
            )
        self.assertIn("designations", ctx.exception.message_dict)

    def test_titles_and_department_are_written_in_one_transaction(self):
        """A failed title must not leave a department with no titles behind."""
        with self.assertRaises(ValidationError):
            services.adopt_department(
                actor=self.owner,
                company_id=self.company.pk,
                values={"branch": self.branch, "department": self.software,
                        "status": "active", "designations": [self.hr_manager]},
            )
        with use_company(self.company):
            self.assertEqual(CompanyDepartment.objects.count(), 0)

    def test_unsupported_field_is_rejected_not_ignored(self):
        with self.assertRaises(ValidationError):
            services.adopt_department(
                actor=self.owner,
                company_id=self.company.pk,
                values={"branch": self.branch, "department": self.software,
                        "company_id": 999},
            )

    def test_another_companys_member_cannot_adopt_here(self):
        with self.assertRaises(PermissionDenied):
            services.adopt_department(
                actor=self.other_owner,
                company_id=self.company.pk,
                values={"branch": self.branch, "department": self.software},
            )

    # --- editing ----------------------------------------------------------

    def _adopt(self, titles=None):
        return services.adopt_department(
            actor=self.owner,
            company_id=self.company.pk,
            values={"branch": self.branch, "department": self.software,
                    "status": "active", "designations": titles or [self.developer]},
        )

    def test_editing_adds_a_title(self):
        adoption = self._adopt()
        services.update_adoption(
            actor=self.owner, company_id=self.company.pk, adoption_id=adoption.pk,
            values={"status": "active", "designations": [self.developer, self.senior]},
        )
        with use_company(self.company):
            self.assertEqual(adoption.designations.count(), 2)

    def test_unticking_a_title_deactivates_rather_than_deletes(self):
        adoption = self._adopt(titles=[self.developer, self.senior])
        services.update_adoption(
            actor=self.owner, company_id=self.company.pk, adoption_id=adoption.pk,
            values={"status": "active", "designations": [self.developer]},
        )
        with use_company(self.company):
            link = CompanyDesignation.objects.get(
                company_department=adoption, designation=self.senior
            )
            self.assertEqual(link.status, "inactive")

    def test_reticking_a_deactivated_title_reactivates_the_same_row(self):
        adoption = self._adopt(titles=[self.developer, self.senior])
        for titles in ([self.developer], [self.developer, self.senior]):
            services.update_adoption(
                actor=self.owner, company_id=self.company.pk,
                adoption_id=adoption.pk,
                values={"status": "active", "designations": titles},
            )
        with use_company(self.company):
            links = CompanyDesignation.objects.filter(
                company_department=adoption, designation=self.senior
            )
            # Reactivated, not duplicated.
            self.assertEqual(links.count(), 1)
            self.assertEqual(links.first().status, "active")

    def test_a_title_employees_hold_cannot_be_removed(self):
        adoption = self._adopt(titles=[self.developer, self.senior])
        with use_company(self.company):
            link = CompanyDesignation.objects.get(
                company_department=adoption, designation=self.developer
            )
            employee = Employee.objects.create(first_name="Ayesha", last_name="Rahman")
            EmployeeAssignment.objects.create(
                employee=employee, employee_code="E-1", branch=self.branch,
                department=adoption, designation=link, effective_from=dt(2026, 1, 1),
            )

        with self.assertRaises(ValidationError) as ctx:
            services.update_adoption(
                actor=self.owner, company_id=self.company.pk, adoption_id=adoption.pk,
                values={"status": "active", "designations": [self.senior]},
            )
        self.assertIn("designations", ctx.exception.message_dict)
        with use_company(self.company):
            link.refresh_from_db()
            self.assertEqual(link.status, "active")

    def test_a_department_with_employees_cannot_be_deactivated(self):
        adoption = self._adopt()
        with use_company(self.company):
            link = CompanyDesignation.objects.get(company_department=adoption)
            employee = Employee.objects.create(first_name="Ayesha", last_name="Rahman")
            EmployeeAssignment.objects.create(
                employee=employee, employee_code="E-1", branch=self.branch,
                department=adoption, designation=link, effective_from=dt(2026, 1, 1),
            )
        with self.assertRaises(ValidationError):
            services.set_adoption_status(
                actor=self.owner, company_id=self.company.pk,
                adoption_id=adoption.pk, status="inactive",
            )

    def test_branch_and_department_cannot_be_changed_after_adoption(self):
        """Changing either would silently move everyone filed under the row."""
        adoption = self._adopt()
        services.update_adoption(
            actor=self.owner, company_id=self.company.pk, adoption_id=adoption.pk,
            values={"branch": self.other_branch, "department": self.hr,
                    "status": "active", "designations": [self.developer]},
        )
        adoption.refresh_from_db()
        self.assertEqual(adoption.branch_id, self.branch.pk)
        self.assertEqual(adoption.department_id, self.software.pk)


class AdoptionScreenTests(AdoptionTestBase):
    def setUp(self):
        super().setUp()
        self.adoption = services.adopt_department(
            actor=self.owner,
            company_id=self.company.pk,
            values={"branch": self.branch, "department": self.software,
                    "status": "active", "designations": [self.developer]},
        )
        self.client.force_login(self.owner)

    def test_screens_render(self):
        for url in (
            reverse("organization:adoption_list"),
            reverse("organization:adoption_create"),
            reverse("organization:adoption_edit", args=[self.adoption.pk]),
            reverse("organization:adoption_status", args=[self.adoption.pk]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_list_shows_the_catalogue_name_and_its_titles(self):
        response = self.client.get(reverse("organization:adoption_list"))
        self.assertContains(response, "Software")
        self.assertContains(response, "Developer")
        self.assertContains(response, "A HQ")

    def test_list_does_not_leak_another_company(self):
        services.adopt_department(
            actor=self.other_owner,
            company_id=self.other_company.pk,
            values={"branch": self.other_branch, "department": self.hr,
                    "status": "active"},
        )
        response = self.client.get(reverse("organization:adoption_list"))
        self.assertNotContains(response, "B HQ")

    def test_another_companys_adoption_is_refused(self):
        other = services.adopt_department(
            actor=self.other_owner,
            company_id=self.other_company.pk,
            values={"branch": self.other_branch, "department": self.hr,
                    "status": "active"},
        )
        response = self.client.get(
            reverse("organization:adoption_edit", args=[other.pk])
        )
        self.assertIn(response.status_code, (403, 404))

    def test_adopting_through_the_form(self):
        response = self.client.post(
            reverse("organization:adoption_create"),
            {"branch": self.branch.pk, "department": self.hr.pk,
             "status": "active", "designations": [self.hr_manager.pk]},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        with use_company(self.company):
            adoption = CompanyDepartment.objects.get(department=self.hr)
            self.assertEqual(adoption.designations.count(), 1)

    def test_repeat_adoption_is_a_field_error_on_department(self):
        response = self.client.post(
            reverse("organization:adoption_create"),
            {"branch": self.branch.pk, "department": self.software.pk, "status": "active"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("department", response.context["form"].errors)
        with use_company(self.company):
            self.assertEqual(
                CompanyDepartment.objects.filter(department=self.software).count(), 1
            )

    def test_form_does_not_offer_titles_from_another_department(self):
        response = self.client.get(
            reverse("organization:adoption_create") + f"?department={self.software.pk}"
        )
        offered = set(response.context["form"].fields["designations"].queryset)
        self.assertEqual(offered, {self.developer, self.senior})
        self.assertNotIn(self.hr_manager, offered)

    def test_titles_endpoint_filters_by_department(self):
        response = self.client.get(
            reverse("organization:department_titles"),
            {"department": self.hr.pk},
        )
        self.assertEqual(response.status_code, 200)
        names = {row["text"] for row in response.json()["results"]}
        self.assertEqual(names, {"HR Manager"})

    def test_anonymous_is_redirected(self):
        self.client.logout()
        self.assertEqual(
            self.client.get(reverse("organization:adoption_list")).status_code, 302
        )
