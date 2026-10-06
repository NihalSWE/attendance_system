"""dump_database and load_database (2026-10-06): a copy of the whole database,
every company's rows, loaded on a developer's PC in place of its own."""

import datetime
import os
import shutil
import tempfile
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from accounts.models import CompanyMembership, User
from common.tenant import use_company
from employees.models import Employee
from employees.services import create_employee
from organization.catalogue import adopt_department, adopt_designation
from organization.models import Branch
from tenants.models import Company
from tenants.services import onboard_company

UTC = datetime.timezone.utc


class DatabaseCopyTests(TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.companies = []
        for code in ("AAA", "BBB"):
            company = onboard_company(code=code, slug=code.lower(), name=f"{code} Ltd")
            with use_company(company):
                branch = Branch.objects.get(is_default=True)
                department = adopt_department(branch, "OPS", "Operations")
                designation = adopt_designation(department, "OPR", "Operator")
            create_employee(company=company, first_name=f"Rahim {code}", employee_code="1",
                            branch=branch, department=department, designation=designation,
                            effective_from=datetime.datetime(2026, 1, 1, tzinfo=UTC),
                            pay_basis="monthly", base_rate=Decimal("20000"))
            user = User.objects.create_user(email=f"manager@{code.lower()}.test", password="pw")
            membership = CompanyMembership.all_objects.create(company=company, user=user,
                                                              role="manager", status="active")
            with use_company(company):
                membership.allowed_branches.set([branch])
            self.companies.append(company)

    def settle_checks(self):
        """A test runs in one open transaction, so the rows it just made still
        have their integrity checks pending - and PostgreSQL will not empty a
        table in that state. Run them now. (A real load starts afresh.)"""
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            # Back to deferred, as Django's foreign keys are: loading inserts a
            # row before the row it points to, and checks them all at the end.
            cursor.execute("SET CONSTRAINTS ALL DEFERRED")

    def dump(self, name="copy.json", *extra):
        path = os.path.join(self.folder, name)
        call_command("dump_database", "--output", path, *extra, stdout=StringIO())
        return path

    def test_every_companys_rows_are_copied(self):
        import json

        rows = json.load(open(self.dump(), encoding="utf-8"))
        names = {row["fields"]["first_name"] for row in rows
                 if row["model"] == "employees.employee"}
        self.assertEqual(names, {"Rahim AAA", "Rahim BBB"})
        links = [row["fields"]["allowed_branches"] for row in rows
                 if row["model"] == "accounts.companymembership"
                 and row["fields"]["allowed_branches"]]
        self.assertEqual(len(links), 2)
        models = {row["model"] for row in rows}
        self.assertFalse(models & {"auth.permission", "sessions.session", "api.apisession"})

    @override_settings(DEBUG=True)
    def test_a_copy_replaces_the_local_data(self):
        for compressed in (False, True):
            with self.subTest(compressed=compressed):
                path = self.dump("copy.json.gz" if compressed else "copy.json")
                stray = onboard_company(code="ZZZ", slug="zzz", name="Only on this PC")
                self.settle_checks()
                out = StringIO()
                call_command("load_database", path, "--noinput", stdout=out)
                self.assertFalse(Company.objects.filter(pk=stray.pk).exists())
                self.assertEqual(set(Company.objects.values_list("name", flat=True)),
                                 {"AAA Ltd", "BBB Ltd"})
                self.assertEqual(Employee.all_objects.count(), 2)
                membership = CompanyMembership.all_objects.get(user__email="manager@aaa.test")
                with use_company(membership.company_id):
                    self.assertEqual(membership.allowed_branches.count(), 1)
                self.assertIn("Companies: 2", out.getvalue())

    def test_it_never_runs_on_a_live_server(self):
        path = self.dump()
        with override_settings(DEBUG=False), self.assertRaisesRegex(CommandError, "DEBUG is off"):
            call_command("load_database", path, "--noinput")
        self.assertEqual(Company.objects.count(), 2)

    @override_settings(DEBUG=True)
    def test_a_bad_file_changes_nothing(self):
        broken = os.path.join(self.folder, "broken.json")
        with open(broken, "w", encoding="utf-8") as stream:
            stream.write('[{"model": "tenants.company", "pk": 1, "fields": {"nope": 1}}]')
        self.settle_checks()
        with self.assertRaises(Exception):
            call_command("load_database", broken, "--noinput", stdout=StringIO())
        self.assertEqual(Company.objects.count(), 2)
