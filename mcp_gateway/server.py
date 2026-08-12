import json
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict

from agent_runtime import ApplicationError, ApprovalAssertion, RequestContext, SiteHitsService
from agent_runtime.contract import load_contract

from .http import OAUTH_SCOPES, protected_resource_metadata_url
from .oauth import oauth_provider
from .versioning import AGENT_CONTRACT_VERSION, SERVER_VERSION, integration_status

TRACKING_SETUP_RESOURCE_URI = "ui://sitehits/tracking-setup-v1.html"
Period = Literal["today", "last24h", "last7d", "last30d", "last90d"]
Granularity = Literal["auto", "hourly", "daily"]
BreakdownDimension = Literal[
    "pages",
    "referrers",
    "countries",
    "regions",
    "cities",
    "devices",
    "browsers",
    "os",
    "campaigns",
    "events",
]
Aggregation = Literal["count", "unique_actors", "sum", "average"]
TrackingSection = Literal["all", "browser", "bot", "product"]
TOOL_REQUIRED_SCOPES: dict[str, tuple[str, ...]] = {}
_REQUEST_ID: ContextVar[str | None] = ContextVar("sitehits_mcp_request_id", default=None)


AGENT_CONTRACT = load_contract()
if AGENT_CONTRACT["agent_contract_version"] != AGENT_CONTRACT_VERSION:
    raise RuntimeError("MCP adapter and canonical Agent Contract versions do not match.")


def _render_contract_server_instructions(contract):
    instructions = contract["server_instructions"]
    lines = [instructions["summary"], "", "Rules:"]
    lines.extend(f"- [{rule['id']}] {rule['text']}" for rule in instructions["rules"])
    return "\n".join(lines)


def _contract_idempotent_hint(contract_tool):
    side_effect = contract_tool["side_effect"]
    mode = contract_tool["idempotency"]["mode"]
    if side_effect == "read_only":
        if mode != "not_required":
            raise RuntimeError("Read-only tools must use not_required idempotency mode.")
        return True
    if mode in {"key", "natural_key", "optimistic_revision"}:
        return True
    if mode == "not_required":
        return False
    raise RuntimeError(f"Unknown Agent Contract idempotency mode: {mode}")


def _contract_annotations(contract_tool):
    return ToolAnnotations(
        readOnlyHint=contract_tool["side_effect"] == "read_only",
        destructiveHint=contract_tool["destructive"],
        idempotentHint=_contract_idempotent_hint(contract_tool),
        openWorldHint=contract_tool["open_world"],
    )


CONTRACT_SERVER_INSTRUCTIONS = _render_contract_server_instructions(AGENT_CONTRACT)


def _contract_schema(schema):
    definitions = deepcopy(AGENT_CONTRACT["$defs"])
    reference = schema.get("$ref")
    if (
        set(schema) == {"$ref"}
        and isinstance(reference, str)
        and reference.startswith("#/$defs/")
    ):
        root_name = reference.removeprefix("#/$defs/")
        return {**deepcopy(definitions[root_name]), "$defs": definitions}
    return {**deepcopy(schema), "$defs": definitions}


class DeleteSiteApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: Literal["agent"]
    action: Literal["delete_site"]
    resource_id: str
    confirmed: Literal[True]


class ChangeMeasurementContractApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: Literal["agent"]
    action: Literal["change_measurement_event_contract"]
    resource_id: str
    confirmed: Literal[True]


class DeleteMeasurementEventApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: Literal["agent"]
    action: Literal["delete_measurement_event"]
    resource_id: str
    confirmed: Literal[True]


class ClearActivationApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: Literal["agent"]
    action: Literal["clear_activation"]
    resource_id: str
    confirmed: Literal[True]


