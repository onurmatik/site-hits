"""Byte-exact OAuth resource identifier validation.

The configured identifier is validated structurally once. Request values are then
compared as UTF-8 byte sequences and are never normalized.
"""

from collections.abc import Iterable
from urllib.parse import urlsplit


class ExactResourceError(ValueError):
    """Raised when an RFC 8707 resource parameter is absent or not byte-exact."""


def validate_canonical_url(
    value: str,
    *,
    require_https: bool,
    allow_root_path: bool = True,
) -> str:
    """Validate an already-canonical absolute URL without rewriting it."""

    if not isinstance(value, str) or not value:
        raise ValueError("URL must be a non-empty string.")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("URL must be a valid absolute URL.") from exc
    if parsed.scheme not in ({"https"} if require_https else {"http", "https"}):
        raise ValueError("URL must use HTTPS." if require_https else "URL must use HTTP or HTTPS.")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError("URL must have a host and must not contain userinfo.")
    if parsed.query or parsed.fragment:
        raise ValueError("URL must not contain a query or fragment.")
    if value.endswith("/") and not (allow_root_path and parsed.path == "/"):
        raise ValueError("URL must not contain an unexpected trailing slash.")
    return value


def exact_resource_from_pairs(
    pairs: Iterable[tuple[str, str]],
    *,
    expected: str,
) -> tuple[list[tuple[str, str]], str]:
    """Require one unique byte-exact resource and safely collapse exact repeats."""

    try:
        materialized = list(pairs)
    except TypeError as exc:
        raise ExactResourceError("Resource parameters must be text pairs.") from exc
    if not all(
        isinstance(pair, (list, tuple))
        and len(pair) == 2
        and isinstance(pair[0], str)
        and isinstance(pair[1], str)
        for pair in materialized
    ):
        raise ExactResourceError("Resource parameters must be text pairs.")
    resources = [value for key, value in materialized if key == "resource"]
    if not resources:
        raise ExactResourceError("The resource parameter is required.")
    try:
        expected_bytes = expected.encode("utf-8")
        resource_bytes = [value.encode("utf-8") for value in resources]
    except (AttributeError, UnicodeEncodeError) as exc:
        raise ExactResourceError(
            "The requested resource is not valid UTF-8 scalar text."
        ) from exc
    if any(value != expected_bytes for value in resource_bytes):
        raise ExactResourceError("The requested resource is not this MCP server.")
    without_resource = [(key, value) for key, value in materialized if key != "resource"]
    return [*without_resource, ("resource", expected)], expected
