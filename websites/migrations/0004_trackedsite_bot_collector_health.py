from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("websites", "0003_trackedsite_bot_key"),
    ]

    operations = [
        migrations.AddField(
            model_name="trackedsite",
            name="bot_collector_last_event_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="trackedsite",
            name="bot_collector_last_seen_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
