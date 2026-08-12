from django.contrib import admin

from .models import (
    MCPAccessToken,
    MCPOAuthAccessToken,
    MCPOAuthAuthorizationCode,
    MCPOAuthAuthorizationRequest,
    MCPOAuthClient,
    MCPOAuthRefreshToken,
)


@admin.register(MCPAccessToken)
class MCPAccessTokenAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "user",
        "prefix",
        "created_at",
        "last_used_at",
        "expires_at",
        "revoked_at",
    )
    list_filter = ("created_at", "revoked_at")
    search_fields = ("name", "prefix", "user__email", "user__username")
    readonly_fields = ("prefix", "token_digest", "created_at", "last_used_at")


@admin.register(MCPOAuthClient)
class MCPOAuthClientAdmin(admin.ModelAdmin):
    list_display = ("client_id", "client_name", "created_at", "revoked_at")
    search_fields = ("client_id",)
    readonly_fields = ("client_id", "metadata", "created_at")

    @admin.display(description="Client name")
    def client_name(self, obj):
        return obj.metadata.get("client_name", "")


class SensitiveOAuthArtifactAdmin(admin.ModelAdmin):
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(MCPOAuthAuthorizationRequest)
class MCPOAuthAuthorizationRequestAdmin(SensitiveOAuthArtifactAdmin):
    list_display = ("id", "client", "created_at", "expires_at", "resolved_at")
    readonly_fields = tuple(field.name for field in MCPOAuthAuthorizationRequest._meta.fields)


@admin.register(MCPOAuthAuthorizationCode)
class MCPOAuthAuthorizationCodeAdmin(SensitiveOAuthArtifactAdmin):
    list_display = ("prefix", "client", "user", "created_at", "expires_at", "consumed_at")
    readonly_fields = tuple(field.name for field in MCPOAuthAuthorizationCode._meta.fields)


@admin.register(MCPOAuthRefreshToken)
class MCPOAuthRefreshTokenAdmin(SensitiveOAuthArtifactAdmin):
    list_display = ("prefix", "client", "user", "created_at", "used_at", "revoked_at")
    readonly_fields = tuple(field.name for field in MCPOAuthRefreshToken._meta.fields)


@admin.register(MCPOAuthAccessToken)
class MCPOAuthAccessTokenAdmin(SensitiveOAuthArtifactAdmin):
    list_display = (
        "prefix",
        "client",
        "user",
        "created_at",
        "expires_at",
        "last_used_at",
        "revoked_at",
    )
    readonly_fields = tuple(field.name for field in MCPOAuthAccessToken._meta.fields)
