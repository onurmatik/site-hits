import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from starlette.testclient import TestClient

from agent_runtime.revisions import revision_for
from mcp_gateway.models import MCPAccessToken


@pytest.fixture
def mcp_user(db):
    return get_user_model().objects.create_user(
        username="mcp-owner",
        email="mcp-owner@example.com",
    )


@pytest.mark.django_db
def test_mcp_access_token_is_hashed_scoped_and_revocable(mcp_user):
    token, raw_token = MCPAccessToken.issue(user=mcp_user, name="Codex")

    assert raw_token.startswith("shm_")
    assert raw_token not in token.token_digest
    assert MCPAccessToken.authenticate(raw_token) == token
    assert MCPAccessToken.authenticate("shm_invalid") is None

    token.revoked_at = timezone.now()
    token.save(update_fields=["revoked_at"])
    assert MCPAccessToken.authenticate(raw_token) is None


def test_tracking_widget_uses_django_template_tailwind_and_mcp_app_bridge():
    from mcp_gateway.server import tracking_setup_widget

    html = tracking_setup_widget()

    assert "static/css/sitehits.css" in html
    assert 'class="bg-slate-950' in html
    assert "ui/notifications/tool-result" in html
    assert "textContent" in html
    assert "setup_guidance" in html


def test_tool_registration_semantics_come_from_agent_contract():
    from mcp_gateway.server import (
        AGENT_CONTRACT,
        TOOL_REQUIRED_SCOPES,
        _contract_annotations,
        mcp,
    )

    registered = mcp._tool_manager._tools
    public_contract_tools = {
        name: tool
        for name, tool in AGENT_CONTRACT["tools"].items()
        if tool["exposure"] == "public"
    }
    assert set(registered) == set(public_contract_tools)
    for name, registered_tool in registered.items():
        contract_tool = public_contract_tools[name]
        assert registered_tool.title == contract_tool["title"]
        assert registered_tool.description == contract_tool["description"]
        assert registered_tool.annotations == _contract_annotations(contract_tool)
        assert TOOL_REQUIRED_SCOPES[name] == tuple(contract_tool["required_scopes"])


@pytest.mark.django_db(transaction=True)
def test_streamable_http_requires_bearer_token_and_calls_tools():
    from config.asgi import application

    user = get_user_model().objects.create_user("protocol-owner")
    site = user.tracked_sites.create(
        name="Protocol site",
        slug="protocol-site",
        allowed_domains=["protocol.example"],
    )
    _, raw_token = MCPAccessToken.issue(user=user, name="Protocol test")
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "list_sites", "arguments": {}},
    }
    headers = {
        "Authorization": f"Bearer {raw_token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }

    with TestClient(application) as client:
        unauthorized = client.post("/mcp", json=request)
        response = client.post("/mcp", json=request, headers=headers)
        tools_response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers=headers,
        )
        resources_response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 3, "method": "resources/list"},
            headers=headers,
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["isError"] is False, payload
    assert payload["result"]["structuredContent"] == {
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
    assert tools_response.status_code == 200
    tools = tools_response.json()["result"]["tools"]
    list_sites_tool = next(tool for tool in tools if tool["name"] == "list_sites")
    render_tool = next(tool for tool in tools if tool["name"] == "render_tracking_setup")
    assert list_sites_tool["outputSchema"]["type"] == "object"
    assert list_sites_tool["annotations"]["readOnlyHint"] is True
    assert render_tool["_meta"]["ui"]["resourceUri"].startswith("ui://sitehits/")

    assert resources_response.status_code == 200
    resource = resources_response.json()["result"]["resources"][0]
    assert resource["uri"] == "ui://sitehits/tracking-setup-v1.html"
    assert resource["mimeType"] == "text/html;profile=mcp-app"
