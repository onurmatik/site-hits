from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote_plus, urlencode

from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods
from django_embedded_mcp.dcr import (
    DynamicClientRegistrationError,
    parse_public_client_registration,
)
from django_embedded_mcp.resource import ExactResourceError
from oauth2_provider.models import AbstractApplication, get_application_model
from oauth2_provider.views import AuthorizationView, RevokeTokenView, TokenView

from mcp_oauth.models import (
    OAuthAccessToken,
    OAuthConsent,
    OAuthGrant,
    OAuthRateLimitBucket,
    OAuthRefreshFamily,
    OAuthRefreshToken,
    OAuthSecurityEvent,
)

from .http import authorization_server_metadata, protected_resource_metadata
from .oauth import (
    _PKCE_S256_PATTERN,
    begin_refresh_audit_capture,
    credential_digest,
    current_refresh_audit_decision,
    end_refresh_audit_capture,
    exact_resource_from_request_pairs,
    normalize_scopes,
    redirect_uri_digest,
)
from .versioning import integration_manifest

MAX_DCR_BODY_BYTES = 16 * 1024
MAX_DCR_REDIRECT_URIS = 10
RATE_WINDOW = timedelta(minutes=1)
RATE_LIMITS = {
    "register": (30, 300),
    "authorize": (120, 1_200),
    "token": (120, 1_200),
    "revoke": (120, 1_200),
}
CLIENT_RATE_LIMITS = {
    "authorize": 600,
    "token": 600,
    "revoke": 600,
}


class OAuthProfileError(ValueError):
    def __init__(self, error, description):
        self.error = error
        super().__init__(description)


_AUTHORIZATION_REQUIRED_SINGLE_VALUE_FIELDS = (
    "client_id",
    "redirect_uri",
    "response_type",
    "code_challenge",
    "code_challenge_method",
)
_TOKEN_SINGLE_VALUE_FIELDS = (
    "grant_type",
    "client_id",
    "redirect_uri",
    "code",
    "code_verifier",
    "refresh_token",
    "scope",
    "client_secret",
)
_REVOKE_SINGLE_VALUE_FIELDS = (
    "client_id",
    "token",
    "token_type_hint",
    "client_secret",
)
_AUTHORIZATION_CODE_REQUIRED_FIELDS = (
    "grant_type",
    "client_id",
    "code",
    "code_verifier",
    "redirect_uri",
)
_REFRESH_REQUIRED_FIELDS = (
    "grant_type",
    "client_id",
    "refresh_token",
)


def _authorization_state(data) -> str | None:
    """Return one opaque RFC 6749 state value, or reject ambiguous input."""

    states = data.getlist("state")
    if len(states) > 1:
        raise OAuthProfileError("invalid_request", "state must not be repeated.")
    if not states:
        return None
    if states[0] == "":
        raise OAuthProfileError(
            "invalid_request",
            "state must not be empty when supplied.",
        )
    state = states[0]
    if len(state) > 1024 or any(
        ord(character) < 0x20 or ord(character) > 0x7E
        for character in state
    ):
        raise OAuthProfileError(
            "invalid_request",
            "state must contain 1 to 1024 visible ASCII characters.",
        )
    return state


def _validate_authorization_parameter_cardinality(data) -> None:
    for field in _AUTHORIZATION_REQUIRED_SINGLE_VALUE_FIELDS:
        if len(data.getlist(field)) != 1:
            raise OAuthProfileError(
                "invalid_request",
                f"{field} must appear exactly once.",
            )
    scope_values = data.getlist("scope")
    if not scope_values:
        raise OAuthProfileError("invalid_scope", "scope is required.")
    if len(scope_values) > 1:
        raise OAuthProfileError("invalid_request", "scope must not be repeated.")
    prompts = data.getlist("prompt")
    if len(prompts) > 1:
        raise OAuthProfileError("invalid_request", "prompt must not be repeated.")
    if prompts and prompts[0] != "none":
        raise OAuthProfileError(
            "invalid_request",
            "The only supported prompt value is none.",
        )
    for unsupported in ("nonce", "claims", "approval_prompt"):
        if data.getlist(unsupported):
            raise OAuthProfileError(
                "invalid_request",
                f"{unsupported} is not supported by this OAuth profile.",
            )
    _authorization_state(data)


def _authorization_redirect_context(data):
    """Resolve an unambiguous, registered callback for an OAuth error redirect."""

    if len(data.getlist("client_id")) != 1 or len(data.getlist("redirect_uri")) != 1:
        return None
    try:
        state = _authorization_state(data)
    except OAuthProfileError:
        return None
    client = _registered_client(data.getlist("client_id")[0])
    redirect_uri = data.getlist("redirect_uri")[0]
    if client is None or not client.redirect_uri_allowed(redirect_uri):
        return None
    return client, redirect_uri, state


def _authorization_audit_resource(data) -> str:
    try:
        return exact_resource_from_request_pairs(_query_pairs(data))
    except (ExactResourceError, ValueError):
        return ""


def _safe_oauth_error_description(description) -> str:
    """Limit a redirect error description to RFC 6749's ASCII character set."""

    value = str(description)[:512]
    return "".join(
        character
        if (
            ord(character) in {0x20, 0x21}
            or 0x23 <= ord(character) <= 0x5B
            or 0x5D <= ord(character) <= 0x7E
        )
        else "?"
        for character in value
    )


def _with_canonical_authorization_issuer(location: str) -> str:
    """Preserve redirect bytes while replacing every response issuer value."""

    without_fragment, fragment_separator, fragment = location.partition("#")
    base, query_separator, raw_query = without_fragment.partition("?")
    raw_fields = raw_query.split("&") if query_separator else []
    kept_fields = [
        field
        for field in raw_fields
        if unquote_plus(field.partition("=")[0]) != "iss"
    ]
    canonical_field = urlencode([("iss", settings.SITEHITS_MCP_ISSUER_URL)])
    query = "&".join([*kept_fields, canonical_field])
    rewritten = f"{base}?{query}"
    if fragment_separator:
        rewritten = f"{rewritten}#{fragment}"
    return rewritten


