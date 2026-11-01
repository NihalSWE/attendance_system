"""Generate salary in the background, with its progress on the page (2026-11-01).

A month for thousands of people takes minutes; inside the click it ran past the
server's time limit with nothing on screen. The click now starts a
``GenerateJob``; tests run it inline (PAYROLL_GENERATE_IN_BACKGROUND is off).
"""

import datetime
from unittest import mock

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from common.tenant import use_company
from payroll import generate_jobs
from payroll.models import GenerateJob, PayrollRun
from payroll.tests_branch_salary import BranchSalaryBase

AUGUST = {"month": 8, "year": 2026}


class GenerateJobTests(BranchSalaryBase):
    def running(self, **fields):
        job = GenerateJob(requested_by=self.admin, year=2026, month=8, heartbeat_at=timezone.now(),
                          **fields)
        job.company_id = self.company.pk
        job.save()
        return job

    def test_the_button_generates_and_says_what_it_did(self):
        self.client.force_login(self.admin)
        page = self.client.post(reverse("payroll:payroll_generate"), AUGUST, follow=True)
        self.assertContains(page, "Salary generated for 3 employee(s)")
        job = GenerateJob.all_objects.get()
        self.assertEqual(job.status, GenerateJob.Status.DONE)
        self.assertTrue(job.announced)
        # Its progress was written as it went: the payslips, all of them.
        self.assertEqual((job.stage, job.done_count, job.total), ("salary", 3, 3))
        with use_company(self.company):
            self.assertEqual(PayrollRun.objects.get().records.count(), 3)
        # Said once, not again on the next visit.
        self.assertNotContains(self.client.get(reverse("payroll:payroll_home"), AUGUST),
                               "Salary generated for 3")

    def test_progress_is_told_for_attendance_then_payslips(self):
        seen = []
        self.generate_with(lambda stage, done, total: seen.append((stage, done, total)))
        stages = [stage for stage, _done, _total in seen]
        self.assertEqual(stages[0], "attendance")
        self.assertEqual(stages[-1], "salary")
        self.assertNotIn("attendance", stages[stages.index("salary"):])
        self.assertEqual(seen[-1][1], seen[-1][2])

    def generate_with(self, progress):
        from payroll.services import generate_payroll

        return generate_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8,
                                progress=progress)

    def test_a_refusal_is_shown_and_nothing_is_left_running(self):
        self.client.force_login(self.admin)
        page = self.client.post(reverse("payroll:payroll_generate"),
                                {"month": 12, "year": 2099}, follow=True)
        self.assertContains(page, "That month has not started yet.")
        self.assertEqual(GenerateJob.all_objects.get().status, GenerateJob.Status.FAILED)

    def test_while_it_runs_the_page_shows_its_progress_instead_of_the_buttons(self):
        self.generate()
        job = self.running(stage="attendance", done_count=640, total=2000)
        self.client.force_login(self.admin)
        page = self.client.get(reverse("payroll:payroll_home"), AUGUST)
        self.assertContains(page, "Generating August 2026")
        self.assertContains(page, "640 of 2000 people")
        self.assertContains(page, f"{reverse('payroll:generate_status')}?job={job.pk}")
        self.assertNotContains(page, "Regenerate")
        self.assertNotContains(page, "Submit August for approval")
        state = self.client.get(reverse("payroll:generate_status"), {"job": job.pk}).json()
        self.assertEqual((state["running"], state["stage"], state["done"], state["total"], state["percent"]),
                         (True, "attendance", 640, 2000, 19))

    def test_when_it_has_finished_the_status_says_so_and_the_page_tells_the_result(self):
        job = self.running(status=GenerateJob.Status.DONE, message="Salary generated for 3 employee(s).")
        self.client.force_login(self.admin)
        state = self.client.get(reverse("payroll:generate_status"), {"job": job.pk}).json()
        self.assertFalse(state["running"])
        self.assertContains(self.client.get(reverse("payroll:payroll_home"), AUGUST),
                            "Salary generated for 3 employee(s).")

    def test_a_second_click_does_not_start_another(self):
        job = self.running()
        self.client.force_login(self.admin)
        self.client.post(reverse("payroll:payroll_generate"), AUGUST)
        self.assertEqual(list(GenerateJob.all_objects.values_list("pk", flat=True)), [job.pk])

    def test_it_cannot_be_submitted_while_it_is_being_generated(self):
        self.generate()
        self.running()
        self.client.force_login(self.admin)
        page = self.client.post(reverse("payroll:payroll_submit"), AUGUST, follow=True)
        self.assertContains(page, "It is being generated right now.")
        with use_company(self.company):
            self.assertEqual(PayrollRun.objects.get().status, PayrollRun.Status.DRAFT)

    def test_another_company_cannot_read_it(self):
        job = self.running()
        job.company_id = self.other_company().pk
        GenerateJob.all_objects.filter(pk=job.pk).update(company_id=job.company_id)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("payroll:generate_status"), {"job": job.pk}).status_code, 404)

    def other_company(self):
        from tenants.models import Company

        return Company.objects.create(name="Other Ltd", slug="other-ltd")

    @override_settings(PAYROLL_GENERATE_IN_BACKGROUND=True)
    def test_in_the_background_the_click_returns_at_once(self):
        self.client.force_login(self.admin)
        with mock.patch.object(generate_jobs.threading, "Thread") as thread, \
                self.captureOnCommitCallbacks(execute=True):
            page = self.client.post(reverse("payroll:payroll_generate"), AUGUST, follow=True)
        thread.assert_called_once()
        self.assertContains(page, "Generating August 2026")
        self.assertEqual(GenerateJob.all_objects.get().status, GenerateJob.Status.RUNNING)

    @override_settings(PAYROLL_GENERATE_IN_BACKGROUND=True)
    def test_a_job_whose_thread_stopped_is_started_again(self):
        job = self.running(stage="salary", done_count=2, total=3)
        GenerateJob.all_objects.filter(pk=job.pk).update(
            heartbeat_at=timezone.now() - datetime.timedelta(minutes=10))
        job.refresh_from_db()
        with mock.patch.object(generate_jobs.threading, "Thread") as thread, \
                self.captureOnCommitCallbacks(execute=True):
            self.assertTrue(generate_jobs.resume_if_stalled(job))
            # Fresh now: not started a second time.
            job.refresh_from_db()
            self.assertFalse(generate_jobs.resume_if_stalled(job))
        thread.assert_called_once()
        self.assertEqual((job.stage, job.done_count), ("attendance", 0))
