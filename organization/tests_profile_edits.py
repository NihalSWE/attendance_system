"""Every Edit employee option on the profile, in modals (Ajay, 2026-09-27).

Each modal is submitted through the profile the way a person would: saved, it
goes back to the profile; refused, the profile comes back with that modal open
and its reasons. The Edit employee page stays as it was.
"""

import datetime
from decimal import Decimal

from django.urls import reverse

from accounts.models import CompanyMembership
from auditlog.models import AuditLog
from common.tenant import use_company
from employees.models import Employee, EmployeeAssignment, EmployeeCompensation
from leaves.tests_branch_access import TwoBranchCase
from payroll import component_services
from scheduling.models import EmployeeShiftAssignment

TODAY = datetime.date.today()


class ProfileEditCase(TwoBranchCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        self.profile_url = reverse("organization:employee_detail", args=[self.employee.pk])
        self.save_url = reverse("organization:employee_profile_edit", args=[self.employee.pk])

    def save(self, section, **data):
        return self.client.post(self.save_url, {"section": section, **data})

    def assertBackOnProfile(self, response, tab="profile"):
        self.assertEqual(response.status_code, 302, getattr(response, "context", None)
                         and [f.errors for f in response.context.get("edit", {}).values()
                              if hasattr(f, "errors")])
        self.assertEqual(response["Location"], f"{self.profile_url}#{tab}")

    def assertModalOpen(self, response, dialog):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'data-open-on-load="{dialog}"')

    def placed(self, employee=None):
        with use_company(self.company):
            return EmployeeAssignment.objects.filter(
                employee=employee or self.employee, effective_to__isnull=True).get()


class ModalsOnThePageTests(ProfileEditCase):
    def test_the_owner_gets_every_edit_modal(self):
        page = self.client.get(self.profile_url)
        for dialog in ("details-dialog", "placement-dialog", "salary-dialog", "shift-dialog",
                       "login_give-dialog", "line_manager-dialog", "personal-dialog",
                       "photo-dialog"):
            with self.subTest(dialog=dialog):
                self.assertContains(page, f'id="{dialog}"')
                self.assertContains(page, f'data-open-dialog="{dialog}"')

    def test_field_ids_do_not_clash_between_modals(self):
        page = self.client.get(self.profile_url).content.decode()
        for prefix in ("details_", "salary_", "shift_form_", "give_login_", "line_manager_"):
            self.assertIn(f'id="{prefix}', page)
        self.assertIn('id="id_branch"', page)            # the dependent selects find it

    def test_view_only_gets_no_modals_and_cannot_post(self):
        self.grant("employees.view", self.branch)
        self.client.force_login(self.clerk_user)
        page = self.client.get(self.profile_url)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'id="details-dialog"')
        self.assertEqual(self.save("details", first_name="X").status_code, 403)

    def test_hr_gets_the_edits_but_no_pay(self):
        self.client.force_login(self.hr)
        page = self.client.get(self.profile_url)
        self.assertContains(page, 'id="details-dialog"')
        self.assertNotContains(page, 'id="salary-dialog"')
        self.assertNotContains(page, 'id="salary"')
        self.assertEqual(self.save("salary", pay_basis="monthly", base_rate="1",
                                   salary_from=TODAY.isoformat()).status_code, 403)

    def test_a_branch_manager_elsewhere_is_refused(self):
        self.client.force_login(self.manager)
        far = reverse("organization:employee_profile_edit", args=[self.far.pk])
        self.assertEqual(self.client.post(far, {"section": "details"}).status_code, 403)


class DetailsAndPlacementTests(ProfileEditCase):
    def test_details_are_saved(self):
        response = self.save("details", first_name="Rahim", last_name="Uddin",
                             work_email="rahim@liv.test", phone="", joining_date="2026-01-01")
        self.assertBackOnProfile(response)
        self.employee.refresh_from_db()
        self.assertEqual((self.employee.last_name, self.employee.work_email),
                         ("Uddin", "rahim@liv.test"))

    def test_refused_details_reopen_the_modal(self):
        response = self.save("details", first_name="", work_email="not an email")
        self.assertModalOpen(response, "details-dialog")
        self.assertContains(response, "Enter a valid email address.")

    def test_a_placement_change_is_saved(self):
        now = self.placed()
        response = self.save("placement", branch=now.branch_id, department=now.department_id,
                             designation=now.designation_id, employee_code="E-77",
                             placement_from=TODAY.isoformat(), placement_reason="New ID")
        self.assertBackOnProfile(response)
        self.assertEqual(self.placed().employee_code, "E-77")

    def test_a_refused_placement_reopens_it(self):
        response = self.save("placement", branch="", employee_code="")
        self.assertModalOpen(response, "placement-dialog")


