import base64
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, close_old_connections, connection, connections
from django.db.migrations.executor import MigrationExecutor
from django.test import Client
from django.utils import timezone
from oauth2_provider.models import (
    AbstractApplication,
    get_access_token_model,
    get_application_model,
    get_grant_model,
    get_id_token_model,
    get_refresh_token_model,
)
from oauthlib.oauth2.rfc6749.errors import InvalidGrantError

from agent_runtime import ApplicationError, RequestContext, SiteHitsService
from agent_runtime.revisions import revision_for
from analytics.models import AgentAuditEvent
from mcp_gateway.oauth import (
    ACCESS_TOKEN_TTL,
    AUTHORIZATION_CODE_TTL,
    REFRESH_FAMILY_TTL,
    SiteHitsOAuth2Validator,
    credential_digest,
    loopback_redirects_match,
    validate_registered_redirect_uri,
)
from mcp_oauth.models import (
    OAuthAccessToken,
    OAuthApplication,
    OAuthConsent,
    OAuthGrant,
    OAuthIDToken,
    OAuthRefreshFamily,
    OAuthRefreshToken,
)
from websites.models import TrackedSite

pytestmark = pytest.mark.django_db(transaction=True)

REDIRECT_URI = "http://127.0.0.1:43127/callback?source=codex"


@pytest.fixture(autouse=True)
def _stage1_oauth_settings(settings):
    resource = "https://sitehits.example/mcp"
    settings.SITEHITS_MCP_RESOURCE_URL = resource
    settings.DJANGO_EMBEDDED_MCP_RESOURCE_URL = resource
    settings.SITEHITS_MCP_TOKEN_SECRET = "stage1-test-secret-that-is-independent-and-long"


def _pkce():
    verifier = "sitehits-stage-one-verifier-" + ("x" * 48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")
    return verifier, challenge


def _register(client, **overrides):
    payload = {
        "redirect_uris": [REDIRECT_URI],
        "application_type": "native",
        "client_name": "Stage 1 test client",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": "read write",
    }
    payload.update(overrides)
    if "application_type" not in overrides:
        payload["application_type"] = (
            "native"
            if all(uri.startswith("http://") for uri in payload["redirect_uris"])
            else "web"
        )
    return client.post(
        "/oauth/register/",
        data=json.dumps(payload),
        content_type="application/json",
    )


def _authorize_code(client, user):
    registration = _register(client)
    assert registration.status_code == 201, registration.content
    client_id = registration.json()["client_id"]
    verifier, challenge = _pkce()
    authorize = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "read write",
        "state": "opaque-state-value",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": settings.SITEHITS_MCP_RESOURCE_URL,
    }
    client.force_login(user)
    consent_page = client.get("/oauth/authorize/", authorize)
    assert consent_page.status_code == 200, consent_page.content
    assert settings.SITEHITS_MCP_RESOURCE_URL.encode() in consent_page.content
    assert consent_page["Cache-Control"] == "no-store"
    assert consent_page["Referrer-Policy"] == "no-referrer"
    assert consent_page["X-Robots-Tag"] == "noindex, nofollow"

    consent = client.post("/oauth/authorize/", {**authorize, "allow": "Authorize"})
    assert consent.status_code == 302, consent.content
    callback = urlsplit(consent["Location"])
    callback_query = parse_qs(callback.query)
    assert callback_query["state"] == ["opaque-state-value"]
    raw_code = callback_query["code"][0]
    return client_id, raw_code, verifier


def _authorize_and_exchange(client, user):
    client_id, raw_code, verifier = _authorize_code(client, user)

    issued = client.post(
        "/oauth/token/",
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "code": raw_code,
            "code_verifier": verifier,
            "resource": settings.SITEHITS_MCP_RESOURCE_URL,
        },
    )
    assert issued.status_code == 200, issued.content
    assert issued["Cache-Control"] == "no-store"
    assert issued["Pragma"] == "no-cache"
    return client_id, raw_code, issued.json()


