from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
from uuid import UUID, uuid4

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import (
    InvalidRedirectUriError,
    OAuthClientInformationFull,
    OAuthToken,
)
from pydantic import AnyUrl

from .models import (
    MCPAccessToken,
    MCPOAuthAccessToken,
    MCPOAuthAuthorizationCode,
    MCPOAuthAuthorizationRequest,
    MCPOAuthClient,
    MCPOAuthRefreshToken,
)

SITEHITS_OAUTH_SCOPES = frozenset({"read", "write"})
_PKCE_S256_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


class SiteHitsOAuthClientInformation(OAuthClientInformationFull):
    """OAuth client metadata with RFC 8252 loopback-port matching."""

    def validate_redirect_uri(self, redirect_uri: AnyUrl | None) -> AnyUrl:
        if redirect_uri is None:
            if self.redirect_uris is None or len(self.redirect_uris) != 1:
                raise InvalidRedirectUriError(
                    "redirect_uri must be specified when client has multiple registered URIs"
                )
            return self.redirect_uris[0]

        if self.redirect_uris is None:
            raise InvalidRedirectUriError(
                f"Redirect URI '{redirect_uri}' not registered for client"
            )

        requested = str(redirect_uri)
        for registered_uri in self.redirect_uris:
            registered = str(registered_uri)
            if requested == registered or _loopback_redirects_match(
                registered,
                requested,
            ):
                return redirect_uri

        raise InvalidRedirectUriError(
            f"Redirect URI '{redirect_uri}' not registered for client"
        )


class SiteHitsAuthorizationCode(AuthorizationCode):
    record_id: int


class SiteHitsRefreshToken(RefreshToken):
    record_id: int
    resource: str
    family_id: str
    was_used: bool = False


class SiteHitsAccessToken(AccessToken):
    record_id: int
    family_id: str | None = None
    legacy: bool = False


class ConsentRequestError(ValueError):
    """Raised when a persisted consent request cannot safely be resolved."""


@dataclass(frozen=True)
class _ExchangeResult:
    token: OAuthToken | None = None
    error: str | None = None


def _is_loopback_host(hostname: str | None) -> bool:
    if hostname is None:
        return False
    normalized = hostname.rstrip(".").lower()
    if normalized == "localhost":
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def _parse_redirect_uri(uri: str):
    try:
        parsed = urlsplit(uri)
        # Accessing .port also rejects malformed or out-of-range ports.
        _ = parsed.port
    except ValueError as exc:
        raise InvalidRedirectUriError(f"Invalid redirect URI '{uri}'") from exc
    return parsed


def _validate_registered_redirect_uri(uri: str) -> None:
    parsed = _parse_redirect_uri(uri)
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise InvalidRedirectUriError(
            "Redirect URIs must not contain credentials or fragments"
        )
    if parsed.scheme == "https" and parsed.hostname:
        return
    if parsed.scheme == "http" and parsed.hostname and _is_loopback_host(parsed.hostname):
        return
    raise InvalidRedirectUriError(
        "Redirect URIs must use HTTPS, except HTTP loopback redirects"
    )


def _loopback_redirects_match(registered_uri: str, requested_uri: str) -> bool:
    registered = _parse_redirect_uri(registered_uri)
    requested = _parse_redirect_uri(requested_uri)
    if registered.fragment or requested.fragment:
        return False
    if registered.username is not None or requested.username is not None:
        return False
    if registered.password is not None or requested.password is not None:
        return False
    if registered.scheme != "http" or requested.scheme != "http":
        return False
    if not _is_loopback_host(registered.hostname):
        return False
    if registered.hostname != requested.hostname:
        return False
    return (
        registered.scheme == requested.scheme
        and registered.path == requested.path
        and registered.query == requested.query
    )


def _canonical_resource(resource: str | None) -> str | None:
    if not resource:
        return None
    try:
        parsed = urlsplit(resource)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    hostname = parsed.hostname.lower()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    netloc = hostname if port is None else f"{hostname}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path, "", ""))


def _configured_resource() -> str:
    configured = _canonical_resource(settings.SITEHITS_MCP_RESOURCE_URL)
    if configured is None:  # pragma: no cover - guarded by deployment checks.
        raise RuntimeError("SITEHITS_MCP_RESOURCE_URL must be an absolute HTTP(S) URL")
    return configured


