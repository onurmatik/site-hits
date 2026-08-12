from __future__ import annotations

import json
import re
from contextvars import ContextVar
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from django_embedded_mcp.oauth import credential_digest
from django_embedded_mcp.oauth import normalize_scopes as shared_normalize_scopes
from django_embedded_mcp.redirects import (
    redirect_uri_matches as shared_redirect_uri_matches,
)
from django_embedded_mcp.redirects import (
    validate_registered_redirect_uri as shared_validate_registered_redirect_uri,
)
from django_embedded_mcp.refresh import (
    RefreshFamilyDecision,
    RefreshFamilyPolicy,
    RefreshFamilyState,
    RefreshMemberState,
)
from django_embedded_mcp.resource import (
    ExactResourceError,
    exact_resource_from_pairs,
    validate_canonical_url,
)
from oauth2_provider.models import (
    get_access_token_model,
    get_grant_model,
    get_refresh_token_model,
)
from oauth2_provider.oauth2_validators import OAuth2Validator
from oauthlib.oauth2.rfc6749 import errors

AUTHORIZATION_CODE_TTL = timedelta(seconds=60)
ACCESS_TOKEN_TTL = timedelta(minutes=15)
REFRESH_FAMILY_TTL = timedelta(days=30)
_PKCE_S256_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_REFRESH_AUDIT_DECISION: ContextVar[RefreshFamilyDecision | None] = ContextVar(
    "sitehits_refresh_audit_decision",
    default=None,
)


def begin_refresh_audit_capture():
    """Start one request-local refresh-policy decision capture."""

    return _REFRESH_AUDIT_DECISION.set(None)


def current_refresh_audit_decision() -> RefreshFamilyDecision | None:
    """Return the latest package-owned decision for the current request."""

    return _REFRESH_AUDIT_DECISION.get()


def end_refresh_audit_capture(token) -> None:
    """Restore the previous context so worker reuse cannot leak audit state."""

    _REFRESH_AUDIT_DECISION.reset(token)


def _capture_refresh_audit_decision(
    decision: RefreshFamilyDecision,
) -> RefreshFamilyDecision:
    _REFRESH_AUDIT_DECISION.set(decision)
    return decision


def configured_resource() -> str:
    """Return the configured byte-exact MCP resource after structural validation."""

    return validate_canonical_url(
        settings.SITEHITS_MCP_RESOURCE_URL,
        require_https=not settings.DEBUG,
        allow_root_path=False,
    )


def validate_registered_redirect_uri(uri: str) -> None:
    """Enforce HTTPS callbacks or the exact supported HTTP loopback hosts."""

    shared_validate_registered_redirect_uri(uri, allow_localhost=True)


def loopback_redirects_match(registered_uri: str, requested_uri: str) -> bool:
    """Allow only a loopback port change; every other URI component stays exact."""

    return shared_redirect_uri_matches(
        registered_uri,
        requested_uri,
        allow_localhost=True,
    )


def normalize_scopes(scopes, *, default=()) -> list[str]:
    try:
        return shared_normalize_scopes(
            scopes,
            supported_scopes=settings.SITEHITS_MCP_OAUTH_SCOPES,
            required_scopes=settings.SITEHITS_MCP_BOOTSTRAP_SCOPES,
            default_scopes=default,
        )
    except ValueError as exc:
        if "required scopes" in str(exc):
            required = " ".join(settings.SITEHITS_MCP_BOOTSTRAP_SCOPES)
            raise ValueError(f"The {required} scope is required.") from exc
        raise


def exact_resource_values(values) -> list[str]:
    expected = configured_resource()
    pairs = [("resource", value) for value in values]
    _, resource = exact_resource_from_pairs(pairs, expected=expected)
    return [resource]


def exact_resource_from_request_pairs(pairs) -> str:
    _, resource = exact_resource_from_pairs(pairs, expected=configured_resource())
    return resource


def redirect_uri_digest(uri: str) -> str:
    return credential_digest(uri)


def _refresh_family_policy() -> RefreshFamilyPolicy:
    return RefreshFamilyPolicy(expected_resource=configured_resource())