def test_dot_models_are_swapped_in_one_initial_migration():
    assert get_application_model() is OAuthApplication
    assert get_grant_model() is OAuthGrant
    assert get_access_token_model() is OAuthAccessToken
    assert get_refresh_token_model() is OAuthRefreshToken
    assert get_id_token_model() is OAuthIDToken


@pytest.mark.parametrize(
    "uri",
    [
        "https://client.example/callback?fixed=1",
        "http://127.0.0.1/callback",
        "http://127.0.0.1:49152/callback",
        "http://[::1]:49152/callback",
        "http://localhost:49152/callback",
    ],
)
def test_redirect_registration_accepts_https_or_supported_loopback(uri):
    validate_registered_redirect_uri(uri)


@pytest.mark.parametrize(
    "uri",
    [
        "https://localhost/callback",
        "http://LOCALHOST/callback",
        "http://0.0.0.0/callback",
        "https://0.0.0.0/callback",
        "http://127.0.0.2/callback",
        "HTTP://127.0.0.1/callback",
        "https://*.example.com/callback",
        "http://user@127.0.0.1/callback",
        "https://client.example/callback#fragment",
    ],
)
def test_redirect_registration_rejects_unapproved_hosts_and_unsafe_components(uri):
    with pytest.raises(ValueError):
        validate_registered_redirect_uri(uri)


def test_loopback_redirect_matching_allows_only_dynamic_port_change():
    registered = "http://127.0.0.1:4000/callback?fixed=1"
    assert loopback_redirects_match(
        registered,
        "http://127.0.0.1:49152/callback?fixed=1",
    )
    assert not loopback_redirects_match(
        registered,
        "http://[::1]:49152/callback?fixed=1",
    )
    assert not loopback_redirects_match(
        registered,
        "http://127.0.0.1:49152/other?fixed=1",
    )
    assert not loopback_redirects_match(
        registered,
        "http://127.0.0.1:49152/callback?fixed=2",
    )
    claude_registered = "http://localhost:4000/callback"
    assert loopback_redirects_match(
        claude_registered,
        "http://localhost:49152/callback",
    )
    assert not loopback_redirects_match(
        claude_registered,
        "http://localhost:49152/other",
    )


def test_dcr_is_public_only_bounded_and_never_returns_a_secret():
    client = Client()
    response = _register(client)
    assert response.status_code == 201
    assert response["Cache-Control"] == "no-store"
    registration = response.json()
    assert registration["token_endpoint_auth_method"] == "none"
    assert registration["grant_types"] == ["authorization_code", "refresh_token"]
    assert "client_secret" not in registration

    application = OAuthApplication.objects.get(client_id=registration["client_id"])
    assert application.client_type == AbstractApplication.CLIENT_PUBLIC
    assert application.client_secret == ""
    assert application.hash_client_secret is False
    assert application.skip_authorization is False
    assert application.registration_source == AbstractApplication.RegistrationSource.DCR

    confidential = _register(client, token_endpoint_auth_method="client_secret_post")
    assert confidential.status_code == 400
    assert confidential.json()["error"] == "invalid_client_metadata"
    oversized = client.post(
        "/oauth/register/",
        data=b"{" + (b"x" * (16 * 1024)),
        content_type="application/json",
    )
    assert oversized.status_code == 413


