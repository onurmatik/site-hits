import secrets
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


def generate_public_key():
    return f"sh_{secrets.token_urlsafe(18)}"


class TrackedSite(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="tracked_sites",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=80, unique=True)
    public_key = models.CharField(
        max_length=64,
        unique=True,
        default=generate_public_key,
        editable=False,
    )
    allowed_domains = models.JSONField(
        default=list,
        help_text="Exact hostnames or *.example.com wildcards. Do not include schemes or paths.",
    )
    timezone = models.CharField(max_length=64, default="Europe/Istanbul")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValidationError({"timezone": "Enter a valid IANA timezone."}) from exc

        if not isinstance(self.allowed_domains, list) or not self.allowed_domains:
            raise ValidationError({"allowed_domains": "Add at least one allowed domain."})

        normalized = []
        for domain in self.allowed_domains:
            if not isinstance(domain, str):
                raise ValidationError({"allowed_domains": "Domains must be strings."})
            value = domain.strip().lower().rstrip(".")
            if not value or "://" in value or "/" in value or ":" in value:
                raise ValidationError(
                    {"allowed_domains": f"Invalid hostname pattern: {domain!r}."}
                )
            normalized.append(value)
        self.allowed_domains = sorted(set(normalized))
