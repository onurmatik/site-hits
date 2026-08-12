"""Contract tests for the versioned django-embedded-mcp public extension seam."""

from __future__ import annotations

import importlib.metadata
import inspect
import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import django_embedded_mcp
import pytest
from django.test import override_settings
from django.utils import timezone
from django_embedded_mcp import (
    DigestDjangoOAuthToolkitTokenVerifier,
    DynamicClientRegistrationError,
    ExactResourceError,
    HeaderOnlyBearerMiddleware,
    RefreshFamilyDecisionCode,
    RefreshFamilyPolicy,
    RefreshFamilyState,
    RefreshMemberState,
    build_auth_failure_challenge,
    build_authorization_server_metadata,
    build_bearer_challenge,
    build_mcp_auth_settings,
    build_protected_resource_metadata,
    build_transport_security_settings,
    credential_digest,
    exact_resource_audience,
    exact_resource_from_pairs,
    non_header_bearer_sources,
    parse_public_client_registration,
    redirect_uri_matches,
    validate_registered_redirect_uri,
)
from mcp.server.auth.provider import TokenVerifier

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "packages" / "django-embedded-mcp"
ISSUER = "https://analytics.example"
RESOURCE = f"{ISSUER}/mcp"
METADATA_URL = f"{ISSUER}/.well-known/oauth-protected-resource/mcp"


def test_package_and_pinned_framework_versions_define_the_public_seam():
    assert django_embedded_mcp.__version__ == "0.2.0"
    assert importlib.metadata.version("django-oauth-toolkit") == "3.4.0"
    assert importlib.metadata.version("mcp") == "2.0.0"
    assert TokenVerifier in DigestDjangoOAuthToolkitTokenVerifier.__mro__
    assert inspect.iscoroutinefunction(
        DigestDjangoOAuthToolkitTokenVerifier.verify_token
    )
    parameters = inspect.signature(
        DigestDjangoOAuthToolkitTokenVerifier.verify_token
    ).parameters
    assert list(parameters) == ["self", "token"]

    package_source = "\n".join(
        path.read_text()
        for path in sorted((PACKAGE_ROOT / "src" / "django_embedded_mcp").glob("*.py"))
    )
    assert "OAuth2Validator" not in package_source
    assert "super()._create" not in package_source
    assert "def _create_" not in package_source
    project_version = re.search(
        r'^version = "([^"]+)"$',
        (PACKAGE_ROOT / "pyproject.toml").read_text(),
        flags=re.MULTILINE,
    ).group(1)
    fallback_version = re.search(
        r'__version__ = "([^"]+)"',
        (PACKAGE_ROOT / "src" / "django_embedded_mcp" / "__init__.py").read_text(),
    ).group(1)
    assert project_version == fallback_version == django_embedded_mcp.__version__


def test_metadata_builders_own_protocol_profile_but_accept_product_identity():
    authorization = build_authorization_server_metadata(
        issuer=ISSUER,
        scopes_supported=("account:read", "site:write"),
        service_documentation=f"{ISSUER}/docs/mcp/",
    )
    assert authorization == {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/oauth/authorize/",
        "token_endpoint": f"{ISSUER}/oauth/token/",
        "registration_endpoint": f"{ISSUER}/oauth/register/",
        "revocation_endpoint": f"{ISSUER}/oauth/revoke/",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "revocation_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
        "authorization_response_iss_parameter_supported": True,
        "scopes_supported": ["account:read", "site:write"],
        "service_documentation": f"{ISSUER}/docs/mcp/",
    }
    protected = build_protected_resource_metadata(
        resource=RESOURCE,
        authorization_server=ISSUER,
        resource_name="Product analytics MCP",
    )
    assert protected == {
        "resource": RESOURCE,
        "authorization_servers": [ISSUER],
        "bearer_methods_supported": ["header"],
        "resource_name": "Product analytics MCP",
    }
    assert "scopes_supported" not in protected