class SiteHitsFastMCP(FastMCP):
    async def list_tools(self):
        tools = await super().list_tools()
        contract_tools = AGENT_CONTRACT["tools"]
        advertised_names = {tool.name for tool in tools}
        public_names = {
            name for name, contract_tool in contract_tools.items()
            if contract_tool["exposure"] == "public"
        }
        if advertised_names != public_names:
            mismatch = sorted(advertised_names ^ public_names)
            raise RuntimeError(f"MCP registry differs from Agent Contract: {', '.join(mismatch)}")
        for tool in tools:
            contract_tool = contract_tools[tool.name]
            scopes = list(contract_tool["required_scopes"])
            if tuple(scopes) != TOOL_REQUIRED_SCOPES[tool.name]:
                raise RuntimeError(f"MCP scopes differ from Agent Contract for {tool.name}.")
            if tool.title != contract_tool["title"]:
                raise RuntimeError(f"MCP title differs from Agent Contract for {tool.name}.")
            if tool.description != contract_tool["description"]:
                raise RuntimeError(
                    f"MCP description differs from Agent Contract for {tool.name}."
                )
            if tool.annotations != _contract_annotations(contract_tool):
                raise RuntimeError(
                    f"MCP annotations differ from Agent Contract for {tool.name}."
                )
            tool.inputSchema = _contract_schema(contract_tool["input_schema"])
            tool.outputSchema = _contract_schema(contract_tool["output_schema"])
            security_schemes = [{"type": "oauth2", "scopes": scopes}]
            # MCP Tool permits extension fields. Publish the current top-level field and
            # retain the metadata copy for OpenAI hosts that consumed the earlier shape.
            tool.__pydantic_extra__ = {
                **(tool.__pydantic_extra__ or {}),
                "securitySchemes": security_schemes,
            }
            tool.meta = {**(tool.meta or {}), "securitySchemes": security_schemes}
        return tools


mcp = SiteHitsFastMCP(
    "sitehits",
    instructions=CONTRACT_SERVER_INSTRUCTIONS,
    website_url=settings.SITEHITS_BASE_URL,
    auth_server_provider=oauth_provider,
    auth=AuthSettings(
        issuer_url=settings.SITEHITS_MCP_ISSUER_URL,
        resource_server_url=settings.SITEHITS_MCP_RESOURCE_URL,
        service_documentation_url=settings.SITEHITS_MCP_DOCUMENTATION_URL,
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=list(OAUTH_SCOPES),
            default_scopes=["read"],
        ),
        revocation_options=RevocationOptions(enabled=True),
        required_scopes=["read"],
    ),
    host=settings.SITEHITS_MCP_HOST,
    port=settings.SITEHITS_MCP_PORT,
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
)
mcp._mcp_server.version = SERVER_VERSION


def _current_user():
    access_token = get_access_token()
    if access_token is None or not access_token.subject:
        raise ToolError("Authentication is required.")
    user_model = get_user_model()
    try:
        return user_model.objects.get(pk=access_token.subject, is_active=True)
    except (user_model.DoesNotExist, ValueError, ValidationError) as exc:
        raise ToolError("The authenticated SiteHits user is unavailable.") from exc


def _current_service():
    access_token = get_access_token()
    if access_token is None:
        raise ToolError("Authentication is required.")
    user = _current_user()
    context = RequestContext(
        authenticated_actor_id=str(user.pk),
        authenticated_client_id=access_token.client_id,
        granted_scopes=frozenset(access_token.scopes),
        request_id=_REQUEST_ID.get() or uuid4().hex,
    )
    return SiteHitsService(context, integration_status_provider=integration_status), context


def _agent_approval(approval):
    if approval is None:
        return None
    return ApprovalAssertion(
        owner=approval.owner,
        action=approval.action,
        resource_id=approval.resource_id,
        confirmed=approval.confirmed,
    )


def _scope_challenge(required_scopes):
    requested = " ".join(required_scopes)
    return (
        "Bearer "
        f'resource_metadata="{protected_resource_metadata_url()}", '
        f'scope="{requested}", error="insufficient_scope", '
        f'error_description="Required scope: {requested}"'
    )


