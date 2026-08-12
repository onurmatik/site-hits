"""Agent Contract vectors executed through authenticated Streamable HTTP MCP."""

from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connections
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker
from mcp.types import LATEST_PROTOCOL_VERSION
from starlette.testclient import TestClient

import mcp_gateway.server as server_module
from agent_runtime import SiteHitsService
from agent_runtime.capabilities import BASE_CAPABILITIES, CapabilityEvaluation
from agent_runtime.contract import idempotency_retention_days
from agent_runtime.hashing import canonical_json, private_digest
from agent_runtime.limits import SiteLimit
from agent_runtime.revisions import revision_for
from analytics.models import (
    ActivationDefinition,
    AgentAuditEvent,
    AgentIdempotencyRecord,
    ProductEventDefinition,
)
from mcp_gateway.auth import token_verifier
from mcp_gateway.http import build_mcp_application
from mcp_gateway.mcp_asgi import transport_security_settings
from mcp_gateway.oauth import credential_digest
from mcp_gateway.server import SiteHitsMCPServer
from mcp_oauth.models import OAuthAccessToken, OAuthApplication, OAuthConsent
from websites.models import TrackedSite

pytestmark = pytest.mark.django_db(transaction=True)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "agent" / "contract.yaml"
MANIFEST_DIR = ROOT / "agent" / "conformance" / "1.0.0"
MANIFEST_PATH = MANIFEST_DIR / "manifest.json"
RESOURCE = settings.SITEHITS_MCP_RESOURCE_URL
BASE_URL = settings.SITEHITS_BASE_URL
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}

# The vector bundle describes independent scenarios, so mutations need a stable
# dependency order when the complete bundle is exercised against one live account.
# Arguments still come exclusively from the pinned vector payloads.
SUCCESS_EXECUTION_ORDER = (
    "get_account_capabilities",
    "create_site",
    "list_sites",
    "get_site",
    "update_site",
    "get_analytics_overview",
    "get_sites_overview",
    "get_analytics_timeseries",
    "get_analytics_breakdown",
    "get_bot_analytics",
    "get_product_metrics",
    "get_measurement_config",
    "create_measurement_event",
    "update_measurement_event",
    "change_measurement_event_contract",
    "delete_measurement_event",
    "set_activation",
    "clear_activation",
    "get_tracking_setup",
    "render_tracking_setup",
    "get_integration_status",
    "delete_site",
)
SYMBOLIC_REVISION = re.compile(r"^rev-(site|event|activation)-[0-9]+$")
TRANSPORT_VECTOR_CATEGORIES = (
    "success",
    "invalid_input",
    "missing_capability",
    "ownership_isolation",
    "capability_plan_matrix",
    "quota_concurrency",
    "idempotent_replay",
    "idempotency_conflict",
    "approval",
    "uncertain_external_effect",
    "redaction",
    "async_status",
    "internal_error",
)
EXPECTED_APPLICABLE_COUNTS = {
    "approval": 4,
    "async_status": 0,
    "capability_plan_matrix": 20,
    "idempotency_conflict": 2,
    "idempotent_replay": 2,
    "internal_error": 22,
    "invalid_input": 21,
    "missing_capability": 20,
    "ownership_isolation": 19,
    "quota_concurrency": 1,
    "redaction": 22,
    "success": 22,
    "uncertain_external_effect": 0,
}


@dataclass(frozen=True, slots=True)
class VectorBundle:
    contract: dict[str, object]
    manifest: dict[str, object]
    vectors: dict[str, dict[str, object]]


@dataclass(frozen=True, slots=True)
class OAuthBearer:
    raw: str
    client_id: str


@dataclass(slots=True)
class ScenarioWorld:
    user: object
    bearer: OAuthBearer
    site: TrackedSite | None = None
    event: ProductEventDefinition | None = None
    activation: ActivationDefinition | None = None
    included_site_slugs: set[str] = field(default_factory=set)
    excluded_site_slugs: set[str] = field(default_factory=set)

    def resolve(self, value):
        if isinstance(value, dict):
            return {key: self.resolve(child) for key, child in value.items()}
        if isinstance(value, list):
            return [self.resolve(child) for child in value]
        if not isinstance(value, str):
            return value

        if value == "example-site":
            assert self.site is not None, "site symbol resolved without a scenario fixture"
            return self.site.slug
        if value.startswith("example-site/"):
            assert self.site is not None, "resource symbol resolved without a scenario fixture"
            return f"{self.site.slug}/{value.split('/', 1)[1]}"
        revision = SYMBOLIC_REVISION.fullmatch(value)
        if revision:
            kind = revision.group(1)
            resource = {
                "site": self.site,
                "event": self.event,
                "activation": self.activation,
            }[kind]
            assert resource is not None, f"{kind} revision resolved without a scenario fixture"
            return revision_for(resource)
        return value


class _StaticCapabilityEvaluator:
    def __init__(self, available: frozenset[str]):
        self.available = available

    def evaluate(self, _user):
        return CapabilityEvaluation(self.available)


class _StaticLimitEvaluator:
    def __init__(self, limit: int | None):
        self.limit = limit

    def site_limit(self, _user):
        return SiteLimit(limit=self.limit)


class _UnexpectedFailureService(SiteHitsService):
    """Inject the vector's failure inside the canonical service error boundary."""

    def _run(self, **kwargs):
        def unexpected_failure():
            raise RuntimeError("injected-secret stack trace database-id=4242")

        return super()._run(**{**kwargs, "operation": unexpected_failure})


