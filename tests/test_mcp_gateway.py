"""Stage 1 conformance tests for the standalone MCP v2 ASGI process."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from mcp.types import LATEST_PROTOCOL_VERSION
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS
from starlette.testclient import TestClient

from agent_runtime.revisions import revision_for
from mcp_gateway.auth import token_verifier
from mcp_gateway.http import build_mcp_application
from mcp_gateway.mcp_asgi import transport_security_settings
from mcp_gateway.oauth import credential_digest
from mcp_gateway.server import (
    AGENT_CONTRACT,
    CONTRACT_SERVER_INSTRUCTIONS,
    TOOL_REGISTRY,
    SiteHitsMCPServer,
)
from mcp_gateway.versioning import SERVER_VERSION
from mcp_oauth.models import OAuthAccessToken, OAuthApplication, OAuthConsent

pytestmark = pytest.mark.django_db(transaction=True)

RESOURCE = settings.SITEHITS_MCP_RESOURCE_URL
METADATA_URL = settings.MCP_RESOURCE_METADATA_URL
BASE_URL = settings.SITEHITS_BASE_URL
ALLOWED_ORIGIN = settings.SITEHITS_MCP_CORS_ORIGINS[0]
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
MODERN_PROTOCOL_VERSION = "2026-07-28"
MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": MODERN_PROTOCOL_VERSION,
    "io.modelcontextprotocol/clientCapabilities": {},
    "io.modelcontextprotocol/clientInfo": {
        "name": "sitehits-modern-tests",
        "version": "1.0.0",
    },
}


@pytest.fixture
def mcp_client(settings):
    settings.DEBUG = True
    try:
        server = SiteHitsMCPServer(token_verifier=token_verifier)
        application = build_mcp_application(
            server.streamable_http_app(
                streamable_http_path="/mcp",
                json_response=True,
                stateless_http=True,
                transport_security=transport_security_settings(),
                host=settings.SITEHITS_MCP_HOST,
            )
        )
        with TestClient(application, base_url=BASE_URL) as client:
            yield client
    finally:
        settings.DEBUG = False


@pytest.fixture
def mcp_user():
    return get_user_model().objects.create_user(
        username=f"mcp-stage-one-{uuid4().hex}",
        email=f"mcp-stage-one-{uuid4().hex}@example.com",
    )


def _oauth_application(scopes=("read", "write")) -> OAuthApplication:
    return OAuthApplication.objects.create(
        client_id=f"mcp-client-{uuid4().hex}",
        redirect_uris="http://127.0.0.1:43127/callback",
        client_type=OAuthApplication.CLIENT_PUBLIC,
        authorization_grant_type=OAuthApplication.GRANT_AUTHORIZATION_CODE,
        client_secret="",
        hash_client_secret=False,
        skip_authorization=False,
        registration_source=OAuthApplication.RegistrationSource.DCR,
        allowed_scopes=list(scopes),
    )


def _access_token(
    user,
    scopes=("read",),
    *,
    resource: str = RESOURCE,
    expires=None,
    revoked=False,
) -> tuple[OAuthAccessToken, str]:
    application = _oauth_application(scopes=scopes)
    OAuthConsent.objects.create(
        user=user,
        application=application,
        resource=resource,
        scopes=list(scopes),
        redirect_uri_digest="a" * 64,
        decision=OAuthConsent.Decision.APPROVED,
    )
    raw = f"mcp-stage-one-access-{uuid4().hex}{uuid4().hex}"
    record = OAuthAccessToken.objects.create(
        user=user,
        application=application,
        token="",
        token_checksum=credential_digest(raw),
        expires=expires or (timezone.now() + timedelta(minutes=15)),
        scope=" ".join(scopes),
        resource=[resource],
        revoked_at=None,
    )
    if revoked:
        record.revoke()
        record.refresh_from_db()
    return record, raw


def _post(client, payload, *, token: str | None = None, origin: str | None = None):
    headers = dict(MCP_HEADERS)
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if origin is not None:
        headers["Origin"] = origin
    return client.post("/mcp", json=payload, headers=headers)


def _initialize(client, token):
    return _post(
        client,
        {
            "jsonrpc": "2.0",
            "id": "initialize-stage-one",
            "method": "initialize",
            "params": {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "stage-one-tests", "version": "1.0.0"},
            },
        },
        token=token,
    )


def _modern_post(
    client,
    method: str,
    *,
    token: str,
    params: dict | None = None,
    name: str | None = None,
):
    headers = {
        **MCP_HEADERS,
        "Authorization": f"Bearer {token}",
        "MCP-Protocol-Version": MODERN_PROTOCOL_VERSION,
        "Mcp-Method": method,
    }
    if name is not None:
        headers["Mcp-Name"] = name
    return client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": f"modern-{method}",
            "method": method,
            "params": {**(params or {}), "_meta": dict(MODERN_META)},
        },
        headers=headers,
    )


def test_mcp_cors_preflight_runs_before_authentication_and_uses_allowlist(mcp_client):
    allowed = mcp_client.options(
        "/mcp",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": (
                "authorization,content-type,mcp-protocol-version,mcp-method,mcp-name"
            ),
        },
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    allowed_headers = allowed.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed_headers
    assert "content-type" in allowed_headers
    assert "mcp-protocol-version" in allowed_headers
    assert "mcp-method" in allowed_headers
    assert "mcp-name" in allowed_headers
    exposed = allowed.headers["access-control-expose-headers"].lower()
    assert "www-authenticate" in exposed
    assert "mcp-session-id" in exposed

    denied = mcp_client.options(
        "/mcp",
        headers={
            "Origin": "https://not-allowed.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert denied.status_code in {400, 403}
    assert "access-control-allow-origin" not in denied.headers


def test_mcp_proxy_correlation_requires_internal_marker_after_client_rewrite(
    mcp_client,
    settings,
):
    settings.SITEHITS_TRUST_PROXY_HEADERS = True
    proxy_request_id = uuid4().hex
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}

    unmarked = mcp_client.post(
        "/mcp",
        json=payload,
        headers={**MCP_HEADERS, "X-Request-ID": proxy_request_id},
    )
    assert unmarked.status_code == 401
    assert unmarked.headers["x-request-id"] != proxy_request_id

    marked = mcp_client.post(
        "/mcp",
        json=payload,
        headers={
            **MCP_HEADERS,
            "X-Request-ID": proxy_request_id,
            "X-SiteHits-Trusted-Proxy": "1",
        },
    )
    assert marked.status_code == 401
    assert marked.headers["x-request-id"] == proxy_request_id


def test_missing_invalid_expired_revoked_and_wrong_resource_challenges(
    mcp_client,
    mcp_user,
):
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    missing = _post(mcp_client, request)
    assert missing.status_code == 401
    challenge = missing.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    assert f'resource_metadata="{METADATA_URL}"' in challenge
    assert 'scope="read"' in challenge
    assert "error=" not in challenge

    _, valid_raw = _access_token(mcp_user)
    invalid_cases = [
        "presented-but-unknown-bearer-token",
        _access_token(
            mcp_user,
            expires=timezone.now() - timedelta(seconds=1),
        )[1],
        _access_token(mcp_user, revoked=True)[1],
        _access_token(mcp_user, resource=f"{RESOURCE}/")[1],
    ]
    for raw in invalid_cases:
        response = _post(mcp_client, request, token=raw)
        assert response.status_code == 401
        invalid_challenge = response.headers["www-authenticate"]
        assert f'resource_metadata="{METADATA_URL}"' in invalid_challenge
        assert 'error="invalid_token"' in invalid_challenge
        assert raw not in response.text
        assert raw not in invalid_challenge

    valid = _post(mcp_client, request, token=valid_raw)
    assert valid.status_code == 200, valid.text


def test_bearer_query_and_cookie_credentials_are_rejected(mcp_client, mcp_user):
    _, raw = _access_token(mcp_user)
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    query = mcp_client.post(
        f"/mcp?access_token={raw}",
        json=payload,
        headers=MCP_HEADERS,
    )
    mcp_client.cookies.set("access_token", raw)
    cookie = _post(mcp_client, payload)
    mcp_client.cookies.clear()

    for response in (query, cookie):
        assert response.status_code == 401
        challenge = response.headers["www-authenticate"]
        assert 'error="invalid_token"' in challenge
        assert 'scope="read"' in challenge
        assert raw not in response.text
        assert raw not in challenge


def test_token_without_bootstrap_scope_gets_http_insufficient_scope(
    mcp_client,
    mcp_user,
):
    _, raw = _access_token(mcp_user, scopes=("write",))
    response = _post(
        mcp_client,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        token=raw,
    )
    assert response.status_code == 403
    challenge = response.headers["www-authenticate"]
    assert f'resource_metadata="{METADATA_URL}"' in challenge
    assert 'scope="read"' in challenge
    assert 'error="insufficient_scope"' in challenge


def test_sdk_v2_initialize_and_tools_list_are_contract_exact(mcp_client, mcp_user):
    _, raw = _access_token(mcp_user, scopes=("read", "write"))
    initialized = _initialize(mcp_client, raw)
    assert initialized.status_code == 200, initialized.text
    initialize_result = initialized.json()["result"]
    assert initialize_result["protocolVersion"] in {
        "2025-11-25",
        LATEST_PROTOCOL_VERSION,
    }
    assert initialize_result["serverInfo"] == {
        "name": "sitehits",
        "title": "SiteHits analytics MCP",
        "version": SERVER_VERSION,
        "description": AGENT_CONTRACT["server_instructions"]["summary"],
        "websiteUrl": settings.SITEHITS_BASE_URL,
    }
    assert initialize_result["instructions"] == CONTRACT_SERVER_INSTRUCTIONS
    assert len(initialize_result["instructions"][:512]) > 0

    listed = _post(
        mcp_client,
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
        token=raw,
    )
    assert listed.status_code == 200, listed.text
    published = {
        tool["name"]: tool for tool in listed.json()["result"]["tools"]
    }
    expected_entries = {
        entry.name: entry for entry in TOOL_REGISTRY if entry.exposure == "public"
    }
    assert set(published) == set(expected_entries)
    for name, entry in expected_entries.items():
        expected = entry.to_mcp_tool().model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )
        assert published[name] == expected

    bootstrap = _post(
        mcp_client,
        {
            "jsonrpc": "2.0",
            "id": 31,
            "method": "tools/call",
            "params": {
                "name": AGENT_CONTRACT["bootstrap"]["tool"],
                "arguments": {},
            },
        },
        token=raw,
    )
    assert bootstrap.status_code == 200, bootstrap.text
    bootstrap_result = bootstrap.json()["result"]
    assert bootstrap_result["isError"] is False
    assert "capabilities" in bootstrap_result["structuredContent"]
    assert "limits" in bootstrap_result["structuredContent"]


@pytest.mark.parametrize("protocol_version", HANDSHAKE_PROTOCOL_VERSIONS)
def test_legacy_handshake_revisions_share_the_contract_registry(
    mcp_client,
    mcp_user,
    protocol_version,
):
    _, raw = _access_token(mcp_user, scopes=("read", "write"))
    initialized = _post(
        mcp_client,
        {
            "jsonrpc": "2.0",
            "id": f"initialize-{protocol_version}",
            "method": "initialize",
            "params": {
                "protocolVersion": protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "legacy-revision-tests", "version": "1.0.0"},
            },
        },
        token=raw,
    )
    assert initialized.status_code == 200, initialized.text
    assert initialized.json()["result"]["protocolVersion"] == protocol_version

    listed = mcp_client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
        headers={
            **MCP_HEADERS,
            "Authorization": f"Bearer {raw}",
            "MCP-Protocol-Version": protocol_version,
        },
    )
    assert listed.status_code == 200, listed.text
    published = {tool["name"]: tool for tool in listed.json()["result"]["tools"]}
    expected_entries = {
        entry.name: entry for entry in TOOL_REGISTRY if entry.exposure == "public"
    }
    assert set(published) == set(expected_entries)
    for name, entry in expected_entries.items():
        expected = entry.to_mcp_tool().model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )
        assert published[name] == expected


def test_modern_mcp_is_stateless_and_contract_exact(mcp_client, mcp_user):
    _, raw = _access_token(mcp_user, scopes=("read", "write"))

    discovered = _modern_post(
        mcp_client,
        "server/discover",
        token=raw,
    )
    assert discovered.status_code == 200, discovered.text
    assert "mcp-session-id" not in discovered.headers
    discovery = discovered.json()["result"]
    assert discovery["supportedVersions"] == [MODERN_PROTOCOL_VERSION]
    assert discovery["instructions"] == CONTRACT_SERVER_INSTRUCTIONS
    assert discovery["resultType"] == "complete"
    assert discovery["_meta"]["io.modelcontextprotocol/serverInfo"] == {
        "name": "sitehits",
        "title": "SiteHits analytics MCP",
        "version": SERVER_VERSION,
        "description": AGENT_CONTRACT["server_instructions"]["summary"],
        "websiteUrl": settings.SITEHITS_BASE_URL,
    }

    listed = _modern_post(mcp_client, "tools/list", token=raw)
    assert listed.status_code == 200, listed.text
    assert "mcp-session-id" not in listed.headers
    result = listed.json()["result"]
    assert result["resultType"] == "complete"
    assert {tool["name"] for tool in result["tools"]} == {
        entry.name for entry in TOOL_REGISTRY if entry.exposure == "public"
    }
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["version"] == (
        SERVER_VERSION
    )

    bootstrap_name = AGENT_CONTRACT["bootstrap"]["tool"]
    bootstrap = _modern_post(
        mcp_client,
        "tools/call",
        token=raw,
        name=bootstrap_name,
        params={"name": bootstrap_name, "arguments": {}},
    )
    assert bootstrap.status_code == 200, bootstrap.text
    bootstrap_result = bootstrap.json()["result"]
    assert bootstrap_result["isError"] is False
    assert "capabilities" in bootstrap_result["structuredContent"]
    assert bootstrap_result["resultType"] == "complete"


def test_modern_mcp_rejects_missing_routing_headers(mcp_client, mcp_user):
    _, raw = _access_token(mcp_user)
    response = mcp_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": "modern-missing-method-header",
            "method": "tools/list",
            "params": {"_meta": dict(MODERN_META)},
        },
        headers={
            **MCP_HEADERS,
            "Authorization": f"Bearer {raw}",
            "MCP-Protocol-Version": MODERN_PROTOCOL_VERSION,
        },
    )
    assert response.status_code == 400
    assert "mcp-method header does not match" in response.json()["error"]["message"]

    bootstrap_name = AGENT_CONTRACT["bootstrap"]["tool"]
    missing_name = _modern_post(
        mcp_client,
        "tools/call",
        token=raw,
        params={"name": bootstrap_name, "arguments": {}},
    )
    assert missing_name.status_code == 400
    assert "mcp-name header does not match" in missing_name.json()["error"]["message"]


def test_established_call_scope_step_up_uses_mcp_meta_challenge(
    mcp_client,
    mcp_user,
):
    _, raw = _access_token(mcp_user, scopes=("read",))
    response = _post(
        mcp_client,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "create_site",
                "arguments": {
                    "name": "Must not be created",
                    "allowed_domains": ["blocked.example"],
                    "idempotency_key": "blocked-stage-one",
                },
            },
        },
        token=raw,
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["isError"] is True
    challenge = result["_meta"]["mcp/www_authenticate"][0]
    assert f'resource_metadata="{METADATA_URL}"' in challenge
    assert 'scope="read write"' in challenge
    assert 'error="insufficient_scope"' in challenge
    assert not mcp_user.tracked_sites.filter(name="Must not be created").exists()


def test_protocol_and_application_errors_remain_in_their_own_layers(
    mcp_client,
    mcp_user,
):
    _, raw = _access_token(mcp_user, scopes=("read", "write"))
    unknown = _post(
        mcp_client,
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "not_a_contract_tool", "arguments": {}},
        },
        token=raw,
    )
    assert unknown.status_code == 200
    assert unknown.json()["error"]["code"] == -32602
    assert "structuredContent" not in unknown.json()["error"]
    assert "www-authenticate" not in unknown.headers

    invalid_input = _post(
        mcp_client,
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "list_sites",
                "arguments": {"include_inactive": "yes"},
            },
        },
        token=raw,
    )
    assert invalid_input.status_code == 200
    application_result = invalid_input.json()["result"]
    assert application_result["isError"] is True
    envelope = application_result["structuredContent"]["error"]
    assert envelope["code"] == "invalid_input"
    assert envelope["retryable"] is False
    assert envelope["request_id"]
    assert "www-authenticate" not in invalid_input.headers
    assert "mcp/www_authenticate" not in application_result.get("_meta", {})


def test_authenticated_read_dispatches_through_contract_service(mcp_client, mcp_user):
    site = mcp_user.tracked_sites.create(
        name="MCP Stage One",
        slug="mcp-stage-one",
        allowed_domains=["stage-one.example"],
    )
    _, raw = _access_token(mcp_user, scopes=("read",))
    response = _post(
        mcp_client,
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "list_sites", "arguments": {}},
        },
        token=raw,
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"]["structuredContent"] == {
        "sites": [
            {
                "slug": site.slug,
                "name": site.name,
                "allowed_domains": site.allowed_domains,
                "timezone": site.timezone,
                "is_active": True,
                "created_at": site.created_at.isoformat(),
                "updated_at": site.updated_at.isoformat(),
                "revision": revision_for(site),
            }
        ]
    }


def test_tracking_widget_remains_a_django_template_resource():
    from mcp_gateway.server import tracking_setup_widget

    html = tracking_setup_widget()
    assert "static/css/sitehits.css" in html
    assert 'class="bg-slate-950' in html
    assert "ui/notifications/tool-result" in html
    assert "setup_guidance" in html