def test_bearer_challenge_is_deterministic_and_rejects_header_injection():
    challenge = build_auth_failure_challenge(
        resource_metadata=METADATA_URL,
        scopes=("account:read",),
        status=401,
        credential_present=False,
    )
    assert challenge == (
        f'Bearer resource_metadata="{METADATA_URL}", scope="account:read"'
    )
    step_up = build_bearer_challenge(
        resource_metadata=METADATA_URL,
        scopes=("site:write",),
        error="insufficient_scope",
        error_description="Required scope: site:write",
    )
    assert 'error="insufficient_scope"' in step_up
    assert 'scope="site:write"' in step_up
    with pytest.raises(ValueError, match="visible ASCII"):
        build_bearer_challenge(
            resource_metadata=f"{METADATA_URL}\r\nX-Injected: yes",
        )


def test_header_only_bearer_guard_detects_query_and_cookie_sources():
    assert non_header_bearer_sources(
        query_string=b"access_token=secret&other=value",
    ) == frozenset({"query"})
    assert non_header_bearer_sources(
        cookie_header=b"session=ok; bearer_token=secret",
    ) == frozenset({"cookie"})
    assert non_header_bearer_sources(
        query_string=b"other=value",
        cookie_header=b"session=ok",
    ) == frozenset()
    assert inspect.isclass(HeaderOnlyBearerMiddleware)


def test_dcr_policy_returns_only_validated_product_model_inputs():
    registration = parse_public_client_registration(
        json.dumps(
            {
                "client_name": "Agent client",
                "redirect_uris": ["http://127.0.0.1:48100/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
                "scope": "account:read site:write",
                "software_id": "agent-host",
            }
        ).encode(),
        supported_scopes=("account:read", "site:write"),
        required_scopes=("account:read",),
        default_scopes=("account:read", "site:write"),
    )
    assert registration.redirect_uris == (
        "http://127.0.0.1:48100/callback",
    )
    assert registration.scopes == ("account:read", "site:write")
    assert registration.metadata == {"software_id": "agent-host"}

    with pytest.raises(ValueError, match="token_endpoint_auth_method must be none"):
        parse_public_client_registration(
            json.dumps(
                {
                    "redirect_uris": ["https://agent.example/callback"],
                    "token_endpoint_auth_method": "client_secret_post",
                }
            ).encode(),
            supported_scopes=("account:read",),
            required_scopes=("account:read",),
            default_scopes=("account:read",),
        )
    with pytest.raises(ValueError, match="must not be repeated"):
        parse_public_client_registration(
            (
                b'{"redirect_uris":["https://agent.example/callback"],'
                b'"token_endpoint_auth_method":"none",'
                b'"token_endpoint_auth_method":"client_secret_post"}'
            ),
            supported_scopes=("account:read",),
            required_scopes=("account:read",),
            default_scopes=("account:read",),
        )


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
            [["https://agent.example/callback"]],
            "invalid_redirect_uri",
        ),
    ],
)
def test_dcr_policy_rejects_non_string_profile_list_members(
    field,
    value,
    expected_error,
):
    payload = {
        "redirect_uris": ["https://agent.example/callback"],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    payload[field] = value

    with pytest.raises(DynamicClientRegistrationError) as caught:
        parse_public_client_registration(
            json.dumps(payload).encode(),
            supported_scopes=("account:read",),
            required_scopes=("account:read",),
            default_scopes=("account:read",),
        )

    assert caught.value.error == expected_error


@pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"-Infinity"])
def test_dcr_policy_rejects_non_json_numeric_constants(constant):
    body = (
        b'{"redirect_uris":["https://agent.example/callback"],'
        b'"token_endpoint_auth_method":"none","unknown":'
        + constant
        + b"}"
    )

    with pytest.raises(DynamicClientRegistrationError) as caught:
        parse_public_client_registration(
            body,
            supported_scopes=("account:read",),
            required_scopes=("account:read",),
            default_scopes=("account:read",),
        )
    assert caught.value.error == "invalid_client_metadata"