@dataclass(slots=True)
class SuccessState:
    site_slug: str | None = None
    revisions: dict[str, str] = field(default_factory=dict)

    def resolve(self, value):
        if isinstance(value, dict):
            return {key: self.resolve(child) for key, child in value.items()}
        if isinstance(value, list):
            return [self.resolve(child) for child in value]
        if not isinstance(value, str):
            return value

        if value == "example-site":
            assert self.site_slug is not None, "site symbol resolved before create_site"
            return self.site_slug
        if value.startswith("example-site/"):
            assert self.site_slug is not None, "resource symbol resolved before create_site"
            return f"{self.site_slug}/{value.split('/', 1)[1]}"
        revision = SYMBOLIC_REVISION.fullmatch(value)
        if revision:
            kind = revision.group(1)
            assert kind in self.revisions, f"{kind} revision resolved before its mutation"
            return self.revisions[kind]
        return value

    def observe(self, tool_name: str, result: dict[str, object]) -> None:
        if tool_name == "create_site":
            self.site_slug = str(result["slug"])
            self.revisions["site"] = str(result["revision"])
        elif tool_name == "update_site":
            self.revisions["site"] = str(result["revision"])
        elif tool_name == "create_measurement_event":
            event = result["event"]
            assert isinstance(event, dict)
            self.revisions["event"] = str(event["revision"])
        elif tool_name in {
            "update_measurement_event",
            "change_measurement_event_contract",
        }:
            self.revisions["event"] = str(result["revision"])
        elif tool_name == "set_activation":
            self.revisions["activation"] = str(result["revision"])


def _load_bundle() -> VectorBundle:
    contract = json.loads(CONTRACT_PATH.read_text())
    manifest = json.loads(MANIFEST_PATH.read_text())
    vectors = {}
    for entry in manifest["vectors"]:
        vector_path = (MANIFEST_DIR / entry["path"]).resolve()
        assert vector_path.is_relative_to(MANIFEST_DIR.resolve())
        vector_bytes = vector_path.read_bytes()
        assert hashlib.sha256(vector_bytes).hexdigest() == entry["sha256"]
        vector = json.loads(vector_bytes)
        assert vector["id"] == entry["id"]
        vectors[vector["tool"]] = vector
    assert manifest["agent_contract_version"] == contract["agent_contract_version"]
    assert len(vectors) == len(manifest["vectors"])
    return VectorBundle(contract=contract, manifest=manifest, vectors=vectors)


@pytest.fixture(scope="module")
def vector_bundle() -> VectorBundle:
    return _load_bundle()


@pytest.fixture
def mcp_client(settings):
    settings.DEBUG = True
    settings.SITEHITS_TRUST_PROXY_HEADERS = True
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


@pytest.fixture
def contract_user():
    suffix = uuid4().hex
    return get_user_model().objects.create_user(
        username=f"contract-transport-{suffix}",
        email=f"contract-transport-{suffix}@example.com",
    )


def _issue_bearer(user, *, label: str) -> OAuthBearer:
    safe_label = re.sub(r"[^a-z0-9-]", "-", label.lower())[:24]
    client_id = f"contract-vector-{safe_label}-{uuid4().hex}"
    application = OAuthApplication.objects.create(
        client_id=client_id,
        redirect_uris="http://127.0.0.1:43127/callback",
        client_type=OAuthApplication.CLIENT_PUBLIC,
        authorization_grant_type=OAuthApplication.GRANT_AUTHORIZATION_CODE,
        client_secret="",
        hash_client_secret=False,
        skip_authorization=False,
        registration_source=OAuthApplication.RegistrationSource.DCR,
        allowed_scopes=["read", "write"],
    )
    OAuthConsent.objects.create(
        user=user,
        application=application,
        resource=RESOURCE,
        scopes=["read", "write"],
        redirect_uri_digest="c" * 64,
        decision=OAuthConsent.Decision.APPROVED,
    )
    raw = f"contract-vector-access-{uuid4().hex}{uuid4().hex}"
    OAuthAccessToken.objects.create(
        user=user,
        application=application,
        token="",
        token_checksum=credential_digest(raw),
        expires=timezone.now() + timedelta(minutes=15),
        scope="read write",
        resource=[RESOURCE],
    )
    return OAuthBearer(raw=raw, client_id=client_id)


@pytest.fixture
def oauth_bearer(contract_user) -> OAuthBearer:
    return _issue_bearer(contract_user, label="default")


@pytest.fixture
def actor_bearer_factory():
    def create(label: str, *, user=None):
        suffix = uuid4().hex
        actor = user or get_user_model().objects.create_user(
            username=f"transport-{label[:24]}-{suffix}",
            email=f"transport-{label[:24]}-{suffix}@example.com",
        )
        return actor, _issue_bearer(actor, label=label)

    return create


def _service_factory(
    *,
    available_capabilities: frozenset[str] = BASE_CAPABILITIES,
    site_limit: int | None = None,
    inject_unexpected_failure: bool = False,
):
    service_class = _UnexpectedFailureService if inject_unexpected_failure else SiteHitsService

    def create(context, *, integration_status_provider=None):
        return service_class(
            context,
            capability_evaluator=_StaticCapabilityEvaluator(available_capabilities),
            limit_evaluator=_StaticLimitEvaluator(site_limit),
            integration_status_provider=integration_status_provider,
        )

    return create


