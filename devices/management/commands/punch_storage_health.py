"""Report storage; optionally vacuum obsolete row versions without deleting records."""
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


TABLES = ("devices_punchevent", "devices_devicemessage")


class Command(BaseCommand):
    help = "Report punch storage. --maintain runs ordinary VACUUM ANALYZE, never DELETE or VACUUM FULL."

    def add_arguments(self, parser):
        parser.add_argument("--maintain", action="store_true")

    def handle(self, *args, maintain=False, **options):
        if connection.vendor != "postgresql":
            raise CommandError("This command requires PostgreSQL.")
        if maintain and (connection.in_atomic_block or not connection.get_autocommit()):
            raise CommandError("Maintenance must run outside a transaction.")
        with connection.cursor() as cursor:
            if maintain:
                # Whitelisted identifiers only. Ordinary vacuum allows normal reads/writes,
                # reuses obsolete row space and updates planner statistics. No table rewrite.
                for table in TABLES:
                    self.stdout.write(f"Maintaining {table}...")
                    cursor.execute(f"VACUUM (ANALYZE, TRUNCATE FALSE) {connection.ops.quote_name(table)}")
            cursor.execute("""
                SELECT relname, n_live_tup, n_dead_tup,
                       pg_table_size(relid), pg_indexes_size(relid),
                       last_analyze, last_autoanalyze, last_vacuum, last_autovacuum
                FROM pg_stat_user_tables WHERE relname IN (%s, %s) ORDER BY relname
            """, TABLES)
            for name, live, dead, data, indexes, *times in cursor.fetchall():
                self.stdout.write(
                    f"{name}: estimated rows={live}, obsolete versions={dead}, "
                    f"data={data / 1048576:.2f} MiB, indexes={indexes / 1048576:.2f} MiB; "
                    f"analyze/autoanalyze/vacuum/autovacuum={times}"
                )
        self.stdout.write("No punch records deleted. Row estimates may be stale before maintenance.")
        self.stdout.write("Vacuum makes obsolete space reusable; it does not guarantee smaller database files.")