def _refresh_family_state(family) -> RefreshFamilyState:
    return RefreshFamilyState(
        family_id=family.pk,
        user_id=family.user_id,
        client_id=family.application_id,
        resource=family.resource,
        expires_at=family.expires_at,
        revoked_at=family.revoked_at,
    )


def _refresh_member_state(member) -> RefreshMemberState:
    return RefreshMemberState(
        user_id=member.user_id,
        client_id=member.application_id,
        family_id=member.family_state_id,
        family_mirror_id=member.token_family,
        resources=tuple(member.resource or ()),
        consumed_at=member.revoked,
    )


def _evaluate_refresh_family(family, resources, *, now=None):
    return _capture_refresh_audit_decision(
        _refresh_family_policy().evaluate_family(
            family=_refresh_family_state(family),
            requested_resources=tuple(resources),
            now=now or timezone.now(),
        )
    )


def _evaluate_refresh_rotation(record, family, client, resources, request, *, now=None):
    return _capture_refresh_audit_decision(
        _refresh_family_policy().evaluate_rotation(
            family=_refresh_family_state(family),
            member=_refresh_member_state(record),
            presented_client_id=getattr(client, "pk", None),
            requested_resources=tuple(resources),
            user_active=bool(record.user.is_active),
            client_active=bool(record.application.is_usable(request)),
            now=now or timezone.now(),
        )
    )


def _request_resource_values(request) -> list[str]:
    resource = getattr(request, "resource", None)
    decoded = getattr(request, "decoded_body", None) or []
    repeated = [value for key, value in decoded if key == "resource"]
    if repeated:
        return repeated
    if isinstance(resource, list):
        return resource
    return [resource] if isinstance(resource, str) and resource else []


def _grant_for_code(raw_code, *, application=None, include_consumed=True):
    Grant = get_grant_model()
    queryset = Grant.objects.select_related("application", "user").filter(
        code_digest=credential_digest(raw_code)
    )
    if application is not None:
        queryset = queryset.filter(application=application)
    if not include_consumed:
        queryset = queryset.filter(consumed_at__isnull=True)
    return queryset.first()


def _revoke_code_family(code_digest: str) -> None:
    Grant = get_grant_model()
    AccessToken = get_access_token_model()
    RefreshToken = get_refresh_token_model()
    with transaction.atomic():
        grant = Grant.objects.select_for_update().filter(code_digest=code_digest).first()
        if grant is None:
            return
        now = timezone.now()
        if grant.replayed_at is None:
            Grant.objects.filter(pk=grant.pk).update(replayed_at=now)
        access_tokens = AccessToken.objects.filter(
            authorization_code_digest=code_digest
        )
        family_state_ids = set(
            RefreshToken.objects.filter(authorization_code_digest=code_digest)
            .exclude(family_state=None)
            .values_list("family_state_id", flat=True)
        )
        family_state_ids.update(
            RefreshToken.objects.filter(access_token__in=access_tokens)
            .exclude(family_state=None)
            .values_list("family_state_id", flat=True)
        )
        for family_state_id in family_state_ids:
            refresh_token = RefreshToken.objects.filter(
                family_state_id=family_state_id
            ).first()
            if refresh_token is not None:
                refresh_token.revoke_family()
        access_tokens.filter(revoked_at__isnull=True).update(revoked_at=now)