def test_authorization_code_and_tokens_are_digest_only_and_exactly_bound():
    user = get_user_model().objects.create_user(username=f"oauth-{uuid4().hex}")
    client = Client()
    client_id, raw_code, token = _authorize_and_exchange(client, user)
    now = timezone.now()

    grant = OAuthGrant.objects.get(code_digest=credential_digest(raw_code))
    assert grant.code == ""
    assert grant.consumed_at is not None
    assert grant.resource == [settings.SITEHITS_MCP_RESOURCE_URL]
    assert timedelta(seconds=55) <= grant.expires - grant.created <= (
        AUTHORIZATION_CODE_TTL + timedelta(seconds=5)
    )

    access = OAuthAccessToken.objects.get(
        token_checksum=credential_digest(token["access_token"])
    )
    refresh = OAuthRefreshToken.objects.get(
        token_checksum=credential_digest(token["refresh_token"])
    )
    assert access.token == refresh.token == ""
    assert access.authorization_code_digest == grant.code_digest
    assert refresh.authorization_code_digest == grant.code_digest
    assert access.resource == refresh.resource == [settings.SITEHITS_MCP_RESOURCE_URL]
    assert ACCESS_TOKEN_TTL - timedelta(seconds=5) <= access.expires - now <= (
        ACCESS_TOKEN_TTL + timedelta(seconds=5)
    )
    assert REFRESH_FAMILY_TTL - timedelta(seconds=5) <= (
        refresh.family_expires_at - refresh.created
    ) <= REFRESH_FAMILY_TTL + timedelta(seconds=5)
    assert access.application.client_id == client_id
    assert access.is_valid(["read"])


def test_authorization_code_consumption_has_one_winner_and_marks_replay():
    user = get_user_model().objects.create_user(username=f"oauth-{uuid4().hex}")
    application_id = _register(Client()).json()["client_id"]
    application = OAuthApplication.objects.get(client_id=application_id)
    raw_code = "opaque-authorization-code-for-atomic-transition"
    grant = OAuthGrant.objects.create(
        code="",
        code_digest=credential_digest(raw_code),
        user=user,
        application=application,
        expires=timezone.now() + AUTHORIZATION_CODE_TTL,
        redirect_uri=REDIRECT_URI,
        scope="read",
        code_challenge=_pkce()[1],
        code_challenge_method="S256",
        resource=[settings.SITEHITS_MCP_RESOURCE_URL],
    )
    validator = SiteHitsOAuth2Validator()

    assert validator.confirm_redirect_uri(
        application.client_id,
        raw_code,
        REDIRECT_URI,
        application,
    )
    with pytest.raises(InvalidGrantError):
        validator.confirm_redirect_uri(
            application.client_id,
            raw_code,
            REDIRECT_URI,
            application,
        )
    grant.refresh_from_db()
    assert grant.consumed_at is not None
    assert grant.replayed_at is not None


def test_resource_is_mandatory_and_consent_post_is_revalidated():
    user = get_user_model().objects.create_user(username=f"oauth-{uuid4().hex}")
    client = Client()
    registration = _register(client).json()
    verifier, challenge = _pkce()
    authorize = {
        "client_id": registration["client_id"],
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "read",
        "state": "state",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    client.force_login(user)
    missing = client.get("/oauth/authorize/", authorize)
    assert missing.status_code == 302
    missing_query = parse_qs(urlsplit(missing["Location"]).query)
    assert missing_query["error"] == ["invalid_target"]
    assert missing_query["state"] == ["state"]
    assert missing["Cache-Control"] == "no-store"

    valid = {**authorize, "resource": settings.SITEHITS_MCP_RESOURCE_URL}
    assert client.get("/oauth/authorize/", valid).status_code == 200
    tampered = client.post(
        "/oauth/authorize/",
        {**valid, "resource": f"{settings.SITEHITS_MCP_RESOURCE_URL}/"},
    )
    assert tampered.status_code == 302
    tampered_query = parse_qs(urlsplit(tampered["Location"]).query)
    assert tampered_query["error"] == ["invalid_target"]
    assert tampered_query["state"] == ["state"]
    assert not OAuthGrant.objects.filter(application__client_id=registration["client_id"]).exists()
    assert not OAuthConsent.objects.filter(
        application__client_id=registration["client_id"]
    ).exists()

    exchange_without_resource = client.post(
        "/oauth/token/",
        {
            "grant_type": "authorization_code",
            "client_id": registration["client_id"],
            "code": "not-a-real-code",
            "code_verifier": verifier,
            "redirect_uri": REDIRECT_URI,
        },
    )
    assert exchange_without_resource.status_code == 400
    assert exchange_without_resource.json()["error"] == "invalid_target"


def test_code_replay_revokes_the_issued_refresh_family():
    user = get_user_model().objects.create_user(username=f"oauth-{uuid4().hex}")
    client = Client()
    client_id, raw_code, token = _authorize_and_exchange(client, user)
    replay = client.post(
        "/oauth/token/",
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "code": raw_code,
            "code_verifier": _pkce()[0],
            "resource": settings.SITEHITS_MCP_RESOURCE_URL,
        },
    )
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"
    refresh = OAuthRefreshToken.objects.get(
        token_checksum=credential_digest(token["refresh_token"])
    )
    access = OAuthAccessToken.objects.get(
        token_checksum=credential_digest(token["access_token"])
    )
    assert refresh.family_revoked_at is not None
    assert access.revoked_at is not None


