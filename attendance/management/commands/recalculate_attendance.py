"""Work attendance out again from the scans, for every company, from a day to
today (2026-09-30). For a change of rule - the repeat window, say - to reach
days already stored: pages only rebuild days still open. A finalised salary
month is never touched. Repeatable.

    python manage.py recalculate_attendance --since 2026-09-01
"""

import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from attendance.services import recalculate
from tenants.models import Company


class Command(BaseCommand):
    help = "Recalculate attendance days from --since to today, for every company."

    def add_arguments(self, parser):
        parser.add_argument("--since", required=True, help="First day, YYYY-MM-DD.")
        parser.add_argument("--company", type=int, help="Only this company (its id).")

    def handle(self, *args, since, company=None, **options):
        try:
            start = datetime.date.fromisoformat(since)
        except ValueError as exc:
            raise CommandError("--since must be a date like 2026-09-01.") from exc
        today = timezone.localdate()
        if start > today:
            raise CommandError("--since is in the future.")
        companies = Company.objects.all()
        if company:
            companies = companies.filter(pk=company)
        for each in companies.order_by("pk"):
            written = recalculate(each.pk, start=start, end=today)
            self.stdout.write(f"{each.name}: {written.get('days', 0)} day(s) from "
                              f"{start:%d %b %Y}; {written.get('skipped_locked', 0)} in finalised "
                              "months left alone.")
