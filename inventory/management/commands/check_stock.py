"""Nightly check (plan §5 rule 10): the ledger and the balances agree.

    python manage.py check_stock [--company ID]

For every company with stock, each balance row (item x batch x store x bin)
must equal the sum of its moves, and each average cost's quantity the stock
it covers. Prints every difference and exits with status 1 if there is one,
so a systemd timer's failure shows in the journal.
"""

from django.core.management.base import BaseCommand, CommandError

from inventory.models import StockMove
from inventory.posting import ledger_differences
from tenants.models import Company


class Command(BaseCommand):
    help = "Check that stock balances equal the stock ledger, for every company."

    def add_arguments(self, parser):
        parser.add_argument("--company", type=int, help="Only this company id.")

    def handle(self, *args, **options):
        companies = Company.objects.filter(
            pk__in=StockMove.all_objects.values("company_id").distinct())
        if options.get("company"):
            companies = companies.filter(pk=options["company"])
        found = 0
        for company in companies.order_by("pk"):
            for difference in ledger_differences(company.pk):
                found += 1
                what = "average cost quantity" if difference.get("cost") else "balance"
                self.stdout.write(
                    f"{company.code}: item {difference['item']} store {difference['warehouse']} "
                    f"batch {difference['batch']} bin {difference['location']}: ledger "
                    f"{difference['ledger']}, {what} {difference['balance']}")
        if found:
            raise CommandError(f"{found} difference(s) between the stock ledger and balances.")
        self.stdout.write("Stock check: no differences.")
