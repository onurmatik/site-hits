from django.contrib import admin

from .models import AnalyticsEvent, BotEvent


@admin.register(AnalyticsEvent)
class AnalyticsEventAdmin(admin.ModelAdmin):
    list_display = (
        "occurred_at",
        "site",
        "event_type",
        "event_name",
        "path",
        "country_code",
        "city_name",
        "device",
    )
    list_filter = ("site", "event_type", "country_code", "device")
    search_fields = (
        "path",
        "event_name",
        "referrer_domain",
        "utm_campaign",
        "region_name",
        "city_name",
    )
    date_hierarchy = "occurred_at"
    readonly_fields = [field.name for field in AnalyticsEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(BotEvent)
class BotEventAdmin(admin.ModelAdmin):
    list_display = (
        "occurred_at",
        "site",
        "provider",
        "crawler",
        "category",
        "path",
        "status_code",
        "verification",
    )
    list_filter = ("site", "category", "provider", "verification", "status_code")
    search_fields = ("path", "provider", "crawler")
    date_hierarchy = "occurred_at"
    readonly_fields = [field.name for field in BotEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