def _seed_scenario_world(
    actor_bearer_factory,
    *,
    tool_name: str,
    scenario: dict[str, object],
    label: str,
    user=None,
) -> ScenarioWorld:
    actor, bearer = actor_bearer_factory(label, user=user)
    world = ScenarioWorld(user=actor, bearer=bearer)
    ownership = scenario.get("context_override", {}).get("ownership", {})
    suffix = uuid4().hex[:16]

    if ownership.get("include_foreign_resource_fixture") is True:
        owned = TrackedSite.objects.create(
            owner=actor,
            name="Owned vector site",
            slug=f"owned-{suffix}",
            allowed_domains=[f"owned-{suffix}.example"],
        )
        foreign_actor = get_user_model().objects.create_user(
            username=f"foreign-{suffix}",
        )
        foreign = TrackedSite.objects.create(
            owner=foreign_actor,
            name="Foreign vector site",
            slug=f"foreign-{suffix}",
            allowed_domains=[f"foreign-{suffix}.example"],
        )
        system = TrackedSite.objects.create(
            owner=None,
            name="System vector site",
            slug=f"system-{suffix}",
            allowed_domains=[f"system-{suffix}.example"],
        )
        world.included_site_slugs.add(owned.slug)
        world.excluded_site_slugs.update({foreign.slug, system.slug})
        return world

    if tool_name in {"get_account_capabilities", "get_integration_status", "create_site"}:
        return world

    if tool_name in {"list_sites", "get_sites_overview"}:
        owned = TrackedSite.objects.create(
            owner=actor,
            name="Owned vector site",
            slug=f"owned-{suffix}",
            allowed_domains=[f"owned-{suffix}.example"],
        )
        world.included_site_slugs.add(owned.slug)
        return world

    site_owner = actor
    if ownership.get("target_owned_by_authenticated_actor") is False:
        site_owner = get_user_model().objects.create_user(username=f"foreign-{suffix}")
    site = TrackedSite.objects.create(
        owner=site_owner,
        name="Example vector site",
        slug=f"example-site-{suffix}",
        allowed_domains=[f"example-site-{suffix}.example"],
    )
    world.site = site

    signup = ProductEventDefinition.objects.create(
        site=site,
        event_name="signup",
        display_name="Signup",
        description="Signup event.",
    )
    activated = ProductEventDefinition.objects.create(
        site=site,
        event_name="activated",
        display_name="Activated",
        description="Activated event.",
    )
    if tool_name != "create_measurement_event":
        world.event = ProductEventDefinition.objects.create(
            site=site,
            event_name="purchase:completed",
            display_name="Purchases",
            description="Completed purchases.",
        )
    if tool_name == "clear_activation":
        world.activation = ActivationDefinition.objects.create(
            site=site,
            start_event=signup,
            goal_event=activated,
        )
    return world


def _post(client, bearer: OAuthBearer, payload, *, request_id: str):
    response = client.post(
        "/mcp",
        json=payload,
        headers={
            **MCP_HEADERS,
            "Authorization": f"Bearer {bearer.raw}",
            "X-Request-ID": request_id,
            "X-SiteHits-Trusted-Proxy": "1",
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers["x-request-id"] == request_id
    return response


def _initialize(client, bearer: OAuthBearer) -> None:
    request_id = uuid4().hex
    response = _post(
        client,
        bearer,
        {
            "jsonrpc": "2.0",
            "id": "contract-transport-initialize",
            "method": "initialize",
            "params": {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "contract-transport-tests",
                    "version": "1.0.0",
                },
            },
        },
        request_id=request_id,
    )
    payload = response.json()
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == "contract-transport-initialize"
    assert "error" not in payload


def _call_tool(
    client,
    bearer: OAuthBearer,
    *,
    tool_name: str,
    arguments: dict[str, object],
    request_id: str,
) -> dict[str, object]:
    response = _post(
        client,
        bearer,
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
        request_id=request_id,
    )
    payload = response.json()
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == request_id
    assert set(payload) == {"jsonrpc", "id", "result"}, payload
    return payload["result"]


def _schema(contract, reference: str) -> dict[str, object]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": reference,
        "$defs": contract["$defs"],
    }


def _assert_success_result(
    *,
    bundle: VectorBundle,
    tool_name: str,
    scenario: dict[str, object],
    result: dict[str, object],
) -> dict[str, object]:
    assert result["isError"] is False
    assert set(result) == {"content", "structuredContent", "isError"}
    structured = result["structuredContent"]
    assert isinstance(structured, dict)

    expected_ref = scenario["expected"]["result_schema_ref"]
    assert expected_ref == bundle.contract["tools"][tool_name]["output_schema"]["$ref"]
    validator = Draft202012Validator(
        _schema(bundle.contract, expected_ref),
        format_checker=FormatChecker(),
    )
    validator.validate(structured)
    assert result["content"] == [
        {
            "type": "text",
            "text": json.dumps(
                structured,
                sort_keys=True,
                separators=(",", ":"),
            ),
        }
    ]
    return structured


def _assert_contract_error(
    *,
    contract: dict[str, object],
    result: dict[str, object],
    expected_code: str,
    request_id: str,
) -> None:
    assert result["isError"] is True
    assert set(result) == {"content", "structuredContent", "isError"}
    assert set(result["structuredContent"]) == {"error"}
    envelope = result["structuredContent"]["error"]
    Draft202012Validator(
        _schema(contract, contract["error_envelope"]["$ref"]),
        format_checker=FormatChecker(),
    ).validate(envelope)
    assert envelope["code"] == expected_code
    assert envelope["retryable"] is contract["error_codes"][expected_code]["retryable"]
    assert envelope["request_id"] == request_id
    assert result["content"] == [
        {
            "type": "text",
            "text": json.dumps(envelope, sort_keys=True, separators=(",", ":")),
        }
    ]
    return envelope


def _expected_target_resource_id(
    *,
    tool_name: str,
    arguments: dict[str, object],
    user,
    bearer: OAuthBearer,
    structured: dict[str, object] | None = None,
    adapter_stopped: bool = False,
) -> str:
    site_slug = arguments.get("site_slug")
    event_name = arguments.get("event_name")
    if adapter_stopped:
        if isinstance(site_slug, str) and isinstance(event_name, str):
            return f"{site_slug}/{event_name}"
        if isinstance(site_slug, str):
            return site_slug
        if tool_name == "get_account_capabilities":
            return str(user.pk)
        if tool_name == "get_integration_status":
            return "sitehits"
        return ""
    if tool_name == "get_account_capabilities":
        return str(user.pk)
    if tool_name == "get_integration_status":
        return bearer.client_id
    if tool_name == "create_site":
        if structured is not None:
            return str(structured["slug"])
        return ""
    if tool_name == "get_sites_overview":
        return "all"
    if (
        tool_name
        in {
            "get_analytics_overview",
            "get_analytics_timeseries",
            "get_bot_analytics",
        }
        and site_slug is None
    ):
        return "all"
    if isinstance(site_slug, str) and isinstance(event_name, str):
        return f"{site_slug}/{event_name}"
    if isinstance(site_slug, str):
        return site_slug
    return ""


