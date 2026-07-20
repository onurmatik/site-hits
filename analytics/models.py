from django.db import models
from django.core.exceptions import ValidationError

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