def _scope_error_result(required_scopes):
    requested = " ".join(required_scopes)
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=f"The access token lacks the required scope: {requested}.",
            )
        ],
        structuredContent={},
        isError=True,
        _meta={"mcp/www_authenticate": [_scope_challenge(required_scopes)]},
    )


def _has_scopes(*required_scopes):
    access_token = get_access_token()
    return access_token is not None and set(required_scopes).issubset(access_token.scopes)


def _contract_tool(*, meta=None):
    """Register a public MCP tool using only canonical Contract semantics."""

    def decorator(function):
        tool_name = function.__name__
        try:
            contract_tool = AGENT_CONTRACT["tools"][tool_name]
        except KeyError as exc:
            raise RuntimeError(f"MCP tool is absent from Agent Contract: {tool_name}") from exc
        if contract_tool["exposure"] != "public":
            raise RuntimeError(f"MCP tool is not public in Agent Contract: {tool_name}")

        required_scopes = tuple(contract_tool["required_scopes"])
        if not required_scopes:
            raise RuntimeError(f"MCP tool has no Contract scopes: {tool_name}")
        TOOL_REQUIRED_SCOPES[tool_name] = required_scopes

        @wraps(function)
        async def wrapped(*args, **kwargs):
            if not _has_scopes(*required_scopes):
                return _scope_error_result(required_scopes)
            request_id = uuid4().hex
            request_id_token = _REQUEST_ID.set(request_id)
            try:
                return await sync_to_async(function, thread_sensitive=True)(*args, **kwargs)
            except ToolError:
                raise
            except ApplicationError as exc:
                envelope = exc.to_envelope(request_id)
                return CallToolResult(
                    content=[TextContent(type="text", text=json.dumps(envelope))],
                    structuredContent={"error": envelope},
                    isError=True,
                )
            except (ValidationError, ValueError) as exc:
                if isinstance(exc, ValidationError):
                    message = "; ".join(exc.messages)
                else:
                    message = str(exc)
                raise ToolError(message) from exc
            finally:
                _REQUEST_ID.reset(request_id_token)

        registration = {
            "title": contract_tool["title"],
            "description": contract_tool["description"],
            "annotations": _contract_annotations(contract_tool),
        }
        if meta is not None:
            registration["meta"] = meta
        return mcp.tool(**registration)(wrapped)

    return decorator


@_contract_tool()
def get_account_capabilities() -> dict[str, object]:
    service, _ = _current_service()
    return service.get_account_capabilities()


@_contract_tool()
def list_sites(include_inactive: bool = False) -> dict[str, object]:
    service, _ = _current_service()
    return service.list_sites(include_inactive=include_inactive)


@_contract_tool()
def get_site(site_slug: str) -> dict[str, object]:
    service, _ = _current_service()
    return service.get_site(site_slug=site_slug)


@_contract_tool()
def create_site(
    name: str,
    allowed_domains: list[str],
    idempotency_key: str,
    timezone: str = "Europe/Istanbul",
) -> dict[str, object]:
    service, _ = _current_service()
    return service.create_site(
        name=name,
        allowed_domains=allowed_domains,
        timezone=timezone,
        idempotency_key=idempotency_key,
    )


@_contract_tool()
def update_site(
    site_slug: str,
    expected_revision: str,
    name: str | None = None,
    allowed_domains: list[str] | None = None,
    timezone: str | None = None,
    is_active: bool | None = None,
) -> dict[str, object]:
    service, _ = _current_service()
    return service.update_site(
        site_slug=site_slug,
        expected_revision=expected_revision,
        name=name,
        allowed_domains=allowed_domains,
        timezone=timezone,
        is_active=is_active,
    )


@_contract_tool()
def delete_site(
    site_slug: str,
    expected_revision: str,
    approval: DeleteSiteApproval | None = None,
) -> dict[str, object]:
    service, _ = _current_service()
    return service.delete_site(
        site_slug=site_slug,
        expected_revision=expected_revision,
        approval=_agent_approval(approval),
    )