@pytest.mark.parametrize(
    "body",
    [
        None,
        b"\xff",
        b'{"unknown":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}",
    ],
)
def test_dcr_policy_maps_decode_type_and_recursion_failures_to_protocol_error(body):
    with pytest.raises(DynamicClientRegistrationError) as caught:
        parse_public_client_registration(
            body,
            supported_scopes=("account:read",),
            required_scopes=("account:read",),
            default_scopes=("account:read",),
        )
    assert caught.value.error == "invalid_client_metadata"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("client_uri", {}),
        ("logo_uri", ["https://agent.example/logo"]),
        ("contacts", "ops@example.com"),
        ("contacts", ["ops@example.com", {}]),
        ("policy_uri", "https://agent.example/policy\x00"),
        ("tos_uri", "https://agent.example/terms\ud800"),
        ("software_id", "agent\x85host"),
        ("software_version", ["1.0"]),
    ],
)
def test_dcr_policy_rejects_unpersistable_metadata(field, value):
    payload = {
        "redirect_uris": ["https://agent.example/callback"],
        "token_endpoint_auth_method": "none",
        field: value,
    }

    with pytest.raises(DynamicClientRegistrationError) as caught:
        parse_public_client_registration(
            json.dumps(payload).encode(),
            supported_scopes=("account:read",),
            required_scopes=("account:read",),
            default_scopes=("account:read",),
        )
    assert caught.value.error == "invalid_client_metadata"


@pytest.mark.parametrize(
    "redirect_uri",
    [
        " https://agent.example/callback",
        "https://agent.example/callback ",
        "https://agent.example/call\tback",
        "https://agent.example/call\x00back",
        "https://agent.example/call\x7fback",
        "https://agent.example/callback#",
        "https://agent.example/call*back",
        "https://agent.example/callback?next=*",
        "https://agent.example/call\\back",
        "https://agent.example/callback?",
        "https://agent.example/call\u00a0back",
        "https://agent.example/call\u0085back",
        "https://agent.example/call\u2028back",
        "https://agent.example/caf\N{LATIN SMALL LETTER E WITH ACUTE}",
        "https://agent.example/call\ud800back",
        "https://agent.example/call<back",
        "https://agent.example/call|back",
        "https://agent.example/callback%",
        "https://agent.example/callback%2",
        "https://agent.example/callback%GG",
        "https://agent.example/callback%2G",
        "https://localhost/callback",
        "https://0.0.0.0/callback",
        "https://[::]/callback",
    ],
)
def test_redirect_policy_rejects_raw_parser_ambiguity_everywhere(redirect_uri):
    with pytest.raises(ValueError):
        validate_registered_redirect_uri(redirect_uri)

    with pytest.raises(ValueError):
        parse_public_client_registration(
            json.dumps(
                {
                    "redirect_uris": [redirect_uri],
                    "token_endpoint_auth_method": "none",
                }
            ).encode(),
            supported_scopes=("account:read",),
            required_scopes=("account:read",),
            default_scopes=("account:read",),
        )


def test_loopback_matcher_rejects_backslash_and_empty_query_ambiguity():
    assert redirect_uri_matches(
        "http://127.0.0.1:41000/callback?fixed=1",
        "http://127.0.0.1:42000/callback?fixed=1",
    )
    assert not redirect_uri_matches(
        "http://127.0.0.1:41000/callback?",
        "http://127.0.0.1:42000/callback",
    )
    assert not redirect_uri_matches(
        "http://127.0.0.1:41000/call\\back",
        "http://127.0.0.1:42000/call\\back",
    )


