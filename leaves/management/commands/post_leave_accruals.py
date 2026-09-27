"""Post the leave ledger's automatic entries due up to today, for everyone a
leave policy covers (Phase E, 2026-09-27). Pages post them as they are read;
this keeps balances current for a nightly run. Repeatable."""

from django.core.management.base import BaseCommand
from django.utils import timezone

from common.tenant import use_company
from employees.models import Employee
from leaves import policies
from leaves.models import LeavePolicy
from tenants.models import Company


class Command(BaseCommand):
    help = "Post leave accruals, carry-forward and expiry due up to today."

    def handle(self, *args, **options):
        today = timezone.localdate()
        total = 0
        for company in Company.objects.all():
            with use_company(company):
                if not LeavePolicy.objects.exists():
                    continue
                for employee in Employee.objects.exclude(
                        employment_status__in=["resigned", "terminated", "retired"]):
                    policies.post(employee, until=today)
                    total += 1
        self.stdout.write(f"Leave ledger brought up to {today:%d %b %Y} for {total} people.")
