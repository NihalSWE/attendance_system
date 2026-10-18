# Every line and challan has its store now (0003 copied the order's): required from here on.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0003_line_and_challan_store'),
    ]

    operations = [
        migrations.AlterField(
            model_name='delivery',
            name='warehouse',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='inventory.warehouse', verbose_name='From store'),
        ),
        migrations.AlterField(
            model_name='salesorderline',
            name='warehouse',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='inventory.warehouse', verbose_name='From store'),
        ),
    ]
