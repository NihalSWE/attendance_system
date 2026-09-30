"""Repeat scans: only those under 5 seconds apart are one scan (Nihal,
2026-09-30). A company still on the old default of 30 seconds moves to 5; a
company that chose its own window keeps it."""

from django.db import migrations, models


def to_five(apps, schema_editor):
    Settings = apps.get_model("scheduling", "CompanyAttendanceSettings")
    Settings._base_manager.filter(duplicate_punch_window_seconds=30).update(
        duplicate_punch_window_seconds=5)


def to_thirty(apps, schema_editor):
    Settings = apps.get_model("scheduling", "CompanyAttendanceSettings")
    Settings._base_manager.filter(duplicate_punch_window_seconds=5).update(
        duplicate_punch_window_seconds=30)


class Migration(migrations.Migration):

    dependencies = [
        ("scheduling", "0003_alter_departmentshift_department"),
    ]

    operations = [
        migrations.AlterField(
            model_name="companyattendancesettings",
            name="duplicate_punch_window_seconds",
            field=models.PositiveIntegerField(default=5),
        ),
        migrations.RunPython(to_five, to_thirty),
    ]
