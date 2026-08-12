import base64
import hashlib
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client
from django.utils import timezone
from django_embedded_mcp.cimd import CIMDDocument, CIMDError
from oauth2_provider.models import AbstractApplication

from mcp_oauth.models import OAuthApplication, OAuthSecurityEvent

pytestmark = pytest.mark.django_db

CLIENT_ID = "https://client.example/oauth/client.json"
REDIRECT_URI = "https://client.example/oauth/callback"


@pytest.fixture(autouse=True)
def _local_oauth_origin(settings):
    settings.DEBUG = True


def _document(*, max_age_seconds=600, redirect_uri=REDIRECT_URI):
    return CIMDDocument(
        client_id=CLIENT_ID,
        client_name="CIMD test client",
        redirect_uris=(redirect_uri,),
        application_type="web",
        scopes=("read", "write"),
        max_age_seconds=max_age_seconds,
        document_sha256="a" * 64,
    )


def _pkce():
    verifier = "cimd-test-verifier-" + ("x" * 48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    return verifier, challenge


def _authorization(client_id=CLIENT_ID, redirect_uri=REDIRECT_URI):
    _, challenge = _pkce()
    return {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "read write",
        "state": "opaque-cimd-state",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": settings.SITEHITS_MCP_RESOURCE_URL,
    }


def test_cimd_first_authorize_persists_fresh_public_client_and_audits_fetch():
    user = get_user_model().objects.create_user(username="cimd-authorize")
    client = Client()
    client.force_login(user)

    with patch("mcp_gateway.cimd.fetch_cimd_document", return_value=_document()) as fetch:
        response = client.get("/oauth/authorize/", _authorization())

    assert response.status_code == 200, response.content
    fetch.assert_called_once_with(CLIENT_ID)
    application = OAuthApplication.objects.get(client_id=CLIENT_ID)
    assert application.registration_source == application.RegistrationSource.CIMD
    assert application.client_type == AbstractApplication.CLIENT_PUBLIC
    assert application.client_secret == ""
    assert application.hash_client_secret is False
    assert application.redirect_uris == REDIRECT_URI
    assert application.allowed_scopes == ["read", "write"]
    assert application.metadata == {
        "registration_method": "cimd",
        "application_type": "web",
        "document_sha256": "a" * 64,
    }
    assert application.cimd_expires_at > timezone.now()
    event = OAuthSecurityEvent.objects.get(event="cimd", application=application)
    assert event.outcome == "created"
    assert event.details["registration_method"] == "cimd"
    assert event.details["document_sha256"] == "a" * 64
    assert REDIRECT_URI not in str(event.details)


def test_cimd_redirect_is_exact_and_does_not_inherit_dcr_port_flexibility():
    user = get_user_model().objects.create_user(username="cimd-redirect")
    client = Client()
    client.force_login(user)
    loopback = "http://localhost:41000/callback"
    document = CIMDDocument(
        client_id=CLIENT_ID,
        client_name="Native CIMD test client",
        redirect_uris=(loopback,),
        application_type="native",
        scopes=("read", "write"),
        max_age_seconds=600,
        document_sha256="b" * 64,
    )
    with patch("mcp_gateway.cimd.fetch_cimd_document", return_value=document):
        exact = client.get(
            "/oauth/authorize/",
            _authorization(redirect_uri=loopback),
        )
    assert exact.status_code == 200, exact.content

    changed_port = client.get(
        "/oauth/authorize/",
        _authorization(redirect_uri="http://localhost:42000/callback"),
    )
    assert changed_port.status_code == 400
    assert changed_port.json()["error"] == "invalid_request"


def test_stale_cimd_fetch_failure_fails_closed_without_overwriting_last_good_document():
    user = get_user_model().objects.create_user(username="cimd-stale")
    client = Client()
    client.force_login(user)
    with patch("mcp_gateway.cimd.fetch_cimd_document", return_value=_document()):
        assert client.get("/oauth/authorize/", _authorization()).status_code == 200
    application = OAuthApplication.objects.get(client_id=CLIENT_ID)
    OAuthApplication.objects.filter(pk=application.pk).update(
        cimd_expires_at=timezone.now() - timedelta(seconds=1)
    )

    with patch(
        "mcp_gateway.cimd.fetch_cimd_document",
        side_effect=CIMDError("fetch_failed", "unavailable"),
    ):
        response = client.get("/oauth/authorize/", _authorization())

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    application.refresh_from_db()
    assert application.metadata["document_sha256"] == "a" * 64
    assert not application.is_usable(None)
    event = OAuthSecurityEvent.objects.filter(event="cimd").latest("created_at")
    assert event.outcome == "rejected"
    assert event.details["error"] == "fetch_failed"


def test_cimd_authorization_code_exchange_uses_native_oauth_flow():
    user = get_user_model().objects.create_user(username="cimd-token")
    client = Client()
    client.force_login(user)
    verifier, challenge = _pkce()
    authorization = _authorization()
    authorization["code_challenge"] = challenge
    with patch("mcp_gateway.cimd.fetch_cimd_document", return_value=_document()):
        page = client.get("/oauth/authorize/", authorization)
    assert page.status_code == 200, page.content
    consent = client.post(
        "/oauth/authorize/",
        {**authorization, "allow": "Authorize"},
    )
    assert consent.status_code == 302, consent.content
    code = parse_qs(urlsplit(consent["Location"]).query)["code"][0]

    token = client.post(
        "/oauth/token/",
        {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "code": code,
            "code_verifier": verifier,
            "resource": settings.SITEHITS_MCP_RESOURCE_URL,
        },
    )
    assert token.status_code == 200, token.content
    assert token.json()["scope"] == "read write"
    audit = OAuthSecurityEvent.objects.filter(event="token").latest("created_at")
    assert audit.details["registration_method"] == "cimd"
    assert audit.details["application_type"] == "web"


def test_cimd_client_id_cannot_overwrite_a_dcr_application():
    application = OAuthApplication.objects.create(
        client_id=CLIENT_ID,
        name="DCR collision",
        redirect_uris=REDIRECT_URI,
        client_type=OAuthApplication.CLIENT_PUBLIC,
        authorization_grant_type=OAuthApplication.GRANT_AUTHORIZATION_CODE,
        client_secret="",
        hash_client_secret=False,
        skip_authorization=False,
        registration_source=OAuthApplication.RegistrationSource.DCR,
        allowed_scopes=["read", "write"],
        metadata={"registration_method": "dcr", "application_type": "web"},
    )
    user = get_user_model().objects.create_user(username="cimd-collision")
    client = Client()
    client.force_login(user)
    with patch("mcp_gateway.cimd.fetch_cimd_document", return_value=_document()) as fetch:
        response = client.get("/oauth/authorize/", _authorization())

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    fetch.assert_not_called()
    application.refresh_from_db()
    assert application.registration_source == application.RegistrationSource.DCR


def test_dcr_fallback_requires_application_type_and_returns_it():
    base = {
        "redirect_uris": ["http://127.0.0.1:43127/callback"],
        "client_name": "DCR fallback",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": "read write",
    }
    client = Client()
    missing = client.post(
        "/oauth/register/",
        data=base,
        content_type="application/json",
    )
    assert missing.status_code == 400
    assert missing.json()["error"] == "invalid_client_metadata"

    mismatch = client.post(
        "/oauth/register/",
        data={**base, "application_type": "web"},
        content_type="application/json",
    )
    assert mismatch.status_code == 400
    assert mismatch.json()["error"] == "invalid_redirect_uri"

    accepted = client.post(
        "/oauth/register/",
        data={**base, "application_type": "native"},
        content_type="application/json",
    )
    assert accepted.status_code == 201, accepted.content
    assert accepted.json()["application_type"] == "native"
    application = OAuthApplication.objects.get(client_id=accepted.json()["client_id"])
    assert application.registration_source == application.RegistrationSource.DCR
    assert application.metadata["application_type"] == "native"


@pytest.mark.parametrize(
    ("redirect_uri", "metadata"),
    [
        ("http://127.0.0.1:43127/callback", {"application_type": "web"}),
        ("https://client.example/callback", {"application_type": "native"}),
        ("https://client.example/callback", []),
    ],
)
def test_application_model_rejects_inconsistent_registration_profiles(
    redirect_uri,
    metadata,
):
    application = OAuthApplication(
        client_id="model-profile-test",
        name="Profile test",
        redirect_uris=redirect_uri,
        client_type=OAuthApplication.CLIENT_PUBLIC,
        authorization_grant_type=OAuthApplication.GRANT_AUTHORIZATION_CODE,
        client_secret="",
        hash_client_secret=False,
        skip_authorization=False,
        registration_source=OAuthApplication.RegistrationSource.DCR,
        allowed_scopes=["read", "write"],
        metadata=metadata,
    )

    with pytest.raises(ValidationError):
        application.full_clean()