def test_refresh_rotation_is_single_use_and_replay_revokes_the_family():
    user = get_user_model().objects.create_user(username=f"oauth-{uuid4().hex}")
    client = Client()
    client_id, _, first = _authorize_and_exchange(client, user)
    refresh_request = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": first["refresh_token"],
        "resource": settings.SITEHITS_MCP_RESOURCE_URL,
    }
    rotated = client.post("/oauth/token/", refresh_request)
    assert rotated.status_code == 200, rotated.content
    second = rotated.json()
    assert second["refresh_token"] != first["refresh_token"]

    original = OAuthRefreshToken.objects.get(
        token_checksum=credential_digest(first["refresh_token"])
    )
    replacement = OAuthRefreshToken.objects.get(
        token_checksum=credential_digest(second["refresh_token"])
    )
    assert original.revoked is not None
    assert original.token_family == replacement.token_family
    assert original.family_expires_at == replacement.family_expires_at

    replay = client.post("/oauth/token/", refresh_request)
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"
    original.refresh_from_db()
    replacement.refresh_from_db()
    assert original.family_revoked_at is not None
    assert replacement.family_revoked_at is not None
    replacement_access = OAuthAccessToken.objects.get(
        token_checksum=credential_digest(second["access_token"])
    )
    assert replacement_access.revoked_at is not None


def test_refresh_checksum_is_unconditionally_unique():
    user = get_user_model().objects.create_user(username=f"oauth-{uuid4().hex}")
    _, _, token = _authorize_and_exchange(Client(), user)
    refresh = OAuthRefreshToken.objects.select_related("family_state").get(
        token_checksum=credential_digest(token["refresh_token"])
    )

    with pytest.raises(IntegrityError):
        OAuthRefreshToken.objects.create(
            user=refresh.user,
            application=refresh.application,
            token="",
            token_checksum=refresh.token_checksum,
            token_family=refresh.token_family,
            family_state=refresh.family_state,
            family_expires_at=refresh.family_expires_at,
            resource=refresh.resource,
            authorization_code_digest=refresh.authorization_code_digest,
        )

    assert OAuthRefreshToken.objects.filter(
        token_checksum=refresh.token_checksum
    ).count() == 1


def test_member_inserted_after_family_revocation_is_terminal_and_revokes_access():
    user = get_user_model().objects.create_user(username=f"oauth-{uuid4().hex}")
    _, _, token = _authorize_and_exchange(Client(), user)
    refresh = OAuthRefreshToken.objects.select_related("family_state").get(
        token_checksum=credential_digest(token["refresh_token"])
    )
    family = refresh.family_state
    family.revoke()

    raw_access = f"access-born-after-family-revocation-{uuid4().hex}"
    access = OAuthAccessToken.objects.create(
        user=user,
        application=refresh.application,
        token="",
        token_checksum=credential_digest(raw_access),
        expires=timezone.now() + ACCESS_TOKEN_TTL,
        scope="read",
        resource=[settings.SITEHITS_MCP_RESOURCE_URL],
        authorization_code_digest=refresh.authorization_code_digest,
    )
    raw_refresh = f"refresh-born-after-family-revocation-{uuid4().hex}"
    late_member = OAuthRefreshToken.objects.create(
        user=user,
        application=refresh.application,
        access_token=access,
        token="",
        token_checksum=credential_digest(raw_refresh),
        token_family=family.pk,
        family_state=family,
        family_expires_at=family.expires_at,
        resource=[family.resource],
        authorization_code_digest=refresh.authorization_code_digest,
    )

    late_member.refresh_from_db()
    access.refresh_from_db()
    assert late_member.family_state_id == family.pk
    assert late_member.family_revoked_at == family.revoked_at
    assert late_member.revoked is not None
    assert late_member.revoked >= late_member.created
    assert access.revoked_at == late_member.revoked


