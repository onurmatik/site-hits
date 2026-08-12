import json
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Mount

OAUTH_SCOPES = ("read", "write")
PROTECTED_RESOURCE_SCOPES = OAUTH_SCOPES
OAUTH_PATHS = {
    "/authorize",
    "/token",
    "/register",
    "/revoke",
}
PUBLIC_HTTP_PATHS = OAUTH_PATHS | {
    "/mcp",
    "/agent-manifest.json",
    "/.well-known/oauth-authorization-server",
    "/.well-known/oauth-protected-resource/mcp",
}


def canonical_resource_url(value):
    """Normalize only the URI components MCP asks servers to compare case-insensitively."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError("resource must be a valid absolute URL") from exc
    if (
        parsed.scheme not in {"http", "https", "HTTP", "HTTPS"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("resource must be an absolute HTTP URL without credentials, query, or fragment")

    hostname = parsed.hostname.lower()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    netloc = f"{hostname}:{port}" if port is not None else hostname
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", "", ""))


def protected_resource_metadata_url():
    resource = urlsplit(settings.SITEHITS_MCP_RESOURCE_URL)
    path = "" if resource.path == "/" else resource.path
    return urlunsplit(
        (
            resource.scheme,
            resource.netloc,
            f"/.well-known/oauth-protected-resource{path}",
            "",
            "",
        )
    )


def validate_oauth_configuration():
    try:
        canonical = canonical_resource_url(settings.SITEHITS_MCP_RESOURCE_URL)
    except ValueError as exc:
        raise ImproperlyConfigured(f"Invalid SITEHITS_MCP_RESOURCE_URL: {exc}") from exc
    if canonical != settings.SITEHITS_MCP_RESOURCE_URL:
        raise ImproperlyConfigured(
            "SITEHITS_MCP_RESOURCE_URL must already be canonical and must not end in '/'."
        )

    for name in ("SITEHITS_MCP_ISSUER_URL", "SITEHITS_MCP_DOCUMENTATION_URL"):
        value = getattr(settings, name)
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ImproperlyConfigured(f"{name} must be an absolute HTTP URL.")
        if parsed.query or parsed.fragment:
            raise ImproperlyConfigured(f"{name} must not contain a query or fragment.")
        if (
            not settings.DEBUG
            and getattr(settings, f"{name}_EXPLICIT", True)
            and parsed.scheme != "https"
        ):
            raise ImproperlyConfigured(f"{name} must use HTTPS outside local development.")
    issuer_path = urlsplit(settings.SITEHITS_MCP_ISSUER_URL).path
    if issuer_path not in {"", "/"}:
        raise ImproperlyConfigured(
            "SITEHITS_MCP_ISSUER_URL must be an origin without a path; OAuth routes are root-mounted."
        )
    if (
        not settings.DEBUG
        and settings.SITEHITS_MCP_RESOURCE_URL_EXPLICIT
        and urlsplit(settings.SITEHITS_MCP_RESOURCE_URL).scheme != "https"
    ):
        raise ImproperlyConfigured(
            "SITEHITS_MCP_RESOURCE_URL must use HTTPS outside local development."
        )
    for setting_name in (
        "SITEHITS_MCP_ACCESS_TOKEN_TTL_SECONDS",
        "SITEHITS_MCP_REFRESH_TOKEN_TTL_SECONDS",
        "SITEHITS_MCP_AUTHORIZATION_CODE_TTL_SECONDS",
        "SITEHITS_MCP_AUTHORIZATION_REQUEST_TTL_SECONDS",
    ):
        if getattr(settings, setting_name) <= 0:
            raise ImproperlyConfigured(f"{setting_name} must be positive.")


def authorization_server_metadata():
    issuer = settings.SITEHITS_MCP_ISSUER_URL
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "registration_endpoint": f"{issuer}/register",
        "revocation_endpoint": f"{issuer}/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "revocation_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": list(OAUTH_SCOPES),
        "service_documentation": settings.SITEHITS_MCP_DOCUMENTATION_URL,
    }


def protected_resource_metadata():
    return {
        "resource": settings.SITEHITS_MCP_RESOURCE_URL,
        "authorization_servers": [settings.SITEHITS_MCP_ISSUER_URL],
        "scopes_supported": list(PROTECTED_RESOURCE_SCOPES),
        "bearer_methods_supported": ["header"],
        "resource_name": "SiteHits analytics MCP",
        "resource_documentation": settings.SITEHITS_MCP_DOCUMENTATION_URL,
    }


async def _send_json(send, status, payload, headers=()):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    response_headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        *headers,
    ]
    await send({"type": "http.response.start", "status": status, "headers": response_headers})
    await send({"type": "http.response.body", "body": body})


class PublicMetadataMiddleware:
    def __init__(self, app):
        self.app = app
        self.handlers = {
            "/.well-known/oauth-authorization-server": authorization_server_metadata,
            protected_resource_metadata_url_path(): protected_resource_metadata,
        }

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] == "GET":
            handler = self.handlers.get(scope["path"])
            if handler is not None:
                await _send_json(
                    send,
                    200,
                    handler(),
                    headers=((b"cache-control", b"public, max-age=300"),),
                )
                return
        await self.app(scope, receive, send)


def protected_resource_metadata_url_path():
    return urlsplit(protected_resource_metadata_url()).path


class OAuthResourceParameterMiddleware:
    """Require one unique canonical RFC 8707 target while accepting safe duplicates."""

    def __init__(self, app, *, max_body_size=1_048_576):
        self.app = app
        self.expected = settings.SITEHITS_MCP_RESOURCE_URL
        self.max_body_size = max_body_size

    def _validated_params(self, pairs):
        resources = [value for key, value in pairs if key == "resource"]
        if not resources:
            raise ValueError("The resource parameter is required.")
        try:
            normalized = {canonical_resource_url(value) for value in resources}
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if normalized != {self.expected}:
            raise ValueError("The requested resource is not this MCP server.")
        return [(key, value) for key, value in pairs if key != "resource"] + [
            ("resource", self.expected)
        ]

    async def _invalid_target(self, send, description):
        await _send_json(
            send,
            400,
            {"error": "invalid_target", "error_description": description},
            headers=((b"cache-control", b"no-store"), (b"pragma", b"no-cache")),
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] not in {"/authorize", "/token"}:
            await self.app(scope, receive, send)
            return

        if scope["path"] == "/authorize" and scope["method"] == "GET":
            pairs = parse_qsl(scope.get("query_string", b"").decode("utf-8"), keep_blank_values=True)
            try:
                pairs = self._validated_params(pairs)
            except ValueError as exc:
                await self._invalid_target(send, str(exc))
                return
            next_scope = dict(scope)
            next_scope["query_string"] = urlencode(pairs).encode("utf-8")
            await self.app(next_scope, receive, send)
            return

        if scope["method"] != "POST":
            await self.app(scope, receive, send)
            return

        chunks = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_body_size:
                await self._invalid_target(send, "OAuth request body is too large.")
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        try:
            pairs = parse_qsl(body.decode("utf-8"), keep_blank_values=True)
            pairs = self._validated_params(pairs)
        except (UnicodeDecodeError, ValueError) as exc:
            await self._invalid_target(send, str(exc))
            return
        normalized_body = urlencode(pairs).encode("utf-8")
        delivered = False

        async def replay_receive():
            nonlocal delivered
            if delivered:
                return {"type": "http.disconnect"}
            delivered = True
            return {"type": "http.request", "body": normalized_body, "more_body": False}

        next_scope = dict(scope)
        next_headers = [
            (key, value)
            for key, value in scope.get("headers", [])
            if key.lower() != b"content-length"
        ]
        next_headers.append((b"content-length", str(len(normalized_body)).encode("ascii")))
        next_scope["headers"] = next_headers
        await self.app(next_scope, replay_receive, send)


class OAuthResponseHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] != "http.response.start":
                await send(message)
                return
            status = message["status"]
            headers = [
                (key, value)
                for key, value in message.get("headers", [])
                if key.lower()
                not in ({b"www-authenticate"} if scope["path"] == "/mcp" else set())
            ]
            if scope["path"] == "/mcp" and status in {401, 403}:
                authorization = next(
                    (
                        value
                        for key, value in scope.get("headers", [])
                        if key.lower() == b"authorization"
                    ),
                    None,
                )
                parts = [
                    f'resource_metadata="{protected_resource_metadata_url()}"',
                    'scope="read"',
                ]
                if status == 403:
                    parts.extend(
                        [
                            'error="insufficient_scope"',
                            'error_description="The token lacks a required scope"',
                        ]
                    )
                elif authorization is not None:
                    parts.extend(
                        [
                            'error="invalid_token"',
                            'error_description="The bearer token is invalid or expired"',
                        ]
                    )
                headers.append((b"www-authenticate", f"Bearer {', '.join(parts)}".encode()))
            if scope["path"] in OAUTH_PATHS:
                headers = [
                    (key, value)
                    for key, value in headers
                    if key.lower() not in {b"cache-control", b"pragma"}
                ]
                headers.extend(
                    [(b"cache-control", b"no-store"), (b"pragma", b"no-cache")]
                )
            await send({**message, "headers": headers})

        await self.app(scope, receive, send_with_headers)


class SelectiveCORSMiddleware:
    def __init__(self, app):
        self.app = app
        self.cors_app = CORSMiddleware(
            app,
            allow_origins=settings.SITEHITS_MCP_CORS_ORIGINS,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=[
                "Accept",
                "Authorization",
                "Content-Type",
                "MCP-Protocol-Version",
            ],
            expose_headers=["WWW-Authenticate", "MCP-Session-Id"],
            max_age=600,
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and (
            scope["path"] in PUBLIC_HTTP_PATHS
            or scope["path"] == protected_resource_metadata_url_path()
        ):
            await self.cors_app(scope, receive, send)
            return
        await self.app(scope, receive, send)


def build_application(mcp, django_application):
    validate_oauth_configuration()
    app = mcp.streamable_http_app()
    app.router.routes.append(Mount("/", app=django_application))
    wrapped = OAuthResponseHeadersMiddleware(app)
    wrapped = OAuthResourceParameterMiddleware(wrapped)
    wrapped = PublicMetadataMiddleware(wrapped)
    return SelectiveCORSMiddleware(wrapped)