def test_redirect_policy_can_opt_in_to_exact_http_localhost_for_claude_code():
    registered = "http://localhost:41000/callback"
    requested = "http://localhost:42000/callback"

    assert (
        validate_registered_redirect_uri(registered, allow_localhost=True)
        == registered
    )
    assert redirect_uri_matches(
        registered,
        requested,
        allow_localhost=True,
    )
    assert not redirect_uri_matches(
        registered,
        "http://localhost:42000/other",
        allow_localhost=True,
    )
    for unsafe in ("https://localhost/callback", "http://LOCALHOST/callback"):
        with pytest.raises(ValueError):
            validate_registered_redirect_uri(unsafe, allow_localhost=True)

    registration = parse_public_client_registration(
        json.dumps(
            {
                "redirect_uris": [registered],
                "token_endpoint_auth_method": "none",
            }
        ).encode(),
        supported_scopes=("account:read",),
        required_scopes=("account:read",),
        default_scopes=("account:read",),
        allow_localhost=True,
    )
    assert registration.redirect_uris == (registered,)


def test_redirect_policy_accepts_rfc3986_ascii_and_valid_percent_escapes():
    redirect_uri = (
        "https://agent.example/a~b!$&'()+,;=:@/[]"
        "?next=%2Fdone&label=caf%C3%A9"
    )

    assert validate_registered_redirect_uri(redirect_uri) == redirect_uri


def test_dcr_client_name_accepts_unicode_but_rejects_unpaired_surrogates():
    payload = {
        "redirect_uris": ["https://agent.example/callback"],
        "token_endpoint_auth_method": "none",
        "client_name": "Ölçüm Agent’ı 🚀",
    }
    registration = parse_public_client_registration(
        json.dumps(payload).encode(),
        supported_scopes=("account:read",),
        required_scopes=("account:read",),
        default_scopes=("account:read",),
    )
    assert registration.client_name == payload["client_name"]

    for client_name in (
        "broken-\ud800-name",
        "broken-\udfff-name",
        "broken-\x00-name",
        "broken-\n-name",
        "broken-\x85-name",
        "broken-\u2028-name",
    ):
        payload["client_name"] = client_name
        with pytest.raises(DynamicClientRegistrationError) as caught:
            parse_public_client_registration(
                json.dumps(payload).encode(),
                supported_scopes=("account:read",),
                required_scopes=("account:read",),
                default_scopes=("account:read",),
            )
        assert caught.value.error == "invalid_client_metadata"


def test_exact_resource_policy_rejects_non_utf8_scalar_values_as_protocol_errors():
    with pytest.raises(ExactResourceError):
        exact_resource_from_pairs(
            [("resource", "https://analytics.example/\ud800")],
            expected=RESOURCE,
        )
    with pytest.raises(ExactResourceError):
        exact_resource_from_pairs(
            [("resource", None)],
            expected=RESOURCE,
        )

    normalized, resource = exact_resource_from_pairs(
        [("state", "opaque"), ("resource", RESOURCE), ("resource", RESOURCE)],
        expected=RESOURCE,
    )
    assert normalized == [("state", "opaque"), ("resource", RESOURCE)]
    assert resource == RESOURCE


