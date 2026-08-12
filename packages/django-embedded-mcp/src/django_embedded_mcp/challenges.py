"""Safe RFC 6750 Bearer challenge construction."""

from __future__ import annotations

from collections.abc import Iterable

from .metadata import validated_scope_catalog


def _quoted(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("Bearer challenge values must be strings.")
    if any(ord(character) < 0x20 or ord(character) > 0x7E for character in value):
        raise ValueError("Bearer challenge values must contain visible ASCII only.")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def build_bearer_challenge(
    *,
    resource_metadata: str,
    scopes: Iterable[str] = (),
    error: str | None = None,
    error_description: str | None = None,
) -> str:
    """Build one deterministic challenge with header-injection-safe values."""

    scope_catalog = validated_scope_catalog(scopes)
    parameters = [("resource_metadata", resource_metadata)]
    if scope_catalog:
        parameters.append(("scope", " ".join(scope_catalog)))
    if error is not None:
        parameters.append(("error", error))
    if error_description is not None:
        parameters.append(("error_description", error_description))
    serialized = ", ".join(f"{name}={_quoted(value)}" for name, value in parameters)
    return f"Bearer {serialized}"


def build_auth_failure_challenge(
    *,
    resource_metadata: str,
    scopes: Iterable[str],
    status: int,
    credential_present: bool,
) -> str:
    """Map an HTTP auth failure to the Stage 1 discovery/error distinction."""

    if status == 403:
        return build_bearer_challenge(
            resource_metadata=resource_metadata,
            scopes=scopes,
            error="insufficient_scope",
            error_description="The token lacks the required scope",
        )
    if credential_present:
        return build_bearer_challenge(
            resource_metadata=resource_metadata,
            scopes=scopes,
            error="invalid_token",
            error_description="The bearer token is invalid or expired",
        )
    return build_bearer_challenge(
        resource_metadata=resource_metadata,
        scopes=scopes,
    )
