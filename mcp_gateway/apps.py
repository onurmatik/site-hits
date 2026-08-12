from django.apps import AppConfig


class MCPGatewayConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "mcp_gateway"
    verbose_name = "MCP gateway"

    def ready(self):
        from . import checks  # noqa: F401
        from .http import validate_oauth_configuration
        from .versioning import validate_version_contract

        validate_version_contract()
        validate_oauth_configuration()