def _harden_oauth_response(response, *, token_response=False):
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Robots-Tag"] = "noindex, nofollow"
    if token_response:
        response["Pragma"] = "no-cache"
    return response


def _oauth_error(error, description, *, status=400, token_response=False):
    response = JsonResponse(
        {"error": error, "error_description": description},
        status=status,
    )
    return _harden_oauth_response(response, token_response=token_response)


def _query_pairs(querydict):
    return [
        (key, value)
        for key, values in querydict.lists()
        for value in values
    ]


def _repeated_parameter(querydict, fields) -> str | None:
    return next((field for field in fields if len(querydict.getlist(field)) > 1), None)


def _token_profile_error(data, grant_type) -> str | None:
    required = (
        _AUTHORIZATION_CODE_REQUIRED_FIELDS
        if grant_type == "authorization_code"
        else _REFRESH_REQUIRED_FIELDS
        if grant_type == "refresh_token"
        else ()
    )
    missing = next(
        (field for field in required if len(data.getlist(field)) != 1 or not data.get(field)),
        None,
    )
    if missing is not None:
        return f"{missing} must appear exactly once and must not be empty."
    forbidden = (
        ("refresh_token", "scope")
        if grant_type == "authorization_code"
        else ("code", "code_verifier", "redirect_uri")
        if grant_type == "refresh_token"
        else ()
    )
    unexpected = next((field for field in forbidden if data.getlist(field)), None)
    if unexpected is not None:
        return f"{unexpected} is not valid for the {grant_type} grant."
    return None


