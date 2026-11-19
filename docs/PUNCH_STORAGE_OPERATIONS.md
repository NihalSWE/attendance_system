# Punch performance and storage

All punch evidence, authorization snapshots, attendance links and raw messages remain stored.
There is no retention deletion, archive job, terminal command or change to payroll rules in
this optimization. Deploy the Python changes and run `python manage.py migrate`.

Migration 0016 creates three PostgreSQL indexes concurrently, allowing normal ingestion
while they build. Run migrations once, outside a surrounding transaction, at a quiet time
to limit I/O competition. Leave disk headroom for index construction. If interrupted,
inspect invalid indexes before retrying; do not blindly mark the migration applied.

Punch list rows fetch displayed fields only; the detail page still loads full evidence.
Duplicate lookup queries load only the matched ID, preserving existing classification.

## Storage maintenance

Run `python manage.py punch_storage_health` for a read-only storage/statistics report.
Row counts are PostgreSQL estimates, not an audit count.

Run `python manage.py punch_storage_health --maintain` with the database owner's maintenance
permissions to perform ordinary VACUUM ANALYZE on the punch and device-message tables.
It processes all companies in the configured database, does not delete punch records,
and uses TRUNCATE FALSE to avoid vacuum's tail-truncation lock. Normal application reads
and writes can continue, but the scan consumes I/O, so schedule during a quiet period.
Keep PostgreSQL autovacuum enabled. Monitor this report and tune maintenance frequency
to actual write volume rather than running full-table maintenance on every request.

This makes space from obsolete row versions reusable and refreshes query-planner
statistics. It does not shrink the database file. No VACUUM FULL or destructive cleanup
is included. More preserved history still requires more storage. Indexes consume disk
in exchange for faster reads; monitor both data and index sizes.
