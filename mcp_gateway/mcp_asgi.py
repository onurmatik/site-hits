"""Standalone ASGI entrypoint for the SiteHits MCP process."""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django_embedded_mcp.mcp import build_transport_security_settings
from mcp.server.transport_security import TransportSecuritySettings

from .http import build_mcp_application
from .server import mcp


def transport_security_settings() -> TransportSecuritySettings:
    try:
        return build_transport_security_settings(
            resource_url=settings.SITEHITS_MCP_RESOURCE_URL,
            allowed_origins=settings.SITEHITS_MCP_CORS_ORIGINS,
            production=not settings.DEBUG,
        )
    except ValueError as exc:
        raise ImproperlyConfigured(str(exc)) from exc


application = build_mcp_application(
    mcp.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=transport_security_settings(),
        host=settings.SITEHITS_MCP_HOST,
    )
)
