from django.contrib import admin

from .models import ActivationDefinition, AnalyticsEvent, BotEvent, ProductEventDefinition


@admin.register(AnalyticsEvent)
class AnalyticsEventAdmin(admin.ModelAdmin):
    list_display = (
        "occurred_at",
        "site",
        "event_type",
        "event_name",
        "source",
        "path",
        "country_code",
        "city_name",
        "device",
    )
    list_filter = ("site", "event_type", "source", "country_code", "device")
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


@admin.register(ProductEventDefinition)
class ProductEventDefinitionAdmin(admin.ModelAdmin):
    list_display = ("display_name", "site", "event_name", "aggregation", "unit")
    list_filter = ("site", "aggregation")
    search_fields = ("display_name", "event_name", "description")


@admin.register(ActivationDefinition)
class ActivationDefinitionAdmin(admin.ModelAdmin):
    list_display = ("site", "start_event", "goal_event", "updated_at")
    list_filter = ("site",)


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