def _assert_audit(
    *,
    bundle: VectorBundle,
    user,
    bearer: OAuthBearer,
    tool_name: str,
    arguments: dict[str, object],
    request_id: str,
    outcome_code: str,
    structured: dict[str, object] | None = None,
):
    audit = AgentAuditEvent.objects.get(request_id=request_id, tool_name=tool_name)
    tool = bundle.contract["tools"][tool_name]
    vector = bundle.vectors[tool_name]
    assert audit.authenticated_actor_id == str(user.pk)
    assert audit.authenticated_client_id == bearer.client_id
    assert audit.tenant_id == ""
    assert audit.target_resource_type == tool["resource_type"]
    assert audit.target_resource_id == _expected_target_resource_id(
        tool_name=tool_name,
        arguments=arguments,
        user=user,
        bearer=bearer,
        structured=structured,
        adapter_stopped=audit.authorization["capability_allowed"] == "not_evaluated",
    )
    assert audit.authorization["authentication_required"] is True
    assert audit.authorization["authenticated"] is True
    assert audit.authorization["required_scopes"] == tool["required_scopes"]
    assert audit.authorization["granted_scopes"] == ["read", "write"]
    assert audit.authorization["scope_allowed"] is True
    assert audit.input_hash == private_digest(canonical_json(arguments))
    assert audit.outcome_code == outcome_code
    assert audit.operation_id == ""
    assert timezone.is_aware(audit.created_at)

    idempotency_key = arguments.get("idempotency_key")
    if isinstance(idempotency_key, str):
        assert audit.idempotency_id != idempotency_key

    audit_projection = {
        "authenticated_actor_id": audit.authenticated_actor_id,
        "authenticated_client_id": audit.authenticated_client_id,
        "tenant_id": audit.tenant_id,
        "tool_name": audit.tool_name,
        "target_resource": {
            "type": audit.target_resource_type,
            "id": audit.target_resource_id,
        },
        "authorization_decisions": audit.authorization,
        "redacted_input_summary_or_hash": audit.input_hash,
        "outcome_code": audit.outcome_code,
        "request_id": audit.request_id,
        "idempotency_id": audit.idempotency_id,
        "operation_id": audit.operation_id,
        "timestamp_utc": audit.created_at.isoformat(),
    }
    expected_audit = vector["defaults"]["audit"]
    assert set(expected_audit["required_fields"]).issubset(audit_projection)
    serialized_audit = json.dumps(audit_projection, sort_keys=True)
    assert bearer.raw not in serialized_audit
    for forbidden in expected_audit["forbidden_fields"]:
        assert forbidden not in serialized_audit
    if isinstance(idempotency_key, str):
        assert idempotency_key not in serialized_audit
    return audit


def _seed_activation_events(user, site_slug: str) -> None:
    site = user.tracked_sites.get(slug=site_slug)
    for event_name in ("signup", "activated"):
        ProductEventDefinition.objects.create(
            site=site,
            event_name=event_name,
            display_name=event_name.title(),
            description=f"{event_name.title()} event.",
        )


def _applicable_tools(bundle: VectorBundle, category: str) -> set[str]:
    return {
        tool_name
        for tool_name, vector in bundle.vectors.items()
        if vector["scenarios"][category]["applicable"] is True
    }


def _invoke_scenario(
    *,
    bundle: VectorBundle,
    client,
    world: ScenarioWorld,
    tool_name: str,
    scenario: dict[str, object],
):
    arguments = world.resolve(deepcopy(scenario["input"]))
    request_id = uuid4().hex
    result = _call_tool(
        client,
        world.bearer,
        tool_name=tool_name,
        arguments=arguments,
        request_id=request_id,
    )
    expected = scenario["expected"]
    structured = None
    envelope = None
    if expected["outcome"] == "success":
        structured = _assert_success_result(
            bundle=bundle,
            tool_name=tool_name,
            scenario=scenario,
            result=result,
        )
    elif expected["outcome"] == "error":
        envelope = _assert_contract_error(
            contract=bundle.contract,
            result=result,
            expected_code=expected["error_code"],
            request_id=request_id,
        )
    else:
        raise AssertionError(f"Scenario {tool_name} requires a specialized executor.")
    audit = _assert_audit(
        bundle=bundle,
        user=world.user,
        bearer=world.bearer,
        tool_name=tool_name,
        arguments=arguments,
        request_id=request_id,
        outcome_code=expected["audit_outcome_code"],
        structured=structured,
    )
    return arguments, result, structured, envelope, audit


def _result_with_contract_output_schema(
    *,
    bundle: VectorBundle,
    tool_name: str,
    result: dict[str, object],
) -> dict[str, object]:
    scenario = {
        "expected": {
            "result_schema_ref": bundle.contract["tools"][tool_name]["output_schema"]["$ref"]
        }
    }
    return _assert_success_result(
        bundle=bundle,
        tool_name=tool_name,
        scenario=scenario,
        result=result,
    )


def _nested_keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(_nested_keys(child) for child in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_nested_keys(child) for child in value), set())
    return set()


def test_transport_vector_manifest_has_155_applicable_scenarios(vector_bundle):
    assert set(TRANSPORT_VECTOR_CATEGORIES) == {
        category for vector in vector_bundle.vectors.values() for category in vector["scenarios"]
    }
    assert all(
        set(vector["scenarios"]) == set(TRANSPORT_VECTOR_CATEGORIES)
        for vector in vector_bundle.vectors.values()
    )
    counts = {
        category: len(_applicable_tools(vector_bundle, category))
        for category in TRANSPORT_VECTOR_CATEGORIES
    }
    assert counts == EXPECTED_APPLICABLE_COUNTS
    assert sum(counts.values()) == 155


