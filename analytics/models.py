import uuid

from django.core.exceptions import ValidationError
from django.db import models

from websites.models import TrackedSite


class AnalyticsEvent(models.Model):
    class EventType(models.TextChoices):
        PAGEVIEW = "pageview", "Pageview"
        CUSTOM = "custom", "Custom event"

    class Source(models.TextChoices):
        BROWSER = "browser", "Browser"
        SERVER = "server", "Server"

    site = models.ForeignKey(TrackedSite, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=16, choices=EventType.choices)
    event_name = models.CharField(max_length=64, blank=True)
    source = models.CharField(
        max_length=16,
        choices=Source.choices,
        default=Source.BROWSER,
    )
    occurred_at = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)
    visitor_hash = models.CharField(max_length=64, blank=True, default="")
    session_id = models.CharField(max_length=64, blank=True, default="")
    actor_hash = models.CharField(max_length=64, blank=True, default="")
    idempotency_hash = models.CharField(max_length=64, blank=True, default="")
    metric_value = models.DecimalField(
        max_digits=20,
        decimal_places=6,
        null=True,
        blank=True,
    )
    metric_unit = models.CharField(max_length=32, blank=True, default="")

    path = models.CharField(max_length=2048)
    referrer_domain = models.CharField(max_length=255, blank=True)
    referrer_path = models.CharField(max_length=2048, blank=True)
    utm_source = models.CharField(max_length=255, blank=True)
    utm_medium = models.CharField(max_length=255, blank=True)
    utm_campaign = models.CharField(max_length=255, blank=True)
    utm_term = models.CharField(max_length=255, blank=True)
    utm_content = models.CharField(max_length=255, blank=True)

    country_code = models.CharField(max_length=2, blank=True)
    country_name = models.CharField(max_length=100, blank=True)
    region_code = models.CharField(max_length=3, blank=True)
    region_name = models.CharField(max_length=100, blank=True)
    city_name = models.CharField(max_length=100, blank=True)
    device = models.CharField(max_length=32, blank=True)
    browser = models.CharField(max_length=100, blank=True)
    operating_system = models.CharField(max_length=100, blank=True)
    language = models.CharField(max_length=35, blank=True)
    client_timezone = models.CharField(max_length=64, blank=True)
    viewport_width = models.PositiveIntegerField(default=0)
    viewport_height = models.PositiveIntegerField(default=0)
    screen_width = models.PositiveIntegerField(default=0)
    screen_height = models.PositiveIntegerField(default=0)
    automation_score = models.PositiveSmallIntegerField(default=0)
    automation_reasons = models.JSONField(default=list, blank=True)
    properties = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(fields=["site", "occurred_at"], name="event_site_time_idx"),
            models.Index(
                fields=["site", "visitor_hash", "occurred_at"],
                name="event_site_visitor_idx",
            ),
            models.Index(
                fields=["site", "session_id", "occurred_at"],
                name="event_site_session_idx",
            ),
            models.Index(
                fields=["site", "event_type", "occurred_at"],
                name="event_site_type_idx",
            ),
            models.Index(
                fields=["site", "event_name", "occurred_at"],
                name="event_site_name_time_idx",
            ),
            models.Index(
                fields=["site", "actor_hash", "occurred_at"],
                name="event_site_actor_time_idx",
            ),
            models.Index(
                fields=["site", "automation_score", "occurred_at"],
                name="event_site_auto_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["site", "idempotency_hash"],
                condition=~models.Q(idempotency_hash=""),
                name="event_site_idempotency_uniq",
            )
        ]

    def __str__(self):
        label = self.event_name or self.event_type
        return f"{self.site}: {label} at {self.occurred_at.isoformat()}"


class ProductEventDefinition(models.Model):
    class Aggregation(models.TextChoices):
        COUNT = "count", "Event count"
        UNIQUE_ACTORS = "unique_actors", "Unique actors"
        SUM = "sum", "Sum"
        AVERAGE = "average", "Average"

    site = models.ForeignKey(
        TrackedSite,
        on_delete=models.CASCADE,
        related_name="product_event_definitions",
    )
    event_name = models.CharField(max_length=64)
    display_name = models.CharField(max_length=120)
    description = models.TextField(max_length=500)
    aggregation = models.CharField(
        max_length=24,
        choices=Aggregation.choices,
        default=Aggregation.COUNT,
    )
    unit = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_name", "event_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["site", "event_name"],
                name="product_event_site_name_uniq",
            )
        ]

    def clean(self):
        super().clean()
        numeric = self.aggregation in {self.Aggregation.SUM, self.Aggregation.AVERAGE}
        if numeric and not self.unit.strip():
            raise ValidationError({"unit": "Numeric metrics require a unit."})
        if not numeric and self.unit:
            raise ValidationError({"unit": "Only numeric metrics can define a unit."})

    def __str__(self):
        return f"{self.site}: {self.display_name} ({self.event_name})"


