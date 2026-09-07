from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0003_alter_company_country_code_alter_company_currency_and_more"),
    ]

    operations = [
        migrations.RunSQL(
            """
            CREATE SEQUENCE tenants_company_code_seq START WITH 10001;
            SELECT setval('tenants_company_code_seq', GREATEST(
                10000,
                COALESCE((SELECT MAX(code::bigint) FROM tenants_company
                    WHERE code ~ '^[0-9]{1,18}$'), 10000)
            ));
            """,
            "DROP SEQUENCE tenants_company_code_seq;",
        ),
    ]
