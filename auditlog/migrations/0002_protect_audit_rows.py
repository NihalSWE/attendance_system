from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("auditlog", "0001_initial")]
    operations = [migrations.RunSQL(
        sql="""
        CREATE FUNCTION auditlog_reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'Audit records are append-only';
        END;
        $$;
        CREATE TRIGGER auditlog_append_only BEFORE UPDATE OR DELETE ON auditlog_auditlog
        FOR EACH ROW EXECUTE FUNCTION auditlog_reject_mutation();
        """,
        reverse_sql="""
        DROP TRIGGER auditlog_append_only ON auditlog_auditlog;
        DROP FUNCTION auditlog_reject_mutation();
        """,
    )]
