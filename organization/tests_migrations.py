"""Does the catalogue migration survive real data?

The rest of the suite builds an empty database and runs every migration against
it, which proves the *schema* steps work but never touches the ``RunPython``
bodies, because there are no rows for them to move. Those bodies are the risky
part: they are the only thing standing between a working dev database and a
mangled one.

So this test rewinds to the tenant-owned world, writes the awkward case by hand
— two companies that each created their own "Software" department containing
their own "Manager" title — and rolls forward. If the collapse mishandles the
merge, this fails here rather than on somebody's data.
"""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


BEFORE = [("organization", "0001_initial")]
AFTER = [("organization", "0003_root_catalogues")]


class CatalogueCollapseMigrationTests(TransactionTestCase):
    """Rewind to before the catalogue existed, seed it, and roll forward."""

    def _executor(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        return executor

    def setUp(self):
        # Rewinding organization also unapplies everything that depends on it:
        # the employees, scheduling, access_control and accounts moves.
        self._executor().migrate(BEFORE)
        old_apps = self._executor().loader.project_state(BEFORE).apps
        self._seed(old_apps)
        self._executor().migrate(AFTER)
        self.apps = self._executor().loader.project_state(AFTER).apps

    def tearDown(self):
        # Leave the database at the latest state whatever happened above, so a
        # failure here does not poison every test that runs afterwards.
        self._executor().migrate(AFTER)

    def _seed(self, old_apps):
        Company = old_apps.get_model("tenants", "Company")
        Branch = old_apps.get_model("organization", "Branch")
        Department = old_apps.get_model("organization", "Department")
        Designation = old_apps.get_model("organization", "Designation")

        for code, slug, name in (
            ("A", "company-a", "Company A"),
            ("B", "company-b", "Company B"),
        ):
            company = Company.objects.create(code=code, slug=slug, name=name)
            branch = Branch.objects.create(
                company=company, code="HQ", name=f"{name} HQ", is_default=True
            )
            # Both companies picked the same names and the same codes. Under the
            # old model these were four unrelated rows.
            software = Department.objects.create(
                company=company, branch=branch, code="SW", name="Software"
            )
            manager = Designation.objects.create(
                company=company, department=software, code="MGR", name="Manager"
            )
            Designation.objects.create(
                company=company,
                department=software,
                code="DEV",
                name="Developer",
                parent=manager,
            )

    # ------------------------------------------------------------------ tests

    def test_duplicate_departments_collapse_to_one_catalogue_row(self):
        Department = self.apps.get_model("organization", "Department")
        self.assertEqual(Department.objects.filter(name="Software").count(), 1)
        self.assertEqual(Department.objects.count(), 1)

    def test_each_company_keeps_its_own_adoption_row(self):
        CompanyDepartment = self.apps.get_model("organization", "CompanyDepartment")
        adoptions = CompanyDepartment.objects.all()
        self.assertEqual(adoptions.count(), 2)
        # Two companies, two branches, one shared catalogue entry.
        self.assertEqual(len({a.company_id for a in adoptions}), 2)
        self.assertEqual(len({a.branch_id for a in adoptions}), 2)
        self.assertEqual(len({a.department_id for a in adoptions}), 1)

    def test_duplicate_titles_collapse_under_the_surviving_department(self):
        Designation = self.apps.get_model("organization", "Designation")
        Department = self.apps.get_model("organization", "Department")
        software = Department.objects.get(name="Software")
        titles = Designation.objects.filter(department=software)
        self.assertEqual(titles.count(), 2)
        self.assertEqual(
            sorted(titles.values_list("name", flat=True)), ["Developer", "Manager"]
        )

    def test_each_company_keeps_its_own_title_adoption_rows(self):
        CompanyDesignation = self.apps.get_model("organization", "CompanyDesignation")
        adoptions = CompanyDesignation.objects.all()
        # Two companies x two titles.
        self.assertEqual(adoptions.count(), 4)
        self.assertEqual(len({a.company_id for a in adoptions}), 2)
        self.assertEqual(len({a.designation_id for a in adoptions}), 2)

    def test_adoption_rows_point_at_their_own_company_department(self):
        CompanyDesignation = self.apps.get_model("organization", "CompanyDesignation")
        for adoption in CompanyDesignation.objects.select_related("company_department"):
            self.assertEqual(
                adoption.company_id, adoption.company_department.company_id
            )

    def test_catalogue_columns_are_gone(self):
        Department = self.apps.get_model("organization", "Department")
        Designation = self.apps.get_model("organization", "Designation")
        department_fields = {f.name for f in Department._meta.get_fields()}
        designation_fields = {f.name for f in Designation._meta.get_fields()}
        self.assertNotIn("company", department_fields)
        self.assertNotIn("branch", department_fields)
        self.assertNotIn("company", designation_fields)
        self.assertNotIn("parent", designation_fields)
        self.assertNotIn("hierarchy_level", designation_fields)
