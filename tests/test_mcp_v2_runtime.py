import asyncio
import hashlib
import importlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from mcp.server import MCPServer
from mcp.server.auth.provider import AccessToken
from mcp.shared.exceptions import MCPError

from agent_runtime.contract import clear_contract_caches, pinned_contract_identity
from agent_runtime.revisions import revision_for
from analytics.models import AgentAuditEvent
from mcp_gateway.registry import canonical_registry_json, registry_sha256
from mcp_gateway.server import AGENT_CONTRACT, TOOL_REGISTRY, SiteHitsMCPServer


class RequestContextStub:
    def __init__(self, request_id, headers=None):
        self.request_id = request_id
        self.headers = headers or {}


def _access_token(user, scopes):
    return SimpleNamespace(
        subject=str(user.pk),
        client_id="oauth-client-v2",
        scopes=list(scopes),
    )


class VerifierStub:
    async def verify_token(self, token):
        return AccessToken(token=token, client_id="test", scopes=["read"])


def _server():
    return SiteHitsMCPServer(token_verifier=VerifierStub())


def _call(server, name, arguments, *, request_id="mcp_v2_request", headers=None):
    return asyncio.run(
        server.call_tool(
            name,
            arguments,
            RequestContextStub(request_id, headers=headers),
        )
    )


def test_v2_server_is_explicit_and_registry_is_contract_exact():
    from mcp_gateway import server as server_module

    assert issubclass(SiteHitsMCPServer, MCPServer)
    source = inspect.getsource(server_module)
    assert "FastMCP" not in source
    assert "@mcp.tool" not in source

    server = _server()
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    public = {
        name: tool
        for name, tool in AGENT_CONTRACT["tools"].items()
        if tool["exposure"] == "public"
    }
    assert set(tools) == set(public)

    registry_by_name = {entry.name: entry for entry in TOOL_REGISTRY}
    for name, tool in tools.items():
        contract_tool = public[name]
        input_name = contract_tool["input_schema"]["$ref"].rsplit("/", 1)[1]
        output_name = contract_tool["output_schema"]["$ref"].rsplit("/", 1)[1]
        assert {key: value for key, value in tool.input_schema.items() if key != "$defs"} == (
            AGENT_CONTRACT["$defs"][input_name]
        )
        assert {key: value for key, value in tool.output_schema.items() if key != "$defs"} == (
            AGENT_CONTRACT["$defs"][output_name]
        )
        expected_security = [
            {"type": "oauth2", "scopes": contract_tool["required_scopes"]}
        ]
        assert list(registry_by_name[name].security_schemes) == expected_security
        assert tool.meta["securitySchemes"] == expected_security
        assert tool.annotations.read_only_hint is (
            contract_tool["side_effect"] == "read_only"
        )
        assert tool.annotations.destructive_hint is contract_tool["destructive"]
        assert tool.annotations.open_world_hint is contract_tool["open_world"]

    canonical = canonical_registry_json(TOOL_REGISTRY)
    assert canonical == canonical_registry_json(TOOL_REGISTRY)
    assert len(registry_sha256(TOOL_REGISTRY)) == 64


def test_runtime_rejects_joint_contract_and_descriptor_tampering(tmp_path):
    root = Path(__file__).resolve().parents[1]
    contract_dir = tmp_path / "agent"
    release_dir = tmp_path / "release"
    contract_dir.mkdir()
    release_dir.mkdir()
    contract_path = contract_dir / "contract.yaml"
    descriptor_path = release_dir / "contract-release.json"
    contract = json.loads((root / "agent" / "contract.yaml").read_text())
    descriptor = json.loads((root / "release" / "contract-release.json").read_text())
    contract["server_instructions"]["summary"] += " Tampered."
    contract_bytes = (json.dumps(contract, indent=2, ensure_ascii=False) + "\n").encode()
    contract_path.write_bytes(contract_bytes)
    descriptor["contract_sha256"] = hashlib.sha256(contract_bytes).hexdigest()
    descriptor_path.write_text(json.dumps(descriptor, indent=2) + "\n")

    with override_settings(BASE_DIR=tmp_path):
        clear_contract_caches()
        with pytest.raises(RuntimeError, match="not the pinned artifact"):
            pinned_contract_identity()
    clear_contract_caches()


def test_first_512_instruction_characters_are_a_complete_standalone_prelude():
    from mcp_gateway.server import CONTRACT_SERVER_INSTRUCTIONS

    prefix = CONTRACT_SERVER_INSTRUCTIONS[:512]
    assert len(prefix) == 512
    assert prefix.rstrip().endswith("authoritative for every call.")
    assert AGENT_CONTRACT["server_instructions"]["summary"] in prefix
    assert "Rules:" not in prefix


@pytest.mark.django_db(transaction=True)
def test_raw_contract_input_is_strict_and_invalid_input_is_audited(monkeypatch):
    user = get_user_model().objects.create_user("mcp-v2-strict")
    monkeypatch.setattr(
        "mcp_gateway.server.get_access_token",
        lambda: _access_token(user, ["read", "write"]),
    )
    server = _server()

    coerced_boolean = _call(
        server,
        "list_sites",
        {"include_inactive": "yes"},
        request_id="strict_boolean",
    )
    unknown_field = _call(
        server,
        "get_account_capabilities",
        {"undeclared": True},
        request_id="strict_extra",
    )
    invalid_enum = _call(
        server,
        "get_analytics_overview",
        {"period": "forever"},
        request_id="strict_enum",
    )

    for result in (coerced_boolean, unknown_field, invalid_enum):
        assert result.is_error is True
        assert result.structured_content["error"]["code"] == "invalid_input"
    assert set(
        AgentAuditEvent.objects.filter(outcome_code="invalid_input").values_list(
            "request_id",
            flat=True,
        )
    ) == {"strict_boolean", "strict_extra", "strict_enum"}
    audit = AgentAuditEvent.objects.get(request_id="strict_boolean")
    assert audit.authenticated_actor_id == str(user.pk)
    assert audit.authenticated_client_id == "oauth-client-v2"
    assert audit.authorization["scope_allowed"] is True


