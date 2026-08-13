from urllib.parse import urlsplit

from django.conf import settings
from django.core.checks import Error, Tags, register
from django.db import DatabaseError, connections


def _stage1_database_errors(database):
    if database.vendor != "postgresql":
        return [
            Error(
                "Stage 1 production is not using PostgreSQL.",
                hint="Configure the production DATABASE_URL for PostgreSQL 17.",
                id="mcp_gateway.E004",
            )
        ]
    try:
        major_version = database.pg_version // 10_000
    except DatabaseError:
        return [
            Error(
                "The Stage 1 production database version could not be verified.",
                hint="Verify PostgreSQL connectivity before starting the MCP release.",
                id="mcp_gateway.E005",
            )
        ]
    if major_version != 17:
        return [
            Error(
                f"Stage 1 production requires PostgreSQL 17; found major {major_version}.",
                hint="Use the PostgreSQL 17 engine exercised by the release concurrency suite.",
                id="mcp_gateway.E006",
            )
        ]
    return []


@register(Tags.database, deploy=True)
def check_stage1_database(app_configs, **kwargs):
    return _stage1_database_errors(connections["default"])


@register(Tags.security, "mcp_oauth", deploy=True)
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
                    "use it only for security-event and rate-limit pseudonyms."
                ),
                id="mcp_gateway.E001",
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