def test_all_missing_capability_vectors_cross_authenticated_transport(
    vector_bundle,
    mcp_client,
    actor_bearer_factory,
    monkeypatch,
):
    category = "missing_capability"
    applicable = _applicable_tools(vector_bundle, category)
    exercised = set()
    for tool_name in sorted(applicable):
        scenario = vector_bundle.vectors[tool_name]["scenarios"][category]
        assert scenario["authorization_override"]["capabilities"] == []
        world = _seed_scenario_world(
            actor_bearer_factory,
            tool_name=tool_name,
            scenario=scenario,
            label=f"missing-{tool_name}",
        )
        _initialize(mcp_client, world.bearer)
        with monkeypatch.context() as patcher:
            patcher.setattr(
                server_module,
                "SiteHitsService",
                _service_factory(available_capabilities=frozenset()),
            )
            _arguments, _result, _structured, envelope, audit = _invoke_scenario(
                bundle=vector_bundle,
                client=mcp_client,
                world=world,
                tool_name=tool_name,
                scenario=scenario,
            )
        required_capability = vector_bundle.contract["tools"][tool_name]["required_capabilities"][0]
        assert envelope["details"] == {"capability": required_capability}
        assert scenario["expected"]["assertions"] == [f"missing_{required_capability}"]
        assert audit.authorization["capability_allowed"] is False
        assert audit.authorization["ownership_allowed"] == "not_evaluated"
        exercised.add(tool_name)

    assert exercised == applicable
    assert len(exercised) == EXPECTED_APPLICABLE_COUNTS[category]


def test_all_ownership_isolation_vectors_cross_authenticated_transport(
    vector_bundle,
    mcp_client,
    actor_bearer_factory,
):
    category = "ownership_isolation"
    applicable = _applicable_tools(vector_bundle, category)
    exercised = set()
    for tool_name in sorted(applicable):
        scenario = vector_bundle.vectors[tool_name]["scenarios"][category]
        world = _seed_scenario_world(
            actor_bearer_factory,
            tool_name=tool_name,
            scenario=scenario,
            label=f"ownership-{tool_name}",
        )
        _initialize(mcp_client, world.bearer)
        _arguments, _result, structured, envelope, audit = _invoke_scenario(
            bundle=vector_bundle,
            client=mcp_client,
            world=world,
            tool_name=tool_name,
            scenario=scenario,
        )

        assertions = set(scenario["expected"]["assertions"])
        if "does_not_reveal_resource_existence" in assertions:
            assert structured is None
            assert envelope["code"] == "resource_not_found"
            assert set(envelope["details"]) == {"resource_type"}
            assert world.site.slug not in json.dumps(envelope, sort_keys=True)
            assert audit.authorization["ownership_allowed"] is False
        else:
            assert assertions == {
                "foreign_resources_are_omitted",
                "system_owned_resources_require_global_resource_access",
            }
            serialized = json.dumps(structured, sort_keys=True)
            assert all(slug in serialized for slug in world.included_site_slugs)
            assert all(slug not in serialized for slug in world.excluded_site_slugs)
            assert audit.authorization["ownership_allowed"] is True
        exercised.add(tool_name)

    assert exercised == applicable
    assert len(exercised) == EXPECTED_APPLICABLE_COUNTS[category]


def test_all_capability_plan_matrix_vectors_cross_authenticated_transport(
    vector_bundle,
    mcp_client,
    actor_bearer_factory,
    monkeypatch,
):
    category = "capability_plan_matrix"
    applicable = _applicable_tools(vector_bundle, category)
    exercised = set()
    call_count = 0
    for tool_name in sorted(applicable):
        scenario = vector_bundle.vectors[tool_name]["scenarios"][category]
        assert scenario["expected"]["assertions"] == [
            "plan_label_is_not_an_authorization_dimension"
        ]
        cases = scenario["expected"]["cases"]
        assert cases == [
            {
                "capability_available": True,
                "outcome": "success",
                "plan_label": "free",
            },
            {
                "capability_available": True,
                "outcome": "success",
                "plan_label": "pro",
            },
            {
                "capability_available": False,
                "outcome": "feature_unavailable",
                "plan_label": "any",
            },
        ]
        for case in cases:
            plan_label = case["plan_label"]
            world = _seed_scenario_world(
                actor_bearer_factory,
                tool_name=tool_name,
                scenario=scenario,
                label=f"matrix-{tool_name}-{plan_label}",
            )
            arguments = world.resolve(deepcopy(scenario["input"]))
            _initialize(mcp_client, world.bearer)
            available = BASE_CAPABILITIES if case["capability_available"] else frozenset()
            request_id = uuid4().hex
            with monkeypatch.context() as patcher:
                patcher.setattr(
                    server_module,
                    "SiteHitsService",
                    _service_factory(available_capabilities=available),
                )
                result = _call_tool(
                    mcp_client,
                    world.bearer,
                    tool_name=tool_name,
                    arguments=arguments,
                    request_id=request_id,
                )

            if case["outcome"] == "success":
                structured = _result_with_contract_output_schema(
                    bundle=vector_bundle,
                    tool_name=tool_name,
                    result=result,
                )
                outcome_code = "success"
            else:
                structured = None
                envelope = _assert_contract_error(
                    contract=vector_bundle.contract,
                    result=result,
                    expected_code=case["outcome"],
                    request_id=request_id,
                )
                required_capability = vector_bundle.contract["tools"][tool_name][
                    "required_capabilities"
                ][0]
                assert envelope["details"] == {"capability": required_capability}
                outcome_code = case["outcome"]
            audit = _assert_audit(
                bundle=vector_bundle,
                user=world.user,
                bearer=world.bearer,
                tool_name=tool_name,
                arguments=arguments,
                request_id=request_id,
                outcome_code=outcome_code,
                structured=structured,
            )
            assert "plan_label" not in audit.authorization
            assert audit.authorization["capability_allowed"] is case["capability_available"]
            call_count += 1
        exercised.add(tool_name)

    assert exercised == applicable
    assert len(exercised) == EXPECTED_APPLICABLE_COUNTS[category]
    assert call_count == 60