@_contract_tool()
def get_analytics_overview(
    site_slug: str = "all",
    period: Period = "last7d",
) -> dict[str, object]:
    service, _ = _current_service()
    return service.get_analytics_overview(site_slug=site_slug, period=period)


@_contract_tool()
def get_sites_overview(period: Period = "last7d") -> dict[str, object]:
    service, _ = _current_service()
    return service.get_sites_overview(period=period)


@_contract_tool()
def get_analytics_timeseries(
    site_slug: str = "all",
    period: Period = "last7d",
    granularity: Granularity = "auto",
) -> dict[str, object]:
    service, _ = _current_service()
    return service.get_analytics_timeseries(
        site_slug=site_slug,
        period=period,
        granularity=granularity,
    )


@_contract_tool()
def get_analytics_breakdown(
    site_slug: str,
    dimension: BreakdownDimension,
    period: Period = "last7d",
    limit: int = 8,
) -> dict[str, object]:
    service, _ = _current_service()
    return service.get_analytics_breakdown(
        site_slug=site_slug,
        dimension=dimension,
        period=period,
        limit=limit,
    )


@_contract_tool()
def get_bot_analytics(
    site_slug: str = "all",
    period: Period = "last7d",
    limit: int = 8,
) -> dict[str, object]:
    service, _ = _current_service()
    return service.get_bot_analytics(site_slug=site_slug, period=period, limit=limit)


@_contract_tool()
def get_product_metrics(site_slug: str, period: Period = "last7d") -> dict[str, object]:
    service, _ = _current_service()
    return service.get_product_metrics(site_slug=site_slug, period=period)


@_contract_tool()
def get_measurement_config(site_slug: str) -> dict[str, object]:
    service, _ = _current_service()
    return service.get_measurement_config(site_slug=site_slug)


@_contract_tool()
def create_measurement_event(
    site_slug: str,
    event_name: str,
    display_name: str,
    description: str,
    aggregation: Aggregation = "count",
    unit: str = "",
) -> dict[str, object]:
    service, _ = _current_service()
    return service.create_measurement_event(
        site_slug=site_slug,
        event_name=event_name,
        display_name=display_name,
        description=description,
        aggregation=aggregation,
        unit=unit,
    )


@_contract_tool()
def update_measurement_event(
    site_slug: str,
    event_name: str,
    expected_revision: str,
    display_name: str | None = None,
    description: str | None = None,
) -> dict[str, object]:
    service, _ = _current_service()
    return service.update_measurement_event(
        site_slug=site_slug,
        event_name=event_name,
        expected_revision=expected_revision,
        display_name=display_name,
        description=description,
    )


@_contract_tool()
def change_measurement_event_contract(
    site_slug: str,
    event_name: str,
    expected_revision: str,
    aggregation: Aggregation,
    unit: str,
    approval: ChangeMeasurementContractApproval | None = None,
) -> dict[str, object]:
    service, _ = _current_service()
    return service.change_measurement_event_contract(
        site_slug=site_slug,
        event_name=event_name,
        expected_revision=expected_revision,
        aggregation=aggregation,
        unit=unit,
        approval=_agent_approval(approval),
    )


@_contract_tool()
def delete_measurement_event(
    site_slug: str,
    event_name: str,
    expected_revision: str,
    approval: DeleteMeasurementEventApproval | None = None,
) -> dict[str, object]:
    service, _ = _current_service()
    return service.delete_measurement_event(
        site_slug=site_slug,
        event_name=event_name,
        expected_revision=expected_revision,
        approval=_agent_approval(approval),
    )


@_contract_tool()
def set_activation(
    site_slug: str,
    start_event: str,
    goal_event: str,
    expected_revision: str | None,
) -> dict[str, object]:
    service, _ = _current_service()
    return service.set_activation(
        site_slug=site_slug,
        start_event=start_event,
        goal_event=goal_event,
        expected_revision=expected_revision,
    )


