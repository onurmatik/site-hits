"""Stage 1 conformance tests for the embedded Django OAuth provider.

These tests intentionally exercise the public Django URLs. The MCP transport is
covered separately in ``test_mcp_gateway.py`` so the documented process boundary
is represented in the test suite.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from datetime import timedelta
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, RequestFactory
from django.utils import timezone
from django.views.debug import get_exception_reporter_class, get_exception_reporter_filter
from oauth2_provider.models import AbstractApplication

from mcp_gateway.oauth import (
    ACCESS_TOKEN_TTL,
    AUTHORIZATION_CODE_TTL,
    REFRESH_FAMILY_TTL,
    credential_digest,
)
from mcp_gateway.views import SiteHitsTokenView
from mcp_oauth.models import (
    OAuthAccessToken,
    OAuthApplication,
    OAuthConsent,
    OAuthGrant,
    OAuthRateLimitBucket,
    OAuthRefreshFamily,
    OAuthRefreshToken,
    OAuthSecurityEvent,
)

pytestmark = pytest.mark.django_db(transaction=True)

REDIRECT_URI = "http://127.0.0.1:43127/callback?client=codex"
OTHER_HTTPS_REDIRECT = "https://client.example/callback"


@pytest.fixture(autouse=True)
def _local_https_policy(settings):
    """The test configuration uses an HTTP loopback product origin."""

    settings.DEBUG = True


def _pkce(verifier: str | None = None) -> tuple[str, str]:
    verifier = verifier or ("sitehits-stage-one-verifier-" + "x" * 48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")
    return verifier, challenge


def _registration_payload(
    *,
    redirect_uris: list[str] | None = None,
    scopes: str = "read write",
    **overrides,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "redirect_uris": redirect_uris or [REDIRECT_URI],
        "client_name": "Stage 1 conformance client",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": scopes,
    }
    payload.update(overrides)
    return payload


def _register(
    client: Client,
    *,
    redirect_uris: list[str] | None = None,
    scopes: str = "read write",
    remote_addr: str = "198.51.100.10",
    **overrides,
):
    return client.post(
        "/oauth/register/",
        data=json.dumps(
            _registration_payload(
                redirect_uris=redirect_uris,
                scopes=scopes,
                **overrides,
            )
        ),
        content_type="application/json",
        REMOTE_ADDR=remote_addr,
    )


def _authorization_pairs(
    client_id: str,
    challenge: str,
    *,
    redirect_uri: str = REDIRECT_URI,
    scopes: str = "read write",
    state: str = "opaque-state/%2B?=value",
    resources: tuple[str, ...] = (),
) -> list[tuple[str, str]]:
    pairs = [
        ("client_id", client_id),
        ("redirect_uri", redirect_uri),
        ("response_type", "code"),
        ("scope", scopes),
        ("state", state),
        ("code_challenge", challenge),
        ("code_challenge_method", "S256"),
    ]
    for resource in resources or (settings.SITEHITS_MCP_RESOURCE_URL,):
        pairs.append(("resource", resource))
    return pairs


def _csrf_value(client: Client) -> str:
    return client.cookies[settings.CSRF_COOKIE_NAME].value


def _assert_authorization_error_redirect(
    response,
    expected_error: str,
    *,
    redirect_uri: str = REDIRECT_URI,
    state: str | None = "opaque-state/%2B?=value",
):
    assert response.status_code == 302, response.content
    if redirect_uri.endswith(("?", "&")):
        separator = ""
    else:
        separator = "&" if "?" in redirect_uri else "?"
    assert response["Location"].startswith(f"{redirect_uri}{separator}")
    query = parse_qs(urlsplit(response["Location"]).query)
    assert query["error"] == [expected_error]
    assert query["error_description"]
    assert query["iss"] == [settings.SITEHITS_MCP_ISSUER_URL]
    if state is None:
        assert "state" not in query
    else:
        assert query["state"] == [state]
    assert response["Cache-Control"] == "no-store"
    assert response["Referrer-Policy"] == "no-referrer"


def _consent(
    client: Client,
    user,
    client_id: str,
    challenge: str,
    *,
    redirect_uri: str = REDIRECT_URI,
    scopes: str = "read write",
    state: str = "opaque-state/%2B?=value",
    allow: bool = True,
) -> tuple[str | None, object]:
    pairs = _authorization_pairs(
        client_id,
        challenge,
        redirect_uri=redirect_uri,
        scopes=scopes,
        state=state,
    )
    client.force_login(user)
    page = client.get(f"/oauth/authorize/?{urlencode(pairs)}")
    assert page.status_code == 200, page.content
    data = dict(pairs)
    data["csrfmiddlewaretoken"] = _csrf_value(client)
    if allow:
        data["allow"] = "Authorize"
    response = client.post("/oauth/authorize/", data=data)
    assert response.status_code == 302, response.content
    query = parse_qs(urlsplit(response["Location"]).query)
    assert query["state"] == [state]
    assert query["iss"] == [settings.SITEHITS_MCP_ISSUER_URL]
    return (query.get("code") or [None])[0], response


def _token_request(
    client: Client,
    *,
    client_id: str,
    code: str,
    verifier: str,
    redirect_uri: str = REDIRECT_URI,
    resources: tuple[str, ...] = (),
):
    pairs = [
        ("grant_type", "authorization_code"),
        ("client_id", client_id),
        ("redirect_uri", redirect_uri),
        ("code", code),
        ("code_verifier", verifier),
    ]
    for resource in resources or (settings.SITEHITS_MCP_RESOURCE_URL,):
        pairs.append(("resource", resource))
    return client.post(
        "/oauth/token/",
        data=urlencode(pairs),
        content_type="application/x-www-form-urlencoded",
        REMOTE_ADDR="198.51.100.10",
    )


def _authorize_and_exchange(
    client: Client,
    user,
    *,
    scopes: str = "read write",
    redirect_uri: str = REDIRECT_URI,
) -> tuple[OAuthApplication, str, dict[str, object]]:
    registration = _register(
        client,
        redirect_uris=[redirect_uri],
        scopes=scopes,
    )
    assert registration.status_code == 201, registration.content
    application = OAuthApplication.objects.get(
        client_id=registration.json()["client_id"]
    )
    verifier, challenge = _pkce()
    raw_code, _ = _consent(
        client,
        user,
        application.client_id,
        challenge,
        redirect_uri=redirect_uri,
        scopes=scopes,
    )
    assert raw_code is not None
    response = _token_request(
        client,
        client_id=application.client_id,
        code=raw_code,
        verifier=verifier,
        redirect_uri=redirect_uri,
    )
    assert response.status_code == 200, response.content
    assert response["Cache-Control"] == "no-store"
    assert response["Pragma"] == "no-cache"
    return application, raw_code, response.json()


def _assert_public_metadata_response(response) -> None:
    assert response.status_code == 200, response.content
    assert response["Access-Control-Allow-Origin"] == "*"
    cache_control = response["Cache-Control"]
    assert "public" in cache_control
    max_age = int(parse_qs(cache_control.replace(", ", "&").replace("=", "="))["max-age"][0])
    assert 0 < max_age <= 3600


def test_standard_metadata_endpoints_publish_only_working_oauth_profile():
    client = Client()
    issuer = settings.SITEHITS_MCP_ISSUER_URL
    resource = settings.SITEHITS_MCP_RESOURCE_URL

    authorization = client.get(
        "/.well-known/oauth-authorization-server",
        HTTP_ORIGIN="https://metadata-client.example",
    )
    root_protected = client.get(
        "/.well-known/oauth-protected-resource",
        HTTP_ORIGIN="https://metadata-client.example",
    )
    path_protected = client.get(
        "/.well-known/oauth-protected-resource/mcp",
        HTTP_ORIGIN="https://metadata-client.example",
    )

    _assert_public_metadata_response(authorization)
    _assert_public_metadata_response(root_protected)
    _assert_public_metadata_response(path_protected)
    assert authorization.json() == {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/oauth/authorize/",
        "token_endpoint": f"{issuer}/oauth/token/",
        "registration_endpoint": f"{issuer}/oauth/register/",
        "revocation_endpoint": f"{issuer}/oauth/revoke/",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "revocation_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
        "authorization_response_iss_parameter_supported": True,
        "scopes_supported": list(settings.SITEHITS_MCP_OAUTH_SCOPES),
        "service_documentation": settings.SITEHITS_MCP_DOCUMENTATION_URL,
    }
    expected_protected = {
        "resource": resource,
        "authorization_servers": [issuer],
        "scopes_supported": list(settings.SITEHITS_MCP_OAUTH_SCOPES),
        "bearer_methods_supported": ["header"],
        "resource_name": "SiteHits analytics MCP",
        "resource_documentation": settings.SITEHITS_MCP_DOCUMENTATION_URL,
    }
    assert root_protected.json() == expected_protected
    assert path_protected.json() == expected_protected
    assert "client_id_metadata_document_supported" not in authorization.json()


def test_dcr_public_profile_limits_and_digest_only_audit():
    client = Client()
    response = _register(client)
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["grant_types"] == ["authorization_code", "refresh_token"]
    assert body["response_types"] == ["code"]
    assert body["token_endpoint_auth_method"] == "none"
    assert "client_secret" not in body
    assert response["Cache-Control"] == "no-store"

    application = OAuthApplication.objects.get(client_id=body["client_id"])
    assert application.client_type == AbstractApplication.CLIENT_PUBLIC
    assert application.authorization_grant_type == (
        AbstractApplication.GRANT_AUTHORIZATION_CODE
    )
    assert application.client_secret == ""
    assert application.hash_client_secret is False
    assert application.skip_authorization is False
    assert application.registration_source == (
        AbstractApplication.RegistrationSource.DCR
    )

    event = OAuthSecurityEvent.objects.get(application=application, event="dcr")
    serialized_event = json.dumps(
        {
            "subject_digest": event.subject_digest,
            "details": event.details,
        }
    )
    assert REDIRECT_URI not in serialized_event
    assert event.details["redirect_uris"] == {
        "count": 1,
        "digests": [credential_digest(REDIRECT_URI)],
    }
    assert event.details["client_id"] == application.client_id
    assert event.details["client_digest"]
    assert event.details["registration_digest"]
    assert event.details["source_trust"]
    assert event.details["source_digest"]
    assert event.details["decision"] == "created"
    assert event.details["error"] == ""

    secret_client = _register(
        client,
        token_endpoint_auth_method="client_secret_post",
        remote_addr="198.51.100.11",
    )
    implicit = _register(
        client,
        response_types=["token"],
        remote_addr="198.51.100.12",
    )
    too_many_redirects = _register(
        client,
        redirect_uris=[f"https://client.example/callback/{index}" for index in range(11)],
        remote_addr="198.51.100.13",
    )
    oversized = client.post(
        "/oauth/register/",
        data=b"{" + b"x" * (16 * 1024),
        content_type="application/json",
        REMOTE_ADDR="198.51.100.14",
    )
    assert secret_client.status_code == 400
    assert secret_client.json()["error"] == "invalid_client_metadata"
    assert implicit.status_code == 400
    assert implicit.json()["error"] == "invalid_client_metadata"
    assert too_many_redirects.status_code == 400
    assert too_many_redirects.json()["error"] == "invalid_redirect_uri"
    assert oversized.status_code == 413
    rejected_events = OAuthSecurityEvent.objects.filter(
        event="dcr",
        outcome="rejected",
    )
    assert rejected_events.filter(
        details__error="invalid_client_metadata"
    ).exists()
    assert rejected_events.filter(details__error="invalid_redirect_uri").exists()


@pytest.mark.parametrize(
    ("field", "value", "expected_error"),
    [
        ("grant_types", [{}, "refresh_token"], "invalid_client_metadata"),
        (
            "grant_types",
            [["authorization_code"], "refresh_token"],
            "invalid_client_metadata",
        ),
        ("response_types", [{}], "invalid_client_metadata"),
        ("response_types", [["code"]], "invalid_client_metadata"),
        ("redirect_uris", [{}], "invalid_redirect_uri"),
        (
            "redirect_uris",
            [["https://client.example/callback"]],
            "invalid_redirect_uri",
        ),
    ],
)
def test_dcr_endpoint_rejects_non_string_profile_list_members(
    field,
    value,
    expected_error,
):
    response = _register(
        Client(),
        remote_addr="203.0.113.240",
        **{field: value},
    )

    assert response.status_code == 400
    assert response.json()["error"] == expected_error


@pytest.mark.parametrize(
    "client_name",
    [
        "broken-\ud800-name",
        "broken-\x00-name",
        "broken-\n-name",
        "broken-\x85-name",
        "broken-\u2028-name",
    ],
)
def test_dcr_endpoint_rejects_unpersistable_client_names(client_name):
    response = _register(
        Client(),
        client_name=client_name,
        remote_addr="203.0.113.241",
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_client_metadata"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("client_uri", {}),
        ("contacts", "ops@example.com"),
        ("contacts", ["ops@example.com", {}]),
        ("policy_uri", "https://client.example/policy\x00"),
        ("software_id", "agent\ud800host"),
    ],
)
def test_dcr_endpoint_rejects_unpersistable_metadata(field, value):
    response = _register(
        Client(),
        remote_addr="203.0.113.242",
        **{field: value},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_client_metadata"


def test_dcr_endpoint_preserves_valid_unicode_client_name_and_metadata():
    client_name = "Ölçüm Agent’ı 🚀"
    contacts = ["ölçüm@example.com"]
    response = _register(
        Client(),
        client_name=client_name,
        contacts=contacts,
        software_id="ölçüm-agent",
        remote_addr="203.0.113.243",
    )

    assert response.status_code == 201, response.content
    assert response.json()["client_name"] == client_name
    application = OAuthApplication.objects.get(client_id=response.json()["client_id"])
    assert application.metadata == {
        "contacts": contacts,
        "software_id": "ölçüm-agent",
    }


@pytest.mark.parametrize(
    "extra",
    [
        {"client_secret": "must-not-be-accepted"},
        {"client_secret": ""},
        {"HTTP_AUTHORIZATION": "Basic ZHVtbXk6c2VjcmV0"},
        {"HTTP_AUTHORIZATION": "Bearer unrelated-token"},
        {"HTTP_AUTHORIZATION": "Digest unrelated-value"},
    ],
)
def test_token_and_revoke_reject_confidential_client_auth(extra):
    client = Client()
    application = _register(client).json()
    token_data = {
        "grant_type": "authorization_code",
        "client_id": application["client_id"],
        "resource": settings.SITEHITS_MCP_RESOURCE_URL,
        "code": "opaque-unused-code",
        "code_verifier": "v" * 43,
    }
    request_kwargs = {
        key: value for key, value in extra.items() if key.startswith("HTTP_")
    }
    token_data.update(
        {key: value for key, value in extra.items() if not key.startswith("HTTP_")}
    )
    token = client.post("/oauth/token/", data=token_data, **request_kwargs)
    assert token.status_code == 401
    assert token.json()["error"] == "invalid_client"

    revoke_data = {
        "client_id": application["client_id"],
        "token": "opaque-unused-token",
    }
    revoke_data.update(
        {key: value for key, value in extra.items() if not key.startswith("HTTP_")}
    )
    revoke = client.post("/oauth/revoke/", data=revoke_data, **request_kwargs)
    assert revoke.status_code == 401
    assert revoke.json()["error"] == "invalid_client"


def test_dcr_rate_limit_is_persistent_per_source_and_global(monkeypatch):
    from mcp_gateway import views

    monkeypatch.setitem(views.RATE_LIMITS, "register", (2, 100))
    client = Client()
    responses = [
        _register(
            client,
            remote_addr="203.0.113.42",
            redirect_uris=[f"https://client.example/callback/{index}"],
        )
        for index in range(3)
    ]
    assert [response.status_code for response in responses] == [201, 201, 429]
    assert responses[-1].json()["error"] == "invalid_client_metadata"
    assert OAuthRateLimitBucket.objects.filter(action="register").count() == 2
    global_bucket = OAuthRateLimitBucket.objects.get(
        action="register",
        subject_digest=views._rate_subject_digest("register", "global"),
    )
    assert global_bucket.count == 2
    fresh_source = _register(
        client,
        remote_addr="203.0.113.43",
        redirect_uris=["https://client.example/callback/fresh"],
    )
    assert fresh_source.status_code == 201
    global_bucket.refresh_from_db()
    assert global_bucket.count == 3


def test_validated_client_rate_limit_is_separate_and_does_not_burn_global(monkeypatch):
    from mcp_gateway import views

    monkeypatch.setitem(views.RATE_LIMITS, "token", (100, 100))
    monkeypatch.setitem(views.CLIENT_RATE_LIMITS, "token", 1)
    client = Client()
    application = _register(client).json()
    payload = {
        "grant_type": "authorization_code",
        "client_id": application["client_id"],
        "resource": settings.SITEHITS_MCP_RESOURCE_URL,
        "code": "invalid-code",
        "code_verifier": "v" * 43,
    }

    assert client.post("/oauth/token/", payload).status_code == 400
    limited = client.post("/oauth/token/", payload)
    assert limited.status_code == 429
    global_bucket = OAuthRateLimitBucket.objects.get(
        action="token",
        subject_digest=views._rate_subject_digest("token", "global"),
    )
    assert global_bucket.count == 1
    client_bucket = OAuthRateLimitBucket.objects.get(
        action="token",
        subject_digest=views._rate_subject_digest(
            "token",
            f"client:{OAuthApplication.objects.get(client_id=application['client_id']).pk}",
        ),
    )
    assert client_bucket.count == 1


def test_authorize_rejects_present_but_empty_state_as_invalid_request():
    client = Client()
    registration = _register(client).json()
    _, challenge = _pkce()
    pairs = _authorization_pairs(
        registration["client_id"],
        challenge,
        state="",
    )

    response = client.get(f"/oauth/authorize/?{urlencode(pairs)}")

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


def test_dcr_without_scope_registers_full_catalog_and_authorize_requires_scope():
    client = Client()
    payload = _registration_payload()
    payload.pop("scope")
    registered = client.post(
        "/oauth/register/",
        data=json.dumps(payload),
        content_type="application/json",
        REMOTE_ADDR="198.51.100.90",
    )
    assert registered.status_code == 201, registered.content
    expected_scopes = list(settings.SITEHITS_MCP_OAUTH_SCOPES)
    assert registered.json()["scope"].split() == expected_scopes
    application = OAuthApplication.objects.get(
        client_id=registered.json()["client_id"]
    )
    assert application.allowed_scopes == expected_scopes

    user = get_user_model().objects.create_user(username=f"scope-{uuid4().hex}")
    client.force_login(user)
    _, challenge = _pkce()
    full_scope = _authorization_pairs(
        application.client_id,
        challenge,
        scopes=" ".join(expected_scopes),
    )
    assert client.get(f"/oauth/authorize/?{urlencode(full_scope)}").status_code == 200

    missing_scope = [(key, value) for key, value in full_scope if key != "scope"]
    rejected = client.get(f"/oauth/authorize/?{urlencode(missing_scope)}")
    _assert_authorization_error_redirect(rejected, "invalid_scope")


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ({"response_type": "token"}, "unsupported_response_type"),
        ({"code_challenge_method": "plain"}, "invalid_request"),
        ({"redirect_uri": "https://attacker.example/callback"}, "invalid_request"),
        ({"scope": "read unsupported"}, "invalid_scope"),
    ],
)
def test_authorize_prevalidation_uses_specific_protocol_errors(
    mutation,
    expected_error,
):
    client = Client()
    registration = _register(client).json()
    user = get_user_model().objects.create_user(username=f"errors-{uuid4().hex}")
    client.force_login(user)
    _, challenge = _pkce()
    pairs = dict(_authorization_pairs(registration["client_id"], challenge))
    pairs.update(mutation)
    response = client.get("/oauth/authorize/", data=pairs)
    if "redirect_uri" in mutation:
        assert response.status_code == 400
        assert response.json()["error"] == expected_error
        assert "Location" not in response
    else:
        _assert_authorization_error_redirect(response, expected_error)


def test_authorization_error_redirect_requires_safe_callback_and_opaque_state():
    client = Client()
    registration = _register(client).json()
    application = OAuthApplication.objects.get(client_id=registration["client_id"])
    user = get_user_model().objects.create_user(
        username=f"authorize-errors-{uuid4().hex}"
    )
    client.force_login(user)
    _, challenge = _pkce()
    state = "opaque +/%25?=&state"
    pairs = _authorization_pairs(
        application.client_id,
        challenge,
        scopes="read unsupported",
        state=state,
    )
    request_id = uuid4().hex
    redirected = client.get(
        f"/oauth/authorize/?{urlencode(pairs)}",
        REMOTE_ADDR="127.0.0.1",
        HTTP_X_REQUEST_ID=request_id,
    )
    _assert_authorization_error_redirect(
        redirected,
        "invalid_scope",
        state=state,
    )
    callback_query = parse_qs(urlsplit(redirected["Location"]).query)
    assert callback_query["client"] == ["codex"]
    event = OAuthSecurityEvent.objects.get(
        request_id=request_id,
        event="authorize",
        outcome="rejected",
    )
    assert event.application == application
    assert event.user == user
    assert event.resource == settings.SITEHITS_MCP_RESOURCE_URL
    assert event.details["error"] == "invalid_scope"
    assert event.details["redirect_uri_digest"] == credential_digest(REDIRECT_URI)
    assert state not in json.dumps(event.details)

    safe_pairs = _authorization_pairs(application.client_id, challenge, state=state)
    unsafe_requests = [
        [
            *(pair for pair in safe_pairs if pair[0] != "client_id"),
            ("client_id", "not-a-client"),
        ],
        [
            *(pair for pair in safe_pairs if pair[0] != "redirect_uri"),
            ("redirect_uri", "https://attacker.example/callback"),
        ],
        [*safe_pairs, ("state", "ambiguous-state")],
        [
            *(pair for pair in safe_pairs if pair[0] != "state"),
            ("state", "non-ascii-☃"),
        ],
        [*safe_pairs, ("client_id", application.client_id)],
        [*safe_pairs, ("redirect_uri", REDIRECT_URI)],
    ]
    for unsafe_pairs in unsafe_requests:
        local_error = client.get(
            f"/oauth/authorize/?{urlencode(unsafe_pairs)}",
        )
        assert local_error.status_code == 400
        assert local_error.json()["error"] == "invalid_request"
        assert "Location" not in local_error


def test_django_correlation_and_dcr_audit_require_trusted_proxy_marker(settings):
    settings.SITEHITS_TRUST_PROXY_HEADERS = True
    spoofed_id = uuid4().hex
    unmarked = Client().post(
        "/oauth/register/",
        data=json.dumps(_registration_payload()),
        content_type="application/json",
        REMOTE_ADDR="198.51.100.91",
        HTTP_X_REQUEST_ID=spoofed_id,
    )
    assert unmarked.status_code == 201, unmarked.content
    assert unmarked["X-Request-ID"] != spoofed_id
    unmarked_event = OAuthSecurityEvent.objects.get(
        application__client_id=unmarked.json()["client_id"],
        event="dcr",
    )
    assert unmarked_event.request_id.hex == unmarked["X-Request-ID"]
    assert unmarked_event.details["source_trust"] == "untrusted_direct_peer"

    proxy_id = uuid4().hex
    marked = Client().post(
        "/oauth/register/",
        data=json.dumps(_registration_payload()),
        content_type="application/json",
        REMOTE_ADDR="198.51.100.92",
        HTTP_X_REQUEST_ID=proxy_id,
        HTTP_X_SITEHITS_TRUSTED_PROXY="1",
    )
    assert marked.status_code == 201, marked.content
    assert marked["X-Request-ID"] == proxy_id
    event = OAuthSecurityEvent.objects.get(
        application__client_id=marked.json()["client_id"],
        event="dcr",
    )
    assert event.request_id.hex == proxy_id
    assert event.details["source_trust"] == "trusted_proxy"
    assert REDIRECT_URI not in json.dumps(event.details)


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "https://client.example/callback?fixed=1",
        "http://127.0.0.1/callback",
        "http://127.0.0.1:49152/callback",
        "http://[::1]:49152/callback",
        "http://localhost:49152/callback",
    ],
)
def test_redirect_registration_accepts_https_and_supported_loopbacks(redirect_uri):
    response = _register(
        Client(),
        redirect_uris=[redirect_uri],
        remote_addr=f"198.51.100.{len(redirect_uri)}",
    )
    assert response.status_code == 201, response.content


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "http://0.0.0.0/callback",
        "https://localhost/callback",
        "http://LOCALHOST/callback",
        "https://0.0.0.0/callback",
        "https://[::]/callback",
        "http://127.0.0.2/callback",
        "http://user@127.0.0.1/callback",
        "https://client.example/callback#fragment",
        "https://client.example/callback#",
        "https://*.example/callback",
        "https://client.example/call*back",
        "https://client.example/callback?next=*",
        "https://client.example/call\\back",
        "https://client.example/callback?",
        " https://client.example/callback",
        "https://client.example/callback ",
        "https://client.example/call\tback",
        "https://client.example/call\x00back",
        "https://client.example/call\x7fback",
        "custom-scheme://client/callback",
    ],
)
def test_redirect_registration_rejects_unapproved_or_unsafe_uris(redirect_uri):
    response = _register(
        Client(),
        redirect_uris=[redirect_uri],
        remote_addr=f"203.0.113.{len(redirect_uri)}",
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_redirect_uri"


def test_authorize_redirect_matrix_allows_only_loopback_port_variation():
    client = Client()
    registration = _register(
        client,
        redirect_uris=[
            "http://127.0.0.1:31000/callback?fixed=1",
            "http://localhost:31000/callback",
            OTHER_HTTPS_REDIRECT,
        ],
    ).json()
    _, challenge = _pkce()
    user = get_user_model().objects.create_user(username=f"redirect-{uuid4().hex}")
    client.force_login(user)

    allowed = _authorization_pairs(
        registration["client_id"],
        challenge,
        redirect_uri="http://127.0.0.1:49152/callback?fixed=1",
    )
    assert client.get(f"/oauth/authorize/?{urlencode(allowed)}").status_code == 200

    claude_code = _authorization_pairs(
        registration["client_id"],
        challenge,
        redirect_uri="http://localhost:49152/callback",
    )
    assert client.get(f"/oauth/authorize/?{urlencode(claude_code)}").status_code == 200

    rejected = [
        "http://127.0.0.1:49152/other?fixed=1",
        "http://127.0.0.1:49152/callback?fixed=2",
        "http://[::1]:49152/callback?fixed=1",
        "http://localhost:49152/other",
        "https://client.example:444/callback",
    ]
    for redirect_uri in rejected:
        pairs = _authorization_pairs(
            registration["client_id"],
            challenge,
            redirect_uri=redirect_uri,
        )
        response = client.get(f"/oauth/authorize/?{urlencode(pairs)}")
        assert response.status_code == 400, (redirect_uri, response.content)


def test_resource_is_utf8_byte_exact_and_only_exact_repeats_are_accepted():
    client = Client()
    registration = _register(client).json()
    _, challenge = _pkce()
    user = get_user_model().objects.create_user(username=f"resource-{uuid4().hex}")
    client.force_login(user)
    exact = settings.SITEHITS_MCP_RESOURCE_URL
    base = _authorization_pairs(
        registration["client_id"],
        challenge,
        resources=(exact,),
    )

    repeated = [*base, ("resource", exact)]
    assert client.get(f"/oauth/authorize/?{urlencode(repeated)}").status_code == 200

    missing = [(key, value) for key, value in base if key != "resource"]
    variants = [
        missing,
        [*missing, ("resource", f"{exact}/")],
        [*missing, ("resource", exact.replace("http://", "HTTP://", 1))],
        [*missing, ("resource", exact), ("resource", f"{exact}/")],
    ]
    for pairs in variants:
        response = client.get(f"/oauth/authorize/?{urlencode(pairs)}")
        _assert_authorization_error_redirect(response, "invalid_target")

    token_base = [
        ("grant_type", "authorization_code"),
        ("client_id", registration["client_id"]),
        ("redirect_uri", REDIRECT_URI),
        ("code", "not-a-code"),
        ("code_verifier", "x" * 43),
    ]
    exact_repeat = client.post(
        "/oauth/token/",
        data=urlencode(
            [*token_base, ("resource", exact), ("resource", exact)]
        ),
        content_type="application/x-www-form-urlencoded",
    )
    assert exact_repeat.status_code == 400
    assert exact_repeat.json()["error"] == "invalid_grant"
    for resources in ((), (f"{exact}/",), (exact, f"{exact}/")):
        response = client.post(
            "/oauth/token/",
            data=urlencode(
                [*token_base, *(("resource", value) for value in resources)]
            ),
            content_type="application/x-www-form-urlencoded",
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_target"


def test_anonymous_login_preserves_only_the_validated_local_authorize_target():
    client = Client()
    registration = _register(client).json()
    _, challenge = _pkce()
    pairs = _authorization_pairs(registration["client_id"], challenge)
    response = client.get(
        f"/oauth/authorize/?{urlencode(pairs)}",
        follow=False,
    )
    assert response.status_code == 302
    login = urlsplit(response["Location"])
    assert login.netloc == ""
    assert login.path == "/accounts/signup/"
    next_target = parse_qs(login.query)["next"][0]
    parsed_next = urlsplit(next_target)
    assert parsed_next.netloc == ""
    assert parsed_next.path == "/oauth/authorize/"
    next_query = parse_qs(parsed_next.query)
    assert next_query["client_id"] == [registration["client_id"]]
    assert next_query["resource"] == [settings.SITEHITS_MCP_RESOURCE_URL]
    signup_page = client.get(response["Location"])
    assert signup_page["Cache-Control"] == "no-store"
    assert signup_page["Referrer-Policy"] == "no-referrer"
    assert "noindex" in signup_page["X-Robots-Tag"]


def test_anonymous_prompt_none_error_has_exact_canonical_issuer():
    redirect_uri = (
        "http://127.0.0.1:43127/callback?client=codex&iss=untrusted-issuer"
    )
    client = Client()
    registration = _register(client, redirect_uris=[redirect_uri]).json()
    _, challenge = _pkce()
    state = "opaque-prompt-none-state"
    pairs = [
        *_authorization_pairs(
            registration["client_id"],
            challenge,
            redirect_uri=redirect_uri,
            state=state,
        ),
        ("prompt", "none"),
    ]
    response = client.get(f"/oauth/authorize/?{urlencode(pairs)}", follow=False)
    assert response.status_code == 302
    query = parse_qs(urlsplit(response["Location"]).query)
    assert query["error"] == ["login_required"]
    assert query["state"] == [state]
    assert query["iss"] == [settings.SITEHITS_MCP_ISSUER_URL]
    assert query["client"] == ["codex"]


@pytest.mark.parametrize(
    "extra_pairs",
    [
        [("prompt", "none"), ("prompt", "create")],
        [("prompt", "create")],
        [("nonce", "unsupported")],
        [("claims", "{}")],
        [("approval_prompt", "force")],
    ],
)
def test_authorization_rejects_ambiguous_or_unsupported_parameters(extra_pairs):
    client = Client()
    registration = _register(client).json()
    user = get_user_model().objects.create_user(username=f"prompt-{uuid4().hex}")
    client.force_login(user)
    _, challenge = _pkce()
    pairs = [
        *_authorization_pairs(registration["client_id"], challenge),
        *extra_pairs,
    ]
    response = client.get(f"/oauth/authorize/?{urlencode(pairs)}")
    _assert_authorization_error_redirect(response, "invalid_request")


def test_explicit_consent_is_csrf_protected_no_store_and_echoes_opaque_state():
    client = Client(enforce_csrf_checks=True)
    registration = _register(client).json()
    user = get_user_model().objects.create_user(username=f"consent-{uuid4().hex}")
    verifier, challenge = _pkce()
    state = "opaque +/%25?=&state"
    pairs = _authorization_pairs(
        registration["client_id"],
        challenge,
        state=state,
    )
    client.force_login(user)
    page = client.get(f"/oauth/authorize/?{urlencode(pairs)}")
    assert page.status_code == 200
    assert registration["client_id"].encode() in page.content
    assert settings.SITEHITS_MCP_RESOURCE_URL.encode() in page.content
    assert b"read" in page.content and b"write" in page.content
    assert page["Cache-Control"] == "no-store"
    assert "noindex" in page["X-Robots-Tag"]
    assert "Access-Control-Allow-Origin" not in page

    without_csrf = client.post(
        "/oauth/authorize/",
        data={**dict(pairs), "allow": "Authorize"},
    )
    assert without_csrf.status_code == 403
    assert "no-store" in without_csrf.get("Cache-Control", "")
    assert not OAuthGrant.objects.filter(
        application__client_id=registration["client_id"]
    ).exists()

    approved = client.post(
        "/oauth/authorize/",
        data={
            **dict(pairs),
            "allow": "Authorize",
            "csrfmiddlewaretoken": _csrf_value(client),
        },
    )
    assert approved.status_code == 302
    callback = parse_qs(urlsplit(approved["Location"]).query)
    assert callback["state"] == [state]
    assert callback["code"][0]
    grant = OAuthGrant.objects.get(
        code_digest=credential_digest(callback["code"][0])
    )
    assert grant.code == ""
    application = OAuthApplication.objects.get(
        client_id=registration["client_id"]
    )
    assert application.last_used_at is not None
    assert state not in json.dumps(
        list(OAuthSecurityEvent.objects.values("subject_digest", "details"))
    )
    assert verifier not in json.dumps(
        list(OAuthSecurityEvent.objects.values("subject_digest", "details"))
    )


def test_denied_consent_round_trips_state_without_issuing_a_code():
    client = Client(enforce_csrf_checks=True)
    registration = _register(client).json()
    application = OAuthApplication.objects.get(client_id=registration["client_id"])
    user = get_user_model().objects.create_user(username=f"deny-{uuid4().hex}")
    _, challenge = _pkce()

    raw_code, denied = _consent(
        client,
        user,
        application.client_id,
        challenge,
        allow=False,
    )
    assert raw_code is None
    denied_query = parse_qs(urlsplit(denied["Location"]).query)
    assert denied_query["error"] == ["access_denied"]
    assert OAuthConsent.objects.get(application=application).decision == "denied"
    assert not OAuthGrant.objects.filter(application=application).exists()


def test_authorization_redirects_replace_fixed_query_issuer_exactly_once():
    redirect_uri = (
        "http://127.0.0.1:43127/callback?client=codex&iss=untrusted-issuer"
    )
    client = Client(enforce_csrf_checks=True)
    registration = _register(client, redirect_uris=[redirect_uri]).json()
    application = OAuthApplication.objects.get(client_id=registration["client_id"])
    user = get_user_model().objects.create_user(username=f"issuer-{uuid4().hex}")
    _, challenge = _pkce()

    raw_code, approved = _consent(
        client,
        user,
        application.client_id,
        challenge,
        redirect_uri=redirect_uri,
        state="approved-state",
    )
    assert raw_code
    assert parse_qs(urlsplit(approved["Location"]).query)["iss"] == [
        settings.SITEHITS_MCP_ISSUER_URL
    ]

    _, denied = _consent(
        client,
        user,
        application.client_id,
        challenge,
        redirect_uri=redirect_uri,
        state="denied-state",
        allow=False,
    )
    assert parse_qs(urlsplit(denied["Location"]).query)["iss"] == [
        settings.SITEHITS_MCP_ISSUER_URL
    ]

    invalid_scope = _authorization_pairs(
        application.client_id,
        challenge,
        redirect_uri=redirect_uri,
        scopes="read unsupported",
        state="error-state",
    )
    rejected = client.get(f"/oauth/authorize/?{urlencode(invalid_scope)}")
    query = parse_qs(urlsplit(rejected["Location"]).query)
    assert query["error"] == ["invalid_scope"]
    assert query["iss"] == [settings.SITEHITS_MCP_ISSUER_URL]
    assert query["client"] == ["codex"]


@pytest.mark.parametrize(
    ("field", "tampered_value"),
    [
        ("resource", f"{settings.SITEHITS_MCP_RESOURCE_URL}/"),
        ("redirect_uri", "http://127.0.0.1:43127/other"),
        ("scope", "read unsupported"),
    ],
)
def test_consent_post_revalidates_resource_redirect_and_scope(field, tampered_value):
    client = Client(enforce_csrf_checks=True)
    registration = _register(client).json()
    application = OAuthApplication.objects.get(client_id=registration["client_id"])
    user = get_user_model().objects.create_user(
        username=f"consent-revalidation-{uuid4().hex}"
    )
    _, challenge = _pkce()
    pairs = _authorization_pairs(application.client_id, challenge)
    client.force_login(user)
    page = client.get(f"/oauth/authorize/?{urlencode(pairs)}")
    assert page.status_code == 200
    data = {
        **dict(pairs),
        "allow": "Authorize",
        "csrfmiddlewaretoken": _csrf_value(client),
        field: tampered_value,
    }
    tampered = client.post("/oauth/authorize/", data=data)
    if field == "redirect_uri":
        assert tampered.status_code == 400
        assert tampered.json()["error"] == "invalid_request"
        assert "Location" not in tampered
    else:
        _assert_authorization_error_redirect(
            tampered,
            "invalid_target" if field == "resource" else "invalid_scope",
        )
    assert not OAuthGrant.objects.filter(application=application).exists()
    assert not OAuthConsent.objects.filter(application=application).exists()


def test_consent_post_revalidates_application_activity():
    client = Client(enforce_csrf_checks=True)
    registration = _register(client).json()
    application = OAuthApplication.objects.get(client_id=registration["client_id"])
    user = get_user_model().objects.create_user(
        username=f"consent-client-revalidation-{uuid4().hex}"
    )
    _, challenge = _pkce()
    pairs = _authorization_pairs(application.client_id, challenge)
    client.force_login(user)
    page = client.get(f"/oauth/authorize/?{urlencode(pairs)}")
    assert page.status_code == 200

    application.revoke()
    inactive = client.post(
        "/oauth/authorize/",
        data={
            **dict(pairs),
            "allow": "Authorize",
            "csrfmiddlewaretoken": _csrf_value(client),
        },
    )
    assert inactive.status_code == 400
    assert not OAuthGrant.objects.filter(application=application).exists()


def test_code_ttl_digest_pkce_redirect_and_single_use_replay():
    client = Client(enforce_csrf_checks=True)
    user = get_user_model().objects.create_user(username=f"code-{uuid4().hex}")
    registration = _register(client).json()
    verifier, challenge = _pkce()
    raw_code, _ = _consent(
        client,
        user,
        registration["client_id"],
        challenge,
    )
    assert raw_code is not None
    grant = OAuthGrant.objects.get(code_digest=credential_digest(raw_code))
    assert grant.code == ""
    assert raw_code not in json.dumps(
        list(OAuthGrant.objects.values("code", "code_digest"))
    )
    assert AUTHORIZATION_CODE_TTL - timedelta(seconds=2) <= (
        grant.expires - grant.created
    ) <= AUTHORIZATION_CODE_TTL + timedelta(seconds=2)

    wrong_redirect = _token_request(
        client,
        client_id=registration["client_id"],
        code=raw_code,
        verifier=verifier,
        redirect_uri="http://127.0.0.1:43127/other",
    )
    wrong_verifier = _token_request(
        client,
        client_id=registration["client_id"],
        code=raw_code,
        verifier="wrong-verifier-" + "z" * 48,
    )
    assert wrong_redirect.status_code == 400
    assert wrong_redirect.json()["error"] in {"invalid_request", "invalid_grant"}
    assert wrong_verifier.status_code == 400
    assert wrong_verifier.json()["error"] == "invalid_grant"
    missing_redirect = client.post(
        "/oauth/token/",
        data={
            "grant_type": "authorization_code",
            "client_id": registration["client_id"],
            "code": raw_code,
            "code_verifier": verifier,
            "resource": settings.SITEHITS_MCP_RESOURCE_URL,
        },
    )
    assert missing_redirect.status_code == 400
    assert missing_redirect.json()["error"] == "invalid_request"
    grant.refresh_from_db()
    assert grant.consumed_at is None

    issued = _token_request(
        client,
        client_id=registration["client_id"],
        code=raw_code,
        verifier=verifier,
    )
    assert issued.status_code == 200, issued.content
    grant.refresh_from_db()
    assert grant.consumed_at is not None
    replay = _token_request(
        client,
        client_id=registration["client_id"],
        code=raw_code,
        verifier=verifier,
    )
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"
    grant.refresh_from_db()
    assert grant.replayed_at is not None
    refresh = OAuthRefreshToken.objects.get(
        token_checksum=credential_digest(issued.json()["refresh_token"])
    )
    access = OAuthAccessToken.objects.get(
        token_checksum=credential_digest(issued.json()["access_token"])
    )
    assert refresh.family_revoked_at is not None
    assert access.revoked_at is not None


def test_expired_authorization_code_is_rejected_without_echoing_it():
    client = Client(enforce_csrf_checks=True)
    user = get_user_model().objects.create_user(username=f"expired-code-{uuid4().hex}")
    registration = _register(client).json()
    verifier, challenge = _pkce()
    raw_code, _ = _consent(
        client,
        user,
        registration["client_id"],
        challenge,
    )
    assert raw_code is not None
    OAuthGrant.objects.filter(code_digest=credential_digest(raw_code)).update(
        expires=timezone.now() - timedelta(seconds=1)
    )
    response = _token_request(
        client,
        client_id=registration["client_id"],
        code=raw_code,
        verifier=verifier,
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"
    assert raw_code not in response.content.decode()


def test_access_and_refresh_credentials_are_opaque_digest_only_and_fixed_ttl():
    client = Client(enforce_csrf_checks=True)
    user = get_user_model().objects.create_user(username=f"tokens-{uuid4().hex}")
    application, raw_code, token = _authorize_and_exchange(client, user)
    access_raw = token["access_token"]
    refresh_raw = token["refresh_token"]
    assert len(access_raw) >= 30 and len(refresh_raw) >= 30
    assert access_raw != refresh_raw

    access = OAuthAccessToken.objects.get(
        token_checksum=credential_digest(access_raw)
    )
    refresh = OAuthRefreshToken.objects.get(
        token_checksum=credential_digest(refresh_raw)
    )
    grant = OAuthGrant.objects.get(code_digest=credential_digest(raw_code))
    assert access.token == refresh.token == grant.code == ""
    assert access.resource == refresh.resource == [settings.SITEHITS_MCP_RESOURCE_URL]
    assert access.authorization_code_digest == grant.code_digest
    assert refresh.authorization_code_digest == grant.code_digest
    assert ACCESS_TOKEN_TTL - timedelta(seconds=2) <= (
        access.expires - access.updated
    ) <= ACCESS_TOKEN_TTL + timedelta(seconds=2)
    assert REFRESH_FAMILY_TTL - timedelta(seconds=2) <= (
        refresh.family_expires_at - refresh.created
    ) <= REFRESH_FAMILY_TTL + timedelta(seconds=2)
    assert access.application == application
    assert token["expires_in"] <= int(ACCESS_TOKEN_TTL.total_seconds())

    response_json = json.dumps(token)
    database_json = json.dumps(
        {
            "access": list(
                OAuthAccessToken.objects.values("token", "token_checksum")
            ),
            "refresh": list(
                OAuthRefreshToken.objects.values("token", "token_checksum")
            ),
            "grant": list(OAuthGrant.objects.values("code", "code_digest")),
            "events": list(
                OAuthSecurityEvent.objects.values("subject_digest", "details")
            ),
        },
        default=str,
    )
    for raw_secret in (access_raw, refresh_raw, raw_code):
        assert raw_secret in response_json or raw_secret == raw_code
        assert raw_secret not in database_json


def test_refresh_rotation_replay_and_revoke_cover_the_whole_family():
    client = Client(enforce_csrf_checks=True)
    user = get_user_model().objects.create_user(username=f"refresh-{uuid4().hex}")
    application, _, first = _authorize_and_exchange(client, user)
    refresh_pairs = [
        ("grant_type", "refresh_token"),
        ("client_id", application.client_id),
        ("refresh_token", first["refresh_token"]),
        ("resource", settings.SITEHITS_MCP_RESOURCE_URL),
        ("resource", settings.SITEHITS_MCP_RESOURCE_URL),
    ]
    rotated = client.post(
        "/oauth/token/",
        data=urlencode(refresh_pairs),
        content_type="application/x-www-form-urlencoded",
    )
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

    replay = client.post(
        "/oauth/token/",
        data=urlencode(refresh_pairs),
        content_type="application/x-www-form-urlencoded",
    )
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"
    replacement.refresh_from_db()
    assert replacement.family_revoked_at is not None
    replacement_access = OAuthAccessToken.objects.get(
        token_checksum=credential_digest(second["access_token"])
    )
    assert replacement_access.revoked_at is not None

    revoke = client.post(
        "/oauth/revoke/",
        {
            "client_id": application.client_id,
            "token": second["refresh_token"],
            "token_type_hint": "refresh_token",
        },
    )
    assert revoke.status_code == 200
    assert revoke["Cache-Control"] == "no-store"
    assert revoke["Pragma"] == "no-cache"


def test_refresh_resource_is_required_byte_exact_and_not_consumed_on_rejection():
    client = Client(enforce_csrf_checks=True)
    user = get_user_model().objects.create_user(
        username=f"refresh-resource-{uuid4().hex}"
    )
    application, _, token = _authorize_and_exchange(client, user)
    base = [
        ("grant_type", "refresh_token"),
        ("client_id", application.client_id),
        ("refresh_token", token["refresh_token"]),
    ]
    exact = settings.SITEHITS_MCP_RESOURCE_URL
    for resources in ((), (f"{exact}/",), (exact, f"{exact}/")):
        response = client.post(
            "/oauth/token/",
            data=urlencode(
                [*base, *(("resource", resource) for resource in resources)]
            ),
            content_type="application/x-www-form-urlencoded",
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_target"

    refresh = OAuthRefreshToken.objects.get(
        token_checksum=credential_digest(token["refresh_token"])
    )
    assert refresh.revoked is None
    accepted = client.post(
        "/oauth/token/",
        data=urlencode(
            [*base, ("resource", exact), ("resource", exact)]
        ),
        content_type="application/x-www-form-urlencoded",
    )
    assert accepted.status_code == 200, accepted.content


def test_refresh_invalid_expanded_scope_does_not_consume_member():
    client = Client(enforce_csrf_checks=True)
    user = get_user_model().objects.create_user(username=f"refresh-scope-{uuid4().hex}")
    application, _, token = _authorize_and_exchange(client, user, scopes="read")
    base = {
        "grant_type": "refresh_token",
        "client_id": application.client_id,
        "refresh_token": token["refresh_token"],
        "resource": settings.SITEHITS_MCP_RESOURCE_URL,
    }
    expanded = client.post(
        "/oauth/token/",
        data={**base, "scope": "read write"},
    )
    assert expanded.status_code == 400
    assert expanded.json()["error"] == "invalid_scope"
    original = OAuthRefreshToken.objects.get(
        token_checksum=credential_digest(token["refresh_token"])
    )
    assert original.revoked is None
    assert original.family_state.revoked_at is None

    retry = client.post("/oauth/token/", data=base)
    assert retry.status_code == 200, retry.content
    original.refresh_from_db()
    assert original.revoked is not None


def test_live_refresh_revocation_revokes_the_family_and_related_access():
    client = Client(enforce_csrf_checks=True)
    user = get_user_model().objects.create_user(username=f"revoke-{uuid4().hex}")
    application, _, token = _authorize_and_exchange(client, user)
    response = client.post(
        "/oauth/revoke/",
        {
            "client_id": application.client_id,
            "token": token["refresh_token"],
            "token_type_hint": "refresh_token",
        },
    )
    assert response.status_code == 200
    refresh = OAuthRefreshToken.objects.get(
        token_checksum=credential_digest(token["refresh_token"])
    )
    access = OAuthAccessToken.objects.get(
        token_checksum=credential_digest(token["access_token"])
    )
    assert refresh.family_revoked_at is not None
    assert refresh.revoked is not None
    assert access.revoked_at is not None

    unknown = client.post(
        "/oauth/revoke/",
        {
            "client_id": application.client_id,
            "token": "unknown-but-opaque-token",
        },
    )
    assert unknown.status_code == 200


def test_revocation_cannot_cross_public_client_boundary():
    client = Client(enforce_csrf_checks=True)
    user = get_user_model().objects.create_user(
        username=f"revoke-owner-{uuid4().hex}"
    )
    application_a, _, token_a = _authorize_and_exchange(client, user)
    application_b, _, token_b = _authorize_and_exchange(client, user)
    access_a = OAuthAccessToken.objects.get(
        token_checksum=credential_digest(token_a["access_token"])
    )
    refresh_a = OAuthRefreshToken.objects.get(
        token_checksum=credential_digest(token_a["refresh_token"])
    )
    access_b = OAuthAccessToken.objects.get(
        token_checksum=credential_digest(token_b["access_token"])
    )
    refresh_b = OAuthRefreshToken.objects.get(
        token_checksum=credential_digest(token_b["refresh_token"])
    )

    for raw_token, token_type_hint in (
        (token_a["access_token"], "access_token"),
        (token_a["refresh_token"], "refresh_token"),
    ):
        response = client.post(
            "/oauth/revoke/",
            {
                "client_id": application_b.client_id,
                "token": raw_token,
                "token_type_hint": token_type_hint,
            },
        )
        # RFC 7009 deliberately does not reveal whether the token was known.
        assert response.status_code == 200

    for record in (access_a, refresh_a, access_b, refresh_b):
        record.refresh_from_db()
    assert access_a.application_id == application_a.pk
    assert access_a.revoked_at is None
    assert refresh_a.revoked is None
    assert refresh_a.family_revoked_at is None
    assert access_b.revoked_at is None
    assert refresh_b.revoked is None
    assert refresh_b.family_revoked_at is None


def test_consent_application_and_user_cascades_invalidate_credentials():
    client = Client(enforce_csrf_checks=True)
    user = get_user_model().objects.create_user(username=f"cascade-{uuid4().hex}")
    application, _, token = _authorize_and_exchange(client, user)
    access = OAuthAccessToken.objects.get(
        token_checksum=credential_digest(token["access_token"])
    )
    consent = OAuthConsent.objects.get(
        user=user,
        application=application,
        decision=OAuthConsent.Decision.APPROVED,
    )
    consent.revoke()
    access.refresh_from_db()
    assert access.revoked_at is not None
    assert not access.is_valid(["read"])

    second_user = get_user_model().objects.create_user(
        username=f"cascade-second-{uuid4().hex}"
    )
    second_application, _, second_token = _authorize_and_exchange(
        Client(enforce_csrf_checks=True), second_user
    )
    second_access = OAuthAccessToken.objects.get(
        token_checksum=credential_digest(second_token["access_token"])
    )
    second_application.revoke()
    second_access.refresh_from_db()
    assert second_access.revoked_at is not None
    assert not second_application.is_usable(None)

    third_user = get_user_model().objects.create_user(
        username=f"cascade-third-{uuid4().hex}"
    )
    _, _, third_token = _authorize_and_exchange(
        Client(enforce_csrf_checks=True), third_user
    )
    third_checksum = credential_digest(third_token["access_token"])
    third_access = OAuthAccessToken.objects.get(token_checksum=third_checksum)
    third_user.is_active = False
    third_user.save(update_fields=["is_active"])
    assert not third_access.is_valid(["read"])
    third_user.delete()
    assert not OAuthAccessToken.objects.filter(token_checksum=third_checksum).exists()


def test_oauth_protocol_errors_and_responses_never_echo_credentials():
    client = Client()
    registration = _register(client).json()
    raw_code = "authorization-code-must-not-echo"
    verifier = "verifier-must-not-echo-" + "x" * 43
    refresh = "refresh-token-must-not-echo"
    response = client.post(
        "/oauth/token/",
        {
            "grant_type": "authorization_code",
            "client_id": registration["client_id"],
            "redirect_uri": REDIRECT_URI,
            "code": raw_code,
            "code_verifier": verifier,
            "refresh_token": refresh,
            "resource": settings.SITEHITS_MCP_RESOURCE_URL,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    assert response["Cache-Control"] == "no-store"
    assert response["Pragma"] == "no-cache"
    serialized = response.content.decode()
    for secret in (raw_code, verifier, refresh):
        assert secret not in serialized


def test_dependency_oauth_loggers_are_non_propagating_and_never_capture_flow_secrets(
    caplog,
):
    for logger_name in ("oauthlib", "oauth2_provider"):
        logger = logging.getLogger(logger_name)
        assert logger.level >= logging.WARNING
        assert logger.propagate is False

    client = Client(enforce_csrf_checks=True)
    user = get_user_model().objects.create_user(username=f"log-safe-{uuid4().hex}")
    with caplog.at_level(logging.DEBUG):
        application, raw_code, first = _authorize_and_exchange(client, user)
        response = client.post(
            "/oauth/token/",
            data={
                "grant_type": "refresh_token",
                "client_id": application.client_id,
                "refresh_token": first["refresh_token"],
                "resource": settings.SITEHITS_MCP_RESOURCE_URL,
            },
        )
    assert response.status_code == 200
    second = response.json()
    rendered = "\n".join(record.getMessage() for record in caplog.records)
    for secret in (
        raw_code,
        first["access_token"],
        first["refresh_token"],
        second["access_token"],
        second["refresh_token"],
        "opaque-state/%2B?=value",
    ):
        assert secret not in rendered


def test_token_revoke_and_dcr_reject_ambiguous_duplicate_parameters():
    client = Client()
    user = get_user_model().objects.create_user(username=f"duplicate-{uuid4().hex}")
    application, raw_code, token = _authorize_and_exchange(client, user)

    token_response = client.post(
        "/oauth/token/",
        data=urlencode(
            [
                ("grant_type", "password"),
                ("grant_type", "authorization_code"),
                ("client_id", application.client_id),
                ("code", raw_code),
                ("resource", settings.SITEHITS_MCP_RESOURCE_URL),
            ]
        ),
        content_type="application/x-www-form-urlencoded",
    )
    assert token_response.status_code == 400
    assert token_response.json()["error"] == "invalid_request"

    revoke_response = client.post(
        "/oauth/revoke/",
        data=urlencode(
            [
                ("client_id", application.client_id),
                ("token", "invalid-first"),
                ("token", token["refresh_token"]),
            ]
        ),
        content_type="application/x-www-form-urlencoded",
    )
    assert revoke_response.status_code == 400
    assert revoke_response.json()["error"] == "invalid_request"

    dcr_response = client.post(
        "/oauth/register/",
        data=(
            '{"redirect_uris":["http://127.0.0.1:43127/callback"],'
            '"token_endpoint_auth_method":"none",'
            '"token_endpoint_auth_method":"client_secret_post"}'
        ),
        content_type="application/json",
    )
    assert dcr_response.status_code == 400
    assert dcr_response.json()["error"] == "invalid_client_metadata"


def test_exception_reporting_redacts_credentials_before_token_dispatch(monkeypatch):
    suffix = uuid4().hex
    raw_values = {
        "code": f"report-code-{suffix}",
        "code_verifier": f"report-verifier-{suffix}",
        "refresh_token": f"report-refresh-{suffix}",
        "client_secret": f"report-secret-{suffix}",
    }
    request = RequestFactory().post(
        "/oauth/token/",
        data={"grant_type": "authorization_code", **raw_values},
    )
    monkeypatch.setattr(
        "mcp_gateway.views._rate_limit_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("rate failure")),
    )

    try:
        SiteHitsTokenView.as_view()(request)
    except RuntimeError as exc:
        reporter = get_exception_reporter_class(request)(
            request,
            RuntimeError,
            exc,
            exc.__traceback__,
        )
        data = reporter.get_traceback_data()
    else:  # pragma: no cover - the patched boundary must raise.
        raise AssertionError("The pre-dispatch exception did not propagate.")

    assert set(request.sensitive_post_parameters) == set(raw_values)
    rendered_request = json.dumps(
        {
            "post": data["filtered_POST_items"],
            "get": data["request_GET_items"],
            "uri": data["request_insecure_uri"],
            "meta": data["request_meta"],
            "frames": data["frames"],
            "exception": data["exception_value"],
        },
        default=str,
    )
    assert all(value not in rendered_request for value in raw_values.values())
    assert get_exception_reporter_filter(request).__class__.__name__ == (
        "SiteHitsExceptionReporterFilter"
    )


def test_exception_reporting_redacts_authorize_state_and_nested_login_target():
    state = "opaque-state-must-not-leak"
    authorize = RequestFactory().get(
        "/oauth/authorize/",
        {"client_id": "client", "scope": "read", "state": state},
    )
    login = RequestFactory().get(
        "/accounts/login/",
        {"next": f"/oauth/authorize/?client_id=client&state={state}"},
    )

    for request in (authorize, login):
        reporter = get_exception_reporter_class(request)(
            request,
            RuntimeError,
            RuntimeError("safe failure"),
            None,
        )
        data = reporter.get_traceback_data()
        rendered_request = json.dumps(
            {
                "get": data["request_GET_items"],
                "uri": data["request_insecure_uri"],
                "meta": data["request_meta"],
                "frames": data["frames"],
                "exception": data["exception_value"],
            },
            default=str,
        )
        assert state not in rendered_request


def test_oauth_lifecycle_audit_mapping_is_correlated_complete_and_digest_only(
    settings,
):
    settings.SITEHITS_TRUST_PROXY_HEADERS = True
    proxy_id = uuid4().hex
    client = Client(
        enforce_csrf_checks=True,
        REMOTE_ADDR="198.51.100.93",
        HTTP_X_REQUEST_ID=proxy_id,
        HTTP_X_SITEHITS_TRUSTED_PROXY="1",
    )
    user = get_user_model().objects.create_user(username=f"audit-{uuid4().hex}")
    application, raw_code, first = _authorize_and_exchange(client, user)

    consent_event = OAuthSecurityEvent.objects.filter(
        event="consent",
        outcome="approved",
        application=application,
    ).latest("created_at")
    assert consent_event.request_id.hex == proxy_id
    assert consent_event.user == user
    assert consent_event.resource == settings.SITEHITS_MCP_RESOURCE_URL
    assert consent_event.scopes == ["read", "write"]
    assert consent_event.details["client_id"] == application.client_id
    assert consent_event.details["requested_scopes"] == ["read", "write"]
    assert consent_event.details["granted_scopes"] == ["read", "write"]
    assert consent_event.details["consent_decision"] == "approved"
    assert consent_event.details["redirect_uri_digest"] == credential_digest(
        REDIRECT_URI
    )

    token_event = OAuthSecurityEvent.objects.get(
        event="token",
        outcome="issued",
        application=application,
    )
    assert token_event.request_id.hex == proxy_id
    assert token_event.user == user
    assert token_event.resource == settings.SITEHITS_MCP_RESOURCE_URL
    assert token_event.scopes == ["read", "write"]
    assert token_event.details["client_id"] == application.client_id
    assert token_event.details["credential_digest"] == credential_digest(raw_code)
    assert token_event.details["grant_digest"] == credential_digest(raw_code)
    assert token_event.details["family_digest"]
    assert token_event.details["replay_detected"] is False
    assert token_event.details["family_revoked"] is False
    assert token_event.details["decision"] == "issued"
    assert token_event.details["error"] == ""

    refresh_response = client.post(
        "/oauth/token/",
        data={
            "grant_type": "refresh_token",
            "client_id": application.client_id,
            "refresh_token": first["refresh_token"],
            "resource": settings.SITEHITS_MCP_RESOURCE_URL,
        },
    )
    assert refresh_response.status_code == 200, refresh_response.content
    refresh_event = OAuthSecurityEvent.objects.get(
        event="refresh",
        outcome="issued",
        application=application,
    )
    assert refresh_event.request_id.hex == proxy_id
    assert refresh_event.user == user
    assert refresh_event.resource == settings.SITEHITS_MCP_RESOURCE_URL
    assert refresh_event.scopes == ["read", "write"]
    assert refresh_event.details["credential_digest"] == credential_digest(
        first["refresh_token"]
    )
    assert refresh_event.details["grant_digest"] == credential_digest(raw_code)
    assert refresh_event.details["family_digest"] == token_event.details[
        "family_digest"
    ]
    assert refresh_event.details["replay_detected"] is False
    assert refresh_event.details["family_revoked"] is False

    second = refresh_response.json()
    revoke_response = client.post(
        "/oauth/revoke/",
        data={
            "client_id": application.client_id,
            "token": second["refresh_token"],
            "token_type_hint": "refresh_token",
        },
    )
    assert revoke_response.status_code == 200
    revoke_event = OAuthSecurityEvent.objects.get(
        event="revoke",
        application=application,
    )
    assert revoke_event.request_id.hex == proxy_id
    assert revoke_event.user == user
    assert revoke_event.resource == settings.SITEHITS_MCP_RESOURCE_URL
    assert revoke_event.scopes == ["read", "write"]
    assert revoke_event.details["credential_digest"] == credential_digest(
        second["refresh_token"]
    )
    assert revoke_event.details["grant_digest"] == credential_digest(raw_code)
    assert revoke_event.details["family_digest"] == token_event.details[
        "family_digest"
    ]
    assert revoke_event.details["family_revoked"] is True
    assert revoke_event.details["revoke_decision"] == "family_revoked"

    serialized_events = json.dumps(
        list(
            OAuthSecurityEvent.objects.filter(application=application).values(
                "subject_digest",
                "details",
            )
        )
    )
    for raw_secret in (
        raw_code,
        first["access_token"],
        first["refresh_token"],
        second["access_token"],
        second["refresh_token"],
    ):
        assert raw_secret not in serialized_events


def test_expired_refresh_audit_is_not_replay_and_preserves_family_scopes():
    client = Client(enforce_csrf_checks=True)
    user = get_user_model().objects.create_user(
        username=f"audit-expiry-{uuid4().hex}"
    )
    application, _, token = _authorize_and_exchange(client, user)
    refresh = OAuthRefreshToken.objects.select_related("family_state").get(
        token_checksum=credential_digest(token["refresh_token"])
    )
    expired_at = refresh.family_state.created_at + timedelta(microseconds=1)
    OAuthRefreshFamily.objects.filter(pk=refresh.family_state_id).update(
        expires_at=expired_at
    )

    response = client.post(
        "/oauth/token/",
        data={
            "grant_type": "refresh_token",
            "client_id": application.client_id,
            "refresh_token": token["refresh_token"],
            "resource": settings.SITEHITS_MCP_RESOURCE_URL,
        },
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"
    event = OAuthSecurityEvent.objects.get(
        event="refresh",
        outcome="rejected",
        application=application,
    )
    assert event.user == user
    assert event.resource == settings.SITEHITS_MCP_RESOURCE_URL
    assert event.scopes == ["read", "write"]
    assert event.details["refresh_family_decision"] == "family_expired"
    assert event.details["replay_detected"] is False
    assert event.details["family_revoked"] is True
    assert event.details["family_digest"]


def test_access_revocation_audit_preserves_and_reports_linked_family():
    client = Client(enforce_csrf_checks=True)
    user = get_user_model().objects.create_user(
        username=f"audit-access-revoke-{uuid4().hex}"
    )
    application, raw_code, token = _authorize_and_exchange(client, user)
    refresh = OAuthRefreshToken.objects.get(
        token_checksum=credential_digest(token["refresh_token"])
    )
    expected_family_digest = hashlib.sha256(
        str(refresh.family_state_id).encode("utf-8")
    ).hexdigest()

    response = client.post(
        "/oauth/revoke/",
        data={
            "client_id": application.client_id,
            "token": token["access_token"],
            "token_type_hint": "access_token",
        },
    )

    assert response.status_code == 200
    event = OAuthSecurityEvent.objects.get(
        event="revoke",
        application=application,
    )
    assert event.user == user
    assert event.resource == settings.SITEHITS_MCP_RESOURCE_URL
    assert event.scopes == ["read", "write"]
    assert event.details["grant_digest"] == credential_digest(raw_code)
    assert event.details["family_digest"] == expected_family_digest
    assert event.details["family_revoked"] is True
    assert event.details["revoke_decision"] == "family_revoked"