def test_all_approval_vectors_cross_authenticated_transport(
    vector_bundle,
    mcp_client,
    actor_bearer_factory,
):
    category = "approval"
    applicable = _applicable_tools(vector_bundle, category)
    exercised = set()
    for tool_name in sorted(applicable):
        scenario = vector_bundle.vectors[tool_name]["scenarios"][category]
        assert "approval" not in scenario["input"]
        world = _seed_scenario_world(
            actor_bearer_factory,
            tool_name=tool_name,
            scenario=scenario,
            label=f"approval-{tool_name}",
        )
        _initialize(mcp_client, world.bearer)
        _arguments, _result, _structured, envelope, audit = _invoke_scenario(
            bundle=vector_bundle,
            client=mcp_client,
            world=world,
            tool_name=tool_name,
            scenario=scenario,
        )
        expected_resource = (
            f"{world.site.slug}/purchase:completed"
            if tool_name
            in {
                "change_measurement_event_contract",
                "delete_measurement_event",
            }
            else world.site.slug
        )
        assert envelope["details"] == {
            "owner": "agent",
            "action": tool_name,
            "resource_id": expected_resource,
        }
        assert set(scenario["expected"]["assertions"]) == {
            "confirmation_owner_is_agent",
            "approval_must_match_action_and_resource_id",
            "no_second_confirmation_field",
        }
        assert "confirmed" not in envelope["details"]
        assert audit.authorization["approval_required"] is True
        assert audit.authorization["approval_confirmed"] is False
        assert audit.authorization["approval_allowed"] is False
        exercised.add(tool_name)

    assert exercised == applicable
    assert len(exercised) == EXPECTED_APPLICABLE_COUNTS[category]


def test_all_idempotent_replay_vectors_cross_authenticated_transport(
    vector_bundle,
    mcp_client,
    actor_bearer_factory,
):
    category = "idempotent_replay"
    applicable = _applicable_tools(vector_bundle, category)
    exercised = set()
    for tool_name in sorted(applicable):
        scenario = vector_bundle.vectors[tool_name]["scenarios"][category]
        world = _seed_scenario_world(
            actor_bearer_factory,
            tool_name=tool_name,
            scenario=scenario,
            label=f"replay-{tool_name}",
        )
        arguments = world.resolve(deepcopy(scenario["input"]))
        _initialize(mcp_client, world.bearer)

        first_request_id = uuid4().hex
        first_result = _call_tool(
            mcp_client,
            world.bearer,
            tool_name=tool_name,
            arguments=arguments,
            request_id=first_request_id,
        )
        first = _assert_success_result(
            bundle=vector_bundle,
            tool_name=tool_name,
            scenario=scenario,
            result=first_result,
        )
        _assert_audit(
            bundle=vector_bundle,
            user=world.user,
            bearer=world.bearer,
            tool_name=tool_name,
            arguments=arguments,
            request_id=first_request_id,
            outcome_code="success",
            structured=first,
        )

        _arguments, _result, replay, _envelope, _audit = _invoke_scenario(
            bundle=vector_bundle,
            client=mcp_client,
            world=world,
            tool_name=tool_name,
            scenario=scenario,
        )
        assertions = set(scenario["expected"]["assertions"])
        assert {
            "same_canonical_input_returns_original_logical_result",
            "no_duplicate_resource_created",
        }.issubset(assertions)
        if tool_name == "create_site":
            assert replay == first
            assert TrackedSite.objects.filter(owner=world.user).count() == 1
            record = AgentIdempotencyRecord.objects.get(
                authenticated_actor_id=str(world.user.pk),
                authenticated_client_id=world.bearer.client_id,
                tool_name=tool_name,
            )
            remaining = record.expires_at - record.created_at
            assert remaining >= timedelta(days=idempotency_retention_days()) - timedelta(seconds=1)
            assert remaining <= timedelta(days=idempotency_retention_days()) + timedelta(seconds=1)
            assert "guarantee_window_is_90_days" in assertions
            assert idempotency_retention_days() == 90

            record.expires_at = timezone.now() - timedelta(seconds=1)
            record.save(update_fields=["expires_at"])
            fresh_request_id = uuid4().hex
            fresh_result = _call_tool(
                mcp_client,
                world.bearer,
                tool_name=tool_name,
                arguments=arguments,
                request_id=fresh_request_id,
            )
            fresh = _assert_success_result(
                bundle=vector_bundle,
                tool_name=tool_name,
                scenario=scenario,
                result=fresh_result,
            )
            _assert_audit(
                bundle=vector_bundle,
                user=world.user,
                bearer=world.bearer,
                tool_name=tool_name,
                arguments=arguments,
                request_id=fresh_request_id,
                outcome_code="success",
                structured=fresh,
            )
            assert fresh["slug"] != first["slug"]
            assert TrackedSite.objects.filter(owner=world.user).count() == 2
            assert "same_key_is_fresh_after_expiry" in assertions
        else:
            assert first["created"] is True
            assert replay == {"created": False, "event": first["event"]}
            assert (
                ProductEventDefinition.objects.filter(
                    site=world.site,
                    event_name=arguments["event_name"],
                ).count()
                == 1
            )
        exercised.add(tool_name)

    assert exercised == applicable
    assert len(exercised) == EXPECTED_APPLICABLE_COUNTS[category]


