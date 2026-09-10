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


class CopyAdoptionsTests(AdoptionTestBase):
    """Copying one branch's departments into another.

    A department adoption is per-branch by design — each carries its own head,
    status, dates and device rules — so a new branch legitimately starts empty.
    These cover the convenience over that, and what it deliberately does not do.
    """

    def setUp(self):
        super().setUp()
        with use_company(self.company):
            self.second_branch = Branch.objects.create(code="BR2", name="Second Branch")
            self.head = Employee.objects.create(first_name="Ayesha", last_name="Rahman")
        self.source = services.adopt_department(
            actor=self.owner, company_id=self.company.pk,
            values={"branch": self.branch, "department": self.software,
                    "status": "active", "head": self.head,
                    "designations": [self.developer, self.senior]},
        )

    def _copy(self, source=None, target=None):
        return services.copy_adoptions_between_branches(
            actor=self.owner, company_id=self.company.pk,
            source_branch=source or self.branch,
            target_branch=target or self.second_branch,
        )

    def test_copy_creates_the_department_and_its_titles(self):
        created, skipped = self._copy()
        self.assertEqual(len(created), 1)
        self.assertEqual(skipped, [])
        with use_company(self.company):
            copied = CompanyDepartment.objects.get(branch=self.second_branch)
            self.assertEqual(copied.department_id, self.software.pk)
            self.assertEqual(
                sorted(l.designation.name for l in copied.designations.all()),
                ["Developer", "Senior Developer"],
            )

    def test_the_copy_is_a_separate_row_not_a_shared_one(self):
        """Each branch owns its adoption; editing one must not touch the other."""
        self._copy()
        with use_company(self.company):
            copied = CompanyDepartment.objects.get(branch=self.second_branch)
            self.assertNotEqual(copied.pk, self.source.pk)
            services.set_adoption_status(
                actor=self.owner, company_id=self.company.pk,
                adoption_id=copied.pk, status="inactive",
            )
            self.source.refresh_from_db()
            self.assertEqual(self.source.status, "active")

    def test_the_head_is_not_copied(self):
        """A head administers people at one branch; copying would appoint them
        over a branch they may not work at."""
        self._copy()
        with use_company(self.company):
            copied = CompanyDepartment.objects.get(branch=self.second_branch)
        self.assertIsNone(copied.head_id)
        self.assertEqual(self.source.head_id, self.head.pk)

    def test_inactive_departments_are_not_copied(self):
        services.set_adoption_status(
            actor=self.owner, company_id=self.company.pk,
            adoption_id=self.source.pk, status="inactive",
        )
        created, skipped = self._copy()
        self.assertEqual(created, [])
        with use_company(self.company):
            self.assertFalse(
                CompanyDepartment.objects.filter(branch=self.second_branch).exists()
            )

    def test_inactive_job_titles_are_not_copied(self):
        services.update_adoption(
            actor=self.owner, company_id=self.company.pk, adoption_id=self.source.pk,
            values={"status": "active", "designations": [self.developer]},
        )
        self._copy()
        with use_company(self.company):
            copied = CompanyDepartment.objects.get(branch=self.second_branch)
            self.assertEqual(
                [l.designation.name for l in copied.designations.all()], ["Developer"]
            )

    def test_running_it_twice_skips_what_is_already_there(self):
        self._copy()
        created, skipped = self._copy()
        self.assertEqual(created, [])
        self.assertEqual(skipped, ["Software"])
        with use_company(self.company):
            self.assertEqual(
                CompanyDepartment.objects.filter(branch=self.second_branch).count(), 1
            )

    def test_copying_a_branch_onto_itself_is_refused(self):
        with self.assertRaises(ValidationError):
            self._copy(target=self.branch)

    def test_copy_is_audited(self):
        created, _ = self._copy()
        self.assertTrue(
            AuditLog.objects.filter(
                action="company_department.copied", object_id=str(created[0].pk)
            ).exists()
        )

    def test_screen_copies_through_the_form(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("organization:adoption_copy"),
            {"source_branch": self.branch.pk, "target_branch": self.second_branch.pk},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        with use_company(self.company):
            self.assertTrue(
                CompanyDepartment.objects.filter(branch=self.second_branch).exists()
            )

    def test_screen_rejects_the_same_branch_twice(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("organization:adoption_copy"),
            {"source_branch": self.branch.pk, "target_branch": self.branch.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("target_branch", response.context["form"].errors)


class NewBranchProvisioningTests(AdoptionTestBase):
    """A new branch offers the same departments as the rest of the company.

    The schema keeps one adoption row per branch, because each carries its own
    head, status, dates and device rules. "Same everywhere" is therefore
    achieved by provisioning the set into the new branch, not by sharing a row.
    """

    def setUp(self):
        super().setUp()
        self.adoption = services.adopt_department(
            actor=self.owner, company_id=self.company.pk,
            values={"branch": self.branch, "department": self.software,
                    "status": "active", "designations": [self.developer, self.senior]},
        )
        self.client.force_login(self.owner)

    def _create_branch(self, code="BR2", name="Second Branch"):
        return self.client.post(
            reverse("organization:branch_create"),
            {"code": code, "name": name, "timezone": "Asia/Dhaka", "status": "active"},
            follow=True,
        )

    def test_a_new_branch_inherits_the_departments_and_titles(self):
        response = self._create_branch()
        self.assertEqual(response.status_code, 200)
        with use_company(self.company):
            new_branch = Branch.objects.get(code="BR2")
            adoptions = CompanyDepartment.objects.filter(branch=new_branch)
            self.assertEqual(adoptions.count(), 1)
            self.assertEqual(
                sorted(l.designation.name for l in adoptions.first().designations.all()),
                ["Developer", "Senior Developer"],
            )

    def test_the_inherited_rows_belong_to_the_new_branch(self):
        """Not shared: deactivating one branch's copy leaves the other alone."""
        self._create_branch()
        with use_company(self.company):
            new_branch = Branch.objects.get(code="BR2")
            copied = CompanyDepartment.objects.get(branch=new_branch)
            services.set_adoption_status(
                actor=self.owner, company_id=self.company.pk,
                adoption_id=copied.pk, status="inactive",
            )
            self.adoption.refresh_from_db()
        self.assertEqual(self.adoption.status, "active")

    def test_the_first_branch_of_a_company_has_nothing_to_inherit(self):
        """Onboarding creates the first branch; there is no source yet."""
        company = Company.objects.create(code="C", slug="c", name="Company C")
        owner = User.objects.create_user(email="owner-c@example.test", password="pw-12345678")
        CompanyMembership.all_objects.create(
            company=company, user=owner, role="company_admin", status="active"
        )
        with use_company(company):
            first = Branch.objects.create(code="HQ", name="C HQ", is_default=True)
        created, skipped = services.provision_new_branch(
            actor=owner, company_id=company.pk, branch=first
        )
        self.assertEqual(created, [])
        self.assertEqual(skipped, [])

    def test_inactive_departments_are_not_inherited(self):
        services.set_adoption_status(
            actor=self.owner, company_id=self.company.pk,
            adoption_id=self.adoption.pk, status="inactive",
        )
        self._create_branch()
        with use_company(self.company):
            new_branch = Branch.objects.get(code="BR2")
            self.assertFalse(
                CompanyDepartment.objects.filter(branch=new_branch).exists()
            )

    def test_a_third_branch_inherits_too(self):
        self._create_branch()
        self._create_branch(code="BR3", name="Third Branch")
        with use_company(self.company):
            for code in ("BR2", "BR3"):
                branch = Branch.objects.get(code=code)
                self.assertEqual(
                    CompanyDepartment.objects.filter(branch=branch).count(), 1, code
                )