class ActivationDefinition(models.Model):
    site = models.OneToOneField(
        TrackedSite,
        on_delete=models.CASCADE,
        related_name="activation_definition",
    )
    start_event = models.ForeignKey(
        ProductEventDefinition,
        on_delete=models.PROTECT,
        related_name="activation_starts",
    )
    goal_event = models.ForeignKey(
        ProductEventDefinition,
        on_delete=models.PROTECT,
        related_name="activation_goals",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(start_event=models.F("goal_event")),
                name="activation_events_differ",
            )
        ]

    def clean(self):
        super().clean()
        if self.start_event_id and self.start_event.site_id != self.site_id:
            raise ValidationError({"start_event": "The start event must belong to this site."})
        if self.goal_event_id and self.goal_event.site_id != self.site_id:
            raise ValidationError({"goal_event": "The goal event must belong to this site."})
        if self.start_event_id and self.start_event_id == self.goal_event_id:
            raise ValidationError("Activation start and goal events must be different.")

    def __str__(self):
        return f"{self.site}: {self.start_event.event_name} → {self.goal_event.event_name}"


class BotEvent(models.Model):
    class Category(models.TextChoices):
        ANSWER = "answer", "AI answer"
        INDEXING = "indexing", "Indexing"
        TRAINING = "training", "Training"
        OTHER = "other", "Other"

    class Verification(models.TextChoices):
        USER_AGENT = "user_agent", "User-agent match"
        IP_VERIFIED = "ip_verified", "IP verified"

    site = models.ForeignKey(
        TrackedSite,
        on_delete=models.CASCADE,
        related_name="bot_events",
    )
    occurred_at = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)
    path = models.CharField(max_length=2048)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    provider = models.CharField(max_length=64)
    crawler = models.CharField(max_length=64)
    category = models.CharField(max_length=16, choices=Category.choices)
    verification = models.CharField(
        max_length=16,
        choices=Verification.choices,
        default=Verification.USER_AGENT,
    )

    class Meta:
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(fields=["site", "occurred_at"], name="bot_site_time_idx"),
            models.Index(
                fields=["site", "category", "occurred_at"],
                name="bot_site_category_idx",
            ),
            models.Index(
                fields=["site", "provider", "occurred_at"],
                name="bot_site_provider_idx",
            ),
        ]

    def __str__(self):
        return f"{self.site}: {self.crawler} requested {self.path}"


class AgentIdempotencyRecord(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"

    idempotency_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    authenticated_actor_id = models.CharField(max_length=255)
    authenticated_client_id = models.CharField(max_length=512)
    tool_name = models.CharField(max_length=120)
    key_digest = models.CharField(max_length=64)
    input_hash = models.CharField(max_length=64)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    result = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "authenticated_actor_id",
                    "authenticated_client_id",
                    "tool_name",
                    "key_digest",
                ],
                name="agent_idempotency_key_uniq",
            )
        ]

    def __str__(self):
        return f"{self.tool_name}: {self.idempotency_id}"


class AgentAuditEvent(models.Model):
    request_id = models.CharField(max_length=64, db_index=True)
    authenticated_actor_id = models.CharField(max_length=255)
    authenticated_client_id = models.CharField(max_length=512)
    tenant_id = models.CharField(max_length=255, blank=True)
    tool_name = models.CharField(max_length=120, db_index=True)
    target_resource_type = models.CharField(max_length=80)
    target_resource_id = models.CharField(max_length=255, blank=True)
    authorization = models.JSONField(default=dict)
    input_hash = models.CharField(max_length=64)
    outcome_code = models.CharField(max_length=80)
    idempotency_id = models.CharField(max_length=64, blank=True)
    operation_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.tool_name}: {self.outcome_code} ({self.request_id})"


