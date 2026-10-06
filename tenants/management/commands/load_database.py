"""Replace this PC's database with a copy made by ``dump_database`` (2026-10-06).

    python manage.py load_database db_dumps/attendance_2026-10-06_1830.json

Everything in the local database is replaced by the file's: companies,
employees, attendance, salary, logins. In one go - if the file cannot be
loaded, nothing is changed. A developer's tool:

- it refuses to run where DEBUG is off, so it can never wipe the live server;
- it asks first (``--noinput`` to skip, e.g. in a script);
- it migrates first, so the tables match this code (load a copy of a live
  server that runs the same version - deploy first, then dump).

Afterwards you sign in with the live server's logins and passwords. Secrets
the live server encrypted with its own keys (company email passwords, API key
and two-step secrets, the ERP webhook's keys, saved fingerprints) cannot be
read with this PC's keys: set them again here if you need them.
"""

import os

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from tenants.database_copy import every_company


class Command(BaseCommand):
    help = "Replace the local database with a dump_database file (developers' PCs only)."

    def add_arguments(self, parser):
        parser.add_argument("file", help="The .json or .json.gz from dump_database.")
        parser.add_argument("--noinput", "--no-input", action="store_false",
                            dest="interactive", help="Do not ask before replacing.")

    def handle(self, *args, file, interactive=True, **options):
        if not settings.DEBUG:
            raise CommandError("Refused: DEBUG is off, so this looks like a live server. "
                               "load_database replaces the whole database - run it on a "
                               "developer's PC only.")
        if not os.path.exists(file):
            raise CommandError(f"No such file: {file}")
        database = settings.DATABASES["default"]
        where = f"{database.get('NAME')} on {database.get('HOST') or 'localhost'}"
        if interactive:
            answer = input(f"This REPLACES everything in the database {where} with {file}.\n"
                           "Type 'yes' to go on: ")
            if answer.strip().lower() != "yes":
                self.stdout.write("Nothing changed.")
                return

        self.stdout.write("1/3  Bringing the tables up to this code's version...")
        call_command("migrate", interactive=False, verbosity=0)
        call_command("createcachetable", verbosity=0)

        self.stdout.write("2/3  Replacing the data (this can take a few minutes)...")
        with transaction.atomic(), every_company():
            # Emptied and filled in one transaction: a file that fails half-way
            # leaves the database exactly as it was. (Every company at once: a
            # row's many-to-many links are written through scoped managers.)
            call_command("flush", interactive=False, verbosity=0)
            call_command("loaddata", file, verbosity=0)

        self.stdout.write("3/3  Done.")
        self._summary()
        self.stdout.write(self.style.WARNING(
            "Sign in with the live server's logins. Secrets encrypted with the live "
            "server's keys (email passwords, API keys, two-step, webhook keys, saved "
            "fingerprints) do not open here - set them again if you need them."))

    def _summary(self):
        from accounts.models import User
        from attendance.models import AttendanceRecord
        from employees.models import Employee
        from tenants.models import Company

        rows = (("Companies", Company.objects.count()), ("Logins", User.objects.count()),
                ("Employees", Employee.all_objects.count()),
                ("Attendance days", AttendanceRecord.all_objects.count()))
        for label, count in rows:
            self.stdout.write(f"     {label}: {count}")
