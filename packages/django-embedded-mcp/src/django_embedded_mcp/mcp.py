"""Stable configuration seam for the pinned MCP Python SDK."""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit

from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings

from .metadata import validated_scope_catalog


def build_mcp_auth_settings(
    *,
    issuer_url: str,
    resource_server_url: str,
    required_scopes: Iterable[str],
    service_documentation_url: str | None = None,
) -> AuthSettings:
    """Bind product configuration to the SDK's public authentication settings."""

    return AuthSettings(
        issuer_url=issuer_url,
        resource_server_url=resource_server_url,
        service_documentation_url=service_documentation_url,
        required_scopes=list(validated_scope_catalog(required_scopes)),
    )


def build_transport_security_settings(
    *,
    resource_url: str,
    allowed_origins: Iterable[str],
    production: bool,
) -> TransportSecuritySettings:
    """Build explicit DNS-rebinding and browser-origin protection settings."""

    resource = urlsplit(resource_url)
    if not resource.netloc:
        raise ValueError("The MCP resource URL must have an authority.")
    origins = list(dict.fromkeys(allowed_origins))
    if not origins:
        raise ValueError("The MCP browser-origin allowlist must not be empty.")
    if production and "*" in origins:
        raise ValueError("The MCP browser origins must be an explicit production allowlist.")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[resource.netloc],
        allowed_origins=origins,
    )
