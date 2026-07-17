from django.db import models

from websites.models import TrackedSite


class AnalyticsEvent(models.Model):
    class EventType(models.TextChoices):
        PAGEVIEW = "pageview", "Pageview"
        CUSTOM = "custom", "Custom event"

    site = models.ForeignKey(TrackedSite, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=16, choices=EventType.choices)
    event_name = models.CharField(max_length=64, blank=True)
    occurred_at = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)
    visitor_hash = models.CharField(max_length=64)
    session_id = models.CharField(max_length=64)

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
                fields=["site", "automation_score", "occurred_at"],
                name="event_site_auto_idx",
            ),
        ]

    def __str__(self):
        label = self.event_name or self.event_type
        return f"{self.site}: {label} at {self.occurred_at.isoformat()}"


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