def _setting_seconds(name: str, default: int) -> int:
    return int(getattr(settings, name, default))


def _token_expiry_seconds(expires_at) -> int:
    return max(0, int((expires_at - timezone.now()).total_seconds()))


def _normalize_scopes(scopes: list[str] | None, *, default: list[str]) -> list[str]:
    requested = scopes if scopes is not None else default
    normalized = list(dict.fromkeys(requested))
    if not normalized:
        normalized = ["read"]
    if any(scope not in SITEHITS_OAUTH_SCOPES for scope in normalized):
        raise ValueError("One or more requested scopes are not supported")
    if "read" not in normalized:
        raise ValueError("The read scope is required")
    return normalized


def _client_from_record(record: MCPOAuthClient) -> SiteHitsOAuthClientInformation:
    metadata = dict(record.metadata)
    metadata["client_id"] = record.client_id
    metadata["client_secret"] = None
    metadata["token_endpoint_auth_method"] = "none"
    return SiteHitsOAuthClientInformation.model_validate(metadata)


def _authorization_code_from_record(
    record: MCPOAuthAuthorizationCode,
    raw_code: str,
) -> SiteHitsAuthorizationCode:
    return SiteHitsAuthorizationCode(
        code=raw_code,
        scopes=list(record.scopes),
        expires_at=record.expires_at.timestamp(),
        client_id=record.client.client_id,
        code_challenge=record.code_challenge,
        redirect_uri=record.redirect_uri,
        redirect_uri_provided_explicitly=(
            record.redirect_uri_provided_explicitly
        ),
        resource=record.resource,
        subject=str(record.user_id),
        record_id=record.pk,
    )


def _refresh_token_from_record(
    record: MCPOAuthRefreshToken,
    raw_token: str,
) -> SiteHitsRefreshToken:
    return SiteHitsRefreshToken(
        token=raw_token,
        client_id=record.client.client_id,
        scopes=list(record.scopes),
        # A rotated token remains loadable so its reuse can revoke the whole family,
        # even after the old token's nominal expiry. The exchange method rechecks the
        # authoritative database timestamps under a row lock.
        expires_at=(None if record.used_at is not None else int(record.expires_at.timestamp())),
        subject=str(record.user_id),
        record_id=record.pk,
        resource=record.resource,
        family_id=str(record.family_id),
        was_used=record.used_at is not None,
    )


def _oauth_access_token_from_record(
    record: MCPOAuthAccessToken,
    raw_token: str,
) -> SiteHitsAccessToken:
    return SiteHitsAccessToken(
        token=raw_token,
        client_id=record.client.client_id,
        scopes=list(record.scopes),
        expires_at=int(record.expires_at.timestamp()),
        resource=record.resource,
        subject=str(record.user_id),
        claims={
            "iss": settings.SITEHITS_MCP_ISSUER_URL,
            "aud": record.resource,
            "token_id": record.pk,
            "family_id": str(record.family_id),
        },
        record_id=record.pk,
        family_id=str(record.family_id),
    )


def _legacy_access_token_from_record(
    record: MCPAccessToken,
    raw_token: str,
) -> SiteHitsAccessToken:
    expires_at = (
        int(record.expires_at.timestamp()) if record.expires_at is not None else None
    )
    return SiteHitsAccessToken(
        token=raw_token,
        client_id=f"sitehits-legacy-token-{record.pk}",
        scopes=["read", "write"],
        expires_at=expires_at,
        resource=_configured_resource(),
        subject=str(record.user_id),
        claims={
            "iss": settings.SITEHITS_MCP_ISSUER_URL,
            "aud": _configured_resource(),
            "token_id": record.pk,
            "legacy": True,
        },
        record_id=record.pk,
        legacy=True,
    )