class LineManagerTests(ProfileEditCase):
    def url(self, employee=None):
        return reverse("organization:employee_line_manager", args=[(employee or self.employee).pk])

    def test_set_and_cleared(self):
        response = self.client.post(self.url(), {"manager": self.clerk.pk})
        self.assertRedirects(response, f"{self.profile_url}#profile",
                             fetch_redirect_response=False)
        self.assertEqual(self.placed().manager_id, self.clerk.pk)
        self.assertContains(self.client.get(self.profile_url), "Clerk")
        entry = AuditLog.objects.get(action="employee.line_manager_changed")
        self.assertEqual(entry.after_data["line_manager"], self.clerk.full_name)
        self.client.post(self.url(), {"manager": ""})
        self.assertIsNone(self.placed().manager_id)

    def test_not_themselves_and_not_someone_out_of_reach(self):
        response = self.client.post(self.url(), {"manager": self.employee.pk})
        self.assertModalOpen(response, "line_manager-dialog")
        self.client.force_login(self.manager)
        response = self.client.post(self.url(), {"manager": self.far.pk})
        self.assertModalOpen(response, "line_manager-dialog")
        self.assertIsNone(self.placed().manager_id)


class SalaryTests(ProfileEditCase):
    def test_salary_is_changed(self):
        response = self.save("salary", pay_basis="monthly", base_rate="35000",
                             salary_from=TODAY.isoformat(), salary_reason="Raise")
        self.assertBackOnProfile(response)
        with use_company(self.company):
            self.assertEqual(EmployeeCompensation.objects.filter(
                employee=self.employee, effective_to__isnull=True).get().base_rate,
                Decimal("35000"))

    def test_a_refused_salary_reopens_it(self):
        self.assertModalOpen(self.save("salary", pay_basis="monthly", base_rate="-1"),
                             "salary-dialog")

    def test_an_allowance_is_given_and_ended(self):
        component = component_services.create_component(
            actor=self.admin, company_id=self.company.pk,
            values={"code": "HR", "name": "House rent", "kind": "earning", "method": "fixed",
                    "default_amount": Decimal("4000"), "default_percent": None,
                    "description": ""})
        page = self.client.get(self.profile_url)
        self.assertContains(page, 'id="component_give-dialog"')
        response = self.save("component_give", component=component.pk, amount="4000",
                             percent="", effective_from="2026-08-01", reason="")
        self.assertBackOnProfile(response)
        with use_company(self.company):
            row = next(iter(component_services.employee_rows(self.company.pk, self.employee)))
        page = self.client.get(self.profile_url)
        self.assertContains(page, f'id="component_end-{row.pk}-dialog"')
        refused = self.save("component_end", row=row.pk, last_day="")
        self.assertModalOpen(refused, f"component_end-{row.pk}-dialog")
        self.assertBackOnProfile(self.save("component_end", row=row.pk, last_day="2026-08-31"))


class ShiftTests(ProfileEditCase):
    def test_their_own_shift_is_given_and_ended(self):
        response = self.save("shift", shift=self.shift.pk, first_day=TODAY.isoformat(),
                             last_day="", reason="Nights")
        self.assertBackOnProfile(response, tab="roster")
        with use_company(self.company):
            own = EmployeeShiftAssignment.objects.get(employee=self.employee)
        page = self.client.get(self.profile_url)
        self.assertContains(page, 'id="shift_end-dialog"')
        response = self.save("shift_end", assignment=own.pk,
                             last_day=(TODAY + datetime.timedelta(days=3)).isoformat())
        self.assertBackOnProfile(response, tab="roster")

    def test_a_refused_shift_reopens_it(self):
        self.assertModalOpen(self.save("shift", shift="", first_day=""), "shift-dialog")


