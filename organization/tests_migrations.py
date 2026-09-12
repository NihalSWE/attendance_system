"""Do the organisation migrations survive real data?

The rest of the suite builds an empty database and runs every migration against
it, which proves the *schema* steps work but never touches the ``RunPython``
bodies, because there are no rows for them to move. Those bodies are the risky
part: they are the only thing standing between a working dev database and a
mangled one.

So this test rewinds to the tenant-owned world, writes the awkward case by hand
— two companies that each created their own Software and Sales departments,
each holding their own "Manager" — and rolls forward to the *leaf* of the
migration graph. Two collapses have to survive that: 0003 merging the
tenant-owned departments into one root list, and 0004 merging the
per-department designations into one flat list.

Rolling forward to the leaf rather than to a named migration matters. This
previously stopped at 0003, which left the database on an old schema and
stranded every test that ran afterwards the moment a 0004 appeared.
"""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


BEFORE = [("organization", "0001_initial")]


class CatalogueCollapseMigrationTests(TransactionTestCase):
    """Rewind to before the root lists existed, seed them, and roll forward."""

    def _executor(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        return executor

    def _leaf(self):
        """Every graph leaf, so "forward" always means fully current."""
        return self._executor().loader.graph.leaf_nodes()

    def setUp(self):
        # Rewinding organization also unapplies everything that depends on it:
        # the employees, scheduling, access_control and accounts moves.
        self._executor().migrate(BEFORE)
        old_apps = self._executor().loader.project_state(BEFORE).apps
        self._seed(old_apps)
        leaf = self._leaf()
        self._executor().migrate(leaf)
        self.apps = self._executor().loader.project_state(leaf).apps

    def tearDown(self):
        # Leave the database fully migrated whatever happened above, so a
        # failure here does not poison every test that runs afterwards.
        self._executor().migrate(self._leaf())

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
            # old model these were unrelated rows.
            software = Department.objects.create(
                company=company, branch=branch, code="SW", name="Software"
            )
            sales = Department.objects.create(
                company=company, branch=branch, code="SL", name="Sales"
            )
            # The awkward case for 0004: the same "Manager" filed separately
            # under two departments, in two companies. Four rows that must end
            # as one, with all four placements still resolving.
            manager = Designation.objects.create(
                company=company, department=software, code="MGR", name="Manager"
            )
            Designation.objects.create(
                company=company, department=sales, code="MGR", name="Manager"
            )
            Designation.objects.create(
                company=company,
                department=software,
                code="DEV",
                name="Developer",
                parent=manager,
            )

    # ------------------------------------------------------------------ tests

    def test_duplicate_departments_collapse_to_one_root_row(self):
        Department = self.apps.get_model("organization", "Department")
        self.assertEqual(Department.objects.filter(name="Software").count(), 1)
        self.assertEqual(Department.objects.count(), 2)  # Software and Sales

    def test_each_company_keeps_its_own_department_rows(self):
        CompanyDepartment = self.apps.get_model("organization", "CompanyDepartment")
        adoptions = CompanyDepartment.objects.all()
        # Two companies x two departments.
        self.assertEqual(adoptions.count(), 4)
        self.assertEqual(len({a.company_id for a in adoptions}), 2)
        self.assertEqual(len({a.branch_id for a in adoptions}), 2)
        # Both companies share the same two root departments.
        self.assertEqual(len({a.department_id for a in adoptions}), 2)

    def test_manager_collapses_to_one_flat_designation(self):
        """Four Manager rows — two departments x two companies — become one."""
        Designation = self.apps.get_model("organization", "Designation")
        self.assertEqual(Designation.objects.filter(name="Manager").count(), 1)
        self.assertEqual(
            sorted(Designation.objects.values_list("name", flat=True)),
            ["Developer", "Manager"],
        )

    def test_every_company_placement_still_resolves_to_the_survivor(self):
        Designation = self.apps.get_model("organization", "Designation")
        CompanyDesignation = self.apps.get_model("organization", "CompanyDesignation")
        manager = Designation.objects.get(name="Manager")
        placements = CompanyDesignation.objects.filter(
            designation=manager
        ).select_related("company_department")
        # Both companies, under both of their departments.
        self.assertEqual(placements.count(), 4)
        self.assertEqual(len({p.company_id for p in placements}), 2)
        self.assertEqual(
            len({p.company_department.department_id for p in placements}), 2
        )

    def test_codes_end_up_globally_unique(self):
        """MGR existed under two departments; only one may keep the bare code."""
        Designation = self.apps.get_model("organization", "Designation")
        codes = list(Designation.objects.values_list("code", flat=True))
        self.assertEqual(len(codes), len(set(codes)))

    def test_each_company_keeps_its_own_designation_rows(self):
        CompanyDesignation = self.apps.get_model("organization", "CompanyDesignation")
        adoptions = CompanyDesignation.objects.all()
        # Two companies x (Manager in Software, Manager in Sales, Developer).
        self.assertEqual(adoptions.count(), 6)
        self.assertEqual(len({a.company_id for a in adoptions}), 2)
        self.assertEqual(len({a.designation_id for a in adoptions}), 2)

    def test_adoption_rows_point_at_their_own_company_department(self):
        CompanyDesignation = self.apps.get_model("organization", "CompanyDesignation")
        for adoption in CompanyDesignation.objects.select_related("company_department"):
            self.assertEqual(
                adoption.company_id, adoption.company_department.company_id
            )

    def test_root_list_columns_are_gone(self):
        Department = self.apps.get_model("organization", "Department")
        Designation = self.apps.get_model("organization", "Designation")
        department_fields = {f.name for f in Department._meta.get_fields()}
        designation_fields = {f.name for f in Designation._meta.get_fields()}
        self.assertNotIn("company", department_fields)
        self.assertNotIn("branch", department_fields)
        self.assertNotIn("company", designation_fields)
        self.assertNotIn("parent", designation_fields)
        self.assertNotIn("hierarchy_level", designation_fields)
        # 0004: a designation no longer belongs to a department.
        self.assertNotIn("department", designation_fields)
