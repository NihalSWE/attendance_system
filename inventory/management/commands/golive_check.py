"""Is a company ready to go live with Store & Inventory? (Phase 10)

    python manage.py golive_check --company ID

Prints the go-live checklist (Inventory → Go-live shows the same): settings,
masters, people and permissions, opening stock and labels, the ledger check,
mail and alerts. Exits with status 1 while anything must still be fixed.
"""

from django.core.management.base import BaseCommand, CommandError

from inventory import golive
from tenants.models import Company

MARK = {golive.OK: "OK  ", golive.WARN: "WARN", golive.FAIL: "FAIL", golive.INFO: "INFO"}


class Command(BaseCommand):
    help = "Print the Store & Inventory go-live checklist for a company."

    def add_arguments(self, parser):
        parser.add_argument("--company", type=int, required=True, help="The company id.")

    def handle(self, *args, **options):
        company = Company.objects.filter(pk=options["company"]).first()
        if company is None:
            raise CommandError("No such company.")
        sections = golive.readiness(company.pk)
        for title, checks in sections:
            self.stdout.write(f"\n{title}")
            for check in checks:
                self.stdout.write(f"  [{MARK[check.status]}] {check.title}" + (f" - {check.detail}" if check.detail
                                                                               else ""))
        totals = golive.totals(sections)
        self.stdout.write(f"\n{totals.get('ok', 0)} ok, {totals.get('warn', 0)} to look at, "
                          f"{totals.get('fail', 0)} to fix.")
        if totals.get(golive.FAIL):
            raise CommandError(f"{company.code} is not ready: {totals[golive.FAIL]} thing(s) to fix.")
