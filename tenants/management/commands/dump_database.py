"""Copy the whole database into one JSON file, to load on a developer's PC
(2026-10-06). On the live server:

    venv/bin/python manage.py dump_database

writes ``db_dumps/attendance_<date>.json`` (git-ignored), UTF-8, every
company's data. Load it on the PC with ``manage.py load_database <file>``.

It differs from a plain ``dumpdata`` where this project needs it to:

- every row of every company: the tenant-scoped managers refuse to read
  without a company, so the unscoped base managers are used (``--all``);
- users' Django permissions by name, not id (``auth.permission`` is not
  copied; the PC has its own, with other ids);
- written straight to the file as it goes, in UTF-8 - a big database is never
  held in memory, and Windows' own encoding never mangles a Bangla name;
- short-lived and secret-bearing rows are left out: sessions, the API's live
  logins, one-time password-reset links, login attempts and replay records.

The file holds people's personal data and password hashes: copy it to the PC,
then delete it from the server; never commit it.
"""

import gzip
import os
import sys

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from tenants.database_copy import every_company

#: Django's own bookkeeping (the PC makes its own) and short-lived security rows.
EXCLUDED = (
    "contenttypes.contenttype",
    "auth.permission",
    "admin.logentry",
    "sessions.session",
    "api.apisession",
    "api.usednonce",
    "api.idempotencyrecord",
    "api.loginattempt",
    "api.passwordreset",
)


class Command(BaseCommand):
    help = "Dump the whole database to db_dumps/attendance_<date>.json (UTF-8), for load_database."

    def add_arguments(self, parser):
        parser.add_argument("--output", help="Where to write it (default: "
                                             "db_dumps/attendance_<date-time>.json).")
        parser.add_argument("--gzip", action="store_true",
                            help="Compress it (.json.gz) - much smaller to download; "
                                 "load_database reads it as it is.")

    def handle(self, *args, output=None, **options):
        compress = options.get("gzip", False)
        folder = os.path.join(settings.BASE_DIR, "db_dumps")
        os.makedirs(folder, exist_ok=True)
        stamp = timezone.localtime().strftime("%Y-%m-%d_%H%M")
        path = output or os.path.join(folder, f"attendance_{stamp}.json" + (".gz" if compress else ""))
        self.stdout.write("Dumping the database... (a minute or two for a large one)")
        opener = _gzip_open if path.endswith(".gz") else _text_open
        try:
            with opener(path) as stream, every_company():
                call_command(
                    "dumpdata",
                    exclude=list(EXCLUDED),
                    use_base_manager=True,       # every company's rows, not one's
                    natural_foreign=True,        # permissions and content types by name
                    indent=1,
                    stdout=stream,
                )
        except Exception as exc:  # noqa: BLE001 - say it plainly and stop
            if os.path.exists(path):
                os.remove(path)
            self.stderr.write(self.style.ERROR(f"The dump failed: {exc}"))
            sys.exit(1)
        size = os.path.getsize(path) / (1024 * 1024)
        self.stdout.write(self.style.SUCCESS(f"Written: {path} ({size:.1f} MB)"))
        self.stdout.write("Copy it to your PC, then delete it here (it holds personal data). "
                          "On the PC: python manage.py load_database <the file>")


def _text_open(path):
    return open(path, "w", encoding="utf-8", newline="\n")


def _gzip_open(path):
    return gzip.open(path, "wt", encoding="utf-8")
