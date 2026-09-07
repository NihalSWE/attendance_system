"""Create a reusable synthetic demonstration (P1 completion evidence).

Builds two companies with branches, departments, designation hierarchies,
shifts, weekly offs, holidays, employees on different pay bases, employees with
and without login accounts, a transfer, a termination and a REUSED employee code.

All identities are invented. Company codes are prefixed DEMO- and every employee
carries ``metadata["demo"] = True`` so synthetic data is always recognisable and
can never be mistaken for a real tenant's records.

    python manage.py seed_demo

Idempotent: re-running skips companies that already have employees.
"""

from datetime import date, datetime, time, timezone as dt_timezone
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from access_control.models import AccessPermission, DesignationPermission
from common.tenant import use_company
from employees.models import Employee
from employees.services import hire_employee, terminate_employee, transfer_employee
from organization.models import Branch, Department, Designation
from scheduling.models import Holiday, Shift, WeeklyOffRule
from tenants.models import Company, CompanyFeature, Feature
from tenants.services import onboard_company


def dt(y, m, d):
    return datetime(y, m, d, tzinfo=dt_timezone.utc)


FEATURES = [
    ("leave", "Leave management", 10),
    ("attendance", "Attendance", 20),
    ("payroll", "Payroll", 30),
]

PERMISSIONS = [
    ("leave", "leave.view", "View leave", "view", False),
    ("leave", "leave.request", "Request leave", "create", False),
    ("leave", "leave.approve", "Approve leave", "approve", False),
    ("attendance", "attendance.view", "View attendance", "view", False),
    ("attendance", "attendance.correct", "Correct attendance", "edit", False),
    ("payroll", "payroll.view", "View payroll", "view", True),
    ("payroll", "payroll.finalize", "Finalize payroll", "finalize", True),
]


