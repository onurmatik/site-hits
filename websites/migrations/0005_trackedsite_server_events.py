from django.db import migrations, models

import websites.models


def populate_server_event_keys(apps, schema_editor):
    TrackedSite = apps.get_model("websites", "TrackedSite")
    for site in TrackedSite.objects.filter(server_event_key__isnull=True).iterator():
        site.server_event_key = websites.models.generate_server_event_key()
        site.save(update_fields=["server_event_key"])


class Migration(migrations.Migration):
    dependencies = [
        ("websites", "0004_trackedsite_bot_collector_health"),
    ]

    operations = [
        migrations.AddField(
            model_name="trackedsite",
            name="server_event_key",
            field=models.CharField(editable=False, max_length=64, null=True),
        ),
        migrations.RunPython(populate_server_event_keys, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="trackedsite",
            name="server_event_key",
            field=models.CharField(
                default=websites.models.generate_server_event_key,
                editable=False,
                max_length=64,
                unique=True,
            ),
        ),
        migrations.AddField(
            model_name="trackedsite",
            name="server_event_collector_last_event_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="trackedsite",
            name="server_event_collector_last_seen_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