def test_refresh_family_identity_mirror_is_database_enforced():
    user = get_user_model().objects.create_user(username=f"oauth-{uuid4().hex}")
    _, _, token = _authorize_and_exchange(Client(), user)
    refresh = OAuthRefreshToken.objects.get(
        token_checksum=credential_digest(token["refresh_token"])
    )

    with pytest.raises(IntegrityError):
        OAuthRefreshToken.objects.filter(pk=refresh.pk).update(
            token_family=uuid4()
        )

    refresh.refresh_from_db()
    assert refresh.token_family == refresh.family_state_id


@pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="PostgreSQL-only refresh replay/rotation concurrency evidence",
)
def test_concurrent_refresh_replay_has_one_winner_and_revokes_new_member(monkeypatch):
    """Exercise the real endpoint with independent PostgreSQL connections.

    SQLite does not provide the row-lock semantics required as release evidence,
    so it is intentionally skipped there rather than treated as a concurrency
    substitute.
    """

    user = get_user_model().objects.create_user(username=f"oauth-{uuid4().hex}")
    setup_client = Client()
    client_id, _, first = _authorize_and_exchange(setup_client, user)
    original = OAuthRefreshToken.objects.select_related("family_state").get(
        token_checksum=credential_digest(first["refresh_token"])
    )
    family_id = original.family_state_id
    rendezvous = Barrier(2)
    original_validate = SiteHitsOAuth2Validator.validate_refresh_token

    def synchronized_validate(validator, *args, **kwargs):
        rendezvous.wait(timeout=10)
        return original_validate(validator, *args, **kwargs)

    monkeypatch.setattr(
        SiteHitsOAuth2Validator,
        "validate_refresh_token",
        synchronized_validate,
    )
    payload = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": first["refresh_token"],
        "resource": settings.SITEHITS_MCP_RESOURCE_URL,
    }

    def rotate_once():
        close_old_connections()
        try:
            response = Client().post("/oauth/token/", payload)
            return response.status_code, response.json()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: rotate_once(), range(2)))

    assert sorted(status for status, _ in results) == [200, 400]
    winner = next(body for status, body in results if status == 200)
    loser = next(body for status, body in results if status == 400)
    assert loser["error"] == "invalid_grant", loser

    family = OAuthRefreshFamily.objects.get(pk=family_id)
    replacement = OAuthRefreshToken.objects.get(
        token_checksum=credential_digest(winner["refresh_token"])
    )
    replacement_access = OAuthAccessToken.objects.get(
        token_checksum=credential_digest(winner["access_token"])
    )
    assert family.revoked_at is not None
    assert replacement.family_state_id == family.pk
    assert replacement.family_revoked_at == family.revoked_at
    assert replacement.revoked is not None
    assert replacement_access.revoked_at is not None


@pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="PostgreSQL-only authorization-code exchange concurrency evidence",
)
def test_concurrent_authorization_code_exchange_has_one_winner_and_terminal_family(
    monkeypatch,
):
    user = get_user_model().objects.create_user(username=f"oauth-{uuid4().hex}")
    client_id, raw_code, verifier = _authorize_code(Client(), user)
    rendezvous = Barrier(2)
    original_confirm = SiteHitsOAuth2Validator.confirm_redirect_uri

    def synchronized_confirm(validator, *args, **kwargs):
        rendezvous.wait(timeout=10)
        return original_confirm(validator, *args, **kwargs)

    monkeypatch.setattr(
        SiteHitsOAuth2Validator,
        "confirm_redirect_uri",
        synchronized_confirm,
    )
    payload = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "code": raw_code,
        "code_verifier": verifier,
        "resource": settings.SITEHITS_MCP_RESOURCE_URL,
    }

    def exchange_once():
        close_old_connections()
        try:
            response = Client().post("/oauth/token/", payload)
            return response.status_code, response.json()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: exchange_once(), range(2)))

    assert sorted(status for status, _ in results) == [200, 400]
    winner = next(body for status, body in results if status == 200)
    loser = next(body for status, body in results if status == 400)
    assert loser["error"] == "invalid_grant", loser

    code_digest = credential_digest(raw_code)
    grant = OAuthGrant.objects.get(code_digest=code_digest)
    family = OAuthRefreshFamily.objects.get(
        authorization_code_digest=code_digest
    )
    refresh = OAuthRefreshToken.objects.get(
        token_checksum=credential_digest(winner["refresh_token"])
    )
    access = OAuthAccessToken.objects.get(
        token_checksum=credential_digest(winner["access_token"])
    )
    assert grant.consumed_at is not None
    assert grant.replayed_at is not None
    assert family.revoked_at is not None
    assert refresh.family_state_id == family.pk
    assert refresh.family_revoked_at == family.revoked_at
    assert refresh.revoked is not None
    assert access.revoked_at is not None


@pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="PostgreSQL-only concurrent revocation evidence",
)
def test_concurrent_refresh_revocation_is_idempotent_and_terminal(monkeypatch):
    user = get_user_model().objects.create_user(username=f"oauth-{uuid4().hex}")
    client_id, _, token = _authorize_and_exchange(Client(), user)
    refresh = OAuthRefreshToken.objects.select_related("family_state").get(
        token_checksum=credential_digest(token["refresh_token"])
    )
    family_id = refresh.family_state_id
    rendezvous = Barrier(2)
    original_revoke = SiteHitsOAuth2Validator.revoke_token

    def synchronized_revoke(validator, *args, **kwargs):
        rendezvous.wait(timeout=10)
        return original_revoke(validator, *args, **kwargs)

    monkeypatch.setattr(
        SiteHitsOAuth2Validator,
        "revoke_token",
        synchronized_revoke,
    )
    payload = {
        "client_id": client_id,
        "token": token["refresh_token"],
        "token_type_hint": "refresh_token",
    }

    def revoke_once():
        close_old_connections()
        try:
            response = Client().post("/oauth/revoke/", payload)
            return response.status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(lambda _: revoke_once(), range(2)))

    assert statuses == [200, 200]
    family = OAuthRefreshFamily.objects.get(pk=family_id)
    refresh.refresh_from_db()
    access = OAuthAccessToken.objects.get(
        token_checksum=credential_digest(token["access_token"])
    )
    assert family.revoked_at is not None
    assert refresh.family_revoked_at == family.revoked_at
    assert refresh.revoked is not None
    assert access.revoked_at is not None


@pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="PostgreSQL-only persistent rate-bucket concurrency evidence",
)
def test_concurrent_oauth_rate_bucket_enforces_exact_limit():
    from mcp_gateway.views import _consume_bucket, _rate_subject_digest

    limit = 4
    contenders = 12
    rendezvous = Barrier(contenders)
    now = timezone.now()
    action = "token"
    subject = f"concurrent-source-{uuid4().hex}"

    def consume_once():
        close_old_connections()
        try:
            rendezvous.wait(timeout=10)
            return _consume_bucket(action, subject, limit=limit, now=now)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=contenders) as executor:
        outcomes = list(executor.map(lambda _: consume_once(), range(contenders)))

    from mcp_oauth.models import OAuthRateLimitBucket

    bucket = OAuthRateLimitBucket.objects.get(
        action=action,
        subject_digest=_rate_subject_digest(action, subject),
    )
    assert outcomes.count(True) == limit
    assert outcomes.count(False) == contenders - limit
    assert bucket.count == limit


@pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="PostgreSQL-only optimistic-revision concurrency evidence",
)
def test_concurrent_site_revision_mutation_has_one_winner():
    user = get_user_model().objects.create_user(username=f"oauth-{uuid4().hex}")
    site = TrackedSite.objects.create(
        owner=user,
        name="Concurrent revision",
        slug=f"concurrent-revision-{uuid4().hex}",
        allowed_domains=["revision.example"],
    )
    expected_revision = revision_for(site)
    rendezvous = Barrier(2)

    def update_once(index):
        close_old_connections()
        service = SiteHitsService(
            RequestContext(
                authenticated_actor_id=str(user.pk),
                authenticated_client_id=f"revision-client-{index}",
                granted_scopes=frozenset({"read", "write"}),
                request_id=f"concurrent_revision_{index}",
            )
        )
        new_name = f"Revision winner {index}"
        try:
            rendezvous.wait(timeout=10)
            result = service.update_site(
                site_slug=site.slug,
                expected_revision=expected_revision,
                name=new_name,
            )
            return "success", new_name, result["revision"]
        except ApplicationError as exc:
            return exc.code, new_name, None
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(update_once, (1, 2)))

    assert sorted(outcome[0] for outcome in outcomes) == [
        "revision_conflict",
        "success",
    ]
    winner = next(outcome for outcome in outcomes if outcome[0] == "success")
    site.refresh_from_db()
    assert site.name == winner[1]
    assert revision_for(site) == winner[2]
    assert sorted(
        AgentAuditEvent.objects.filter(
            request_id__startswith="concurrent_revision_"
        ).values_list("outcome_code", flat=True)
    ) == ["revision_conflict", "success"]


def test_refresh_family_migration_backfills_and_neutralizes_duplicate_checksums():
    """Exercise the 0001 -> 0002 data and constraint transition."""

    old_target = [("mcp_oauth", "0001_initial")]
    new_target = [("mcp_oauth", "0004_refresh_family_constraints")]
    executor = MigrationExecutor(connection)
    leaf_targets = executor.loader.graph.leaf_nodes()
    executor.migrate(old_target)
    try:
        old_apps = executor.loader.project_state(old_target).apps
        Application = old_apps.get_model("mcp_oauth", "OAuthApplication")
        RefreshToken = old_apps.get_model("mcp_oauth", "OAuthRefreshToken")
        user = get_user_model().objects.create_user(
            username=f"oauth-migration-{uuid4().hex}"
        )
        application = Application.objects.create(
            client_id=f"migration-client-{uuid4().hex}",
            redirect_uris=REDIRECT_URI,
            client_type="public",
            authorization_grant_type="authorization-code",
            client_secret="",
            hash_client_secret=False,
            skip_authorization=False,
            registration_source="dcr",
            allowed_scopes=["read"],
        )
        family_id = uuid4()
        duplicate_checksum = credential_digest("legacy-duplicate-refresh")
        legacy_fields = {
            "user_id": user.pk,
            "application_id": application.pk,
            "token": "",
            "token_checksum": duplicate_checksum,
            "token_family": family_id,
            "resource": [settings.SITEHITS_MCP_RESOURCE_URL],
            "family_expires_at": timezone.now() + REFRESH_FAMILY_TTL,
            "authorization_code_digest": credential_digest("legacy-code"),
        }
        RefreshToken.objects.create(**legacy_fields)
        RefreshToken.objects.create(**legacy_fields)

        executor = MigrationExecutor(connection)
        executor.migrate(new_target)
        new_apps = executor.loader.project_state(new_target).apps
        Family = new_apps.get_model("mcp_oauth", "OAuthRefreshFamily")
        MigratedRefreshToken = new_apps.get_model(
            "mcp_oauth", "OAuthRefreshToken"
        )
        migrated = list(MigratedRefreshToken.objects.order_by("pk"))
        family = Family.objects.get(pk=family_id)

        assert len(migrated) == 2
        assert all(record.family_state_id == family.pk for record in migrated)
        assert all(record.token_family == record.family_state_id for record in migrated)
        assert len({record.token_checksum for record in migrated}) == 2
        assert family.revoked_at is not None
        assert all(record.revoked is not None for record in migrated)
        assert all(record.family_revoked_at is not None for record in migrated)
    finally:
        MigrationExecutor(connection).migrate(leaf_targets)


