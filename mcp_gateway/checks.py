from urllib.parse import urlsplit

from django.conf import settings
from django.core.checks import Error, Tags, register


@register(Tags.security, deploy=True)
def check_mcp_oauth_secrets(app_configs, **kwargs):
    errors = []
    if (
        not getattr(settings, "SITEHITS_MCP_TOKEN_SECRET_EXPLICIT", False)
        or settings.SITEHITS_MCP_TOKEN_SECRET == settings.SECRET_KEY
        or len(settings.SITEHITS_MCP_TOKEN_SECRET) < 32
    ):
        errors.append(
            Error(
                "SITEHITS_MCP_TOKEN_SECRET is not an independent production secret.",
                hint=(
                    "Set SITEHITS_MCP_TOKEN_SECRET to a random value of at least 32 characters; "
                    "changing it invalidates existing OAuth artifacts."
                ),
                id="mcp_gateway.E001",
            )
        )
    if settings.SITEHITS_MCP_ALLOW_LEGACY_TOKENS:
        errors.append(
            Error(
                "Legacy static MCP bearer tokens are enabled.",
                hint=(
                    "Set SITEHITS_MCP_ALLOW_LEGACY_TOKENS=false after any time-bounded "
                    "migration and use OAuth for distributed clients."
                ),
                id="mcp_gateway.E002",
            )
        )
    for setting_name in (
        "SITEHITS_MCP_ISSUER_URL",
        "SITEHITS_MCP_RESOURCE_URL",
        "SITEHITS_MCP_DOCUMENTATION_URL",
    ):
        if urlsplit(getattr(settings, setting_name)).scheme != "https":
            errors.append(
                Error(
                    f"{setting_name} does not use HTTPS.",
                    hint="OAuth endpoints and metadata must use externally reachable HTTPS URLs.",
                    id="mcp_gateway.E003",
                )
            )
    return errors
