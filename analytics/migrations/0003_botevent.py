from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("analytics", "0002_analyticsevent_city_region"),
        ("websites", "0003_trackedsite_bot_key"),
    ]

    operations = [
        migrations.CreateModel(
            name="BotEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("occurred_at", models.DateTimeField()),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                ("path", models.CharField(max_length=2048)),
                ("status_code", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("provider", models.CharField(max_length=64)),
                ("crawler", models.CharField(max_length=64)),
                (
                    "category",
                    models.CharField(
                        choices=[
                            ("answer", "AI answer"),
                            ("indexing", "Indexing"),
                            ("training", "Training"),
                            ("other", "Other"),
                        ],
                        max_length=16,
                    ),
                ),
                (
                    "verification",
                    models.CharField(
                        choices=[
                            ("user_agent", "User-agent match"),
                            ("ip_verified", "IP verified"),
                        ],
                        default="user_agent",
                        max_length=16,
                    ),
                ),
                (
                    "site",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="bot_events",
                        to="websites.trackedsite",
                    ),
                ),
            ],
            options={
                "ordering": ["-occurred_at", "-id"],
                "indexes": [
                    models.Index(fields=["site", "occurred_at"], name="bot_site_time_idx"),
                    models.Index(
                        fields=["site", "category", "occurred_at"],
                        name="bot_site_category_idx",
                    ),
                    models.Index(
                        fields=["site", "provider", "occurred_at"],
                        name="bot_site_provider_idx",
                    ),
                ],
            },
        ),
    ]