def test_all_idempotency_conflict_vectors_cross_authenticated_transport(
    vector_bundle,
    mcp_client,
    actor_bearer_factory,
):
    category = "idempotency_conflict"
    applicable = _applicable_tools(vector_bundle, category)
    exercised = set()
    for tool_name in sorted(applicable):
        scenario = vector_bundle.vectors[tool_name]["scenarios"][category]
        world = _seed_scenario_world(
            actor_bearer_factory,
            tool_name=tool_name,
            scenario=scenario,
            label=f"conflict-{tool_name}",
        )
        success_scenario = vector_bundle.vectors[tool_name]["scenarios"]["success"]
        original_arguments = world.resolve(deepcopy(success_scenario["input"]))
        _initialize(mcp_client, world.bearer)
        original_request_id = uuid4().hex
        original_result = _call_tool(
            mcp_client,
            world.bearer,
            tool_name=tool_name,
            arguments=original_arguments,
            request_id=original_request_id,
        )
        original = _assert_success_result(
            bundle=vector_bundle,
            tool_name=tool_name,
            scenario=success_scenario,
            result=original_result,
        )
        _assert_audit(
            bundle=vector_bundle,
            user=world.user,
            bearer=world.bearer,
            tool_name=tool_name,
            arguments=original_arguments,
            request_id=original_request_id,
            outcome_code="success",
            structured=original,
        )

        arguments, _result, _structured, envelope, audit = _invoke_scenario(
            bundle=vector_bundle,
            client=mcp_client,
            world=world,
            tool_name=tool_name,
            scenario=scenario,
        )
        serialized = json.dumps(envelope, sort_keys=True)
        assertions = set(scenario["expected"]["assertions"])
        assert "error_never_exposes_raw_idempotency_key" in assertions
        if "idempotency_key" in arguments:
            assert arguments["idempotency_key"] not in serialized
            assert set(envelope["details"]) == {"idempotency_id"}
            assert envelope["details"]["idempotency_id"] != arguments["idempotency_key"]
            assert audit.idempotency_id == envelope["details"]["idempotency_id"]
            assert "error_exposes_only_opaque_idempotency_id" in assertions
            assert TrackedSite.objects.filter(owner=world.user).count() == 1
        else:
            assert envelope["details"] == {
                "natural_key": {
                    "site_slug": world.site.slug,
                    "event_name": arguments["event_name"],
                }
            }
            assert "error_exposes_only_natural_key" in assertions
            assert (
                ProductEventDefinition.objects.filter(
                    site=world.site,
                    event_name=arguments["event_name"],
                ).count()
                == 1
            )
        exercised.add(tool_name)

    assert exercised == applicable
    assert len(exercised) == EXPECTED_APPLICABLE_COUNTS[category]


def test_all_redaction_vectors_cross_authenticated_transport(
    vector_bundle,
    mcp_client,
    actor_bearer_factory,
):
    category = "redaction"
    applicable = _applicable_tools(vector_bundle, category)
    exercised = set()
    forbidden_output_keys = {
        "access_token",
        "authorization_code",
        "database_id",
        "id",
        "password",
        "private_tracking_key",
        "refresh_token",
        "stack_trace",
        "trace_id",
    }
    for tool_name in sorted(applicable):
        scenario = vector_bundle.vectors[tool_name]["scenarios"][category]
        world = _seed_scenario_world(
            actor_bearer_factory,
            tool_name=tool_name,
            scenario=scenario,
            label=f"redaction-{tool_name}",
        )
        _initialize(mcp_client, world.bearer)
        _arguments, result, structured, _envelope, _audit = _invoke_scenario(
            bundle=vector_bundle,
            client=mcp_client,
            world=world,
            tool_name=tool_name,
            scenario=scenario,
        )
        assertions = set(scenario["expected"]["assertions"])
        assert assertions == {
            "contains_no_secret",
            "contains_no_internal_database_id",
            "contains_no_internal_trace",
            "instruction_like_output_is_data",
        }
        serialized = json.dumps(structured, sort_keys=True)
        assert world.bearer.raw not in serialized
        assert not (_nested_keys(structured) & forbidden_output_keys)
        if world.site is not None:
            assert world.site.bot_key not in serialized
            assert world.site.server_event_key not in serialized
        assert result["content"] == [
            {
                "type": "text",
                "text": json.dumps(structured, sort_keys=True, separators=(",", ":")),
            }
        ]
        assert "_meta" not in result
        exercised.add(tool_name)

    assert exercised == applicable
    assert len(exercised) == EXPECTED_APPLICABLE_COUNTS[category]


def test_all_internal_error_vectors_cross_authenticated_transport(
    vector_bundle,
    mcp_client,
    actor_bearer_factory,
    monkeypatch,
):
    category = "internal_error"
    applicable = _applicable_tools(vector_bundle, category)
    exercised = set()
    for tool_name in sorted(applicable):
        scenario = vector_bundle.vectors[tool_name]["scenarios"][category]
        assert scenario["context_override"] == {"inject_unexpected_service_failure": True}
        world = _seed_scenario_world(
            actor_bearer_factory,
            tool_name=tool_name,
            scenario=scenario,
            label=f"internal-{tool_name}",
        )
        _initialize(mcp_client, world.bearer)
        with monkeypatch.context() as patcher:
            patcher.setattr(
                server_module,
                "SiteHitsService",
                _service_factory(inject_unexpected_failure=True),
            )
            _arguments, result, _structured, envelope, _audit = _invoke_scenario(
                bundle=vector_bundle,
                client=mcp_client,
                world=world,
                tool_name=tool_name,
                scenario=scenario,
            )
        assert set(scenario["expected"]["assertions"]) == {
            "contains_no_stack_trace",
            "contains_no_internal_trace",
            "contains_no_database_identifier",
        }
        assert envelope == {
            "code": "internal_error",
            "message": "The operation could not be completed.",
            "retryable": False,
            "request_id": envelope["request_id"],
            "details": {},
        }
        serialized = json.dumps(result, sort_keys=True)
        assert "injected-secret" not in serialized
        assert "stack trace" not in serialized
        assert "database-id" not in serialized
        exercised.add(tool_name)

    assert exercised == applicable
    assert len(exercised) == EXPECTED_APPLICABLE_COUNTS[category]


