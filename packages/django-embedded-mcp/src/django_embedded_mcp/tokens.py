"""Digest-only Django OAuth Toolkit bearer verification for MCP SDK v2."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from asgiref.sync import sync_to_async
from mcp.server.auth.provider import AccessToken, TokenVerifier

from .metadata import validated_scope_catalog
from .oauth import credential_digest


def resolve_dot_access_token_by_digest(
    checksum: str,
    *,
    model_getter: Callable[[], Any] | None = None,
) -> Any | None:
    """Resolve a swapped DOT access-token model through its public model getter."""

    if model_getter is None:
        from oauth2_provider.models import get_access_token_model

        model_getter = get_access_token_model
    model = model_getter()
    return (
        model.objects.select_related("user", "application")
        .filter(token_checksum=checksum)
        .first()
    )


class DigestDjangoOAuthToolkitTokenVerifier(TokenVerifier):
    """Verify opaque MCP bearer credentials against digest-only swapped DOT rows."""

    def __init__(
        self,
        *,
        resource: str,
        issuer: str,
        allowed_scopes: Iterable[str],
        minimum_token_length: int = 24,
        model_getter: Callable[[], Any] | None = None,
        record_resolver: Callable[[str], Any | None] | None = None,
    ):
        if minimum_token_length < 1:
            raise ValueError("minimum_token_length must be positive.")
        self.resource = resource
        self.issuer = issuer
        self.allowed_scopes = frozenset(validated_scope_catalog(allowed_scopes))
        self.minimum_token_length = minimum_token_length
        self.model_getter = model_getter
        self.record_resolver = record_resolver

    async def verify_token(self, token: str) -> AccessToken | None:
        return await sync_to_async(
            self._verify_token_with_connection_boundary,
            thread_sensitive=True,
        )(token)

    def _verify_token_with_connection_boundary(self, raw_token: str) -> AccessToken | None:
        """Own the ORM connection lifecycle outside Django's request handler."""

        from django.db import close_old_connections, connections

        close_old_connections()
        try:
            return self.verify_token_sync(raw_token)
        finally:
            connections.close_all()

    def verify_token_sync(self, raw_token: str) -> AccessToken | None:
        """Run the model lookup and exact binding checks in Django's sync context."""

        if not isinstance(raw_token, str) or len(raw_token) < self.minimum_token_length:
            return None
        checksum = credential_digest(raw_token)
        if self.record_resolver is not None:
            record = self.record_resolver(checksum)
        else:
            record = resolve_dot_access_token_by_digest(
                checksum,
                model_getter=self.model_getter,
            )
        if record is None or not record.is_valid():
            return None
        if getattr(record, "resource", None) != [self.resource]:
            return None
        scopes = str(getattr(record, "scope", "")).split()
        if not scopes or len(scopes) != len(set(scopes)):
            return None
        if not set(scopes).issubset(self.allowed_scopes):
            return None
        application = getattr(record, "application", None)
        user_id = getattr(record, "user_id", None)
        expires = getattr(record, "expires", None)
        client_id = getattr(application, "client_id", "")
        if not client_id or user_id is None or expires is None:
            return None
        return AccessToken(
            token=raw_token,
            client_id=str(client_id),
            scopes=scopes,
            expires_at=int(expires.timestamp()),
            resource=self.resource,
            subject=str(user_id),
            claims={"iss": self.issuer, "aud": self.resource},
        )
