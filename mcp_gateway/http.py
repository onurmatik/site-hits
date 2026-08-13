"""Stage 1 HTTP policy shared by the Django OAuth and MCP ASGI processes."""

from __future__ import annotations

import json
import re
from typing import ClassVar
from urllib.parse import urlsplit
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django_embedded_mcp.challenges import build_auth_failure_challenge
from django_embedded_mcp.http import HeaderOnlyBearerMiddleware
from django_embedded_mcp.metadata import (
    build_authorization_server_metadata,
    build_protected_resource_metadata,
)
from django_embedded_mcp.resource import validate_canonical_url
from starlette.middleware.cors import CORSMiddleware

_REQUEST_ID_PATTERN = re.compile(
    r"^(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)
def protected_resource_metadata_url() -> str:
    """Return the configured RFC 9728 metadata URL without normalization."""

    return settings.MCP_RESOURCE_METADATA_URL


def authorization_server_metadata() -> dict[str, object]:
    """Advertise only the OAuth profile actually exposed by Django."""

    return build_authorization_server_metadata(
        issuer=settings.SITEHITS_MCP_ISSUER_URL,
        scopes_supported=settings.SITEHITS_MCP_OAUTH_SCOPES,
        service_documentation=settings.SITEHITS_MCP_DOCUMENTATION_URL,
        client_id_metadata_document_supported=settings.SITEHITS_MCP_CIMD_ENABLED,
        dynamic_client_registration_supported=True,
    )


def protected_resource_metadata() -> dict[str, object]:
    """Return canonical protected-resource metadata for both well-known paths."""

    return build_protected_resource_metadata(
        resource=settings.SITEHITS_MCP_RESOURCE_URL,
        authorization_server=settings.SITEHITS_MCP_ISSUER_URL,
        scopes_supported=settings.SITEHITS_MCP_OAUTH_SCOPES,
        resource_name="SiteHits analytics MCP",
        resource_documentation=settings.SITEHITS_MCP_DOCUMENTATION_URL,
    )


def validate_oauth_configuration() -> None:
    """Fail startup if the public OAuth identity is not byte-stable and coherent."""

    try:
        base = validate_canonical_url(
            settings.SITEHITS_BASE_URL,
            require_https=not settings.DEBUG,
            allow_root_path=True,
        )
        issuer = validate_canonical_url(
            settings.SITEHITS_MCP_ISSUER_URL,
            require_https=not settings.DEBUG,
            allow_root_path=True,
        )
        resource = validate_canonical_url(
            settings.SITEHITS_MCP_RESOURCE_URL,
            require_https=not settings.DEBUG,
            allow_root_path=False,
        )
        metadata = validate_canonical_url(
            settings.MCP_RESOURCE_METADATA_URL,
            require_https=not settings.DEBUG,
            allow_root_path=False,
        )
    except ValueError as exc:
        raise ImproperlyConfigured(str(exc)) from exc

    if base != issuer:
        raise ImproperlyConfigured(
            "SITEHITS_BASE_URL and SITEHITS_MCP_ISSUER_URL must be byte-identical."
        )
    base_parts = urlsplit(base)
    resource_parts = urlsplit(resource)
    metadata_parts = urlsplit(metadata)
    origin = (base_parts.scheme, base_parts.netloc)
    if (resource_parts.scheme, resource_parts.netloc) != origin:
        raise ImproperlyConfigured("The MCP resource must share the OAuth issuer origin.")
    if (metadata_parts.scheme, metadata_parts.netloc) != origin:
        raise ImproperlyConfigured("Protected-resource metadata must share the issuer origin.")
    if resource_parts.path != "/mcp":
        raise ImproperlyConfigured("SITEHITS_MCP_RESOURCE_URL must use the exact /mcp path.")
    if metadata_parts.path != "/.well-known/oauth-protected-resource/mcp":
        raise ImproperlyConfigured("MCP_RESOURCE_METADATA_URL has an unexpected path.")
    if not settings.SITEHITS_MCP_CIMD_ENABLED:
        raise ImproperlyConfigured("SiteHits requires CIMD-first client registration.")
    if settings.OAUTH2_PROVIDER.get("CIMD_ENABLED"):
        raise ImproperlyConfigured(
            "DOT's independent CIMD resolver must remain disabled; use django-embedded-mcp."
        )
    if not settings.OAUTH2_PROVIDER.get("DCR_ENABLED"):
        raise ImproperlyConfigured("DCR must remain enabled as the compatibility fallback.")
    if not (
        0 < settings.SITEHITS_MCP_CIMD_FETCH_TIMEOUT_SECONDS <= 5
        and 0 < settings.SITEHITS_MCP_CIMD_MAX_DOCUMENT_BYTES <= 16 * 1024
        and 0 < settings.SITEHITS_MCP_CIMD_MIN_CACHE_SECONDS
        <= settings.SITEHITS_MCP_CIMD_MAX_CACHE_SECONDS
        <= 24 * 60 * 60
        and 0 < settings.SITEHITS_MCP_CIMD_MAX_CONCURRENT_FETCHES <= 32
    ):
        raise ImproperlyConfigured("CIMD fetch and cache safety limits are invalid.")


def _challenge(*, status: int, credential_present: bool) -> str:
    return build_auth_failure_challenge(
        resource_metadata=protected_resource_metadata_url(),
        scopes=settings.SITEHITS_MCP_BOOTSTRAP_SCOPES,
        status=status,
        credential_present=credential_present,
    )


async def _send_json(send, status: int, payload: dict[str, object], headers=()) -> None:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                *headers,
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class PublicMetadataMiddleware:
    """Serve identical wildcard-CORS metadata from either production process."""

    _handlers: ClassVar = {
        "/.well-known/oauth-authorization-server": authorization_server_metadata,
        "/.well-known/oauth-protected-resource": protected_resource_metadata,
        "/.well-known/oauth-protected-resource/mcp": protected_resource_metadata,
    }

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] not in self._handlers:
            await self.app(scope, receive, send)
            return
        if scope["method"] == "OPTIONS":
            await _send_json(
                send,
                204,
                {},
                headers=(
                    (b"access-control-allow-origin", b"*"),
                    (b"access-control-allow-methods", b"GET, OPTIONS"),
                    (b"access-control-max-age", b"300"),
                    (b"cache-control", b"public, max-age=300"),
                ),
            )
            return
        if scope["method"] != "GET":
            await _send_json(send, 405, {"error": "method_not_allowed"})
            return
        await _send_json(
            send,
            200,
            self._handlers[scope["path"]](),
            headers=(
                (b"access-control-allow-origin", b"*"),
                (b"cache-control", b"public, max-age=300"),
            ),
        )


