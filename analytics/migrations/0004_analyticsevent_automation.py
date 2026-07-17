from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("analytics", "0003_botevent"),
    ]

    operations = [
        migrations.AddField(
            model_name="analyticsevent",
            name="automation_reasons",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="analyticsevent",
            name="automation_score",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddIndex(
            model_name="analyticsevent",
            index=models.Index(
                fields=["site", "automation_score", "occurred_at"],
                name="event_site_auto_idx",
            ),
        ),
    ]