class ArchivePartition(models.Model):
    class Stream(models.TextChoices):
        ANALYTICS = "analytics", "Analytics events"
        BOTS = "bots", "Bot events"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        EXPORTED = "exported", "Exported"
        VERIFIED = "verified", "Verified"
        DELETING = "deleting", "Deleting source"
        SOURCE_DELETED = "source_deleted", "Source deleted"
        SUPERSEDED = "superseded", "Superseded"
        FAILED = "failed", "Failed"

    class StorageClass(models.TextChoices):
        STANDARD = "STANDARD", "S3 queryable"
        GLACIER = "GLACIER", "S3 Glacier"

    generation = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    site = models.ForeignKey(
        TrackedSite,
        on_delete=models.SET_NULL,
        related_name="archive_partitions",
        null=True,
        blank=True,
    )
    site_id_snapshot = models.PositiveBigIntegerField(db_index=True)
    stream = models.CharField(max_length=16, choices=Stream.choices)
    range_start = models.DateTimeField()
    range_end = models.DateTimeField()
    timezone = models.CharField(max_length=64)
    schema_version = models.PositiveSmallIntegerField(default=1)
    object_key = models.CharField(max_length=1024, blank=True)
    object_version = models.CharField(max_length=255, blank=True)
    storage_class = models.CharField(
        max_length=24,
        choices=StorageClass.choices,
        default=StorageClass.STANDARD,
    )
    row_count = models.PositiveBigIntegerField(default=0)
    min_event_id = models.PositiveBigIntegerField(null=True, blank=True)
    max_event_id = models.PositiveBigIntegerField(null=True, blank=True)
    min_occurred_at = models.DateTimeField(null=True, blank=True)
    max_occurred_at = models.DateTimeField(null=True, blank=True)
    verification_sha256 = models.CharField(max_length=64, blank=True)
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.PENDING,
    )
    error_message = models.CharField(max_length=500, blank=True)
    exported_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    source_deleted_at = models.DateTimeField(null=True, blank=True)
    transitioned_at = models.DateTimeField(null=True, blank=True)
    restore_requested_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["range_start", "site_id_snapshot", "stream"]
        constraints = [
            models.UniqueConstraint(
                fields=["site_id_snapshot", "stream", "range_start", "range_end"],
                name="archive_site_stream_range_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(range_end__gt=models.F("range_start")),
                name="archive_range_ordered",
            ),
        ]
        indexes = [
            models.Index(
                fields=["status", "range_end"],
                name="archive_status_end_idx",
            ),
            models.Index(
                fields=["site_id_snapshot", "stream", "range_start"],
                name="archive_site_stream_idx",
            ),
        ]

    def __str__(self):
        return f"{self.site_id_snapshot}:{self.stream}:{self.range_start:%Y-%m} ({self.status})"


class DailyAnalyticsRollup(models.Model):
    site = models.ForeignKey(
        TrackedSite,
        on_delete=models.CASCADE,
        related_name="daily_analytics_rollups",
    )
    day = models.DateField()
    timezone = models.CharField(max_length=64)
    bucket_start = models.DateTimeField()
    bucket_end = models.DateTimeField()
    event_count = models.PositiveBigIntegerField(default=0)
    browser_event_count = models.PositiveBigIntegerField(default=0)
    pageview_count = models.PositiveBigIntegerField(default=0)
    identified_event_count = models.PositiveBigIntegerField(default=0)
    automated_event_count = models.PositiveBigIntegerField(default=0)
    metric_value_sum = models.DecimalField(
        max_digits=30,
        decimal_places=6,
        null=True,
        blank=True,
    )
    metric_value_count = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["site", "day"],
                name="daily_analytics_site_day_uniq",
            )
        ]
        indexes = [models.Index(fields=["site", "day"], name="daily_analytics_day_idx")]


class DailyDimensionRollup(models.Model):
    site = models.ForeignKey(
        TrackedSite,
        on_delete=models.CASCADE,
        related_name="daily_dimension_rollups",
    )
    day = models.DateField()
    timezone = models.CharField(max_length=64)
    bucket_start = models.DateTimeField()
    bucket_end = models.DateTimeField()
    dimension = models.CharField(max_length=32)
    label = models.CharField(max_length=2048)
    event_count = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["site", "day", "dimension", "label"],
                name="daily_dimension_row_uniq",
            )
        ]
        indexes = [
            models.Index(
                fields=["site", "dimension", "day"],
                name="daily_dimension_query_idx",
            )
        ]


