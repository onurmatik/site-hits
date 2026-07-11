from django.conf import settings
from django.contrib import admin
from django.utils.html import format_html

from .models import TrackedSite


@admin.register(TrackedSite)
class TrackedSiteAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "domain_list", "timezone", "is_active", "updated_at")
    list_filter = ("is_active", "timezone")
    search_fields = ("name", "slug", "public_key")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("public_key", "tracking_snippet", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "slug", "is_active")}),
        ("Tracking", {"fields": ("allowed_domains", "timezone", "public_key", "tracking_snippet")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Domains")
    def domain_list(self, obj):
        return ", ".join(obj.allowed_domains)

    @admin.display(description="Install snippet")
    def tracking_snippet(self, obj):
        if not obj.pk:
            return "Save the site to generate its tracking snippet."
        base_url = settings.SITEHITS_BASE_URL
        snippet = (
            f'<script defer src="{base_url}/js/script.js" '
            f'data-site-key="{obj.public_key}" '
            f'data-api-url="{base_url}/api/events"></script>'
        )
        return format_html("<code style='white-space:pre-wrap'>{}</code>", snippet)