class SiteHitsOAuth2Validator(OAuth2Validator):
    """DOT extension enforcing the Stage 1 public-client OAuth profile."""

    def get_default_scopes(self, client_id, request, *args, **kwargs):
        return []

    def validate_scopes(self, client_id, scopes, client, request, *args, **kwargs):
        try:
            requested = normalize_scopes(scopes)
        except ValueError:
            return False
        return set(requested).issubset(set(getattr(client, "allowed_scopes", [])))

    def validate_grant_type(self, client_id, grant_type, client, request, *args, **kwargs):
        return grant_type in {
            "authorization_code",
            "refresh_token",
        } and super().validate_grant_type(
            client_id, grant_type, client, request, *args, **kwargs
        )

    def validate_response_type(self, client_id, response_type, client, request, *args, **kwargs):
        return response_type == "code" and super().validate_response_type(
            client_id,
            response_type,
            client,
            request,
            *args,
            **kwargs,
        )

    def is_pkce_required(self, client_id, request):
        return True

    def rotate_refresh_token(self, request):
        return True

    def validate_redirect_uri(self, client_id, redirect_uri, request, *args, **kwargs):
        client = getattr(request, "client", None)
        return bool(client and client.redirect_uri_allowed(redirect_uri))

    def save_authorization_code(self, client_id, code, request, *args, **kwargs):
        Grant = get_grant_model()
        raw_code = code["code"]
        resources = exact_resource_values(_request_resource_values(request))
        if request.code_challenge_method != "S256" or not _PKCE_S256_PATTERN.fullmatch(
            request.code_challenge or ""
        ):
            raise errors.InvalidRequestError(
                description="S256 PKCE is required.",
                request=request,
            )
        if not request.client.is_usable(request) or not request.user.is_active:
            raise errors.InvalidGrantError(request=request)
        with transaction.atomic():
            Grant.objects.create(
                application=request.client,
                user=request.user,
                code="",
                code_digest=credential_digest(raw_code),
                expires=timezone.now() + AUTHORIZATION_CODE_TTL,
                redirect_uri=request.redirect_uri,
                scope=" ".join(normalize_scopes(request.scopes)),
                code_challenge=request.code_challenge,
                code_challenge_method="S256",
                nonce=request.nonce or "",
                claims=json.dumps(request.claims or {}),
                resource=resources,
            )
            # This is the durable "ever authorized" marker. It is written in
            # the same transaction as the unexchanged grant so cleanup cannot
            # later mistake this DCR client for an unused registration after
            # terminal grant metadata has been deleted.
            type(request.client).objects.filter(pk=request.client.pk).update(
                last_used_at=timezone.now()
            )

    def validate_code(self, client_id, code, client, request, *args, **kwargs):
        grant = _grant_for_code(code, application=client)
        if grant is None:
            return False
        if grant.consumed_at is not None:
            _revoke_code_family(grant.code_digest)
            return False
        try:
            resources = exact_resource_values(_request_resource_values(request))
        except (ExactResourceError, ValueError):
            return False
        if (
            grant.is_expired()
            or not grant.user.is_active
            or not grant.application.is_usable(request)
            or grant.resource != resources
        ):
            return False
        request.scopes = grant.scope.split()
        request.user = grant.user
        request.resource = resources
        if grant.nonce:
            request.nonce = grant.nonce
        if grant.claims:
            request.claims = json.loads(grant.claims)
        return True

    def get_code_challenge(self, code, request):
        grant = _grant_for_code(code, application=request.client, include_consumed=False)
        return grant.code_challenge if grant is not None else None

    def get_code_challenge_method(self, code, request):
        grant = _grant_for_code(code, application=request.client, include_consumed=False)
        return grant.code_challenge_method if grant is not None else None

    def get_authorization_code_nonce(self, client_id, code, redirect_uri, request):
        grant = _grant_for_code(code, application=request.client, include_consumed=False)
        return grant.nonce if grant is not None and grant.nonce else None

    def get_authorization_code_scopes(self, client_id, code, redirect_uri, request):
        grant = _grant_for_code(code, application=request.client, include_consumed=False)
        return grant.scope.split() if grant is not None else []

    def confirm_redirect_uri(self, client_id, code, redirect_uri, client, *args, **kwargs):
        Grant = get_grant_model()
        now = timezone.now()
        replayed_digest = None
        with transaction.atomic():
            grant = (
                Grant.objects.select_for_update()
                .select_related("application", "user")
                .filter(
                    code_digest=credential_digest(code),
                    application=client,
                )
                .first()
            )
            if grant is not None and grant.consumed_at is not None:
                replayed_digest = grant.code_digest
            if (
                grant is None
                or replayed_digest is not None
                or grant.expires <= now
                or not grant.user.is_active
                or not grant.application.is_usable(None)
                or grant.resource != [configured_resource()]
                or grant.redirect_uri != redirect_uri
            ):
                updated = 0
            else:
                updated = Grant.objects.filter(
                    pk=grant.pk,
                    consumed_at__isnull=True,
                ).update(consumed_at=now)
                if updated != 1:
                    replayed_digest = grant.code_digest
        if replayed_digest is not None:
            _revoke_code_family(replayed_digest)
            # Returning False here makes oauthlib misclassify a concurrent
            # single-use-code loss as a redirect mismatch. This transition is
            # a credential replay and must surface as invalid_grant.
            oauth_request = args[0] if args else kwargs.get("request")
            raise errors.InvalidGrantError(request=oauth_request)
        return updated == 1

    def invalidate_authorization_code(self, client_id, code, request, *args, **kwargs):
        grant = _grant_for_code(code, application=request.client)
        if grant is None or grant.consumed_at is None:
            raise errors.InvalidGrantError(request=request)

    def save_bearer_token(self, token, request, *args, **kwargs):
        resources = exact_resource_values(_request_resource_values(request))
        request.resource = resources
        code_digest = ""
        if request.grant_type == "authorization_code":
            code_digest = credential_digest(request.code)
        elif request.grant_type == "refresh_token":
            prior = getattr(request, "refresh_token_instance", None)
            code_digest = getattr(prior, "authorization_code_digest", "")

        with transaction.atomic():
            from mcp_oauth.models import OAuthRefreshFamily

            grant = None
            # Authorization-code exchange follows grant -> family lock order.
            # Refresh rotation already holds the durable family lock from
            # validation; taking the historical grant lock here would invert
            # _revoke_code_family's grant -> family order and permit deadlock.
            if request.grant_type == "authorization_code" and code_digest:
                Grant = get_grant_model()
                grant = Grant.objects.select_for_update().filter(
                    code_digest=code_digest
                ).first()
            locked_family = None
            if request.grant_type == "refresh_token":
                prior = getattr(request, "refresh_token_instance", None)
                if prior is None or prior.family_state_id is None:
                    raise errors.InvalidGrantError(request=request)
                locked_family = OAuthRefreshFamily.objects.select_for_update().get(
                    pk=prior.family_state_id
                )
                prior = get_refresh_token_model().objects.select_for_update().get(
                    pk=prior.pk
                )
                request.refresh_token_instance = prior
                family_decision = _evaluate_refresh_rotation(
                    prior,
                    locked_family,
                    request.client,
                    resources,
                    request,
                )
                if not family_decision.rotation_allowed:
                    request.refresh_family_decision = family_decision.code.value
                    locked_family.revoke()
                    raise errors.InvalidGrantError(request=request)
                claimed_at = timezone.now()
                claimed = get_refresh_token_model().objects.filter(
                    pk=prior.pk,
                    revoked__isnull=True,
                ).update(revoked=claimed_at)
                claim_decision = _capture_refresh_audit_decision(
                    _refresh_family_policy().evaluate_claim(
                        claimed_rows=claimed,
                    )
                )
                request.refresh_family_decision = claim_decision.code.value
                if not claim_decision.rotation_allowed:
                    locked_family.revoke()
                    raise errors.InvalidGrantError(request=request)
                prior.revoked = claimed_at
            result = super().save_bearer_token(token, request, *args, **kwargs)
            AccessToken = get_access_token_model()
            access = AccessToken.objects.select_for_update().get(
                token_checksum=credential_digest(token["access_token"])
            )
            access.token = ""
            access.expires = timezone.now() + ACCESS_TOKEN_TTL
            access.resource = resources
            access.authorization_code_digest = code_digest
            access.save(
                update_fields=[
                    "token",
                    "expires",
                    "resource",
                    "authorization_code_digest",
                    "updated",
                ]
            )
            raw_refresh = token.get("refresh_token")
            refresh = None
            if raw_refresh:
                RefreshToken = get_refresh_token_model()
                refresh = (
                    RefreshToken.objects.select_for_update()
                    .filter(token_checksum=credential_digest(raw_refresh))
                    .order_by(F("revoked").desc(nulls_first=True))
                    .first()
                )
                if refresh is None:
                    raise RuntimeError("OAuth refresh token persistence failed.")
                if refresh.family_state_id is None:
                    raise RuntimeError("OAuth refresh family persistence failed.")
                family_state = OAuthRefreshFamily.objects.select_for_update().get(
                    pk=refresh.family_state_id
                )
                family_decision = _evaluate_refresh_family(
                    family_state,
                    resources,
                )
                if not family_decision.rotation_allowed:
                    request.refresh_family_decision = family_decision.code.value
                    family_state.revoke()
                    raise errors.InvalidGrantError(request=request)
                if not family_state.authorization_code_digest and code_digest:
                    family_state.authorization_code_digest = code_digest
                    family_state.save(
                        update_fields=["authorization_code_digest", "updated_at"]
                    )
                refresh.token = ""
                refresh.resource = resources
                refresh.authorization_code_digest = code_digest
                refresh.save(
                    update_fields=[
                        "token",
                        "resource",
                        "authorization_code_digest",
                        "family_expires_at",
                        "updated",
                    ]
                )
            if grant is not None and grant.replayed_at is not None:
                access.revoke()
                if refresh is not None:
                    refresh.revoke_family()
            return result

    def validate_refresh_token(self, refresh_token, client, request, *args, **kwargs):
        RefreshToken = get_refresh_token_model()
        try:
            resources = exact_resource_values(_request_resource_values(request))
        except (ExactResourceError, ValueError):
            return False
        with transaction.atomic():
            from mcp_oauth.models import OAuthRefreshFamily

            record_hint = (
                RefreshToken.objects.filter(
                    token_checksum=credential_digest(refresh_token)
                )
                .values("pk", "family_state_id")
                .first()
            )
            if record_hint is None or record_hint["family_state_id"] is None:
                return False
            try:
                family_state = OAuthRefreshFamily.objects.select_for_update().get(
                    pk=record_hint["family_state_id"]
                )
            except OAuthRefreshFamily.DoesNotExist:
                return False
            record = (
                RefreshToken.objects.select_for_update()
                .filter(pk=record_hint["pk"])
                .first()
            )
            if record is None:
                return False
            family_decision = _evaluate_refresh_rotation(
                record,
                family_state,
                client,
                resources,
                request,
            )
            request.refresh_family_decision = family_decision.code.value
            if not family_decision.rotation_allowed:
                family_state.revoke()
                return False
        request.user = record.user
        request.refresh_token = refresh_token
        request.refresh_token_instance = record
        request.resource = resources
        return True

    def revoke_token(self, token, token_type_hint, request, *args, **kwargs):
        checksum = credential_digest(token)
        AccessToken = get_access_token_model()
        RefreshToken = get_refresh_token_model()
        application = getattr(request, "client", None)
        if application is None or not application.is_usable(request):
            return
        with transaction.atomic():
            from mcp_oauth.models import OAuthRefreshFamily

            refresh_hint = (
                RefreshToken.objects
                .filter(
                    token_checksum=checksum,
                    application=application,
                )
                .values("pk", "family_state_id")
                .first()
            )
            if refresh_hint is not None:
                family_state = OAuthRefreshFamily.objects.select_for_update().get(
                    pk=refresh_hint["family_state_id"]
                )
                refresh = RefreshToken.objects.select_for_update().get(
                    pk=refresh_hint["pk"]
                )
                family_state.revoke()
                refresh.family_revoked_at = family_state.revoked_at
                return
            access_hint = (
                AccessToken.objects
                .filter(
                    token_checksum=checksum,
                    application=application,
                )
                .values("pk", "source_refresh_token_id")
                .first()
            )
            if access_hint is None:
                return
            related_refresh_hint = (
                RefreshToken.objects.filter(
                    Q(access_token_id=access_hint["pk"])
                    | Q(pk=access_hint["source_refresh_token_id"])
                )
                .filter(application=application)
                .values("pk", "family_state_id")
                .first()
            )
            if related_refresh_hint is not None:
                family_state = OAuthRefreshFamily.objects.select_for_update().get(
                    pk=related_refresh_hint["family_state_id"]
                )
                family_state.revoke()
            else:
                access = AccessToken.objects.select_for_update().get(
                    pk=access_hint["pk"]
                )
                access.revoke()