def test_refresh_family_policy_owns_status_binding_and_replay_decisions():
    now = datetime.now(UTC)
    family_id = uuid4()
    family = RefreshFamilyState(
        family_id=family_id,
        user_id="user-1",
        client_id="client-1",
        resource=RESOURCE,
        expires_at=now + timedelta(days=1),
        revoked_at=None,
    )
    member = RefreshMemberState(
        user_id="user-1",
        client_id="client-1",
        family_id=family_id,
        family_mirror_id=family_id,
        resources=(RESOURCE,),
        consumed_at=None,
    )
    policy = RefreshFamilyPolicy(expected_resource=RESOURCE)

    def evaluate(
        *,
        evaluated_family=family,
        evaluated_member=member,
        presented_client_id="client-1",
        requested_resources=(RESOURCE,),
        user_active=True,
        client_active=True,
    ):
        return policy.evaluate_rotation(
            family=evaluated_family,
            member=evaluated_member,
            presented_client_id=presented_client_id,
            requested_resources=requested_resources,
            user_active=user_active,
            client_active=client_active,
            now=now,
        )

    active = evaluate()
    assert active.code == RefreshFamilyDecisionCode.ACTIVE
    assert active.rotation_allowed is True
    assert active.revoke_family is False
    assert active.replay_detected is False
    assert policy.evaluate_member_binding(
        family=family,
        member=member,
    ).code == RefreshFamilyDecisionCode.ACTIVE
    assert policy.family_is_expired(expires_at=family.expires_at, now=now) is False
    assert policy.family_is_expired(expires_at=now, now=now) is True

    cases = [
        (
            {"evaluated_family": replace(family, revoked_at=now)},
            RefreshFamilyDecisionCode.FAMILY_REVOKED,
            False,
        ),
        (
            {"evaluated_family": replace(family, expires_at=now)},
            RefreshFamilyDecisionCode.FAMILY_EXPIRED,
            False,
        ),
        (
            {"evaluated_member": replace(member, consumed_at=now)},
            RefreshFamilyDecisionCode.MEMBER_CONSUMED,
            True,
        ),
        ({"user_active": False}, RefreshFamilyDecisionCode.USER_INACTIVE, False),
        ({"client_active": False}, RefreshFamilyDecisionCode.CLIENT_INACTIVE, False),
        (
            {"presented_client_id": "other-client"},
            RefreshFamilyDecisionCode.CLIENT_MISMATCH,
            False,
        ),
        (
            {"evaluated_family": replace(family, user_id="other-user")},
            RefreshFamilyDecisionCode.FAMILY_USER_MISMATCH,
            False,
        ),
        (
            {"evaluated_family": replace(family, client_id="other-client")},
            RefreshFamilyDecisionCode.FAMILY_CLIENT_MISMATCH,
            False,
        ),
        (
            {"evaluated_member": replace(member, family_mirror_id=uuid4())},
            RefreshFamilyDecisionCode.FAMILY_IDENTITY_MISMATCH,
            False,
        ),
        (
            {"requested_resources": (f"{RESOURCE}/",)},
            RefreshFamilyDecisionCode.REQUEST_RESOURCE_MISMATCH,
            False,
        ),
        (
            {"evaluated_member": replace(member, resources=(f"{RESOURCE}/",))},
            RefreshFamilyDecisionCode.MEMBER_RESOURCE_MISMATCH,
            False,
        ),
        (
            {"evaluated_family": replace(family, resource=f"{RESOURCE}/")},
            RefreshFamilyDecisionCode.FAMILY_RESOURCE_MISMATCH,
            False,
        ),
    ]
    for overrides, expected_code, replay_detected in cases:
        decision = evaluate(**overrides)
        assert decision.code == expected_code
        assert decision.rotation_allowed is False
        assert decision.revoke_family is True
        assert decision.replay_detected is replay_detected

    claimed = policy.evaluate_claim(claimed_rows=1)
    replay = policy.evaluate_claim(claimed_rows=0)
    assert claimed.code == RefreshFamilyDecisionCode.CLAIMED
    assert claimed.rotation_allowed is True
    assert replay.code == RefreshFamilyDecisionCode.CONCURRENT_REPLAY
    assert replay.rotation_allowed is False
    assert replay.revoke_family is True
    assert replay.replay_detected is True


