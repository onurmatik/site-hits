"""Pure RFC 7591 policy for the Stage 1 public-client fallback profile."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from unicodedata import category
from urllib.parse import urlsplit

from .oauth import normalize_scopes
from .redirects import LOOPBACK_HOSTS, validate_registered_redirect_uri

DCR_METADATA_FIELDS = (
    "client_uri",
    "logo_uri",
    "contacts",
    "tos_uri",
    "policy_uri",
    "software_id",
    "software_version",
)
URI_METADATA_FIELDS = frozenset({"client_uri", "logo_uri", "tos_uri", "policy_uri"})
TEXT_METADATA_FIELDS = frozenset({"software_id", "software_version"})


class DynamicClientRegistrationError(ValueError):
    """Structured RFC 7591 error raised by the reusable DCR policy."""

    def __init__(self, error: str, description: str, *, status: int = 400):
        self.error = error
        self.description = description
        self.status = status
        super().__init__(description)


@dataclass(frozen=True)
class PublicClientRegistration:
    """Validated public-client registration fields consumed by a product model."""

    redirect_uris: tuple[str, ...]
    application_type: str
    scopes: tuple[str, ...]
    client_name: str
    metadata: Mapping[str, Any]


def _is_persistable_text(value: object) -> bool:
    """Return whether text is UTF-8 scalar data without database-hostile controls."""

    if not isinstance(value, str):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return not any(
        ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F
        or category(character) in {"Zl", "Zp"}
        for character in value
    )


def _validated_metadata(data: Mapping[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for field in URI_METADATA_FIELDS | TEXT_METADATA_FIELDS:
        if field not in data:
            continue
        value = data[field]
        if not _is_persistable_text(value):
            raise DynamicClientRegistrationError(
                "invalid_client_metadata",
                f"{field} must be valid Unicode text without control characters.",
            )
        metadata[field] = value

    if "contacts" in data:
        contacts = data["contacts"]
        if not isinstance(contacts, list) or not all(
            _is_persistable_text(contact) for contact in contacts
        ):
            raise DynamicClientRegistrationError(
                "invalid_client_metadata",
                "contacts must be a list of valid Unicode strings without control characters.",
            )
        metadata["contacts"] = contacts
    return metadata


def parse_public_client_registration(
    body: bytes,
    *,
    supported_scopes: Iterable[str],
    required_scopes: Iterable[str],
    default_scopes: Iterable[str],
    max_body_bytes: int = 16 * 1024,
    max_redirect_uris: int = 10,
    allow_localhost: bool = False,
) -> PublicClientRegistration:
    """Validate the DCR fallback's fixed public-client profile."""

    if not isinstance(body, (bytes, bytearray)):
        raise DynamicClientRegistrationError(
            "invalid_client_metadata",
            "Request body must be JSON-encoded bytes.",
        )
    if len(body) > max_body_bytes:
        raise DynamicClientRegistrationError(
            "invalid_client_metadata",
            f"Registration request exceeds {max_body_bytes // 1024} KiB.",
            status=413,
        )

    def reject_duplicate_pairs(pairs):
        data = {}
        for key, value in pairs:
            if key in data:
                raise DynamicClientRegistrationError(
                    "invalid_client_metadata",
                    f"{key} must not be repeated.",
                )
            data[key] = value
        return data

    def reject_non_json_constant(constant):
        raise DynamicClientRegistrationError(
            "invalid_client_metadata",
            f"{constant} is not a valid JSON number.",
        )

    try:
        data = json.loads(
            body,
            object_pairs_hook=reject_duplicate_pairs,
            parse_constant=reject_non_json_constant,
        )
    except DynamicClientRegistrationError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError, TypeError) as exc:
        raise DynamicClientRegistrationError(
            "invalid_client_metadata",
            "Request body must be valid JSON.",
        ) from exc
    if not isinstance(data, dict):
        raise DynamicClientRegistrationError(
            "invalid_client_metadata",
            "Request body must be a JSON object.",
        )

    redirect_uris = data.get("redirect_uris")
    if (
        not isinstance(redirect_uris, list)
        or not redirect_uris
        or len(redirect_uris) > max_redirect_uris
        or not all(isinstance(uri, str) for uri in redirect_uris)
    ):
        raise DynamicClientRegistrationError(
            "invalid_redirect_uri",
            f"redirect_uris must contain between 1 and {max_redirect_uris} string values.",
        )
    try:
        for uri in redirect_uris:
            validate_registered_redirect_uri(uri, allow_localhost=allow_localhost)
    except ValueError as exc:
        raise DynamicClientRegistrationError(
            "invalid_redirect_uri",
            str(exc),
        ) from exc

    application_type = data.get("application_type")
    if application_type not in {"web", "native"}:
        raise DynamicClientRegistrationError(
            "invalid_client_metadata",
            "application_type must be web or native.",
        )
    if application_type == "web":
        profile_matches = all(urlsplit(uri).scheme == "https" for uri in redirect_uris)
    else:
        allowed_loopbacks = LOOPBACK_HOSTS | ({"localhost"} if allow_localhost else set())
        profile_matches = all(
            urlsplit(uri).scheme == "http"
            and urlsplit(uri).hostname in allowed_loopbacks
            for uri in redirect_uris
        )
    if not profile_matches:
        raise DynamicClientRegistrationError(
            "invalid_redirect_uri",
            "redirect_uris do not match application_type.",
        )

    if data.get("token_endpoint_auth_method") != "none":
        raise DynamicClientRegistrationError(
            "invalid_client_metadata",
            "token_endpoint_auth_method must be none.",
        )
    response_types = data.get("response_types", ["code"])
    if (
        not isinstance(response_types, list)
        or not all(isinstance(response_type, str) for response_type in response_types)
        or response_types != ["code"]
    ):
        raise DynamicClientRegistrationError(
            "invalid_client_metadata",
            "response_types must contain only code.",
        )
    grant_types = data.get(
        "grant_types",
        ["authorization_code", "refresh_token"],
    )
    if (
        not isinstance(grant_types, list)
        or not all(isinstance(grant_type, str) for grant_type in grant_types)
        or set(grant_types) != {"authorization_code", "refresh_token"}
        or len(grant_types) != 2
    ):
        raise DynamicClientRegistrationError(
            "invalid_client_metadata",
            "grant_types must contain authorization_code and refresh_token only.",
        )

    raw_scope = data.get("scope")
    if raw_scope is None:
        raw_scope = " ".join(default_scopes)
    if not isinstance(raw_scope, str):
        raise DynamicClientRegistrationError(
            "invalid_client_metadata",
            "scope must be a string.",
        )
    try:
        scopes = normalize_scopes(
            raw_scope.split(),
            supported_scopes=supported_scopes,
            required_scopes=required_scopes,
        )
    except ValueError as exc:
        raise DynamicClientRegistrationError(
            "invalid_client_metadata",
            str(exc),
        ) from exc

    client_name = data.get("client_name", "")
    if not _is_persistable_text(client_name) or len(client_name) > 255:
        raise DynamicClientRegistrationError(
            "invalid_client_metadata",
            "client_name must be valid Unicode without control characters and no longer than 255 characters.",
        )
    metadata = _validated_metadata(data)
    metadata["application_type"] = application_type
    return PublicClientRegistration(
        redirect_uris=tuple(redirect_uris),
        application_type=application_type,
        scopes=tuple(scopes),
        client_name=client_name,
        metadata=metadata,
    )