def _rate_subject_digest(action, subject):
    return hmac.new(
        settings.SITEHITS_MCP_TOKEN_SECRET.encode("utf-8"),
        f"{action}:{subject}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _consume_bucket(action, subject, *, limit, now):
    epoch = int(now.timestamp())
    window_seconds = int(RATE_WINDOW.total_seconds())
    window_started = datetime.fromtimestamp(
        epoch - (epoch % window_seconds),
        tz=UTC,
    )
    window_ends = window_started + RATE_WINDOW
    digest = _rate_subject_digest(action, subject)
    with transaction.atomic():
        bucket, _ = OAuthRateLimitBucket.objects.select_for_update().get_or_create(
            action=action,
            subject_digest=digest,
            window_started_at=window_started,
            defaults={"window_ends_at": window_ends},
        )
        bucket = OAuthRateLimitBucket.objects.select_for_update().get(pk=bucket.pk)
        if bucket.count >= limit:
            return False
        OAuthRateLimitBucket.objects.filter(pk=bucket.pk).update(count=F("count") + 1)
        return True


def _rate_limit_request(request, action, *, client=None):
    per_source, global_limit = RATE_LIMITS[action]
    source = request.META.get("REMOTE_ADDR") or "unknown"
    now = timezone.now()
    source_allowed = _consume_bucket(action, source, limit=per_source, now=now)
    if not source_allowed:
        # A source that exhausted its own budget must not burn shared capacity
        # and deny service to every other public OAuth client.
        return False
    if client is not None and action in CLIENT_RATE_LIMITS:
        client_allowed = _consume_bucket(
            action,
            f"client:{client.pk}",
            limit=CLIENT_RATE_LIMITS[action],
            now=now,
        )
        if not client_allowed:
            return False
    global_allowed = _consume_bucket(
        action,
        "global",
        limit=global_limit,
        now=now,
    )
    return global_allowed


def _record_security_event(
    *,
    event,
    outcome,
    request_id,
    application=None,
    user=None,
    resource="",
    scopes=None,
    subject="",
    details=None,
):
    OAuthSecurityEvent.objects.create(
        request_id=request_id,
        event=event,
        outcome=outcome,
        application=application,
        user=user if getattr(user, "is_authenticated", False) else None,
        resource=resource,
        scopes=list(scopes or []),
        subject_digest=_rate_subject_digest(event, subject) if subject else "",
        details=details or {},
    )


def _request_id(request):
    """Return the correlation ID established by the outer Django middleware."""

    return request.sitehits_request_id


def _audit_digest(value) -> str:
    if isinstance(value, bytes):
        payload = value
    else:
        payload = str(value).encode("utf-8", errors="surrogatepass")
    return hashlib.sha256(payload).hexdigest()


def _audit_scopes(value) -> list[str]:
    """Return a bounded, non-sensitive representation of requested scope names."""

    if isinstance(value, str):
        candidates = value.split()
    elif isinstance(value, (list, tuple)):
        candidates = value
    else:
        return []
    return [
        candidate[:128]
        for candidate in candidates[:32]
        if isinstance(candidate, str) and candidate
    ]


def _redirect_uri_summary(redirect_uris) -> dict[str, object]:
    values = [uri for uri in redirect_uris if isinstance(uri, str)]
    return {
        "count": len(values),
        "digests": [_audit_digest(uri) for uri in values[:MAX_DCR_REDIRECT_URIS]],
    }


def _trusted_source_details(request) -> dict[str, str]:
    source = request.META.get("REMOTE_ADDR") or "unknown"
    return {
        "source_trust": getattr(
            request,
            "sitehits_source_trust",
            "untrusted_direct_peer",
        ),
        "source_digest": _rate_subject_digest("source", source),
    }


def _oauth_response_payload(response) -> dict[str, object]:
    try:
        payload = json.loads(response.content)
    except (AttributeError, TypeError, ValueError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _uses_confidential_client_auth(request) -> bool:
    authorization = request.META.get("HTTP_AUTHORIZATION", "")
    return "client_secret" in request.POST or bool(authorization.strip())


def _family_digest(token_family) -> str:
    return _audit_digest(token_family) if token_family else ""


def _resource_from_record(record) -> str:
    resources = getattr(record, "resource", None)
    if resources == [settings.SITEHITS_MCP_RESOURCE_URL]:
        return settings.SITEHITS_MCP_RESOURCE_URL
    return ""


@dataclass(frozen=True)
class _CredentialAuditSnapshot:
    """Non-secret credential context captured before destructive transitions."""

    application: object | None = None
    user: object | None = None
    resource: str = ""
    scopes: tuple[str, ...] = ()
    grant_digest: str = ""
    family_id: object | None = None


def _credential_audit_snapshot(
    *,
    refresh=None,
    access=None,
) -> _CredentialAuditSnapshot:
    """Preserve audit bindings that revocation intentionally disconnects."""

    if refresh is None and access is not None:
        refresh = (
            OAuthRefreshToken.objects.select_related(
                "application",
                "user",
                "access_token",
                "family_state",
            )
            .filter(Q(access_token=access) | Q(pk=access.source_refresh_token_id))
            .filter(application_id=access.application_id)
            .first()
        )
    record = refresh or access
    if record is None:
        return _CredentialAuditSnapshot()

    family_id = None
    grant_digest = getattr(record, "authorization_code_digest", "")
    if refresh is not None:
        family_id = refresh.family_state_id or refresh.token_family

    scope_source = access
    if scope_source is None and refresh is not None and refresh.access_token_id:
        scope_source = refresh.access_token
    if scope_source is None and family_id is not None:
        linked_member = (
            OAuthRefreshToken.objects.select_related("access_token")
            .filter(family_state_id=family_id, access_token__isnull=False)
            .order_by("-created")
            .first()
        )
        scope_source = linked_member.access_token if linked_member is not None else None
    if scope_source is None and grant_digest:
        scope_source = (
            OAuthAccessToken.objects.filter(
                application_id=record.application_id,
                user_id=record.user_id,
                authorization_code_digest=grant_digest,
            )
            .order_by("-created")
            .first()
        )

    return _CredentialAuditSnapshot(
        application=getattr(record, "application", None),
        user=getattr(record, "user", None),
        resource=_resource_from_record(record),
        scopes=tuple(scope_source.scope.split()) if scope_source is not None else (),
        grant_digest=grant_digest,
        family_id=family_id,
    )


def _token_audit_snapshot(request, grant_type) -> _CredentialAuditSnapshot:
    raw_credential, _, grant, refresh = _credential_records(request, grant_type)
    del raw_credential
    if refresh is not None:
        return _credential_audit_snapshot(refresh=refresh)
    if grant is not None:
        return _CredentialAuditSnapshot(
            application=grant.application,
            user=grant.user,
            resource=_resource_from_record(grant),
            scopes=tuple(grant.scope.split()),
            grant_digest=grant.code_digest,
        )
    return _CredentialAuditSnapshot()


def _credential_records(request, grant_type):
    """Resolve credential context by digest without retaining raw credentials."""

    if grant_type == "authorization_code":
        raw_credential = request.POST.get("code", "")
        digest = credential_digest(raw_credential) if raw_credential else ""
        grant = (
            OAuthGrant.objects.select_related("application", "user")
            .filter(code_digest=digest)
            .first()
            if digest
            else None
        )
        return raw_credential, digest, grant, None
    if grant_type == "refresh_token":
        raw_credential = request.POST.get("refresh_token", "")
        digest = credential_digest(raw_credential) if raw_credential else ""
        refresh = (
            OAuthRefreshToken.objects.select_related(
                "application",
                "user",
                "access_token",
                "family_state",
            )
            .filter(token_checksum=digest)
            .order_by(F("revoked").desc(nulls_first=True))
            .first()
            if digest
            else None
        )
        return raw_credential, digest, None, refresh
    return "", "", None, None


def _record_token_lifecycle_event(
    request,
    *,
    grant_type,
    outcome,
    error="",
    resource="",
    client=None,
    grant=None,
    refresh=None,
    response_payload=None,
    snapshot=None,
    refresh_decision=None,
):
    response_payload = response_payload or {}
    snapshot = snapshot or _token_audit_snapshot(request, grant_type)
    raw_credential, credential_checksum, resolved_grant, resolved_refresh = (
        _credential_records(request, grant_type)
    )
    # Do not let a raw credential escape this function. Only its one-way digest
    # is used below; the variable is explicitly discarded before persistence.
    del raw_credential
    grant = grant or resolved_grant
    refresh = refresh or resolved_refresh
    issued_access = None
    issued_refresh = None
    raw_access = response_payload.get("access_token")
    if isinstance(raw_access, str) and raw_access:
        issued_access = (
            OAuthAccessToken.objects.select_related("application", "user")
            .filter(token_checksum=credential_digest(raw_access))
            .first()
        )
    raw_refresh = response_payload.get("refresh_token")
    if isinstance(raw_refresh, str) and raw_refresh:
        issued_refresh = (
            OAuthRefreshToken.objects.select_related(
                "application",
                "user",
                "access_token",
                "family_state",
            )
            .filter(token_checksum=credential_digest(raw_refresh))
            .order_by(F("revoked").desc(nulls_first=True))
            .first()
        )
    del raw_access, raw_refresh

    record = issued_access or issued_refresh or refresh or grant
    actual_client = (
        client or snapshot.application or getattr(record, "application", None)
    )
    actual_user = snapshot.user or getattr(record, "user", None)
    actual_resource = resource or snapshot.resource or _resource_from_record(record)
    if actual_resource != settings.SITEHITS_MCP_RESOURCE_URL:
        actual_resource = ""
    if issued_access is not None:
        scopes = issued_access.scope.split()
    elif grant is not None:
        scopes = grant.scope.split()
    elif snapshot.scopes:
        scopes = list(snapshot.scopes)
    elif refresh is not None and refresh.access_token is not None:
        scopes = refresh.access_token.scope.split()
    else:
        scopes = _audit_scopes(request.POST.get("scope", ""))

    family_record = issued_refresh or refresh
    if family_record is None and grant is not None:
        family_record = (
            OAuthRefreshToken.objects.select_related("family_state")
            .filter(authorization_code_digest=grant.code_digest)
            .order_by("created")
            .first()
        )
    family_id = (
        getattr(family_record, "family_state_id", None)
        or getattr(family_record, "token_family", None)
        or snapshot.family_id
    )
    family_revoked = bool(
        family_id
        and OAuthRefreshFamily.objects.filter(
            pk=family_id,
            revoked_at__isnull=False,
        ).exists()
    )
    replay_detected = bool(
        (grant is not None and grant.replayed_at is not None)
        or getattr(refresh_decision, "replay_detected", False)
    )
    grant_digest = (
        getattr(grant, "code_digest", "")
        or getattr(family_record, "authorization_code_digest", "")
        or snapshot.grant_digest
    )
    supplied_client_id = request.POST.get("client_id", "")
    details = {
        "client_id": (
            actual_client.client_id
            if actual_client is not None
            else supplied_client_id[:255]
        ),
        "grant_type": grant_type,
        "credential_digest": credential_checksum,
        "grant_digest": grant_digest,
        "family_digest": _family_digest(family_id),
        "replay_detected": replay_detected,
        "family_revoked": family_revoked,
        "refresh_family_decision": (
            refresh_decision.code.value if refresh_decision is not None else ""
        ),
        **_trusted_source_details(request),
        "decision": outcome,
        "error": error,
    }
    _record_security_event(
        event="refresh" if grant_type == "refresh_token" else "token",
        outcome=outcome,
        request_id=_request_id(request),
        application=actual_client,
        user=actual_user,
        resource=actual_resource,
        scopes=scopes,
        subject=credential_checksum,
        details=details,
    )


def _client_profile(client):
    return (
        client is not None
        and client.is_usable(None)
        and client.client_type == AbstractApplication.CLIENT_PUBLIC
        and client.authorization_grant_type
        == AbstractApplication.GRANT_AUTHORIZATION_CODE
        and client.registration_source == AbstractApplication.RegistrationSource.DCR
        and not client.client_secret
    )


def _registered_client(client_id):
    Application = get_application_model()
    try:
        client = Application.objects.get(client_id=client_id)
    except Application.DoesNotExist:
        return None
    return client if _client_profile(client) else None


def agent_manifest(request):
    response = JsonResponse(integration_manifest())
    response["Cache-Control"] = "public, max-age=300"
    return response


def mcp_documentation(request):
    return render(
        request,
        "mcp_gateway/documentation.html",
        {"mcp_resource_url": settings.SITEHITS_MCP_RESOURCE_URL},
    )


def _metadata_response(payload):
    response = JsonResponse(payload)
    response["Access-Control-Allow-Origin"] = "*"
    response["Cache-Control"] = "public, max-age=300"
    return response


@login_not_required
@require_http_methods(["GET", "OPTIONS"])
def oauth_authorization_server_metadata(request):
    if request.method == "OPTIONS":
        response = JsonResponse({}, status=204)
        response["Access-Control-Allow-Origin"] = "*"
        response["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response["Access-Control-Max-Age"] = "300"
        return response
    return _metadata_response(authorization_server_metadata())


@login_not_required
@require_http_methods(["GET", "OPTIONS"])
def oauth_protected_resource_metadata(request):
    if request.method == "OPTIONS":
        response = JsonResponse({}, status=204)
        response["Access-Control-Allow-Origin"] = "*"
        response["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response["Access-Control-Max-Age"] = "300"
        return response
    return _metadata_response(protected_resource_metadata())


@method_decorator(csrf_exempt, name="dispatch")
@method_decorator(login_not_required, name="dispatch")
class SiteHitsDynamicClientRegistrationView(View):
    """Narrow RFC 7591 endpoint for anonymous public MCP clients."""

    http_method_names = ["post", "options"]

    def post(self, request):
        request_id = _request_id(request)
        source = request.META.get("REMOTE_ADDR", "unknown")
        registration_digest = _audit_digest(request.body)

        def audit(
            outcome,
            *,
            error="",
            application=None,
            scopes=None,
            redirect_uris=(),
        ):
            client_id = application.client_id if application is not None else ""
            details = {
                "client_id": client_id,
                "client_digest": _audit_digest(client_id or request.body),
                "registration_digest": registration_digest,
                "redirect_uris": _redirect_uri_summary(redirect_uris),
                **_trusted_source_details(request),
                "decision": outcome,
                "error": error,
            }
            _record_security_event(
                event="dcr",
                outcome=outcome,
                request_id=request_id,
                application=application,
                scopes=scopes,
                subject=source,
                details=details,
            )

        def reject(error, description, *, status=400, redirect_uris=()):
            audit(
                "rejected" if status != 429 else "rate_limited",
                error=error,
                redirect_uris=redirect_uris,
            )
            return _oauth_error(error, description, status=status)

        if not _rate_limit_request(request, "register"):
            return reject(
                "invalid_client_metadata",
                "Registration rate limit exceeded.",
                status=429,
            )
        try:
            registration = parse_public_client_registration(
                request.body,
                supported_scopes=settings.SITEHITS_MCP_OAUTH_SCOPES,
                required_scopes=settings.SITEHITS_MCP_BOOTSTRAP_SCOPES,
                default_scopes=settings.SITEHITS_MCP_OAUTH_SCOPES,
                max_body_bytes=MAX_DCR_BODY_BYTES,
                max_redirect_uris=MAX_DCR_REDIRECT_URIS,
                allow_localhost=True,
            )
        except DynamicClientRegistrationError as exc:
            try:
                rejected_data = json.loads(request.body)
            except (json.JSONDecodeError, UnicodeDecodeError, RecursionError, TypeError):
                rejected_data = {}
            rejected_redirects = (
                rejected_data.get("redirect_uris", ())
                if isinstance(rejected_data, dict)
                else ()
            )
            return reject(
                exc.error,
                exc.description,
                status=exc.status,
                redirect_uris=(
                    rejected_redirects
                    if isinstance(rejected_redirects, list)
                    else ()
                ),
            )
        redirect_uris = list(registration.redirect_uris)
        scopes = list(registration.scopes)
        Application = get_application_model()
        application = Application(
            name=registration.client_name,
            redirect_uris=" ".join(redirect_uris),
            client_type=AbstractApplication.CLIENT_PUBLIC,
            authorization_grant_type=AbstractApplication.GRANT_AUTHORIZATION_CODE,
            client_secret="",
            hash_client_secret=False,
            skip_authorization=False,
            registration_source=AbstractApplication.RegistrationSource.DCR,
            allowed_scopes=scopes,
            metadata=dict(registration.metadata),
        )
        try:
            application.full_clean()
            with transaction.atomic():
                application.save()
                audit(
                    "created",
                    application=application,
                    scopes=scopes,
                    redirect_uris=redirect_uris,
                )
        except ValidationError as exc:
            return reject(
                "invalid_client_metadata",
                "; ".join(exc.messages),
                redirect_uris=redirect_uris,
            )

        response = JsonResponse(
            {
                "client_id": application.client_id,
                "client_id_issued_at": int(application.created.timestamp()),
                "client_name": application.name,
                "redirect_uris": redirect_uris,
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
                "scope": " ".join(scopes),
            },
            status=201,
        )
        return _harden_oauth_response(response)


def _record_authorization_event(
    request,
    *,
    event,
    outcome,
    client=None,
    resource="",
    requested_scopes=(),
    granted_scopes=(),
    redirect_uri="",
    decision="",
    error="",
):
    requested = _audit_scopes(requested_scopes)
    granted = _audit_scopes(granted_scopes)
    supplied_client_id = request.POST.get("client_id", "") or request.GET.get(
        "client_id", ""
    )
    details = {
        "client_id": client.client_id if client is not None else supplied_client_id[:255],
        "requested_scopes": requested,
        "granted_scopes": granted,
        "consent_decision": decision,
        "redirect_uri_digest": (
            _audit_digest(redirect_uri)
            if isinstance(redirect_uri, str) and redirect_uri
            else ""
        ),
        **_trusted_source_details(request),
        "decision": outcome,
        "error": error,
    }
    _record_security_event(
        event=event,
        outcome=outcome,
        request_id=_request_id(request),
        application=client,
        user=request.user,
        resource=(
            resource if resource == settings.SITEHITS_MCP_RESOURCE_URL else ""
        ),
        scopes=granted if granted else requested,
        subject=details["client_id"],
        details=details,
    )


@method_decorator(csrf_exempt, name="dispatch")
class SiteHitsAuthorizationView(AuthorizationView):
    """DOT authorization endpoint with byte-exact resource and consent checks."""

    template_name = "mcp_gateway/oauth_authorize.html"

    def _authorization_error_response(
        self,
        request,
        error,
        description,
        *,
        status=400,
    ):
        """Redirect a non-fatal authorization error only to a proven callback."""

        data = request.POST if request.method == "POST" else request.GET
        context = _authorization_redirect_context(data)
        if context is None:
            return _oauth_error(error, description, status=status)
        client, redirect_uri, state = context
        parameters = [
            ("error", error),
            (
                "error_description",
                _safe_oauth_error_description(description),
            ),
        ]
        if state is not None:
            parameters.append(("state", state))
        if "?" not in redirect_uri:
            separator = "?"
        elif redirect_uri.endswith(("?", "&")):
            separator = ""
        else:
            separator = "&"
        response = self.redirect(
            f"{redirect_uri}{separator}{urlencode(parameters)}",
            client,
        )
        response["Location"] = _with_canonical_authorization_issuer(
            response["Location"]
        )
        return _harden_oauth_response(response)

    def dispatch(self, request, *args, **kwargs):
        data = request.POST if request.method == "POST" else request.GET
        rate_client = _registered_client(data.get("client_id", ""))
        if not _rate_limit_request(
            request,
            "authorize",
            client=rate_client,
        ):
            _record_authorization_event(
                request,
                event="consent" if request.method == "POST" else "authorize",
                outcome="rate_limited",
                client=_registered_client(data.get("client_id", "")),
                resource=_authorization_audit_resource(data),
                requested_scopes=data.get("scope", ""),
                redirect_uri=data.get("redirect_uri", ""),
                decision="not_evaluated",
                error="rate_limit_exceeded",
            )
            return self._authorization_error_response(
                request,
                "temporarily_unavailable",
                "Authorization rate limit exceeded.",
                status=429,
            )
        if request.method == "GET":
            try:
                self._profile = self._validate_request_profile(request.GET)
            except ExactResourceError as exc:
                _record_authorization_event(
                    request,
                    event="authorize",
                    outcome="rejected",
                    client=_registered_client(request.GET.get("client_id", "")),
                    resource=_authorization_audit_resource(request.GET),
                    requested_scopes=request.GET.get("scope", ""),
                    redirect_uri=request.GET.get("redirect_uri", ""),
                    decision="not_evaluated",
                    error="invalid_target",
                )
                return self._authorization_error_response(
                    request,
                    "invalid_target",
                    str(exc),
                )
            except OAuthProfileError as exc:
                _record_authorization_event(
                    request,
                    event="authorize",
                    outcome="rejected",
                    client=_registered_client(request.GET.get("client_id", "")),
                    resource=_authorization_audit_resource(request.GET),
                    requested_scopes=request.GET.get("scope", ""),
                    redirect_uri=request.GET.get("redirect_uri", ""),
                    decision="not_evaluated",
                    error=exc.error,
                )
                return self._authorization_error_response(
                    request,
                    exc.error,
                    str(exc),
                )
        response = super().dispatch(request, *args, **kwargs)
        if 300 <= response.status_code < 400 and response.has_header("Location"):
            location = response["Location"]
            callback = _authorization_redirect_context(data)
            if callback is not None and location.startswith(callback[1]):
                response["Location"] = _with_canonical_authorization_issuer(location)
        if request.method == "GET" and hasattr(self, "_profile"):
            client, scopes, resource, redirect_uri = self._profile
            _record_authorization_event(
                request,
                event="authorize",
                outcome=(
                    "consent_presented" if response.status_code == 200 else "login_required"
                ),
                client=client,
                resource=resource,
                requested_scopes=scopes,
                redirect_uri=redirect_uri,
                decision="pending",
            )
        elif request.method == "POST" and not OAuthSecurityEvent.objects.filter(
            request_id=_request_id(request),
            event="consent",
        ).exists():
            _record_authorization_event(
                request,
                event="consent",
                outcome="rejected",
                client=_registered_client(request.POST.get("client_id", "")),
                resource=_authorization_audit_resource(request.POST),
                requested_scopes=request.POST.get("scope", ""),
                redirect_uri=request.POST.get("redirect_uri", ""),
                decision="not_evaluated",
                error="csrf_rejected" if response.status_code == 403 else "invalid_request",
            )
        return _harden_oauth_response(response)

    def _validate_request_profile(self, data, *, post=False):
        _validate_authorization_parameter_cardinality(data)
        client = _registered_client(data.get("client_id", ""))
        if client is None:
            raise OAuthProfileError(
                "invalid_request",
                "OAuth client is not registered or active.",
            )
        if data.get("response_type") != "code":
            raise OAuthProfileError(
                "unsupported_response_type",
                "response_type must be code.",
            )
        if data.get("code_challenge_method") != "S256" or not _PKCE_S256_PATTERN.fullmatch(
            data.get("code_challenge", "")
        ):
            raise OAuthProfileError(
                "invalid_request",
                "A valid S256 PKCE challenge is required.",
            )
        redirect_uri = data.get("redirect_uri")
        if not isinstance(redirect_uri, str) or not client.redirect_uri_allowed(redirect_uri):
            raise OAuthProfileError(
                "invalid_request",
                "redirect_uri does not match the registered callback.",
            )
        raw_scope = data.get("scope")
        if not isinstance(raw_scope, str) or not raw_scope.strip():
            raise OAuthProfileError("invalid_scope", "scope is required.")
        try:
            scopes = normalize_scopes(raw_scope.split())
        except ValueError as exc:
            raise OAuthProfileError("invalid_scope", str(exc)) from exc
        if not set(scopes).issubset(set(client.allowed_scopes)):
            raise OAuthProfileError(
                "invalid_scope",
                "Requested scopes exceed the client registration.",
            )
        if hasattr(data, "lists"):
            resource = exact_resource_from_request_pairs(_query_pairs(data))
        elif post:
            resource = exact_resource_from_request_pairs([("resource", data.get("resource", ""))])
        else:
            resource = exact_resource_from_request_pairs(_query_pairs(data))
        return client, scopes, resource, redirect_uri

    def get(self, request, *args, **kwargs):
        query = request.GET.copy()
        query["approval_prompt"] = "force"
        query.setlist("resource", [self._profile[2]])
        request.GET = query
        return super().get(request, *args, **kwargs)

    @method_decorator(csrf_protect)
    def post(self, request, *args, **kwargs):
        # The middleware exemption lets dispatch harden the inner CSRF check's
        # 403 response with OAuth no-store/no-referrer headers.
        return super().post(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = getattr(self, "_profile", None)
        if profile is not None:
            context["scopes_descriptions"] = [
                *context.get("scopes_descriptions", []),
                f"Exact resource: {profile[2]}",
            ]
        return context

    def form_valid(self, form):
        try:
            client, scopes, resource, redirect_uri = self._validate_request_profile(
                self.request.POST,
            )
        except ExactResourceError as exc:
            _record_authorization_event(
                self.request,
                event="consent",
                outcome="rejected",
                client=_registered_client(self.request.POST.get("client_id", "")),
                resource=_authorization_audit_resource(self.request.POST),
                requested_scopes=self.request.POST.get("scope", ""),
                redirect_uri=self.request.POST.get("redirect_uri", ""),
                decision="not_evaluated",
                error="invalid_target",
            )
            return self._authorization_error_response(
                self.request,
                "invalid_target",
                str(exc),
            )
        except OAuthProfileError as exc:
            _record_authorization_event(
                self.request,
                event="consent",
                outcome="rejected",
                client=_registered_client(self.request.POST.get("client_id", "")),
                resource=_authorization_audit_resource(self.request.POST),
                requested_scopes=self.request.POST.get("scope", ""),
                redirect_uri=self.request.POST.get("redirect_uri", ""),
                decision="not_evaluated",
                error=exc.error,
            )
            return self._authorization_error_response(
                self.request,
                exc.error,
                str(exc),
            )
        decision = (
            OAuthConsent.Decision.APPROVED
            if form.cleaned_data.get("allow")
            else OAuthConsent.Decision.DENIED
        )
        response = super().form_valid(form)
        if 300 <= response.status_code < 400 and response.has_header("Location"):
            response["Location"] = _with_canonical_authorization_issuer(
                response["Location"]
            )
        consent = OAuthConsent.objects.create(
            request_id=_request_id(self.request),
            user=self.request.user,
            application=client,
            resource=resource,
            scopes=scopes,
            redirect_uri_digest=redirect_uri_digest(redirect_uri),
            decision=decision,
        )
        if decision == OAuthConsent.Decision.DENIED:
            consent.revoked_at = consent.created_at
            consent.save(update_fields=["revoked_at"])
        _record_authorization_event(
            self.request,
            event="consent",
            outcome=decision,
            client=client,
            resource=resource,
            requested_scopes=scopes,
            granted_scopes=(
                scopes if decision == OAuthConsent.Decision.APPROVED else []
            ),
            redirect_uri=redirect_uri,
            decision=decision,
        )
        return response


@method_decorator(csrf_exempt, name="dispatch")
@method_decorator(login_not_required, name="dispatch")
@method_decorator(
    sensitive_post_parameters("code", "code_verifier", "refresh_token", "client_secret"),
    name="dispatch",
)
class SiteHitsTokenView(TokenView):
    def dispatch(self, request, *args, **kwargs):
        rate_client = _registered_client(request.POST.get("client_id", ""))
        if not _rate_limit_request(request, "token", client=rate_client):
            grant_type = request.POST.get("grant_type", "")
            _record_token_lifecycle_event(
                request,
                grant_type=grant_type,
                outcome="rate_limited",
                error="rate_limit_exceeded",
                client=_registered_client(request.POST.get("client_id", "")),
            )
            return _oauth_error(
                "temporarily_unavailable",
                "Token rate limit exceeded.",
                status=429,
                token_response=True,
            )
        response = super().dispatch(request, *args, **kwargs)
        return _harden_oauth_response(response, token_response=True)

    @method_decorator(
        sensitive_post_parameters(
            "code",
            "code_verifier",
            "refresh_token",
            "client_secret",
        )
    )
    def post(self, request, *args, **kwargs):
        repeated = _repeated_parameter(request.POST, _TOKEN_SINGLE_VALUE_FIELDS)
        if repeated is not None:
            try:
                audit_resource = exact_resource_from_request_pairs(
                    _query_pairs(request.POST)
                )
            except (ValueError, ExactResourceError):
                audit_resource = ""
            _record_token_lifecycle_event(
                request,
                grant_type="",
                outcome="rejected",
                error="invalid_request",
                resource=audit_resource,
            )
            return _oauth_error(
                "invalid_request",
                f"{repeated} must not be repeated.",
                token_response=True,
            )
        grant_type = request.POST.get("grant_type", "")
        client = _registered_client(request.POST.get("client_id", ""))
        if _uses_confidential_client_auth(request):
            _record_token_lifecycle_event(
                request,
                grant_type=grant_type,
                outcome="rejected",
                error="invalid_client",
                client=client,
            )
            return _oauth_error(
                "invalid_client",
                "SiteHits accepts public clients without a client secret.",
                status=401,
                token_response=True,
            )
        try:
            resource = exact_resource_from_request_pairs(_query_pairs(request.POST))
        except (ValueError, ExactResourceError) as exc:
            _record_token_lifecycle_event(
                request,
                grant_type=grant_type,
                outcome="rejected",
                error="invalid_target",
                client=client,
            )
            return _oauth_error("invalid_target", str(exc), token_response=True)
        if grant_type not in {"authorization_code", "refresh_token"}:
            _record_token_lifecycle_event(
                request,
                grant_type=grant_type,
                outcome="rejected",
                error="unsupported_grant_type",
                resource=resource,
                client=client,
            )
            return _oauth_error(
                "unsupported_grant_type",
                "Only authorization_code and refresh_token are supported.",
                token_response=True,
            )
        profile_error = _token_profile_error(request.POST, grant_type)
        if profile_error is not None:
            _record_token_lifecycle_event(
                request,
                grant_type=grant_type,
                outcome="rejected",
                error="invalid_request",
                resource=resource,
                client=client,
            )
            return _oauth_error(
                "invalid_request",
                profile_error,
                token_response=True,
            )
        if client is None:
            _record_token_lifecycle_event(
                request,
                grant_type=grant_type,
                outcome="rejected",
                error="invalid_client",
                resource=resource,
            )
            return _oauth_error(
                "invalid_client",
                "OAuth client is not registered or active.",
                status=401,
                token_response=True,
            )
        audit_snapshot = _token_audit_snapshot(request, grant_type)
        refresh_decision = None
        capture_token = (
            begin_refresh_audit_capture()
            if grant_type == "refresh_token"
            else None
        )
        # Keep refresh-family validation, rotation, and persistence in one
        # transaction so the durable family row remains locked until the new
        # member is either committed or rejected.
        try:
            with transaction.atomic():
                response = super().post(request, *args, **kwargs)
        finally:
            if capture_token is not None:
                refresh_decision = current_refresh_audit_decision()
                end_refresh_audit_capture(capture_token)
        outcome = "issued" if response.status_code == 200 else "rejected"
        response_payload = _oauth_response_payload(response)
        if response.status_code == 200:
            type(client).objects.filter(pk=client.pk).update(last_used_at=timezone.now())
        _record_token_lifecycle_event(
            request,
            grant_type=grant_type,
            outcome=outcome,
            error=str(response_payload.get("error", ""))[:128],
            resource=resource,
            client=client,
            response_payload=response_payload,
            snapshot=audit_snapshot,
            refresh_decision=refresh_decision,
        )
        return response


def _revocation_records(request, client):
    raw_token = request.POST.get("token", "")
    token_checksum = credential_digest(raw_token) if raw_token else ""
    del raw_token
    if not token_checksum or client is None:
        return token_checksum, None, None
    refresh = (
        OAuthRefreshToken.objects.select_related(
            "application",
            "user",
            "access_token",
            "family_state",
        )
        .filter(token_checksum=token_checksum, application=client)
        .order_by(F("revoked").desc(nulls_first=True))
        .first()
    )
    access = (
        OAuthAccessToken.objects.select_related("application", "user")
        .filter(token_checksum=token_checksum, application=client)
        .first()
    )
    return token_checksum, refresh, access


def _record_revocation_event(
    request,
    *,
    outcome,
    error="",
    client=None,
    token_checksum="",
    refresh=None,
    access=None,
    scopes=None,
    snapshot=None,
):
    snapshot = snapshot or _credential_audit_snapshot(
        refresh=refresh,
        access=access,
    )
    record = refresh or access
    actual_client = (
        client or snapshot.application or getattr(record, "application", None)
    )
    actual_user = snapshot.user or getattr(record, "user", None)
    resource = snapshot.resource or _resource_from_record(record)
    family_id = snapshot.family_id
    grant_digest = snapshot.grant_digest or getattr(
        record,
        "authorization_code_digest",
        "",
    )
    family_revoked = bool(
        family_id
        and OAuthRefreshFamily.objects.filter(
            pk=family_id,
            revoked_at__isnull=False,
        ).exists()
    )
    if family_id is not None:
        decision = "family_revoked" if family_revoked else "family_not_revoked"
    elif access is not None:
        decision = "access_revoked" if access.revoked_at else "access_not_revoked"
    else:
        decision = "credential_not_found"
    supplied_client_id = request.POST.get("client_id", "")
    details = {
        "client_id": (
            actual_client.client_id
            if actual_client is not None
            else supplied_client_id[:255]
        ),
        "credential_digest": token_checksum,
        "grant_digest": grant_digest,
        "family_digest": _family_digest(family_id),
        "replay_detected": False,
        "family_revoked": family_revoked,
        "revoke_decision": decision,
        **_trusted_source_details(request),
        "decision": outcome,
        "error": error,
    }
    _record_security_event(
        event="revoke",
        outcome=outcome,
        request_id=_request_id(request),
        application=actual_client,
        user=actual_user,
        resource=resource,
        scopes=_audit_scopes(snapshot.scopes if scopes is None else scopes),
        subject=token_checksum,
        details=details,
    )


@method_decorator(csrf_exempt, name="dispatch")
@method_decorator(login_not_required, name="dispatch")
@method_decorator(
    sensitive_post_parameters("token", "client_secret"),
    name="dispatch",
)
class SiteHitsRevokeTokenView(RevokeTokenView):
    def dispatch(self, request, *args, **kwargs):
        rate_client = _registered_client(request.POST.get("client_id", ""))
        if not _rate_limit_request(request, "revoke", client=rate_client):
            client = _registered_client(request.POST.get("client_id", ""))
            checksum, refresh, access = _revocation_records(request, client)
            _record_revocation_event(
                request,
                outcome="rate_limited",
                error="rate_limit_exceeded",
                client=client,
                token_checksum=checksum,
                refresh=refresh,
                access=access,
            )
            return _oauth_error(
                "temporarily_unavailable",
                "Revocation rate limit exceeded.",
                status=429,
                token_response=True,
            )
        response = super().dispatch(request, *args, **kwargs)
        return _harden_oauth_response(response, token_response=True)

    @method_decorator(sensitive_post_parameters("token", "client_secret"))
    def post(self, request, *args, **kwargs):
        repeated = _repeated_parameter(request.POST, _REVOKE_SINGLE_VALUE_FIELDS)
        if repeated is not None:
            _record_revocation_event(
                request,
                outcome="rejected",
                error="invalid_request",
            )
            return _oauth_error(
                "invalid_request",
                f"{repeated} must not be repeated.",
                token_response=True,
            )
        client = _registered_client(request.POST.get("client_id", ""))
        if _uses_confidential_client_auth(request):
            _record_revocation_event(
                request,
                outcome="rejected",
                error="invalid_client",
                client=client,
            )
            return _oauth_error(
                "invalid_client",
                "SiteHits accepts public clients without a client secret.",
                status=401,
                token_response=True,
            )
        token_checksum, refresh, access = _revocation_records(request, client)
        audit_snapshot = _credential_audit_snapshot(
            refresh=refresh,
            access=access,
        )
        if refresh is not None and refresh.access_token is not None:
            scopes = refresh.access_token.scope.split()
        elif access is not None:
            scopes = access.scope.split()
        else:
            scopes = []
        response = super().post(request, *args, **kwargs)
        if refresh is None and access is not None:
            refresh = (
                OAuthRefreshToken.objects.select_related(
                    "application",
                    "user",
                    "access_token",
                    "family_state",
                )
                .filter(Q(access_token=access) | Q(pk=access.source_refresh_token_id))
                .filter(application=client)
                .first()
            )
        if refresh is not None:
            refresh.refresh_from_db()
            if refresh.family_state_id:
                refresh.family_state.refresh_from_db()
        if access is not None:
            access.refresh_from_db()
        payload = _oauth_response_payload(response)
        _record_revocation_event(
            request,
            outcome="accepted" if response.status_code == 200 else "rejected",
            error=str(payload.get("error", ""))[:128],
            client=client,
            token_checksum=token_checksum,
            refresh=refresh,
            access=access,
            scopes=scopes,
            snapshot=audit_snapshot,
        )
        return response
