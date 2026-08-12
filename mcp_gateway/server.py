"""Explicit MCP v2 server backed by the canonical SiteHits Agent Contract."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import close_old_connections, connections
from django.template.loader import render_to_string
from django_embedded_mcp.challenges import build_bearer_challenge
from django_embedded_mcp.mcp import build_mcp_auth_settings
from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import TokenVerifier
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.resources.types import FunctionResource
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS, CallToolResult, TextContent, Tool

from agent_runtime import ApplicationError, ApprovalAssertion, RequestContext, SiteHitsService
from agent_runtime.audit import AuditRecorder
from agent_runtime.contract import (
    get_tool_contract,
    load_contract,
    validate_tool_input,
    validate_tool_output,
)
from agent_runtime.hashing import private_digest
from analytics.models import AgentAuditEvent

from .auth import token_verifier
from .http import protected_resource_metadata_url
from .registry import (
    TRACKING_SETUP_RESOURCE_URI,
    RegistryEntry,
    build_deployment_registry,
    contract_annotations,
)
from .versioning import AGENT_CONTRACT_VERSION, SERVER_VERSION, integration_status

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


AGENT_CONTRACT = load_contract()
if AGENT_CONTRACT["agent_contract_version"] != AGENT_CONTRACT_VERSION:
    raise RuntimeError("MCP adapter and canonical Agent Contract versions do not match.")


def _render_contract_server_instructions(contract: Mapping[str, Any]) -> str:
    instructions = contract["server_instructions"]
    standalone = (
        f"{instructions['summary']}\n\n"
        "The complete cross-tool rules follow below and remain authoritative for every call."
    )
    if len(standalone) > 512:
        raise RuntimeError("The Agent Contract instruction summary exceeds 512 characters.")
    # MCP hosts may preview exactly 512 characters. Keep that prefix complete and
    # readable, then append every canonical rule without truncating or paraphrasing it.
    prefix = standalone.ljust(512)
    lines = [prefix, "", "Rules:"]
    lines.extend(f"- [{rule['id']}] {rule['text']}" for rule in instructions["rules"])
    return "\n".join(lines)


def _tracking_resource_meta() -> dict[str, Any]:
    base_url = urlsplit(settings.SITEHITS_BASE_URL)
    resource_origin = f"{base_url.scheme}://{base_url.netloc}"
    return {
        "ui": {
            "prefersBorder": True,
            "csp": {"connectDomains": [], "resourceDomains": [resource_origin]},
        },
        "openai/widgetCSP": {
            "connect_domains": [],
            "resource_domains": [resource_origin],
        },
        "openai/widgetDescription": (
            "Shows browser, bot, and product tracking setup returned by SiteHits."
        ),
        "openai/widgetPrefersBorder": True,
    }


CONTRACT_SERVER_INSTRUCTIONS = _render_contract_server_instructions(AGENT_CONTRACT)
TOOL_REGISTRY = build_deployment_registry(contract=AGENT_CONTRACT)
TOOL_REQUIRED_SCOPES = {entry.name: entry.required_scopes for entry in TOOL_REGISTRY}


def _contract_annotations(contract_tool: Mapping[str, Any]):
    """Compatibility import for callers that inspect Contract-derived annotations."""

    return contract_annotations(contract_tool)


def tracking_setup_widget() -> str:
    stylesheet_url = f"{settings.SITEHITS_BASE_URL}{settings.STATIC_URL}css/sitehits.css"
    return render_to_string(
        "mcp_gateway/tracking_setup_widget.html",
        {"stylesheet_url": stylesheet_url},
    )


def _request_id(context: Context[None, Any] | None) -> str:
    if context is not None:
        try:
            headers = context.headers or {}
        except (AttributeError, ValueError):
            headers = {}
        lowered = {str(key).lower(): str(value) for key, value in headers.items()}
        forwarded = lowered.get("x-request-id")
        if forwarded and _REQUEST_ID_PATTERN.fullmatch(forwarded):
            return forwarded
        try:
            candidate = str(context.request_id)
        except (AttributeError, ValueError):
            candidate = ""
        if _REQUEST_ID_PATTERN.fullmatch(candidate):
            return candidate
    return uuid4().hex


def _scope_challenge(required_scopes: tuple[str, ...]) -> str:
    requested = " ".join(required_scopes)
    return build_bearer_challenge(
        resource_metadata=protected_resource_metadata_url(),
        scopes=required_scopes,
        error="insufficient_scope",
        error_description=f"Required scope: {requested}",
    )


def _scope_error_result(required_scopes: tuple[str, ...]) -> CallToolResult:
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


def _application_error_result(error: ApplicationError, request_id: str) -> CallToolResult:
    envelope = error.to_envelope(request_id)
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=json.dumps(envelope, sort_keys=True, separators=(",", ":")),
            )
        ],
        structuredContent={"error": envelope},
        isError=True,
    )


def _success_result(result: object) -> CallToolResult:
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=json.dumps(result, sort_keys=True, separators=(",", ":")),
            )
        ],
        structuredContent=result,
        isError=False,
    )


def _target_resource_id(entry: RegistryEntry, arguments: Mapping[str, Any], actor_id: str) -> str:
    site_slug = arguments.get("site_slug")
    event_name = arguments.get("event_name")
    if isinstance(site_slug, str) and isinstance(event_name, str):
        return f"{site_slug}/{event_name}"
    if isinstance(site_slug, str):
        return site_slug
    if entry.resource_type == "account":
        return actor_id
    if entry.resource_type == "integration":
        return "sitehits"
    return ""


def _authorization_snapshot(
    entry: RegistryEntry,
    granted_scopes: frozenset[str],
    arguments: Mapping[str, Any],
) -> dict[str, object]:
    spec = get_tool_contract(entry.name)
    approval = arguments.get("approval")
    return {
        "authentication_required": entry.authentication == "required",
        "authenticated": True,
        "required_scopes": list(entry.required_scopes),
        "granted_scopes": sorted(granted_scopes),
        "scope_allowed": set(entry.required_scopes).issubset(granted_scopes),
        "capability": spec.required_capability,
        "capability_allowed": "not_evaluated",
        "ownership": entry.ownership,
        "ownership_allowed": "not_evaluated",
        "limit_applicable": spec.limit_name is not None,
        "limit_name": spec.limit_name,
        "limit_allowed": "not_evaluated" if spec.limit_name else True,
        "limit_details": {},
        "approval_required": spec.approval_required,
        "approval_confirmed": bool(
            isinstance(approval, dict) and approval.get("confirmed") is True
        ),
        "approval_allowed": "not_evaluated" if spec.approval_required else True,
    }


def _record_adapter_audit(
    *,
    request_context: RequestContext,
    entry: RegistryEntry,
    arguments: Mapping[str, Any],
    outcome_code: str,
) -> None:
    # Service-dispatched calls already own their audit event. The adapter records
    # only failures that correctly stop before business dispatch (scope/input) or
    # an adapter-level implementation fault.
    if AgentAuditEvent.objects.filter(
        request_id=request_context.request_id,
        tool_name=entry.name,
    ).exists():
        return
    AuditRecorder().record(
        context=request_context,
        tool_name=entry.name,
        target_resource_type=entry.resource_type,
        target_resource_id=_target_resource_id(
            entry,
            arguments,
            request_context.authenticated_actor_id,
        ),
        authorization=_authorization_snapshot(
            entry,
            request_context.granted_scopes,
            arguments,
        ),
        inputs=dict(arguments),
        outcome_code=outcome_code,
        idempotency_id=(
            private_digest(str(arguments["idempotency_key"]))
            if "idempotency_key" in arguments
            else ""
        ),
    )


def _run_sync_db(operation, /, *args, **kwargs):
    """Run one MCP ORM operation with an explicit non-Django request boundary."""

    close_old_connections()
    try:
        return operation(*args, **kwargs)
    finally:
        connections.close_all()


def _dispatch_service(
    entry: RegistryEntry,
    arguments: Mapping[str, Any],
    request_context: RequestContext,
) -> object:
    spec = get_tool_contract(entry.name)
    raw_arguments = dict(arguments)
    # Validate the caller's raw JSON. No Pydantic coercion or unknown-field
    # dropping is allowed before this exact Contract check.
    validate_tool_input(spec, raw_arguments)

    service_arguments = dict(raw_arguments)
    approval = service_arguments.get("approval")
    if isinstance(approval, dict):
        service_arguments["approval"] = ApprovalAssertion(**approval)

    service = SiteHitsService(
        request_context,
        integration_status_provider=integration_status,
    )
    operation = getattr(service, entry.name)
    result = operation(**service_arguments)
    # The service also validates output; this adapter-level check prevents a
    # replaced or incorrectly wired service provider from escaping the Contract.
    validate_tool_output(spec, result)
    return result


class SiteHitsMCPServer(MCPServer[None]):
    """SiteHits MCP v2 server with explicit Contract registry and dispatch."""

    def __init__(
        self,
        *,
        token_verifier: TokenVerifier,
        registry: tuple[RegistryEntry, ...] | None = None,
    ):
        self.registry = registry or TOOL_REGISTRY
        self._registry_by_name = {entry.name: entry for entry in self.registry}
        if len(self._registry_by_name) != len(self.registry):
            raise RuntimeError("The SiteHits MCP registry contains duplicate tool names.")

        bootstrap_name = AGENT_CONTRACT["bootstrap"]["tool"]
        bootstrap_scopes = list(self._registry_by_name[bootstrap_name].required_scopes)
        tracking_resource = FunctionResource(
            uri=TRACKING_SETUP_RESOURCE_URI,
            name="SiteHits tracking setup",
            description="Optional inline presentation for SiteHits tracking setup data.",
            mime_type="text/html;profile=mcp-app",
            fn=tracking_setup_widget,
            meta=_tracking_resource_meta(),
        )
        super().__init__(
            "sitehits",
            title="SiteHits analytics MCP",
            description=AGENT_CONTRACT["server_instructions"]["summary"],
            instructions=CONTRACT_SERVER_INSTRUCTIONS,
            website_url=settings.SITEHITS_BASE_URL,
            version=SERVER_VERSION,
            token_verifier=token_verifier,
            auth=build_mcp_auth_settings(
                issuer_url=settings.SITEHITS_MCP_ISSUER_URL,
                resource_server_url=settings.SITEHITS_MCP_RESOURCE_URL,
                service_documentation_url=settings.SITEHITS_MCP_DOCUMENTATION_URL,
                required_scopes=bootstrap_scopes,
            ),
            resources=[tracking_resource],
        )

    async def list_tools(self) -> list[Tool]:
        return [
            entry.to_mcp_tool()
            for entry in self.registry
            if entry.exposure == "public"
        ]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Context[None, Any] | None = None,
    ) -> CallToolResult:
        try:
            entry = self._registry_by_name[name]
        except KeyError as exc:
            raise MCPError(INVALID_PARAMS, "Unknown MCP tool.") from exc
        if entry.exposure != "public":
            raise MCPError(INVALID_PARAMS, "Unknown MCP tool.")

        access_token = get_access_token()
        if access_token is None or not access_token.subject or not access_token.client_id:
            # Streamable HTTP auth rejects this before dispatch. Preserve protocol
            # ownership if call_tool is invoked without the middleware invariant.
            raise MCPError(INVALID_PARAMS, "Authenticated MCP context is required.")

        granted_scopes = frozenset(access_token.scopes)
        if not granted_scopes.issubset(set(AGENT_CONTRACT["scopes"])):
            raise MCPError(INVALID_PARAMS, "The bearer token contains unsupported scopes.")
        public_request_id = _request_id(context)
        request_context = RequestContext(
            authenticated_actor_id=str(access_token.subject),
            authenticated_client_id=str(access_token.client_id),
            granted_scopes=granted_scopes,
            request_id=public_request_id,
        )

        if not set(entry.required_scopes).issubset(granted_scopes):
            await sync_to_async(_run_sync_db, thread_sensitive=True)(
                _record_adapter_audit,
                request_context=request_context,
                entry=entry,
                arguments=arguments,
                outcome_code="insufficient_scope",
            )
            return _scope_error_result(entry.required_scopes)

        try:
            result = await sync_to_async(_run_sync_db, thread_sensitive=True)(
                _dispatch_service,
                entry,
                arguments,
                request_context,
            )
        except ApplicationError as error:
            await sync_to_async(_run_sync_db, thread_sensitive=True)(
                _record_adapter_audit,
                request_context=request_context,
                entry=entry,
                arguments=arguments,
                outcome_code=error.code,
            )
            return _application_error_result(error, public_request_id)
        except Exception:  # noqa: BLE001 - adapter boundary must sanitize implementation faults.
            error = ApplicationError(
                code="internal_error",
                message="The operation could not be completed.",
            )
            await sync_to_async(_run_sync_db, thread_sensitive=True)(
                _record_adapter_audit,
                request_context=request_context,
                entry=entry,
                arguments=arguments,
                outcome_code=error.code,
            )
            return _application_error_result(error, public_request_id)
        return _success_result(result)


mcp = SiteHitsMCPServer(token_verifier=token_verifier)
