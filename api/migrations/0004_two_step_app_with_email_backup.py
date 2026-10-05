# Two-step login: the authenticator app is the main way and a code by email
# the backup for everyone - the "email only" way is gone (2026-10-05).

from django.db import migrations


def forget_email_only(apps, schema_editor):
    """Anyone set up with email codes only starts over with the app: owners and
    administrators are asked to set it up at their next login."""
    TwoStep = apps.get_model("api", "TwoStep")
    TwoStep.objects.filter(method="email").delete()
    # A move to email that was never confirmed: drop it.
    TwoStep.objects.filter(pending_method="email").update(pending_method="",
                                                          pending_secret_encrypted="")


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0003_two_step_email'),
    ]

    operations = [
        migrations.RunPython(forget_email_only, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='twostep',
            name='method',
        ),
        migrations.RemoveField(
            model_name='twostep',
            name='pending_method',
        ),
    ]