@pytest.mark.django_db(transaction=True)
def test_scope_step_up_is_protocol_native_and_audited(monkeypatch):
    user = get_user_model().objects.create_user("mcp-v2-scope")
    monkeypatch.setattr(
        "mcp_gateway.server.get_access_token",
        lambda: _access_token(user, ["read"]),
    )
    server = _server()

    result = _call(
        server,
        "create_site",
        {
            "name": "Blocked",
            "allowed_domains": ["blocked.example"],
            "idempotency_key": "blocked-create-v2",
        },
        request_id="scope_step_up",
    )

    assert result.is_error is True
    challenge = result.meta["mcp/www_authenticate"][0]
    assert 'scope="read write"' in challenge
    assert 'error="insufficient_scope"' in challenge
    assert not user.tracked_sites.filter(name="Blocked").exists()
    audit = AgentAuditEvent.objects.get(request_id="scope_step_up")
    assert audit.outcome_code == "insufficient_scope"
    assert audit.authorization["scope_allowed"] is False
    assert audit.idempotency_id != "blocked-create-v2"
    assert len(audit.idempotency_id) == 64


@pytest.mark.django_db(transaction=True)
def test_success_and_application_error_keep_one_correlation_id(monkeypatch):
    user = get_user_model().objects.create_user("mcp-v2-correlation")
    site = user.tracked_sites.create(
        name="Correlation",
        slug="correlation",
        allowed_domains=["correlation.example"],
    )
    monkeypatch.setattr(
        "mcp_gateway.server.get_access_token",
        lambda: _access_token(user, ["read", "write"]),
    )
    server = _server()

    listed = _call(
        server,
        "list_sites",
        {},
        request_id="ignored_request_id",
        headers={"X-Request-ID": "proxy_correlation_001"},
    )
    denied = _call(
        server,
        "delete_site",
        {
            "site_slug": site.slug,
            "expected_revision": revision_for(site),
        },
        request_id="approval_correlation_001",
    )

    assert listed.is_error is False
    assert listed.structured_content["sites"][0]["slug"] == site.slug
    assert denied.is_error is True
    assert denied.structured_content["error"]["code"] == "confirmation_required"
    assert denied.structured_content["error"]["request_id"] == "approval_correlation_001"
    assert AgentAuditEvent.objects.filter(
        request_id="proxy_correlation_001",
        tool_name="list_sites",
        outcome_code="success",
    ).exists()
    assert AgentAuditEvent.objects.filter(
        request_id="approval_correlation_001",
        tool_name="delete_site",
        outcome_code="confirmation_required",
    ).exists()
    assert user.tracked_sites.filter(pk=site.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_invalid_service_output_is_sanitized_and_audited(monkeypatch):
    user = get_user_model().objects.create_user("mcp-v2-output")
    monkeypatch.setattr(
        "mcp_gateway.server.get_access_token",
        lambda: _access_token(user, ["read"]),
    )

    class InvalidService:
        def __init__(self, *args, **kwargs):
            pass

        def get_account_capabilities(self):
            return {"provider_secret": "must-not-leak"}

    monkeypatch.setattr("mcp_gateway.server.SiteHitsService", InvalidService)
    result = _call(
        _server(),
        "get_account_capabilities",
        {},
        request_id="invalid_output",
    )

    assert result.is_error is True
    assert result.structured_content["error"] == {
        "code": "internal_error",
        "message": "The operation could not be completed.",
        "retryable": False,
        "request_id": "invalid_output",
        "details": {},
    }
    assert "provider_secret" not in result.content[0].text
    assert AgentAuditEvent.objects.get(request_id="invalid_output").outcome_code == (
        "internal_error"
    )


def test_unknown_tool_remains_an_mcp_protocol_error(monkeypatch):
    monkeypatch.setattr("mcp_gateway.server.get_access_token", lambda: None)
    with pytest.raises(MCPError):
        _call(_server(), "undeclared_tool", {})


def test_standalone_asgi_uses_explicit_transport_security():
    with override_settings(
        DEBUG=False,
        SITEHITS_BASE_URL="https://sitehits.example",
        SITEHITS_MCP_ISSUER_URL="https://sitehits.example",
        SITEHITS_MCP_RESOURCE_URL="https://sitehits.example/mcp",
        MCP_RESOURCE_METADATA_URL=(
            "https://sitehits.example/.well-known/oauth-protected-resource/mcp"
        ),
        SITEHITS_MCP_CORS_ORIGINS=["https://chatgpt.com", "https://codex.openai.com"],
    ):
        mcp_asgi = importlib.import_module("mcp_gateway.mcp_asgi")
        transport_security_settings = mcp_asgi.transport_security_settings
        security = transport_security_settings()
    assert security.enable_dns_rebinding_protection is True
    assert security.allowed_hosts == ["sitehits.example"]
    assert security.allowed_origins == [
        "https://chatgpt.com",
        "https://codex.openai.com",
    ]

    with override_settings(
        DEBUG=False,
        SITEHITS_MCP_RESOURCE_URL="https://sitehits.example/mcp",
        SITEHITS_MCP_CORS_ORIGINS=["*"],
    ), pytest.raises(Exception, match="explicit production allowlist"):
        transport_security_settings()