class FormsOpenOnWhatTheyHaveTests(ProfileEditCase):
    """Nihal, 2026-09-29: an edit form shows the data they already have; only
    what is missing is blank."""

    def page(self):
        return self.client.get(self.profile_url).context

    def test_their_own_shift_is_chosen_in_set_shift(self):
        self.assertIsNone(self.page()["edit"]["shift_form"].initial.get("shift"))
        self.save("shift", shift=self.shift.pk, first_day=TODAY.isoformat(), last_day="",
                  reason="Nights")
        self.assertEqual(self.page()["edit"]["shift_form"].initial["shift"], self.shift.pk)

    def test_their_leave_policy_is_chosen(self):
        from leaves import policies, policy_admin

        staff = policy_admin.save_policy(actor=self.admin, company_id=self.company.pk, values={
            "code": "STAFF", "name": "Staff", "description": "", "is_default": False})
        self.assertIsNone(self.page()["policy_form"].initial.get("policy"))
        policies.assign_policy(actor=self.admin, company_id=self.company.pk,
                               employee=self.employee, policy=staff,
                               effective_from=datetime.date(2026, 1, 1))
        self.assertEqual(self.page()["policy_form"].initial["policy"], staff.pk)

    def test_personal_choices_saved_in_another_spelling_are_shown_and_kept(self):
        with use_company(self.company):
            Employee.objects.filter(pk=self.employee.pk).update(
                gender="Male", blood_group="a+", marital_status="Engaged")
        form = self.page()["personal_form"]
        self.assertEqual((form.initial["gender"], form.initial["blood_group"],
                          form.initial["marital_status"]), ("male", "A+", "Engaged"))
        self.assertIn(("Engaged", "Engaged"), form.fields["marital_status"].choices)
        # Saved without touching it, the value that is no choice stays.
        response = self.client.post(
            reverse("organization:employee_personal", args=[self.employee.pk]),
            {"gender": "male", "blood_group": "A+", "marital_status": "Engaged"})
        self.assertEqual(response.status_code, 302)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.marital_status, "Engaged")

    def test_a_line_manager_outside_the_list_is_shown_and_kept(self):
        # Karim is at Chittagong, which the branch manager does not see.
        placement = self.placed()
        with use_company(self.company):
            EmployeeAssignment.objects.filter(pk=placement.pk).update(manager=self.far)
        self.client.force_login(self.manager)
        form = self.page()["line_manager_form"]
        self.assertEqual(form.initial["manager"], self.far.pk)
        self.assertIn(self.far, form.fields["manager"].queryset)
        response = self.client.post(
            reverse("organization:employee_line_manager", args=[self.employee.pk]),
            {"manager": self.far.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.placed().manager_id, self.far.pk)
        # Choosing someone else out of reach is still refused.
        with use_company(self.company):
            EmployeeAssignment.objects.filter(pk=placement.pk).update(manager=None)
        response = self.client.post(
            reverse("organization:employee_line_manager", args=[self.employee.pk]),
            {"manager": self.far.pk})
        self.assertModalOpen(response, "line_manager-dialog")


class LoginTests(ProfileEditCase):
    PASSWORD = "Str0ng-pass-2026"

    def give(self):
        return self.save("login_give", login_email="rahim@liv.test",
                         login_password=self.PASSWORD, login_password_confirm=self.PASSWORD,
                         login_role="employee")

    def test_give_change_password_disable_enable(self):
        self.assertBackOnProfile(self.give())
        self.employee.refresh_from_db()
        self.assertIsNotNone(self.employee.user_id)
        page = self.client.get(self.profile_url)
        for dialog in ("login_password-dialog", "login_role-dialog"):
            self.assertContains(page, f'id="{dialog}"')
        new = "An0ther-pass-2026"
        self.assertBackOnProfile(self.save("login_password", login_new_password=new,
                                           login_new_password_confirm=new))
        self.employee.user.refresh_from_db()
        self.assertTrue(self.employee.user.check_password(new))
        self.assertBackOnProfile(self.save("login_disable"))
        membership = CompanyMembership.all_objects.get(user=self.employee.user,
                                                       company=self.company)
        self.assertNotEqual(membership.status, "active")
        self.assertBackOnProfile(self.save("login_enable"))

    def test_mismatched_passwords_reopen_the_modal(self):
        response = self.save("login_give", login_email="rahim@liv.test",
                             login_password=self.PASSWORD, login_password_confirm="other",
                             login_role="employee")
        self.assertModalOpen(response, "login_give-dialog")
        self.employee.refresh_from_db()
        self.assertIsNone(self.employee.user_id)


class EditPageUnchangedTests(ProfileEditCase):
    def test_the_edit_page_still_saves_and_comes_back_to_itself(self):
        url = reverse("organization:employee_edit", args=[self.employee.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {"section": "details", "first_name": "Rahim",
                                          "last_name": "K", "work_email": "", "phone": "",
                                          "joining_date": "2026-01-01"})
        self.assertRedirects(response, url, fetch_redirect_response=False)
        self.assertTrue(Employee.all_objects.filter(pk=self.employee.pk, last_name="K").exists())