class Command(BaseCommand):
    help = "Seed two synthetic demo companies with people, schedules and permissions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Allow seeding when DEBUG is False (synthetic data in a real environment).",
        )

    def handle(self, *args, **options):
        # Synthetic data must stay in development/demo environments.
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "DEBUG is False. Refusing to seed synthetic data without --force."
            )

        self._seed_catalogue()
        self._seed_northwind()
        self._seed_sunrise()
        self._seed_logins()
        self.stdout.write(self.style.SUCCESS("\nDemo seed complete."))
        self.stdout.write(
            "Companies: DEMO-NWT (Northwind Textiles), DEMO-SNR (Sunrise Logistics)"
        )

    # -- logins ---------------------------------------------------------------

    def _seed_logins(self):
        """Give the demo users company memberships so they can actually sign in.

        Runs on every invocation (get_or_create) rather than inside the
        skip-if-seeded branch, so an existing demo database gains logins too.
        A membership is what the tenant middleware resolves; without one a user
        signs in but has no active company.
        """
        from django.contrib.auth import get_user_model

        from accounts.models import CompanyMembership

        user_model = get_user_model()
        password = "demo12345"
        wanted = [
            ("ayesha.rahman@demo-northwind.test", "DEMO-NWT", "owner", "Ayesha", "Rahman"),
            ("imran.chowdhury@demo-sunrise.test", "DEMO-SNR", "owner", "Imran", "Chowdhury"),
        ]

        for email, company_code, role, first, last in wanted:
            company = Company.objects.filter(code=company_code).first()
            if company is None:
                continue
            user, created = user_model.objects.get_or_create(
                email=email, defaults={"first_name": first, "last_name": last}
            )
            if created or not user.has_usable_password():
                user.set_password(password)
                user.save(update_fields=["password"])
            CompanyMembership.all_objects.get_or_create(
                company=company,
                user=user,
                defaults={"role": role, "status": CompanyMembership.Status.ACTIVE},
            )

        self.stdout.write(self.style.SUCCESS(
            f"Logins ready (password '{password}'): ayesha.rahman@demo-northwind.test, "
            f"imran.chowdhury@demo-sunrise.test"
        ))

    # -- global catalogue ---------------------------------------------------

    @transaction.atomic
    def _seed_catalogue(self):
        """Features and permissions are global, shared by every tenant."""
        for code, name, order in FEATURES:
            Feature.objects.get_or_create(
                code=code, defaults={"name": name, "sort_order": order}
            )
        for feature_code, code, name, action, sensitive in PERMISSIONS:
            AccessPermission.objects.get_or_create(
                code=code,
                defaults={
                    "feature": Feature.objects.get(code=feature_code),
                    "name": name,
                    "action": action,
                    "is_sensitive": sensitive,
                },
            )
        self.stdout.write(
            f"Catalogue: {Feature.objects.count()} features, "
            f"{AccessPermission.objects.count()} permissions"
        )

    def _enable_features(self, company, codes):
        with use_company(company):
            for code in codes:
                feature = Feature.objects.get(code=code)
                CompanyFeature.all_objects.get_or_create(
                    company=company,
                    feature=feature,
                    effect=CompanyFeature.Effect.ENABLE,
                    defaults={"is_active": True},
                )

    def _grant(self, company, designation, permission_code, level="allowed"):
        with use_company(company):
            DesignationPermission.all_objects.get_or_create(
                company=company,
                designation=designation,
                permission=AccessPermission.objects.get(code=permission_code),
                defaults={
                    "access_level": level,
                    "effective_from": dt(2023, 1, 1),
                },
            )

    # -- company A: full demonstration ---------------------------------------

    def _seed_northwind(self):
        company = onboard_company(
            code="DEMO-NWT",
            slug="demo-northwind",
            name="Northwind Textiles",
            timezone="Asia/Dhaka",
            currency="BDT",
            country_code="BD",
            default_branch_name="Dhaka Head Office",
        )
        with use_company(company):
            if Employee.objects.exists():
                self.stdout.write("Northwind already seeded - skipping.")
                return

        self._enable_features(company, ["leave", "attendance", "payroll"])

        with use_company(company):
            hq = Branch.objects.get(is_default=True)
            unit = Branch.objects.create(
                code="CTG", name="Chittagong Unit", timezone="Asia/Dhaka",
                country_code="BD",
            )

            software = Department.objects.create(branch=hq, code="SW", name="Software")
            hr = Department.objects.create(branch=hq, code="HR", name="Human Resources")
            sales = Department.objects.create(branch=unit, code="SL", name="Sales")

            # Designation hierarchy: assistants report to managers.
            hr_manager = Designation.objects.create(
                department=hr, code="HRM", name="HR Manager"
            )
            hr_assistant = Designation.objects.create(
                department=hr, code="AHR", name="Assistant HR", parent=hr_manager
            )
            senior_dev = Designation.objects.create(
                department=software, code="SDV", name="Senior Developer"
            )
            junior_dev = Designation.objects.create(
                department=software, code="JDV", name="Junior Developer",
                parent=senior_dev,
            )
            sales_rep = Designation.objects.create(
                department=sales, code="REP", name="Sales Representative"
            )

            day = Shift.objects.create(
                code="DAY", name="Day shift", start_time=time(9), end_time=time(18),
                scheduled_minutes=480, default_break_minutes=60,
                grace_in_minutes=10, minimum_full_day_minutes=420,
                minimum_half_day_minutes=210, overtime_after_minutes=30,
            )
            Shift.objects.create(
                code="NGT", name="Night shift", start_time=time(22), end_time=time(6),
                spans_next_day=True, scheduled_minutes=480,
                default_break_minutes=45, minimum_full_day_minutes=420,
                minimum_half_day_minutes=210,
            )

            WeeklyOffRule.objects.create(weekday=4, effective_from=date(2023, 1, 1))
            Holiday.objects.create(
                holiday_date=date(2024, 3, 26), name="Independence Day"
            )
            Holiday.objects.create(
                holiday_date=date(2024, 12, 16), name="Victory Day"
            )

        User = settings.AUTH_USER_MODEL  # noqa: F841 - documented below
        from django.contrib.auth import get_user_model

        user_model = get_user_model()

        # An employee WITH a login account...
        hr_login, _ = user_model.objects.get_or_create(
            email="ayesha.rahman@demo-northwind.test",
            defaults={"first_name": "Ayesha", "last_name": "Rahman"},
        )

        ayesha = hire_employee(
            company=company, first_name="Ayesha", last_name="Rahman",
            employee_code="NWT-001", branch=hq, department=hr,
            designation=hr_manager, effective_from=dt(2023, 1, 1),
            pay_basis="monthly", base_rate=Decimal("60000"),
            user=hr_login, shift=day, joining_date=date(2023, 1, 1),
        )["employee"]

        # ...and employees with NO login at all (HR acts on their behalf).
        tanvir = hire_employee(
            company=company, first_name="Tanvir", last_name="Hossain",
            employee_code="NWT-002", branch=hq, department=software,
            designation=senior_dev, effective_from=dt(2023, 2, 1),
            pay_basis="monthly", base_rate=Decimal("90000"),
            shift=day, joining_date=date(2023, 2, 1),
        )["employee"]

        nusrat = hire_employee(
            company=company, first_name="Nusrat", last_name="Jahan",
            employee_code="NWT-003", branch=hq, department=software,
            designation=junior_dev, effective_from=dt(2023, 6, 1),
            pay_basis="monthly", base_rate=Decimal("45000"),
            manager=tanvir, shift=day, joining_date=date(2023, 6, 1),
        )["employee"]

        # Different pay bases: daily and hourly.
        hire_employee(
            company=company, first_name="Rakib", last_name="Islam",
            employee_code="NWT-004", branch=unit, department=sales,
            designation=sales_rep, effective_from=dt(2023, 8, 1),
            pay_basis="daily", base_rate=Decimal("1200"),
            joining_date=date(2023, 8, 1),
        )
        hire_employee(
            company=company, first_name="Sabbir", last_name="Ahmed",
            employee_code="NWT-005", branch=unit, department=sales,
            designation=sales_rep, effective_from=dt(2023, 9, 1),
            pay_basis="hourly", base_rate=Decimal("150"),
            joining_date=date(2023, 9, 1),
        )

        # A transfer: Nusrat moves from Software to Sales in Chittagong.
        transfer_employee(
            employee=nusrat, effective_at=dt(2024, 4, 1), branch=unit,
            department=sales, designation=sales_rep,
            reason="Moved to Sales, Chittagong Unit",
        )

        # A REUSED employee code: Karim leaves, and his code is later reissued.
        karim = hire_employee(
            company=company, first_name="Karim", last_name="Uddin",
            employee_code="NWT-014", branch=hq, department=software,
            designation=junior_dev, effective_from=dt(2023, 3, 1),
            pay_basis="monthly", base_rate=Decimal("40000"),
            joining_date=date(2023, 3, 1),
        )["employee"]
        terminate_employee(
            employee=karim, effective_at=dt(2024, 2, 1), reason="Resigned"
        )
        sadia = hire_employee(
            company=company, first_name="Sadia", last_name="Akter",
            employee_code="NWT-014",  # same code, only after Karim's interval ended
            branch=hq, department=software, designation=junior_dev,
            effective_from=dt(2024, 5, 1),
            pay_basis="monthly", base_rate=Decimal("42000"),
            joining_date=date(2024, 5, 1),
        )["employee"]

        # Mark every seeded person as synthetic.
        with use_company(company):
            Employee.objects.update(metadata={"demo": True})

        # Permissions: HR Manager approves leave; assistants only view.
        self._grant(company, hr_manager, "leave.approve")
        self._grant(company, hr_manager, "leave.view")
        self._grant(company, hr_manager, "payroll.view")
        self._grant(company, hr_assistant, "leave.view")
        self._grant(company, senior_dev, "attendance.view")
        # Junior devs are deliberately granted nothing - proving that enabling a
        # company feature does not hand every employee its actions.

        self.stdout.write(
            self.style.SUCCESS(
                f"Northwind Textiles: {Employee.all_objects.filter(company=company).count()} "
                f"employees, code NWT-014 held by Karim then reused by Sadia "
                f"(ids {karim.pk} -> {sadia.pk}), Nusrat transferred to Sales, "
                f"Ayesha has a login, others do not."
            )
        )

    # -- company B: proves isolation ------------------------------------------

    def _seed_sunrise(self):
        company = onboard_company(
            code="DEMO-SNR", slug="demo-sunrise", name="Sunrise Logistics",
            timezone="Asia/Dhaka", currency="BDT", country_code="BD",
            default_branch_name="Sunrise Depot",
        )
        with use_company(company):
            if Employee.objects.exists():
                self.stdout.write("Sunrise already seeded - skipping.")
                return

        # Only leave is enabled here: payroll permissions must resolve to DENY
        # for this tenant even though the catalogue defines them.
        self._enable_features(company, ["leave"])

        with use_company(company):
            depot = Branch.objects.get(is_default=True)
            ops = Department.objects.create(
                branch=depot, code="OPS", name="Operations"
            )
            supervisor = Designation.objects.create(
                department=ops, code="SUP", name="Supervisor"
            )
            Shift.objects.create(
                code="DAY", name="Day shift", start_time=time(8), end_time=time(17),
                scheduled_minutes=480, default_break_minutes=60,
                minimum_full_day_minutes=420, minimum_half_day_minutes=210,
            )
            WeeklyOffRule.objects.create(weekday=4, effective_from=date(2023, 1, 1))

        # Same employee code as Northwind's first employee is fine: codes are
        # unique per company, not globally.
        hire_employee(
            company=company, first_name="Imran", last_name="Chowdhury",
            employee_code="NWT-001", branch=depot, department=ops,
            designation=supervisor, effective_from=dt(2024, 1, 1),
            pay_basis="monthly", base_rate=Decimal("55000"),
            joining_date=date(2024, 1, 1),
        )
        with use_company(company):
            Employee.objects.update(metadata={"demo": True})

        self._grant(company, supervisor, "leave.approve")

        self.stdout.write(
            self.style.SUCCESS(
                "Sunrise Logistics: 1 employee reusing code NWT-001 "
                "(unique per company, not globally); payroll feature NOT enabled."
            )
        )