class DailyProductEventRollup(models.Model):
    site = models.ForeignKey(
        TrackedSite,
        on_delete=models.CASCADE,
        related_name="daily_product_rollups",
    )
    day = models.DateField()
    timezone = models.CharField(max_length=64)
    bucket_start = models.DateTimeField()
    bucket_end = models.DateTimeField()
    event_name = models.CharField(max_length=64)
    event_count = models.PositiveBigIntegerField(default=0)
    identified_event_count = models.PositiveBigIntegerField(default=0)
    value_sum = models.DecimalField(
        max_digits=30,
        decimal_places=6,
        null=True,
        blank=True,
    )
    value_count = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["site", "day", "event_name"],
                name="daily_product_event_uniq",
            )
        ]
        indexes = [
            models.Index(
                fields=["site", "event_name", "day"],
                name="daily_product_query_idx",
            )
        ]


class DailyBotRollup(models.Model):
    site = models.ForeignKey(
        TrackedSite,
        on_delete=models.CASCADE,
        related_name="daily_bot_rollups",
    )
    day = models.DateField()
    timezone = models.CharField(max_length=64)
    bucket_start = models.DateTimeField()
    bucket_end = models.DateTimeField()
    dimension = models.CharField(max_length=24)
    label = models.CharField(max_length=2048)
    secondary_label = models.CharField(max_length=255, blank=True)
    event_count = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["site", "day", "dimension", "label", "secondary_label"],
                name="daily_bot_dimension_uniq",
            )
        ]
        indexes = [
            models.Index(
                fields=["site", "dimension", "day"],
                name="daily_bot_query_idx",
            )
        ]


class HistoricalReportCache(models.Model):
    site = models.ForeignKey(
        TrackedSite,
        on_delete=models.CASCADE,
        related_name="historical_report_cache",
        null=True,
        blank=True,
    )
    site_selector = models.CharField(max_length=80)
    report_type = models.CharField(max_length=32)
    period = models.CharField(max_length=16)
    parameters_hash = models.CharField(max_length=64)
    parameters = models.JSONField(default=dict)
    result = models.JSONField(default=dict)
    archive_generation = models.CharField(max_length=64)
    generated_at = models.DateTimeField()
    expires_at = models.DateTimeField(db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["site_selector", "report_type", "period", "parameters_hash"],
                name="historical_report_cache_uniq",
            )
        ]
        indexes = [
            models.Index(
                fields=["report_type", "period", "expires_at"],
                name="historical_cache_query_idx",
            )
        ]


class ColdDataTombstone(models.Model):
    class Kind(models.TextChoices):
        ACTOR = "actor", "Actor"
        SITE = "site", "Site"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSED = "processed", "Processed"
        FAILED = "failed", "Failed"

    site = models.ForeignKey(
        TrackedSite,
        on_delete=models.SET_NULL,
        related_name="cold_data_tombstones",
        null=True,
        blank=True,
    )
    site_id_snapshot = models.PositiveBigIntegerField(db_index=True)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    actor_hash = models.CharField(max_length=64, blank=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    error_message = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["site_id_snapshot", "status", "created_at"],
                name="cold_tombstone_pending_idx",
            )
        ]


class ColdDeletionJob(models.Model):
    class Kind(models.TextChoices):
        ACTOR = "actor", "Actor"
        SITE = "site", "Site"

    class Status(models.TextChoices):
        ACCEPTED = "accepted", "Accepted"
        RUNNING = "running", "Running"
        WAITING_RESTORE = "waiting_restore", "Waiting for Glacier restore"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        UNKNOWN = "unknown", "Unknown"

    request_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    site = models.ForeignKey(
        TrackedSite,
        on_delete=models.SET_NULL,
        related_name="cold_deletion_jobs",
        null=True,
        blank=True,
    )
    site_id_snapshot = models.PositiveBigIntegerField(db_index=True)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.ACTOR)
    actor_hash = models.CharField(max_length=64, blank=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACCEPTED,
    )
    deleted_hot_events = models.PositiveBigIntegerField(default=0)
    rewritten_partitions = models.PositiveIntegerField(default=0)
    deleted_cold_events = models.PositiveBigIntegerField(default=0)
    error_message = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["status", "created_at"],
                name="cold_deletion_status_idx",
            )
        ]
