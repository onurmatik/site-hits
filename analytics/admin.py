from django.contrib import admin

from .models import AnalyticsEvent


@admin.register(AnalyticsEvent)
class AnalyticsEventAdmin(admin.ModelAdmin):
    list_display = (
        "occurred_at",
        "site",
        "event_type",
        "event_name",
        "path",
        "country_code",
        "device",
    )
    list_filter = ("site", "event_type", "country_code", "device")
    search_fields = ("path", "event_name", "referrer_domain", "utm_campaign")
    date_hierarchy = "occurred_at"
    readonly_fields = [field.name for field in AnalyticsEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

