import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("analytics", "0005_product_events"),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentAuditEvent",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("request_id", models.CharField(db_index=True, max_length=64)),
                ("authenticated_actor_id", models.CharField(max_length=255)),
                ("authenticated_client_id", models.CharField(max_length=512)),
                ("tenant_id", models.CharField(blank=True, max_length=255)),
                ("tool_name", models.CharField(db_index=True, max_length=120)),
                ("target_resource_type", models.CharField(max_length=80)),
                ("target_resource_id", models.CharField(blank=True, max_length=255)),
                ("authorization", models.JSONField(default=dict)),
                ("input_hash", models.CharField(max_length=64)),
                ("outcome_code", models.CharField(max_length=80)),
                ("idempotency_id", models.CharField(blank=True, max_length=64)),
                ("operation_id", models.CharField(blank=True, max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.CreateModel(
            name="AgentIdempotencyRecord",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "idempotency_id",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                ("authenticated_actor_id", models.CharField(max_length=255)),
                ("authenticated_client_id", models.CharField(max_length=512)),
                ("tool_name", models.CharField(max_length=120)),
                ("key_digest", models.CharField(max_length=64)),
                ("input_hash", models.CharField(max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[("pending", "Pending"), ("completed", "Completed")],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("result", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("expires_at", models.DateTimeField(db_index=True)),
            ],
        ),
        migrations.AddConstraint(
            model_name="agentidempotencyrecord",
            constraint=models.UniqueConstraint(
                fields=(
                    "authenticated_actor_id",
                    "authenticated_client_id",
                    "tool_name",
                    "key_digest",
                ),
                name="agent_idempotency_key_uniq",
            ),
        ),
    ]