class DjangoOAuthProvider(
    OAuthAuthorizationServerProvider[
        SiteHitsAuthorizationCode,
        SiteHitsRefreshToken,
        SiteHitsAccessToken,
    ]
):
    """Django-backed OAuth 2.1 provider for the SiteHits MCP server."""

    async def get_client(
        self,
        client_id: str,
    ) -> SiteHitsOAuthClientInformation | None:
        return await sync_to_async(
            self._get_client,
            thread_sensitive=True,
        )(client_id)

    @staticmethod
    def _get_client(client_id: str) -> SiteHitsOAuthClientInformation | None:
        record = MCPOAuthClient.objects.filter(
            client_id=client_id,
            revoked_at__isnull=True,
        ).first()
        return _client_from_record(record) if record is not None else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        await sync_to_async(
            self._register_client,
            thread_sensitive=True,
        )(client_info)

    @staticmethod
    def _register_client(client_info: OAuthClientInformationFull) -> None:
        if (
            client_info.token_endpoint_auth_method != "none"
            or client_info.client_secret is not None
        ):
            raise RegistrationError(
                error="invalid_client_metadata",
                error_description=(
                    "SiteHits accepts public OAuth clients only; "
                    "token_endpoint_auth_method must be 'none'"
                ),
            )
        if not client_info.client_id:
            raise RegistrationError(
                error="invalid_client_metadata",
                error_description="client_id is required",
            )
        if set(client_info.grant_types) != {"authorization_code", "refresh_token"}:
            raise RegistrationError(
                error="invalid_client_metadata",
                error_description=(
                    "grant_types must contain only authorization_code and refresh_token"
                ),
            )
        if client_info.response_types != ["code"]:
            raise RegistrationError(
                error="invalid_client_metadata",
                error_description="response_types must contain only code",
            )
        if not client_info.redirect_uris:
            raise RegistrationError(
                error="invalid_redirect_uri",
                error_description="At least one redirect URI is required",
            )
        try:
            for redirect_uri in client_info.redirect_uris:
                _validate_registered_redirect_uri(str(redirect_uri))
        except InvalidRedirectUriError as exc:
            raise RegistrationError(
                error="invalid_redirect_uri",
                error_description=exc.message,
            ) from exc

        requested_scopes = (
            client_info.scope.split() if client_info.scope is not None else ["read"]
        )
        try:
            normalized_scopes = _normalize_scopes(
                requested_scopes,
                default=["read"],
            )
        except ValueError as exc:
            raise RegistrationError(
                error="invalid_client_metadata",
                error_description=str(exc),
            ) from exc
        client_info.scope = " ".join(normalized_scopes)

        metadata = client_info.model_dump(mode="json", exclude_none=True)
        metadata["client_secret"] = None
        metadata["token_endpoint_auth_method"] = "none"
        try:
            MCPOAuthClient.objects.create(
                client_id=client_info.client_id,
                metadata=metadata,
            )
        except IntegrityError as exc:  # pragma: no cover - UUID collision defense.
            raise RegistrationError(
                error="invalid_client_metadata",
                error_description="client_id is already registered",
            ) from exc

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        resource = _canonical_resource(params.resource)
        if resource != _configured_resource():
            raise AuthorizeError(
                error="invalid_request",
                error_description="resource must identify this SiteHits MCP server",
            )
        if not _PKCE_S256_PATTERN.fullmatch(params.code_challenge):
            raise AuthorizeError(
                error="invalid_request",
                error_description="A valid S256 PKCE code_challenge is required",
            )
        try:
            scopes = _normalize_scopes(params.scopes, default=["read"])
        except ValueError as exc:
            raise AuthorizeError(
                error="invalid_scope",
                error_description=str(exc),
            ) from exc

        consent_request_id = await sync_to_async(
            self._create_authorization_request,
            thread_sensitive=True,
        )(
            client_id=client.client_id,
            redirect_uri=str(params.redirect_uri),
            redirect_uri_provided_explicitly=(
                params.redirect_uri_provided_explicitly
            ),
            scopes=scopes,
            resource=resource,
            state=params.state or "",
            code_challenge=params.code_challenge,
        )
        consent_path = reverse("mcp-oauth-consent")
        consent_url = urljoin(
            f"{settings.SITEHITS_BASE_URL.rstrip('/')}/",
            consent_path.lstrip("/"),
        )
        return f"{consent_url}?{urlencode({'request': str(consent_request_id)})}"

    @staticmethod
    def _create_authorization_request(
        *,
        client_id: str | None,
        redirect_uri: str,
        redirect_uri_provided_explicitly: bool,
        scopes: list[str],
        resource: str,
        state: str,
        code_challenge: str,
    ) -> UUID:
        client = MCPOAuthClient.objects.filter(
            client_id=client_id,
            revoked_at__isnull=True,
        ).first()
        if client is None:
            raise AuthorizeError(
                error="unauthorized_client",
                error_description="OAuth client is no longer active",
            )
        request = MCPOAuthAuthorizationRequest.objects.create(
            client=client,
            redirect_uri=redirect_uri,
            redirect_uri_provided_explicitly=redirect_uri_provided_explicitly,
            scopes=scopes,
            resource=resource,
            state=state,
            code_challenge=code_challenge,
            expires_at=timezone.now()
            + timedelta(
                seconds=_setting_seconds(
                    "SITEHITS_MCP_AUTHORIZATION_REQUEST_TTL_SECONDS",
                    600,
                )
            ),
        )
        return request.pk

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> SiteHitsAuthorizationCode | None:
        return await sync_to_async(
            self._load_authorization_code,
            thread_sensitive=True,
        )(client.client_id, authorization_code)

    @staticmethod
    def _load_authorization_code(
        client_id: str | None,
        raw_code: str,
    ) -> SiteHitsAuthorizationCode | None:
        if not raw_code.startswith("shc_") or len(raw_code) < 24:
            return None
        record = (
            MCPOAuthAuthorizationCode.objects.select_related("client", "user")
            .filter(
                client__client_id=client_id,
                client__revoked_at__isnull=True,
                code_digest=MCPAccessToken.digest(raw_code),
                consumed_at__isnull=True,
            )
            .first()
        )
        if (
            record is None
            or not record.user.is_active
            or _canonical_resource(record.resource) != _configured_resource()
        ):
            return None
        return _authorization_code_from_record(record, raw_code)

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: SiteHitsAuthorizationCode,
    ) -> OAuthToken:
        result = await sync_to_async(
            self._exchange_authorization_code,
            thread_sensitive=True,
        )(client.client_id, authorization_code)
        if result.error:
            raise TokenError(error="invalid_grant", error_description=result.error)
        if result.token is None:  # pragma: no cover - defensive invariant.
            raise TokenError(error="invalid_grant", error_description="Token exchange failed")
        return result.token

    @staticmethod
    def _exchange_authorization_code(
        client_id: str | None,
        authorization_code: SiteHitsAuthorizationCode,
    ) -> _ExchangeResult:
        now = timezone.now()
        with transaction.atomic():
            record = (
                MCPOAuthAuthorizationCode.objects.select_for_update()
                .select_related("client", "user")
                .filter(
                    pk=authorization_code.record_id,
                    client__client_id=client_id,
                    client__revoked_at__isnull=True,
                    code_digest=MCPAccessToken.digest(authorization_code.code),
                )
                .first()
            )
            if (
                record is None
                or record.consumed_at is not None
                or record.expires_at <= now
                or not record.user.is_active
                or _canonical_resource(record.resource) != _configured_resource()
            ):
                return _ExchangeResult(error="Authorization code is invalid or expired")
            record.consumed_at = now
            record.save(update_fields=["consumed_at"])

            family_id = uuid4()
            _, raw_refresh_token = MCPOAuthRefreshToken.issue(
                user=record.user,
                client=record.client,
                scopes=list(record.scopes),
                resource=_configured_resource(),
                family_id=family_id,
            )
            access_record, raw_access_token = MCPOAuthAccessToken.issue(
                user=record.user,
                client=record.client,
                scopes=list(record.scopes),
                resource=_configured_resource(),
                family_id=family_id,
            )
        return _ExchangeResult(
            token=OAuthToken(
                access_token=raw_access_token,
                token_type="Bearer",
                expires_in=_token_expiry_seconds(access_record.expires_at),
                scope=" ".join(record.scopes),
                refresh_token=raw_refresh_token,
            )
        )

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> SiteHitsRefreshToken | None:
        return await sync_to_async(
            self._load_refresh_token,
            thread_sensitive=True,
        )(client.client_id, refresh_token)

    @staticmethod
    def _load_refresh_token(
        client_id: str | None,
        raw_token: str,
    ) -> SiteHitsRefreshToken | None:
        if not raw_token.startswith("shr_") or len(raw_token) < 24:
            return None
        record = (
            MCPOAuthRefreshToken.objects.select_related("client", "user")
            .filter(
                client__client_id=client_id,
                client__revoked_at__isnull=True,
                token_digest=MCPAccessToken.digest(raw_token),
                revoked_at__isnull=True,
            )
            .first()
        )
        if (
            record is None
            or not record.user.is_active
            or _canonical_resource(record.resource) != _configured_resource()
        ):
            return None
        return _refresh_token_from_record(record, raw_token)

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: SiteHitsRefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        result = await sync_to_async(
            self._exchange_refresh_token,
            thread_sensitive=True,
        )(client.client_id, refresh_token, scopes)
        if result.error:
            raise TokenError(error="invalid_grant", error_description=result.error)
        if result.token is None:  # pragma: no cover - defensive invariant.
            raise TokenError(error="invalid_grant", error_description="Token refresh failed")
        return result.token

    @staticmethod
    def _exchange_refresh_token(
        client_id: str | None,
        refresh_token: SiteHitsRefreshToken,
        scopes: list[str],
    ) -> _ExchangeResult:
        now = timezone.now()
        with transaction.atomic():
            record = (
                MCPOAuthRefreshToken.objects.select_for_update()
                .select_related("client", "user")
                .filter(
                    pk=refresh_token.record_id,
                    client__client_id=client_id,
                    token_digest=MCPAccessToken.digest(refresh_token.token),
                )
                .first()
            )
            if record is None:
                return _ExchangeResult(error="Refresh token is invalid")
            if record.used_at is not None:
                _revoke_family(record.family_id, now=now)
                return _ExchangeResult(
                    error="Refresh token replay detected; the token family was revoked"
                )
            if (
                record.revoked_at is not None
                or record.client.revoked_at is not None
                or record.expires_at <= now
                or not record.user.is_active
                or _canonical_resource(record.resource) != _configured_resource()
            ):
                return _ExchangeResult(error="Refresh token is invalid or expired")
            if not set(scopes).issubset(set(record.scopes)):
                return _ExchangeResult(error="Requested scopes exceed the refresh token grant")
            try:
                normalized_scopes = _normalize_scopes(scopes, default=list(record.scopes))
            except ValueError as exc:
                return _ExchangeResult(error=str(exc))

            record.used_at = now
            record.save(update_fields=["used_at"])
            MCPOAuthAccessToken.objects.filter(
                family_id=record.family_id,
                revoked_at__isnull=True,
            ).update(revoked_at=now)
            _, raw_refresh_token = MCPOAuthRefreshToken.issue(
                user=record.user,
                client=record.client,
                scopes=normalized_scopes,
                resource=_configured_resource(),
                family_id=record.family_id,
            )
            access_record, raw_access_token = MCPOAuthAccessToken.issue(
                user=record.user,
                client=record.client,
                scopes=normalized_scopes,
                resource=_configured_resource(),
                family_id=record.family_id,
            )
        return _ExchangeResult(
            token=OAuthToken(
                access_token=raw_access_token,
                token_type="Bearer",
                expires_in=_token_expiry_seconds(access_record.expires_at),
                scope=" ".join(normalized_scopes),
                refresh_token=raw_refresh_token,
            )
        )

    async def load_access_token(self, token: str) -> SiteHitsAccessToken | None:
        return await sync_to_async(
            self._load_access_token,
            thread_sensitive=True,
        )(token)

    @staticmethod
    def _load_access_token(raw_token: str) -> SiteHitsAccessToken | None:
        if raw_token.startswith("sho_") and len(raw_token) >= 24:
            record = MCPOAuthAccessToken.authenticate(raw_token)
            if record is not None:
                return _oauth_access_token_from_record(record, raw_token)
        if bool(getattr(settings, "SITEHITS_MCP_ALLOW_LEGACY_TOKENS", False)):
            legacy_record = MCPAccessToken.authenticate(raw_token)
            if legacy_record is not None:
                return _legacy_access_token_from_record(legacy_record, raw_token)
        return None

    async def revoke_token(
        self,
        token: SiteHitsAccessToken | SiteHitsRefreshToken,
    ) -> None:
        await sync_to_async(
            self._revoke_token,
            thread_sensitive=True,
        )(token)

    @staticmethod
    def _revoke_token(token: SiteHitsAccessToken | SiteHitsRefreshToken) -> None:
        now = timezone.now()
        if isinstance(token, SiteHitsAccessToken) and token.legacy:
            MCPAccessToken.objects.filter(
                pk=token.record_id,
                revoked_at__isnull=True,
            ).update(revoked_at=now)
            return
        family_id = getattr(token, "family_id", None)
        if family_id is None:
            return
        try:
            parsed_family_id = UUID(str(family_id))
        except ValueError:  # pragma: no cover - provider-created values only.
            return
        with transaction.atomic():
            _revoke_family(parsed_family_id, now=now)