@_contract_tool()
def clear_activation(
    site_slug: str,
    expected_revision: str,
    approval: ClearActivationApproval | None = None,
) -> dict[str, object]:
    service, _ = _current_service()
    return service.clear_activation(
        site_slug=site_slug,
        expected_revision=expected_revision,
        approval=_agent_approval(approval),
    )


@_contract_tool()
def get_tracking_setup(
    site_slug: str,
    section: TrackingSection = "all",
) -> dict[str, object]:
    service, _ = _current_service()
    return service.get_tracking_setup(site_slug=site_slug, section=section)


@_contract_tool(
    meta={
        "ui": {"resourceUri": TRACKING_SETUP_RESOURCE_URI},
        "openai/outputTemplate": TRACKING_SETUP_RESOURCE_URI,
        "openai/toolInvocation/invoking": "Preparing SiteHits tracking setup",
        "openai/toolInvocation/invoked": "SiteHits tracking setup is ready",
    },
)
def render_tracking_setup(
    site_slug: str,
    section: TrackingSection = "all",
) -> dict[str, object]:
    service, _ = _current_service()
    return service.render_tracking_setup(site_slug=site_slug, section=section)


@_contract_tool()
def get_integration_status(skill_version: str | None = None) -> dict[str, object]:
    service, _ = _current_service()
    return service.get_integration_status(skill_version=skill_version)


@mcp.resource(
    TRACKING_SETUP_RESOURCE_URI,
    name="SiteHits tracking setup",
    description="Optional inline presentation for SiteHits tracking setup data.",
    mime_type="text/html;profile=mcp-app",
    meta={
        "ui": {
            "prefersBorder": True,
            "csp": {
                "connectDomains": [],
                "resourceDomains": [
                    (
                        f"{urlsplit(settings.SITEHITS_BASE_URL).scheme}://"
                        f"{urlsplit(settings.SITEHITS_BASE_URL).netloc}"
                    )
                ],
            },
        },
        "openai/widgetCSP": {
            "connect_domains": [],
            "resource_domains": [
                (
                    f"{urlsplit(settings.SITEHITS_BASE_URL).scheme}://"
                    f"{urlsplit(settings.SITEHITS_BASE_URL).netloc}"
                )
            ],
        },
        "openai/widgetDescription": (
            "Shows browser, bot, and product tracking setup returned by SiteHits."
        ),
        "openai/widgetPrefersBorder": True,
    },
)
def tracking_setup_widget() -> str:
    stylesheet_url = f"{settings.SITEHITS_BASE_URL}{settings.STATIC_URL}css/sitehits.css"
    return render_to_string(
        "mcp_gateway/tracking_setup_widget.html",
        {"stylesheet_url": stylesheet_url},
    )


def _validate_tool_scope_map():
    if tuple(AGENT_CONTRACT["scopes"]) != OAUTH_SCOPES:
        raise RuntimeError("OAuth scope catalog differs from Agent Contract.")
    registered_tools = mcp._tool_manager._tools
    if set(registered_tools) != set(TOOL_REQUIRED_SCOPES):
        missing = sorted(set(registered_tools) ^ set(TOOL_REQUIRED_SCOPES))
        raise RuntimeError(f"SiteHits tool scope map is incomplete: {', '.join(missing)}")
    contract_tools = {
        name: tool
        for name, tool in AGENT_CONTRACT["tools"].items()
        if tool["exposure"] == "public"
    }
    if set(registered_tools) != set(contract_tools):
        mismatch = sorted(set(registered_tools) ^ set(contract_tools))
        raise RuntimeError(f"MCP registry differs from Agent Contract: {', '.join(mismatch)}")
    for name, scopes in TOOL_REQUIRED_SCOPES.items():
        if list(scopes) != contract_tools[name]["required_scopes"]:
            raise RuntimeError(f"MCP scopes differ from Agent Contract for {name}.")


_validate_tool_scope_map()
