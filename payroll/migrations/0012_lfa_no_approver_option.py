"""LFA: "Decided by the approver" removed from How much (Nihal, 2026-09-28) -
it was the same as a fixed amount. A setting saved with it becomes a fixed
amount of its maximum. Claims already made keep what they were made with."""

from django.db import migrations, models


def approver_to_fixed(apps, schema_editor):
    LfaSettings = apps.get_model("payroll", "LfaSettings")
    for row in LfaSettings._base_manager.filter(amount_method="approver"):
        row.amount_method = "fixed"
        row.fixed_amount = row.fixed_amount or row.max_amount
        row.save(update_fields=["amount_method", "fixed_amount"])


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0011_lfa_months"),
    ]

    operations = [
        migrations.RunPython(approver_to_fixed, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="lfasettings",
            name="amount_method",
            field=models.CharField(
                choices=[("fixed", "A fixed amount"),
                         ("basic_months", "Months of basic salary"),
                         ("gross_months", "Months of gross salary (basic + allowances)")],
                default="basic_months", max_length=16),
        ),
    ]