def _revoke_family(family_id: UUID, *, now) -> None:
    MCPOAuthRefreshToken.objects.filter(
        family_id=family_id,
        revoked_at__isnull=True,
    ).update(revoked_at=now)
    MCPOAuthAccessToken.objects.filter(
        family_id=family_id,
        revoked_at__isnull=True,
    ).update(revoked_at=now)


def get_authorization_request(
    request_id: str | UUID,
) -> MCPOAuthAuthorizationRequest | None:
    """Return an active request for a Django consent view, or ``None``."""

    try:
        request_uuid = UUID(str(request_id))
    except (TypeError, ValueError):
        return None
    return (
        MCPOAuthAuthorizationRequest.objects.select_related("client")
        .filter(
            pk=request_uuid,
            resolved_at__isnull=True,
            expires_at__gt=timezone.now(),
            client__revoked_at__isnull=True,
        )
        .first()
    )


def resolve_authorization_request(
    request_id: str | UUID,
    user: Any,
    approved: bool,
) -> str:
    """Resolve consent once and return only the pre-registered callback URL."""

    try:
        request_uuid = UUID(str(request_id))
    except (TypeError, ValueError) as exc:
        raise ConsentRequestError("Authorization request is invalid") from exc
    if not getattr(user, "is_authenticated", False) or not getattr(
        user,
        "is_active",
        False,
    ):
        raise ConsentRequestError("An active authenticated user is required")

    now = timezone.now()
    with transaction.atomic():
        authorization_request = (
            MCPOAuthAuthorizationRequest.objects.select_for_update()
            .select_related("client")
            .filter(pk=request_uuid)
            .first()
        )
        if authorization_request is None or authorization_request.resolved_at is not None:
            raise ConsentRequestError("Authorization request is unavailable")

        client = _client_from_record(authorization_request.client)
        try:
            safe_redirect = client.validate_redirect_uri(
                AnyUrl(authorization_request.redirect_uri)
            )
        except (InvalidRedirectUriError, ValueError) as exc:
            raise ConsentRequestError("The registered callback is no longer valid") from exc

        authorization_request.resolved_at = now
        authorization_request.save(update_fields=["resolved_at"])
        state = authorization_request.state or None
        if authorization_request.client.revoked_at is not None:
            return construct_redirect_uri(
                str(safe_redirect),
                error="unauthorized_client",
                error_description="OAuth client is no longer active",
                state=state,
                iss=settings.SITEHITS_MCP_ISSUER_URL,
            )
        if authorization_request.expires_at <= now:
            return construct_redirect_uri(
                str(safe_redirect),
                error="access_denied",
                error_description="Authorization request expired",
                state=state,
                iss=settings.SITEHITS_MCP_ISSUER_URL,
            )
        if not approved:
            return construct_redirect_uri(
                str(safe_redirect),
                error="access_denied",
                error_description="The user denied the authorization request",
                state=state,
                iss=settings.SITEHITS_MCP_ISSUER_URL,
            )

        _, raw_code = MCPOAuthAuthorizationCode.issue(
            authorization_request=authorization_request,
            user=user,
        )
        return construct_redirect_uri(
            str(safe_redirect),
            code=raw_code,
            state=state,
            iss=settings.SITEHITS_MCP_ISSUER_URL,
        )


oauth_provider = DjangoOAuthProvider()
