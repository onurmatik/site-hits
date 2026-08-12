from django.apps import AppConfig


class MCPOAuthConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "mcp_oauth"
    verbose_name = "SiteHits OAuth"

    def ready(self):
        from . import signals  # noqa: F401
