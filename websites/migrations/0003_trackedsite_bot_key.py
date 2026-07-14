from django.db import migrations, models

import websites.models


def populate_bot_keys(apps, schema_editor):
    TrackedSite = apps.get_model("websites", "TrackedSite")
    for site in TrackedSite.objects.filter(bot_key__isnull=True).iterator():
        site.bot_key = websites.models.generate_bot_key()
        site.save(update_fields=["bot_key"])


class Migration(migrations.Migration):
    dependencies = [
        ("websites", "0002_trackedsite_owner"),
    ]

    operations = [
        migrations.AddField(
            model_name="trackedsite",
            name="bot_key",
            field=models.CharField(editable=False, max_length=64, null=True),
        ),
        migrations.RunPython(populate_bot_keys, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="trackedsite",
            name="bot_key",
            field=models.CharField(
                default=websites.models.generate_bot_key,
                editable=False,
                max_length=64,
                unique=True,
            ),
        ),
    ]
