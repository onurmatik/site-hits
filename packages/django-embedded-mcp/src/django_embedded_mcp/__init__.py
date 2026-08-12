"""Shared primitives for embedding OAuth-backed MCP servers in Django products."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("django-embedded-mcp")
except PackageNotFoundError:  # pragma: no cover - source tree without installation.
    __version__ = "0.2.0"

from .challenges import build_auth_failure_challenge, build_bearer_challenge
from .cimd import (
    CIMDDocument,
    CIMDError,
    SafeCIMDFetcher,
    address_is_public,
    is_cimd_client_id,
    resolve_public_addresses,
    validate_cimd_client_id,
    validate_cimd_document,
)
from .dcr import (
    DynamicClientRegistrationError,
    PublicClientRegistration,
    parse_public_client_registration,
)
from .http import HeaderOnlyBearerMiddleware, non_header_bearer_sources
from .mcp import build_mcp_auth_settings, build_transport_security_settings
from .metadata import (
    build_authorization_server_metadata,
    build_protected_resource_metadata,
    validated_scope_catalog,
)
from .oauth import credential_digest, exact_resource_audience, normalize_scopes
from .redirects import redirect_uri_matches, validate_registered_redirect_uri
from .refresh import (
    RefreshFamilyDecision,
    RefreshFamilyDecisionCode,
    RefreshFamilyPolicy,
    RefreshFamilyState,
    RefreshMemberState,
)
from .resource import (
    ExactResourceError,
    exact_resource_from_pairs,
    validate_canonical_url,
)
from .tokens import (
    DigestDjangoOAuthToolkitTokenVerifier,
    resolve_dot_access_token_by_digest,
)

__all__ = [
    "DigestDjangoOAuthToolkitTokenVerifier",
    "CIMDDocument",
    "CIMDError",
    "DynamicClientRegistrationError",
    "ExactResourceError",
    "HeaderOnlyBearerMiddleware",
    "PublicClientRegistration",
    "RefreshFamilyDecision",
    "RefreshFamilyDecisionCode",
    "RefreshFamilyPolicy",
    "RefreshFamilyState",
    "RefreshMemberState",
    "SafeCIMDFetcher",
    "__version__",
    "build_auth_failure_challenge",
    "build_authorization_server_metadata",
    "build_bearer_challenge",
    "build_mcp_auth_settings",
    "build_protected_resource_metadata",
    "build_transport_security_settings",
    "address_is_public",
    "credential_digest",
    "exact_resource_audience",
    "exact_resource_from_pairs",
    "is_cimd_client_id",
    "non_header_bearer_sources",
    "normalize_scopes",
    "parse_public_client_registration",
    "redirect_uri_matches",
    "resolve_public_addresses",
    "resolve_dot_access_token_by_digest",
    "validate_canonical_url",
    "validate_cimd_client_id",
    "validate_cimd_document",
    "validate_registered_redirect_uri",
    "validated_scope_catalog",
]
