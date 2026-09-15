from decimal import Decimal

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    """A10c: optional yearly allowance per leave type. Existing types get no limit."""

    dependencies = [
        ("leaves", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="leavetype",
            name="days_per_year",
            field=models.DecimalField(
                blank=True, decimal_places=1, max_digits=5, null=True,
                validators=[django.core.validators.MinValueValidator(Decimal("0.5"))],
            ),
        ),
    ]