def test_consent_application_and_user_state_invalidate_credentials():
    user = get_user_model().objects.create_user(username=f"oauth-{uuid4().hex}")
    client = Client()
    _, _, token = _authorize_and_exchange(client, user)
    access = OAuthAccessToken.objects.get(
        token_checksum=credential_digest(token["access_token"])
    )
    consent = OAuthConsent.objects.get(
        user=user,
        application=access.application,
        revoked_at__isnull=True,
    )
    consent.revoked_at = timezone.now()
    consent.save(update_fields=["revoked_at"])
    access.refresh_from_db()
    assert access.revoked_at is not None
    assert not access.is_valid(["read"])

    other_user = get_user_model().objects.create_user(username=f"oauth-{uuid4().hex}")
    other_client = Client()
    other_client_id, _, other_token = _authorize_and_exchange(other_client, other_user)
    other_access = OAuthAccessToken.objects.get(
        token_checksum=credential_digest(other_token["access_token"])
    )
    pending_grant = OAuthGrant.objects.create(
        code="",
        code_digest=credential_digest("pending-code-before-user-deactivation"),
        user=other_user,
        application=other_access.application,
        expires=timezone.now() + AUTHORIZATION_CODE_TTL,
        redirect_uri=REDIRECT_URI,
        scope="read",
        code_challenge=_pkce()[1],
        code_challenge_method="S256",
        resource=[settings.SITEHITS_MCP_RESOURCE_URL],
    )
    other_user.is_active = False
    other_user.save(update_fields=["is_active"])
    other_access.refresh_from_db()
    pending_grant.refresh_from_db()
    inactive_consent = OAuthConsent.objects.get(
        user=other_user,
        application=other_access.application,
    )
    inactive_family = OAuthRefreshToken.objects.get(
        token_checksum=credential_digest(other_token["refresh_token"])
    )
    assert other_access.revoked_at is not None
    assert pending_grant.consumed_at is not None
    assert inactive_consent.revoked_at is not None
    assert inactive_family.family_revoked_at is not None
    assert not other_access.is_valid(["read"])
    inactive_refresh = other_client.post(
        "/oauth/token/",
        {
            "grant_type": "refresh_token",
            "client_id": other_client_id,
            "refresh_token": other_token["refresh_token"],
            "resource": settings.SITEHITS_MCP_RESOURCE_URL,
        },
    )
    assert inactive_refresh.status_code == 400

    application = other_access.application
    application.revoked_at = timezone.now()
    application.save(update_fields=["revoked_at"])
    other_access.refresh_from_db()
    assert application.revoked_at is not None
    assert other_access.revoked_at is not None
    assert not application.is_usable(None)


def test_user_deletion_cascades_credentials_and_consents():
    user = get_user_model().objects.create_user(username=f"oauth-{uuid4().hex}")
    client = Client()
    _, raw_code, token = _authorize_and_exchange(client, user)
    application_id = OAuthAccessToken.objects.get(
        token_checksum=credential_digest(token["access_token"])
    ).application_id

    user.delete()

    assert not OAuthGrant.objects.filter(code_digest=credential_digest(raw_code)).exists()
    assert not OAuthAccessToken.objects.filter(application_id=application_id).exists()
    assert not OAuthRefreshToken.objects.filter(application_id=application_id).exists()
    assert not OAuthConsent.objects.filter(application_id=application_id).exists()
    assert OAuthApplication.objects.filter(pk=application_id).exists()
