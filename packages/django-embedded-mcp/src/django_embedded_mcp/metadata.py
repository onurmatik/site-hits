"""Deterministic OAuth metadata builders for the embedded MCP profile."""

from __future__ import annotations

from collections.abc import Iterable

from .resource import validate_canonical_url


def _validated_issuer(issuer: str) -> str:
    issuer = validate_canonical_url(
        issuer,
        require_https=False,
        allow_root_path=False,
    )
    if issuer.endswith("/"):
        raise ValueError("OAuth issuer must not end with a slash.")
    return issuer


def validated_scope_catalog(scopes: Iterable[str]) -> tuple[str, ...]:
    """Return an ordered, unique OAuth scope catalog without normalization."""

    catalog = tuple(scopes)
    if any(
        not isinstance(scope, str)
        or not scope
        or any(character.isspace() for character in scope)
        for scope in catalog
    ):
        raise ValueError("OAuth scope names must be non-empty and contain no whitespace.")
    if len(set(catalog)) != len(catalog):
        raise ValueError("OAuth scope names must be unique.")
    return catalog


def build_authorization_server_metadata(
    *,
    issuer: str,
    scopes_supported: Iterable[str] = (),
    service_documentation: str | None = None,
    client_id_metadata_document_supported: bool = False,
    dynamic_client_registration_supported: bool = True,
) -> dict[str, object]:
    """Build the exact public-client authorization-server profile."""

    issuer = _validated_issuer(issuer)
    scopes = validated_scope_catalog(scopes_supported)
    payload: dict[str, object] = {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/oauth/authorize/",
        "token_endpoint": f"{issuer}/oauth/token/",
        "revocation_endpoint": f"{issuer}/oauth/revoke/",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "revocation_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
        "authorization_response_iss_parameter_supported": True,
    }
    if client_id_metadata_document_supported:
        payload["client_id_metadata_document_supported"] = True
    if dynamic_client_registration_supported:
        payload["registration_endpoint"] = f"{issuer}/oauth/register/"
    if scopes:
        payload["scopes_supported"] = list(scopes)
    if service_documentation is not None:
        payload["service_documentation"] = service_documentation
    return payload


def build_protected_resource_metadata(
    *,
    resource: str,
    authorization_server: str,
    scopes_supported: Iterable[str] = (),
    resource_name: str | None = None,
    resource_documentation: str | None = None,
) -> dict[str, object]:
    """Build RFC 9728 metadata without rewriting the protected resource."""

    resource = validate_canonical_url(
        resource,
        require_https=False,
        allow_root_path=False,
    )
    authorization_server = _validated_issuer(authorization_server)
    scopes = validated_scope_catalog(scopes_supported)
    payload: dict[str, object] = {
        "resource": resource,
        "authorization_servers": [authorization_server],
        "bearer_methods_supported": ["header"],
    }
    if resource_name is not None:
        payload["resource_name"] = resource_name
    if resource_documentation is not None:
        payload["resource_documentation"] = resource_documentation
    if scopes:
        payload["scopes_supported"] = list(scopes)
    return payload