def test_quota_concurrency_vector_crosses_authenticated_transport(
    vector_bundle,
    mcp_client,
    actor_bearer_factory,
    monkeypatch,
):
    category = "quota_concurrency"
    applicable = _applicable_tools(vector_bundle, category)
    assert applicable == {"create_site"}
    tool_name = "create_site"
    scenario = vector_bundle.vectors[tool_name]["scenarios"][category]
    assert scenario["authorization_override"]["limits"]["sites"] == {
        "used": 0,
        "limit": 1,
        "period": "permanent",
        "reset_at": None,
    }
    user = get_user_model().objects.create_user(username=f"quota-{uuid4().hex}")
    first_world = _seed_scenario_world(
        actor_bearer_factory,
        tool_name=tool_name,
        scenario=scenario,
        label="quota-first",
        user=user,
    )
    second_world = _seed_scenario_world(
        actor_bearer_factory,
        tool_name=tool_name,
        scenario=scenario,
        label="quota-second",
        user=user,
    )
    worlds = (first_world, second_world)
    for world in worlds:
        _initialize(mcp_client, world.bearer)

    barrier = Barrier(2)

    def invoke(index: int):
        close_old_connections()
        world = worlds[index]
        arguments = world.resolve(deepcopy(scenario["input"]))
        request_id = uuid4().hex
        barrier.wait(timeout=5)
        try:
            result = _call_tool(
                mcp_client,
                world.bearer,
                tool_name=tool_name,
                arguments=arguments,
                request_id=request_id,
            )
            return world, arguments, request_id, result
        finally:
            connections.close_all()

    with monkeypatch.context() as patcher:
        patcher.setattr(
            server_module,
            "SiteHitsService",
            _service_factory(site_limit=1),
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            executions = list(executor.map(invoke, (0, 1)))

    outcomes = []
    for world, arguments, request_id, result in executions:
        if result["isError"] is False:
            structured = _result_with_contract_output_schema(
                bundle=vector_bundle,
                tool_name=tool_name,
                result=result,
            )
            outcome = "success"
        else:
            structured = None
            envelope = _assert_contract_error(
                contract=vector_bundle.contract,
                result=result,
                expected_code="capacity_reached",
                request_id=request_id,
            )
            assert envelope["details"] == {
                "used": 1,
                "limit": 1,
                "period": "permanent",
                "reset_at": None,
            }
            outcome = "capacity_reached"
        audit = _assert_audit(
            bundle=vector_bundle,
            user=user,
            bearer=world.bearer,
            tool_name=tool_name,
            arguments=arguments,
            request_id=request_id,
            outcome_code=outcome,
            structured=structured,
        )
        assert audit.authorization["limit_applicable"] is True
        assert audit.authorization["limit_name"] == "sites"
        assert audit.authorization["limit_allowed"] is (outcome == "success")
        outcomes.append(outcome)

    assert sorted(outcomes) == sorted(scenario["expected"]["results"])
    assert set(scenario["expected"]["assertions"]) == {
        "mutation_and_capacity_consumption_are_atomic",
        "exactly_one_creation_succeeds",
    }
    assert TrackedSite.objects.filter(owner=user).count() == 1
    assert AgentIdempotencyRecord.objects.filter(authenticated_actor_id=str(user.pk)).count() == 1


def test_all_success_vectors_cross_authenticated_streamable_http(
    vector_bundle,
    mcp_client,
    contract_user,
    oauth_bearer,
):
    assert set(SUCCESS_EXECUTION_ORDER) == set(vector_bundle.vectors)
    assert len(SUCCESS_EXECUTION_ORDER) == 22
    assert all(
        vector_bundle.vectors[name]["scenarios"]["success"]["applicable"] is True
        for name in SUCCESS_EXECUTION_ORDER
    )
    _initialize(mcp_client, oauth_bearer)

    state = SuccessState()
    exercised = set()
    for tool_name in SUCCESS_EXECUTION_ORDER:
        scenario = vector_bundle.vectors[tool_name]["scenarios"]["success"]
        assert scenario["expected"]["assertions"] == ["matches_exact_output_schema"]
        arguments = state.resolve(deepcopy(scenario["input"]))
        request_id = uuid4().hex
        result = _call_tool(
            mcp_client,
            oauth_bearer,
            tool_name=tool_name,
            arguments=arguments,
            request_id=request_id,
        )
        structured = _assert_success_result(
            bundle=vector_bundle,
            tool_name=tool_name,
            scenario=scenario,
            result=result,
        )
        _assert_audit(
            bundle=vector_bundle,
            user=contract_user,
            bearer=oauth_bearer,
            tool_name=tool_name,
            arguments=arguments,
            request_id=request_id,
            outcome_code=scenario["expected"]["audit_outcome_code"],
            structured=structured,
        )
        state.observe(tool_name, structured)
        if tool_name == "create_site":
            assert state.site_slug is not None
            _seed_activation_events(contract_user, state.site_slug)
        exercised.add(tool_name)

    assert exercised == set(vector_bundle.vectors)
    assert len(exercised) == len(SUCCESS_EXECUTION_ORDER)


def test_all_applicable_invalid_input_vectors_cross_authenticated_transport(
    vector_bundle,
    mcp_client,
    contract_user,
    oauth_bearer,
):
    site = contract_user.tracked_sites.create(
        name="Example Site",
        slug="example-site",
        allowed_domains=["example.com"],
    )
    ProductEventDefinition.objects.create(
        site=site,
        event_name="purchase",
        display_name="Purchase",
        description="Completed purchase.",
    )
    _initialize(mcp_client, oauth_bearer)

    applicable = {
        name
        for name, vector in vector_bundle.vectors.items()
        if vector["scenarios"]["invalid_input"]["applicable"] is True
    }
    assert len(applicable) == 21
    exercised = set()
    for entry in vector_bundle.manifest["vectors"]:
        vector = next(
            candidate
            for candidate in vector_bundle.vectors.values()
            if candidate["id"] == entry["id"]
        )
        scenario = vector["scenarios"]["invalid_input"]
        if scenario["applicable"] is not True:
            continue
        tool_name = vector["tool"]
        arguments = deepcopy(scenario["input"])
        request_id = uuid4().hex
        result = _call_tool(
            mcp_client,
            oauth_bearer,
            tool_name=tool_name,
            arguments=arguments,
            request_id=request_id,
        )
        expected = scenario["expected"]
        _assert_contract_error(
            contract=vector_bundle.contract,
            result=result,
            expected_code=expected["error_code"],
            request_id=request_id,
        )
        _assert_audit(
            bundle=vector_bundle,
            user=contract_user,
            bearer=oauth_bearer,
            tool_name=tool_name,
            arguments=arguments,
            request_id=request_id,
            outcome_code=expected["audit_outcome_code"],
        )
        if expected.get("assertions") == ["service_business_validation"]:
            audit = AgentAuditEvent.objects.get(request_id=request_id, tool_name=tool_name)
            assert audit.authorization["capability_allowed"] is True
        exercised.add(tool_name)

    assert exercised == applicable
    assert len(exercised) == len(applicable)