class RequestCorrelationMiddleware:
    """Accept proxy correlation only from trusted peers; otherwise create a public ID."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = list(scope.get("headers", []))
        direct_peer = (scope.get("client") or ("", 0))[0]
        proxy_marker = any(
            key.lower() == b"x-sitehits-trusted-proxy" and value == b"1"
            for key, value in headers
        )
        # Uvicorn validates the direct peer and then rewrites ``scope.client``
        # from X-Forwarded-For. In that production mode the external address is
        # expected here, while nginx has already overwritten X-Request-ID.
        trusted = (
            settings.SITEHITS_TRUST_PROXY_HEADERS and proxy_marker
        ) or direct_peer in set(settings.SITEHITS_TRUSTED_PROXY_IPS)
        candidate = ""
        if trusted:
            candidate = next(
                (
                    value.decode("ascii", "ignore")
                    for key, value in headers
                    if key.lower() == b"x-request-id"
                ),
                "",
            )
        request_id = candidate if _REQUEST_ID_PATTERN.fullmatch(candidate) else uuid4().hex
        headers = [
            (key, value)
            for key, value in headers
            if key.lower() not in {b"x-request-id", b"x-sitehits-trusted-proxy"}
        ]
        headers.append((b"x-request-id", request_id.encode("ascii")))
        next_scope = {**scope, "headers": headers}

        async def send_with_request_id(message):
            if message["type"] == "http.response.start":
                response_headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"x-request-id"
                ]
                response_headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": response_headers}
            await send(message)

        await self.app(next_scope, receive, send_with_request_id)


class MCPChallengeMiddleware:
    """Adapt SDK auth failures to the Stage 1 discovery/invalid-token distinction."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != "/mcp":
            await self.app(scope, receive, send)
            return
        credential_present = any(
            key.lower() == b"authorization" and value.lower().startswith(b"bearer ")
            for key, value in scope.get("headers", [])
        )

        async def send_with_challenge(message):
            if message["type"] == "http.response.start" and message["status"] in {401, 403}:
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() not in {b"www-authenticate", b"cache-control"}
                ]
                headers.extend(
                    (
                        (
                            b"www-authenticate",
                            _challenge(
                                status=message["status"],
                                credential_present=credential_present,
                            ).encode("ascii"),
                        ),
                        (b"cache-control", b"no-store"),
                    )
                )
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_challenge)


class MCPPathCORSMiddleware:
    """Apply the explicit browser-origin allowlist to /mcp only."""

    def __init__(self, app):
        self.app = app
        self.cors_app = CORSMiddleware(
            app,
            allow_origins=list(settings.SITEHITS_MCP_CORS_ORIGINS),
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=[
                "Accept",
                "Authorization",
                "Content-Type",
                "MCP-Protocol-Version",
                "MCP-Session-Id",
                "Mcp-Method",
                "Mcp-Name",
                "X-Request-ID",
            ],
            expose_headers=["WWW-Authenticate", "MCP-Session-Id", "X-Request-ID"],
            max_age=600,
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] == "/mcp":
            async def send_with_exposed_headers(message):
                if message["type"] == "http.response.start":
                    headers = [
                        (key, value)
                        for key, value in message.get("headers", [])
                        if key.lower() != b"access-control-expose-headers"
                    ]
                    headers.append(
                        (
                            b"access-control-expose-headers",
                            b"WWW-Authenticate, MCP-Session-Id, X-Request-ID",
                        )
                    )
                    message = {**message, "headers": headers}
                await send(message)

            await self.cors_app(scope, receive, send_with_exposed_headers)
            return
        await self.app(scope, receive, send)


def build_mcp_application(app):
    """Wrap the SDK resource app in the normative Stage 1 middleware order."""

    validate_oauth_configuration()
    wrapped = MCPChallengeMiddleware(app)
    wrapped = HeaderOnlyBearerMiddleware(
        wrapped,
        path="/mcp",
        invalid_token_challenge=lambda: _challenge(
            status=401,
            credential_present=True,
        ),
    )
    wrapped = RequestCorrelationMiddleware(wrapped)
    wrapped = MCPPathCORSMiddleware(wrapped)
    return PublicMetadataMiddleware(wrapped)
