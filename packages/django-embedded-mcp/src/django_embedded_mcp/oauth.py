"""Stable, model-neutral OAuth primitives for embedded MCP products.

Product validators own their model transitions. This module deliberately avoids
Django OAuth Toolkit's private ``_...`` methods so pinned upgrades can be verified
through public adapter contracts.
"""

import hashlib
from collections.abc import Iterable


def credential_digest(value: str) -> str:
    """Return a one-way lookup digest for high-entropy OAuth credentials."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def exact_resource_audience(
    request_uri: str,
    audiences: list[str],
    expected: str | None = None,
) -> bool:
    """Validate a protected-resource audience without URI normalization.

    Django OAuth Toolkit calls resource validators with two positional arguments.
    The optional third argument keeps the primitive directly testable while the
    dotted-setting form resolves the package's product-supplied resource setting.
    """

    if expected is None:
        from django.conf import settings

        expected = settings.DJANGO_EMBEDDED_MCP_RESOURCE_URL
    return request_uri == expected and audiences == [expected]


def normalize_scopes(
    scopes: Iterable[str] | None,
    *,
    supported_scopes: Iterable[str],
    required_scopes: Iterable[str] = (),
    default_scopes: Iterable[str] = (),
) -> list[str]:
    """Validate a scope request while preserving its first-seen order."""

    supported = frozenset(supported_scopes)
    required = frozenset(required_scopes)
    requested = list(scopes) if scopes is not None else list(default_scopes)
    normalized = list(dict.fromkeys(scope for scope in requested if scope))
    if not normalized:
        normalized = list(default_scopes)
    if not required.issubset(normalized):
        raise ValueError("One or more required scopes are missing.")
    if not set(normalized).issubset(supported):
        raise ValueError("One or more requested scopes are not supported.")
    return normalized
