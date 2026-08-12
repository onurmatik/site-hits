"""Django HTTP policy for public OAuth endpoints."""

from __future__ import annotations

import re
from uuid import UUID, uuid4

from django.conf import settings

_REQUEST_ID_PATTERN = re.compile(
    r"^(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)


def _valid_request_id(candidate: str) -> bool:
    """Accept UUID-shaped proxy IDs, including nginx's 32 hex characters."""

    if not _REQUEST_ID_PATTERN.fullmatch(candidate):
        return False
    try:
        UUID(candidate)
    except ValueError:
        return False
    return True


class DjangoRequestCorrelationMiddleware:
    """Carry one proxy-generated request ID through Django and its audit rows.

    Uvicorn validates the direct peer before applying forwarded headers, then
    rewrites ``REMOTE_ADDR`` to the forwarded client. Consequently the app must
    use the explicit proxy-trust setting, rather than the rewritten address, to
    decide whether the nginx-overwritten correlation header is authoritative.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        direct_address = request.META.get("REMOTE_ADDR", "")
        trusted_direct_peer = direct_address in set(settings.SITEHITS_TRUSTED_PROXY_IPS)
        trusted_proxy_path = bool(
            settings.SITEHITS_TRUST_PROXY_HEADERS
            and request.META.get("HTTP_X_SITEHITS_TRUSTED_PROXY") == "1"
        )
        candidate = request.META.get("HTTP_X_REQUEST_ID", "")
        request_id = (
            candidate
            if (trusted_proxy_path or trusted_direct_peer) and _valid_request_id(candidate)
            else uuid4().hex
        )
        request.sitehits_request_id = request_id
        request.sitehits_source_trust = (
            "trusted_proxy"
            if trusted_proxy_path
            else "trusted_direct_peer"
            if trusted_direct_peer
            else "untrusted_direct_peer"
        )
        request.META["HTTP_X_REQUEST_ID"] = request_id
        request.META.pop("HTTP_X_SITEHITS_TRUSTED_PROXY", None)
        response = self.get_response(request)
        response["X-Request-ID"] = request_id
        return response


class OAuthNoStoreMiddleware:
    """Apply OAuth cache/privacy headers even when CSRF rejects before the view."""

    oauth_paths = frozenset(
        {
            "/oauth/register/",
            "/oauth/authorize/",
            "/oauth/token/",
            "/oauth/revoke/",
        }
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        # Login/signup, Google, and magic-link hops can carry the validated
        # authorize target in `next`; that target includes opaque OAuth state.
        auth_hop = request.path.startswith("/accounts/")
        if request.path in self.oauth_paths or auth_hop:
            response["Cache-Control"] = "no-store"
            response["Referrer-Policy"] = "no-referrer"
            response["X-Robots-Tag"] = "noindex, nofollow"
            if request.path in {"/oauth/token/", "/oauth/revoke/"}:
                response["Pragma"] = "no-cache"
        return response