def test_sitehits_refresh_adapter_delegates_to_package_policy(settings, monkeypatch):
    from mcp_gateway.oauth import _evaluate_refresh_rotation

    settings.DEBUG = True
    family_id = uuid4()
    client_id = 71
    user_id = 83
    expected = settings.SITEHITS_MCP_RESOURCE_URL
    application = SimpleNamespace(
        pk=client_id,
        is_usable=lambda request: True,
    )
    record = SimpleNamespace(
        user_id=user_id,
        application_id=client_id,
        family_state_id=family_id,
        token_family=family_id,
        resource=[expected],
        revoked=None,
        user=SimpleNamespace(is_active=True),
        application=application,
    )
    family = SimpleNamespace(
        pk=family_id,
        user_id=user_id,
        application_id=client_id,
        resource=expected,
        expires_at=timezone.now() + timedelta(days=1),
        revoked_at=None,
    )
    calls = []
    original = RefreshFamilyPolicy.evaluate_rotation

    def observed(policy, **kwargs):
        calls.append(kwargs)
        return original(policy, **kwargs)

    monkeypatch.setattr(RefreshFamilyPolicy, "evaluate_rotation", observed)
    decision = _evaluate_refresh_rotation(
        record,
        family,
        application,
        [expected],
        SimpleNamespace(),
    )
    assert decision.code == RefreshFamilyDecisionCode.ACTIVE
    assert len(calls) == 1
    assert calls[0]["member"].family_mirror_id == family_id
    assert calls[0]["family"].client_id == client_id


class _Application:
    client_id = "public-client"


class _TokenRecord:
    application = _Application()
    user_id = 42
    expires = timezone.now() + timedelta(minutes=15)
    scope = "account:read"

    def __init__(self, *, valid=True):
        self.valid = valid
        self.resource = [RESOURCE]

    def is_valid(self):
        return self.valid


def test_digest_token_verifier_uses_only_digest_lookup_and_exact_resource():
    raw_token = "opaque-access-token-with-enough-entropy"
    seen = []
    record = _TokenRecord()
    verifier = DigestDjangoOAuthToolkitTokenVerifier(
        resource=RESOURCE,
        issuer=ISSUER,
        allowed_scopes=("account:read", "site:write"),
        record_resolver=lambda checksum: seen.append(checksum) or record,
    )
    verified = verifier.verify_token_sync(raw_token)
    assert seen == [credential_digest(raw_token)]
    assert raw_token not in seen
    assert verified is not None
    assert verified.client_id == "public-client"
    assert verified.subject == "42"
    assert verified.resource == RESOURCE
    assert verified.claims == {"iss": ISSUER, "aud": RESOURCE}

    record.resource = [f"{RESOURCE}/"]
    assert verifier.verify_token_sync(raw_token) is None


def test_dot_resource_validator_keeps_two_argument_setting_contract():
    with override_settings(DJANGO_EMBEDDED_MCP_RESOURCE_URL=RESOURCE):
        assert exact_resource_audience(RESOURCE, [RESOURCE]) is True
        assert exact_resource_audience(f"{RESOURCE}/", [RESOURCE]) is False
        assert exact_resource_audience(RESOURCE, [RESOURCE, RESOURCE]) is False


def test_mcp_sdk_builders_bind_only_public_sdk_configuration():
    auth = build_mcp_auth_settings(
        issuer_url=ISSUER,
        resource_server_url=RESOURCE,
        required_scopes=("account:read",),
        service_documentation_url=f"{ISSUER}/docs/mcp/",
    )
    assert str(auth.issuer_url).rstrip("/") == ISSUER
    assert str(auth.resource_server_url).rstrip("/") == RESOURCE
    assert auth.required_scopes == ["account:read"]

    security = build_transport_security_settings(
        resource_url=RESOURCE,
        allowed_origins=("https://chatgpt.com", "https://codex.openai.com"),
        production=True,
    )
    assert security.enable_dns_rebinding_protection is True
    assert security.allowed_hosts == ["analytics.example"]
    assert security.allowed_origins == [
        "https://chatgpt.com",
        "https://codex.openai.com",
    ]
    with pytest.raises(ValueError, match="explicit production allowlist"):
        build_transport_security_settings(
            resource_url=RESOURCE,
            allowed_origins=("*",),
            production=True,
        )
