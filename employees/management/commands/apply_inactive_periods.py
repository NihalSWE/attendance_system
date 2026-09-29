"""Suspend employees whose inactive period starts today and make active again
those whose period has ended (2026-09-29). Pages do it on their own at most
once an hour; this is for a scheduler, e.g. just after midnight. Repeatable."""

from django.core.management.base import BaseCommand

from organization.employee_inactive import apply_due
from tenants.models import Company


class Command(BaseCommand):
    help = "Bring employee statuses in line with their inactive periods."

    def handle(self, *args, **options):
        changed = sum(apply_due(company.pk) for company in Company.objects.all())
        self.stdout.write(f"{changed} employee status(es) changed.")
