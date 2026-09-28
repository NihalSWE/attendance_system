"""LFA: "a share of monthly basic (%)" becomes "months of basic salary", and
"months of gross salary" is added (Nihal, 2026-09-28). A saved 100 % becomes
1 month; claims already made keep the rules they were made under."""

from decimal import Decimal

from django.db import migrations, models


def percent_to_months(apps, schema_editor):
    LfaSettings = apps.get_model("payroll", "LfaSettings")
    for row in LfaSettings._base_manager.all():
        if row.amount_method == "basic_percent":
            row.amount_method = "basic_months"
        if row.months is not None:
            row.months = (Decimal(row.months) / 100).quantize(Decimal("0.01"))
        row.save(update_fields=["amount_method", "months"])


def months_to_percent(apps, schema_editor):
    LfaSettings = apps.get_model("payroll", "LfaSettings")
    for row in LfaSettings._base_manager.all():
        if row.amount_method in ("basic_months", "gross_months"):
            row.amount_method = "basic_percent"
        if row.months is not None:
            row.months = Decimal(row.months) * 100
        row.save(update_fields=["amount_method", "months"])


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0010_lfa"),
    ]

    operations = [
        migrations.RenameField(model_name="lfasettings", old_name="basic_percent",
                               new_name="months"),
        migrations.RunPython(percent_to_months, months_to_percent),
        migrations.AlterField(
            model_name="lfasettings", name="months",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=4, null=True),
        ),
        migrations.AlterField(
            model_name="lfasettings", name="amount_method",
            field=models.CharField(
                choices=[("fixed", "A fixed amount"),
                         ("basic_months", "Months of basic salary"),
                         ("gross_months", "Months of gross salary (basic + allowances)"),
                         ("approver", "Decided by the approver")],
                default="basic_months", max_length=16),
        ),
    ]
