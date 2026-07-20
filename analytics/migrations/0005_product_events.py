from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("analytics", "0004_analyticsevent_automation"),
        ("websites", "0005_trackedsite_server_events"),
    ]

    operations = [
        migrations.AddField(
            model_name="analyticsevent",
            name="actor_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="analyticsevent",
            name="idempotency_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="analyticsevent",
            name="metric_unit",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="analyticsevent",
            name="metric_value",
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=20, null=True),
        ),
        migrations.AddField(
            model_name="analyticsevent",
            name="source",
            field=models.CharField(
                choices=[("browser", "Browser"), ("server", "Server")],
                default="browser",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="analyticsevent",
            name="session_id",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AlterField(
            model_name="analyticsevent",
            name="visitor_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.CreateModel(
            name="ProductEventDefinition",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_name", models.CharField(max_length=64)),
                ("display_name", models.CharField(max_length=120)),
                ("description", models.TextField(max_length=500)),
                ("aggregation", models.CharField(choices=[("count", "Event count"), ("unique_actors", "Unique actors"), ("sum", "Sum"), ("average", "Average")], default="count", max_length=24)),
                ("unit", models.CharField(blank=True, max_length=32)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("site", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="product_event_definitions", to="websites.trackedsite")),
            ],
            options={
                "ordering": ["display_name", "event_name"],
            },
        ),
        migrations.AddConstraint(
            model_name="producteventdefinition",
            constraint=models.UniqueConstraint(fields=("site", "event_name"), name="product_event_site_name_uniq"),
        ),
        migrations.CreateModel(
            name="ActivationDefinition",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("goal_event", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="activation_goals", to="analytics.producteventdefinition")),
                ("site", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="activation_definition", to="websites.trackedsite")),
                ("start_event", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="activation_starts", to="analytics.producteventdefinition")),
            ],
        ),
        migrations.AddConstraint(
            model_name="activationdefinition",
            constraint=models.CheckConstraint(condition=models.Q(("start_event", models.F("goal_event")), _negated=True), name="activation_events_differ"),
        ),
        migrations.AddIndex(
            model_name="analyticsevent",
            index=models.Index(fields=["site", "event_name", "occurred_at"], name="event_site_name_time_idx"),
        ),
        migrations.AddIndex(
            model_name="analyticsevent",
            index=models.Index(fields=["site", "actor_hash", "occurred_at"], name="event_site_actor_time_idx"),
        ),
        migrations.AddConstraint(
            model_name="analyticsevent",
            constraint=models.UniqueConstraint(condition=models.Q(("idempotency_hash", ""), _negated=True), fields=("site", "idempotency_hash"), name="event_site_idempotency_uniq"),
        ),
    ]
