import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connections
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker

from agent_runtime import ApplicationError, ApprovalAssertion, RequestContext, SiteHitsService
from agent_runtime.capabilities import CapabilityEvaluation
from agent_runtime.contract import audit_retention_days, idempotency_retention_days
from agent_runtime.limits import SiteLimit
from agent_runtime.revisions import revision_for
from analytics.models import (
    ActivationDefinition,
    AgentAuditEvent,
    AgentIdempotencyRecord,
    ProductEventDefinition,
)
from websites.models import TrackedSite


@pytest.fixture
def agent_user(db):
    return get_user_model().objects.create_user(
        username="agent-owner",
        email="agent-owner@example.com",
    )


@pytest.fixture
def runtime(agent_user):
    context = RequestContext(
        authenticated_actor_id=str(agent_user.pk),
        authenticated_client_id="contract-test-client",
        granted_scopes=frozenset({"read", "write"}),
        request_id="request_001",
    )
    return SiteHitsService(context)


def approval(action, resource_id):
    return ApprovalAssertion(
        owner="agent",
        action=action,
        resource_id=resource_id,
        confirmed=True,
    )


def assert_error(code, function, *args, **kwargs):
    with pytest.raises(ApplicationError) as caught:
        function(*args, **kwargs)
    assert caught.value.code == code
    return caught.value


@pytest.mark.django_db
def test_context_and_error_envelope_are_stable(agent_user):
    with pytest.raises(ValueError, match="request_id"):
        RequestContext(
            authenticated_actor_id=str(agent_user.pk),
            authenticated_client_id="client",
            granted_scopes=frozenset({"read", "write"}),
            request_id="not safe!",
        )

    context = RequestContext(
        authenticated_actor_id=str(agent_user.pk),
        authenticated_client_id="client",
        granted_scopes=frozenset({"read", "write"}),
        request_id="opaque-1",
    )
    error = ApplicationError(code="resource_not_found", message="Not found.")
    assert error.to_envelope(context.request_id) == {
        "code": "resource_not_found",
        "message": "Not found.",
        "retryable": False,
        "request_id": "opaque-1",
        "details": {},
    }


@pytest.mark.django_db
def test_unavailable_actor_uses_each_tools_declared_permission_error(agent_user):
    service = SiteHitsService(
        RequestContext(
            authenticated_actor_id=str(agent_user.pk),
            authenticated_client_id="inactive-actor-client",
            granted_scopes=frozenset({"read"}),
            request_id="inactive_actor_001",
        )
    )
    agent_user.is_active = False
    agent_user.save(update_fields=["is_active"])

    error = assert_error("permission_denied", service.list_sites)

    assert error.to_envelope("inactive_actor_001")["details"] == {}
    audit = AgentAuditEvent.objects.get(request_id="inactive_actor_001")
    assert audit.authorization["authenticated"] is False
    assert audit.outcome_code == "permission_denied"


@pytest.mark.django_db
def test_bootstrap_requires_no_product_capability_and_keeps_ownership_separate(agent_user):
    class NoCapabilities:
        def evaluate(self, user):
            return CapabilityEvaluation(frozenset())

    service = SiteHitsService(
        RequestContext(
            authenticated_actor_id=str(agent_user.pk),
            authenticated_client_id="limited-client",
            granted_scopes=frozenset({"read", "write"}),
        ),
        capability_evaluator=NoCapabilities(),
    )
    result = service.get_account_capabilities()

    assert set(result) == {"capabilities", "limits"}
    assert all(item["available"] is False for item in result["capabilities"])
    assert result["limits"] == [
        {
            "name": "sites",
            "used": 0,
            "limit": None,
            "period": "permanent",
            "reset_at": None,
        }
    ]
    assert_error("feature_unavailable", service.list_sites)
    denied = AgentAuditEvent.objects.get(
        authenticated_client_id="limited-client",
        tool_name="list_sites",
    )
    assert denied.authorization["capability_allowed"] is False
    assert denied.authorization["ownership_allowed"] == "not_evaluated"
    assert denied.authorization["ownership"] == "owned_sites_plus_global_resources_when_capable"


@pytest.mark.django_db
def test_create_site_is_atomic_idempotent_and_audited(runtime):
    first = runtime.create_site(
        name="Agent Site",
        allowed_domains=["agent.example"],
        timezone="Europe/Istanbul",
        idempotency_key="create-agent-site",
    )
    replay = runtime.create_site(
        name="Agent Site",
        allowed_domains=["agent.example"],
        timezone="Europe/Istanbul",
        idempotency_key="create-agent-site",
    )

    assert replay == first
    assert "id" not in first
    assert len(first["revision"]) == 64
    assert first["revision"] != first["updated_at"]
    assert AgentIdempotencyRecord.objects.count() == 1
    assert AgentAuditEvent.objects.filter(tool_name="create_site", outcome_code="success").count() == 2
    assert_error(
        "idempotency_conflict",
        runtime.create_site,
        name="Different",
        allowed_domains=["different.example"],
        timezone="Europe/Istanbul",
        idempotency_key="create-agent-site",
    )
    assert AgentAuditEvent.objects.filter(
        tool_name="create_site",
        outcome_code="idempotency_conflict",
    ).exists()


@pytest.mark.django_db
def test_create_site_validation_rolls_back_idempotency_record(runtime):
    assert_error(
        "invalid_input",
        runtime.create_site,
        name="Invalid",
        allowed_domains=[],
        timezone="Europe/Istanbul",
        idempotency_key="invalid-create",
    )
    assert not AgentIdempotencyRecord.objects.filter(tool_name="create_site").exists()
    assert AgentAuditEvent.objects.filter(
        tool_name="create_site",
        outcome_code="invalid_input",
    ).exists()


@pytest.mark.django_db
def test_site_capacity_is_plan_independent_atomic_and_audited(agent_user):
    class OneSiteLimit:
        def site_limit(self, user):
            return SiteLimit(limit=1)

    service = SiteHitsService(
        RequestContext(
            authenticated_actor_id=str(agent_user.pk),
            authenticated_client_id="finite-client",
            granted_scopes=frozenset({"read", "write"}),
        ),
        limit_evaluator=OneSiteLimit(),
    )
    bootstrap = service.get_account_capabilities()
    assert bootstrap["limits"][0] == {
        "name": "sites",
        "used": 0,
        "limit": 1,
        "period": "permanent",
        "reset_at": None,
    }
    user_model = get_user_model()
    with patch.object(
        user_model.objects,
        "select_for_update",
        wraps=user_model.objects.select_for_update,
    ) as actor_lock:
        service.create_site(
            name="Only Site",
            allowed_domains=["only.example"],
            idempotency_key="only-site-key",
        )
    actor_lock.assert_called_once_with()
    error = assert_error(
        "capacity_reached",
        service.create_site,
        name="Too Many",
        allowed_domains=["too-many.example"],
        idempotency_key="too-many-key",
    )
    assert error.details == {
        "used": 1,
        "limit": 1,
        "period": "permanent",
        "reset_at": None,
    }
    assert agent_user.tracked_sites.count() == 1
    assert AgentIdempotencyRecord.objects.count() == 1
    assert AgentAuditEvent.objects.filter(
        tool_name="create_site",
        outcome_code="capacity_reached",
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_site_capacity_concurrency_allows_exactly_one_creation(agent_user):
    class OneSiteLimit:
        def site_limit(self, user):
            return SiteLimit(limit=1)

    barrier = Barrier(2)

    def create_site(index):
        close_old_connections()
        service = SiteHitsService(
            RequestContext(
                authenticated_actor_id=str(agent_user.pk),
                authenticated_client_id=f"concurrent-client-{index}",
                granted_scopes=frozenset({"read", "write"}),
                request_id=f"concurrent_create_{index}",
            ),
            limit_evaluator=OneSiteLimit(),
        )
        barrier.wait(timeout=5)
        try:
            service.create_site(
                name=f"Concurrent {index}",
                allowed_domains=[f"concurrent-{index}.example"],
                idempotency_key=f"concurrent-create-{index}",
            )
        except ApplicationError as exc:
            return exc.code
        finally:
            connections.close_all()
        return "success"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(create_site, (1, 2)))

    assert sorted(outcomes) == ["capacity_reached", "success"]
    assert TrackedSite.objects.filter(owner=agent_user).count() == 1
    assert sorted(
        AgentAuditEvent.objects.filter(request_id__startswith="concurrent_create_").values_list(
            "outcome_code",
            flat=True,
        )
    ) == ["capacity_reached", "success"]


@pytest.mark.django_db
def test_expired_idempotency_key_is_fresh_after_guarantee_window(runtime):
    first = runtime.create_site(
        name="Original Window",
        allowed_domains=["original-window.example"],
        idempotency_key="windowed-create-key",
    )
    AgentIdempotencyRecord.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

    fresh = runtime.create_site(
        name="Fresh Window",
        allowed_domains=["fresh-window.example"],
        idempotency_key="windowed-create-key",
    )

    assert fresh["slug"] != first["slug"]
    assert runtime.user.tracked_sites.count() == 2
    assert AgentIdempotencyRecord.objects.count() == 1


@pytest.mark.django_db
def test_site_mutations_require_revision_and_agent_approval(runtime):
    site = runtime.create_site(
        name="Mutable",
        allowed_domains=["mutable.example"],
        idempotency_key="mutable-site",
    )
    assert_error(
        "revision_conflict",
        runtime.update_site,
        site_slug=site["slug"],
        expected_revision="rev-opaque",
        name="Must not parse revisions",
    )
    assert_error(
        "invalid_input",
        runtime.update_site,
        site_slug=site["slug"],
        expected_revision=site["revision"],
    )
    updated = runtime.update_site(
        site_slug=site["slug"],
        expected_revision=site["revision"],
        name="Renamed",
    )
    assert updated["name"] == "Renamed"
    assert_error(
        "revision_conflict",
        runtime.update_site,
        site_slug=site["slug"],
        expected_revision=site["revision"],
        name="Stale",
    )
    assert_error(
        "confirmation_required",
        runtime.delete_site,
        site_slug=site["slug"],
        expected_revision=updated["revision"],
        approval=None,
    )
    assert runtime.delete_site(
        site_slug=site["slug"],
        expected_revision=updated["revision"],
        approval=approval("delete_site", site["slug"]),
    ) == {"deleted": True, "site_slug": site["slug"]}


@pytest.mark.django_db
def test_agent_approval_rejects_wrong_owner_action_resource_and_confirmation(runtime):
    site = runtime.create_site(
        name="Approval Binding",
        allowed_domains=["approval-binding.example"],
        idempotency_key="approval-binding-site",
    )
    cases = [
        ApprovalAssertion(
            owner="client",
            action="delete_site",
            resource_id=site["slug"],
            confirmed=True,
        ),
        ApprovalAssertion(
            owner="agent",
            action="clear_activation",
            resource_id=site["slug"],
            confirmed=True,
        ),
        ApprovalAssertion(
            owner="agent",
            action="delete_site",
            resource_id="different-site",
            confirmed=True,
        ),
        ApprovalAssertion(
            owner="agent",
            action="delete_site",
            resource_id=site["slug"],
            confirmed=False,
        ),
    ]

    for assertion in cases:
        with patch("agent_runtime.service.validate_tool_input"):
            assert_error(
                "confirmation_required",
                runtime.delete_site,
                site_slug=site["slug"],
                expected_revision=site["revision"],
                approval=assertion,
            )

    for index, assertion in enumerate(cases):
        expected_code = "confirmation_required" if index == 2 else "invalid_input"
        assert_error(
            expected_code,
            runtime.delete_site,
            site_slug=site["slug"],
            expected_revision=site["revision"],
            approval=assertion,
        )

    assert TrackedSite.objects.filter(slug=site["slug"]).exists()
    rejected_bindings = AgentAuditEvent.objects.filter(
        tool_name="delete_site",
        outcome_code="confirmation_required",
    )
    assert rejected_bindings.count() == 5
    assert all(event.authorization["approval_required"] is True for event in rejected_bindings)
    assert all(event.authorization["approval_allowed"] is False for event in rejected_bindings)


@pytest.mark.django_db
def test_measurement_metadata_and_contract_changes_are_separate(runtime):
    site = runtime.create_site(
        name="Product",
        allowed_domains=["product.example"],
        idempotency_key="product-site",
    )
    created = runtime.create_measurement_event(
        site_slug=site["slug"],
        event_name="purchase",
        display_name="Purchases",
        description="Completed purchase.",
    )["event"]
    assert "id" not in created
    assert_error(
        "invalid_input",
        runtime.update_measurement_event,
        site_slug=site["slug"],
        event_name="purchase",
        expected_revision=created["revision"],
    )
    metadata = runtime.update_measurement_event(
        site_slug=site["slug"],
        event_name="purchase",
        expected_revision=created["revision"],
        display_name="Orders",
    )
    resource_id = f"{site['slug']}/purchase"
    assert_error(
        "confirmation_required",
        runtime.change_measurement_event_contract,
        site_slug=site["slug"],
        event_name="purchase",
        expected_revision=metadata["revision"],
        aggregation="sum",
        unit="TRY",
        approval=None,
    )
    changed = runtime.change_measurement_event_contract(
        site_slug=site["slug"],
        event_name="purchase",
        expected_revision=metadata["revision"],
        aggregation="sum",
        unit="TRY",
        approval=approval("change_measurement_event_contract", resource_id),
    )
    assert changed["aggregation"] == "sum"
    assert changed["unit"] == "TRY"


@pytest.mark.django_db
def test_measurement_event_natural_key_replay_and_conflict(runtime):
    site = runtime.create_site(
        name="Natural Key",
        allowed_domains=["natural-key.example"],
        idempotency_key="natural-key-site",
    )
    request = {
        "site_slug": site["slug"],
        "event_name": "purchase",
        "display_name": "Purchases",
        "description": "Completed purchase.",
        "aggregation": "count",
        "unit": "",
    }

    first = runtime.create_measurement_event(**request)
    replay = runtime.create_measurement_event(**request)

    assert first["created"] is True
    assert replay == {"created": False, "event": first["event"]}
    conflict = assert_error(
        "idempotency_conflict",
        runtime.create_measurement_event,
        **{**request, "display_name": "Orders"},
    )
    assert conflict.details == {
        "natural_key": {
            "site_slug": site["slug"],
            "event_name": "purchase",
        }
    }
    assert AgentAuditEvent.objects.filter(
        tool_name="create_measurement_event",
        outcome_code="idempotency_conflict",
    ).exists()


@pytest.mark.django_db
def test_activation_references_and_deletion_use_stable_errors(runtime):
    site = runtime.create_site(
        name="Activation",
        allowed_domains=["activation.example"],
        idempotency_key="activation-site",
    )
    for event_name in ("signup", "activated"):
        runtime.create_measurement_event(
            site_slug=site["slug"],
            event_name=event_name,
            display_name=event_name.title(),
            description=f"{event_name} event.",
        )
    activation = runtime.set_activation(
        site_slug=site["slug"],
        start_event="signup",
        goal_event="activated",
        expected_revision=None,
    )
    signup = ProductEventDefinition.objects.get(
        site__slug=site["slug"],
        event_name="signup",
    )
    assert_error(
        "referenced_resource_conflict",
        runtime.delete_measurement_event,
        site_slug=site["slug"],
        event_name="signup",
        expected_revision=revision_for(signup),
        approval=approval(
            "delete_measurement_event",
            f"{site['slug']}/signup",
        ),
    )
    assert runtime.clear_activation(
        site_slug=site["slug"],
        expected_revision=activation["revision"],
        approval=approval("clear_activation", site["slug"]),
    ) == {"site_slug": site["slug"], "cleared": True}
    assert not ActivationDefinition.objects.filter(site__slug=site["slug"]).exists()


@pytest.mark.django_db
def test_tracking_setup_never_returns_private_credentials(runtime):
    site_data = runtime.create_site(
        name="Tracking",
        allowed_domains=["tracking.example"],
        idempotency_key="tracking-site",
    )
    site = runtime.user.tracked_sites.get(slug=site_data["slug"])

    for method in (runtime.get_tracking_setup, runtime.render_tracking_setup):
        payload = method(site_slug=site.slug)
        rendered = json.dumps(payload)
        assert "credentials_included" not in payload
        assert site.public_key in rendered
        assert site.bot_key not in rendered
        assert site.server_event_key not in rendered
        assert "agent_instruction" not in rendered
        assert "setup_guidance" in rendered

    assert AgentAuditEvent.objects.filter(tool_name="get_tracking_setup").exists()
    assert AgentAuditEvent.objects.filter(tool_name="render_tracking_setup").exists()


@pytest.mark.django_db
def test_ownership_isolation_and_reporting_use_resource_not_found(runtime, tracked_site):
    assert_error("resource_not_found", runtime.get_site, site_slug=tracked_site.slug)
    denied = AgentAuditEvent.objects.get(tool_name="get_site")
    assert denied.authorization["capability_allowed"] is True
    assert denied.authorization["ownership"] == "site_owner_or_global_resource_access"
    assert denied.authorization["ownership_allowed"] is False
    assert_error(
        "resource_not_found",
        runtime.get_analytics_overview,
        site_slug=tracked_site.slug,
        period="last7d",
    )
    report_denied = AgentAuditEvent.objects.get(tool_name="get_analytics_overview")
    assert report_denied.authorization["ownership"] == "inherit_each_site_ownership"
    assert report_denied.authorization["ownership_allowed"] is False


@pytest.mark.django_db
def test_collections_omit_foreign_and_system_owned_sites_without_global_access(agent_user):
    owned = TrackedSite.objects.create(
        owner=agent_user,
        name="Owned",
        slug="owned",
        allowed_domains=["owned.example"],
    )
    foreign_user = get_user_model().objects.create_user(username="foreign-owner")
    TrackedSite.objects.create(
        owner=foreign_user,
        name="Foreign",
        slug="foreign",
        allowed_domains=["foreign.example"],
    )
    TrackedSite.objects.create(
        owner=None,
        name="System",
        slug="system",
        allowed_domains=["system.example"],
    )
    regular = SiteHitsService(
        RequestContext(
            authenticated_actor_id=str(agent_user.pk),
            authenticated_client_id="ownership-collection-client",
            granted_scopes=frozenset({"read"}),
        )
    )

    assert [site["slug"] for site in regular.list_sites()["sites"]] == [owned.slug]
    assert [
        site["slug"] for site in regular.get_sites_overview(period="last7d")["sites"]
    ] == [owned.slug]


@pytest.mark.django_db
def test_global_resource_access_includes_foreign_and_system_owned_sites(superuser):
    owner = get_user_model().objects.create_user(username="global-resource-owner")
    TrackedSite.objects.create(
        owner=owner,
        name="Foreign Global",
        slug="foreign-global",
        allowed_domains=["foreign-global.example"],
    )
    TrackedSite.objects.create(
        owner=None,
        name="System Global",
        slug="system-global",
        allowed_domains=["system-global.example"],
    )
    privileged = SiteHitsService(
        RequestContext(
            authenticated_actor_id=str(superuser.pk),
            authenticated_client_id="global-resource-client",
            granted_scopes=frozenset({"read"}),
        )
    )

    bootstrap = privileged.get_account_capabilities()
    assert next(
        item
        for item in bootstrap["capabilities"]
        if item["name"] == "global_resource_access"
    )["available"] is True
    assert {site["slug"] for site in privileged.list_sites()["sites"]} == {
        "foreign-global",
        "system-global",
    }
    assert privileged.get_site(site_slug="system-global")["slug"] == "system-global"


@pytest.mark.django_db
def test_audit_contains_identity_hmac_and_no_raw_input(runtime):
    runtime.list_sites()
    event = AgentAuditEvent.objects.get(tool_name="list_sites")
    assert event.authenticated_actor_id == runtime.context.authenticated_actor_id
    assert event.authenticated_client_id == runtime.context.authenticated_client_id
    assert event.request_id == runtime.context.request_id
    assert len(event.input_hash) == 64
    assert event.input_hash != "{}"
    assert not hasattr(event, "input")
    assert event.authorization == {
        "authentication_required": True,
        "authenticated": True,
        "required_scopes": ["read"],
        "granted_scopes": ["read", "write"],
        "scope_allowed": True,
        "capability": "site_management",
        "capability_allowed": True,
        "ownership": "owned_sites_plus_global_resources_when_capable",
        "ownership_allowed": True,
        "limit_applicable": False,
        "limit_name": None,
        "limit_allowed": True,
        "limit_details": {},
        "approval_required": False,
        "approval_confirmed": False,
        "approval_allowed": True,
    }


@pytest.mark.django_db
def test_scope_is_independent_and_refused_before_mutation(agent_user):
    service = SiteHitsService(
        RequestContext(
            authenticated_actor_id=str(agent_user.pk),
            authenticated_client_id="read-only-client",
            granted_scopes=frozenset({"read"}),
            request_id="scope_refusal_001",
        )
    )

    error = assert_error(
        "internal_error",
        service.create_site,
        name="Must Not Exist",
        allowed_domains=["must-not-exist.example"],
        idempotency_key="scope-refusal-key",
    )

    assert error.message == "The operation could not be completed."
    assert agent_user.tracked_sites.count() == 0
    event = AgentAuditEvent.objects.get(request_id="scope_refusal_001")
    assert event.authorization["authenticated"] is True
    assert event.authorization["scope_allowed"] is False
    assert event.authorization["capability_allowed"] == "not_evaluated"
    assert event.outcome_code == "internal_error"


@pytest.mark.django_db
def test_unexpected_exception_is_redacted_and_audited(runtime):
    service = SiteHitsService(
        runtime.context,
        integration_status_provider=lambda _version: 1 / 0,
    )
    error = assert_error("internal_error", service.get_integration_status)
    assert error.message == "The operation could not be completed."
    assert "division" not in error.message
    assert AgentAuditEvent.objects.filter(
        tool_name="get_integration_status",
        outcome_code="internal_error",
    ).exists()


@pytest.mark.django_db
def test_invalid_provider_output_is_redacted_and_audited(runtime):
    service = SiteHitsService(
        runtime.context,
        integration_status_provider=lambda _version: {"unexpected": "secret-like-value"},
    )

    error = assert_error("internal_error", service.get_integration_status)

    assert error.to_envelope(runtime.context.request_id) == {
        "code": "internal_error",
        "message": "The operation could not be completed.",
        "retryable": False,
        "request_id": runtime.context.request_id,
        "details": {},
    }
    assert AgentAuditEvent.objects.filter(
        tool_name="get_integration_status",
        outcome_code="internal_error",
    ).exists()


def test_runtime_retention_windows_are_canonical_contract_values():
    contract = json.loads(Path("agent/contract.yaml").read_text())
    assert audit_retention_days() == contract["retention"]["audit_days"] == 90
    assert (
        idempotency_retention_days()
        == contract["retention"]["idempotency_records"]["create_site_days"]
        == 90
    )


@pytest.mark.django_db
def test_all_public_tools_return_exact_contract_outputs_and_audit(agent_user):
    contract = json.loads(Path("agent/contract.yaml").read_text())
    manifest_dir = Path("agent/conformance/1.0.0")
    manifest = json.loads((manifest_dir / "manifest.json").read_text())
    vectors = {
        entry["id"].removesuffix("-contract-1.0.0"): json.loads(
            (manifest_dir / entry["path"]).read_text()
        )
        for entry in manifest["vectors"]
    }
    service = SiteHitsService(
        RequestContext(
            authenticated_actor_id=str(agent_user.pk),
            authenticated_client_id="success-vector-client",
            granted_scopes=frozenset({"read", "write"}),
            request_id="success_vectors_001",
        ),
        integration_status_provider=lambda version: {
            "server_version": "0.2.0",
            "agent_contract_version": "1.0.0",
            "latest_skill_version": "1.0.0",
            "minimum_skill_version": "1.0.0",
            "reported_skill_version": version,
            "skill_status": "unknown",
            "upgrade_required": False,
            "update_available": False,
            "skill_update_url": "https://sitehits.io/INSTALL.md",
        },
    )
    execution_order = (
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
    assert set(execution_order) == set(vectors) == set(contract["tools"])
    state = {}
    results = {}

    def resolve_symbols(value):
        if isinstance(value, dict):
            return {key: resolve_symbols(child) for key, child in value.items()}
        if isinstance(value, list):
            return [resolve_symbols(child) for child in value]
        if not isinstance(value, str):
            return value
        if value == "example-site":
            return state["site_slug"]
        if value.startswith("example-site/"):
            return f"{state['site_slug']}/{value.split('/', 1)[1]}"
        if value.startswith("rev-site-"):
            return state["site_revision"]
        if value.startswith("rev-event-"):
            return state["event_revision"]
        if value.startswith("rev-activation-"):
            return state["activation_revision"]
        return value

    for tool_name in execution_order:
        vector = vectors[tool_name]
        scenario = vector["scenarios"]["success"]
        assert scenario["applicable"] is True
        arguments = resolve_symbols(scenario["input"])
        if "approval" in arguments:
            arguments["approval"] = ApprovalAssertion(**arguments["approval"])

        result = getattr(service, tool_name)(**arguments)
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": scenario["expected"]["result_schema_ref"],
            "$defs": contract["$defs"],
        }
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(result)
        results[tool_name] = result
        assert scenario["expected"]["audit_outcome_code"] == "success"
        assert AgentAuditEvent.objects.filter(
            request_id="success_vectors_001",
            tool_name=tool_name,
            outcome_code="success",
        ).exists()

        if tool_name == "create_site":
            state["site_slug"] = result["slug"]
            state["site_revision"] = result["revision"]
            site = TrackedSite.objects.get(slug=result["slug"])
            for event_name in ("signup", "activated"):
                ProductEventDefinition.objects.create(
                    site=site,
                    event_name=event_name,
                    display_name=event_name.title(),
                    description=f"{event_name} event.",
                )
        elif tool_name == "update_site":
            state["site_revision"] = result["revision"]
        elif tool_name == "create_measurement_event":
            state["event_revision"] = result["event"]["revision"]
        elif tool_name in {
            "update_measurement_event",
            "change_measurement_event_contract",
        }:
            state["event_revision"] = result["revision"]
        elif tool_name == "set_activation":
            state["activation_revision"] = result["revision"]

    assert set(results) == set(contract["tools"])
    audited = list(
        AgentAuditEvent.objects.filter(
            request_id="success_vectors_001",
            outcome_code="success",
        ).values_list("tool_name", flat=True)
    )
    assert len(audited) == len(contract["tools"])
    assert set(audited) == set(contract["tools"])


@pytest.mark.django_db
def test_all_applicable_vector_inputs_have_executable_service_outcomes(agent_user):
    site = agent_user.tracked_sites.create(
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
    service = SiteHitsService(
        RequestContext(
            authenticated_actor_id=str(agent_user.pk),
            authenticated_client_id="invalid-vector-client",
            granted_scopes=frozenset({"read", "write"}),
            request_id="invalid_vectors_001",
        ),
        integration_status_provider=lambda _version: {},
    )
    manifest_dir = Path("agent/conformance/1.0.0")
    manifest = json.loads((manifest_dir / "manifest.json").read_text())
    exercised = set()

    for entry in manifest["vectors"]:
        vector = json.loads((manifest_dir / entry["path"]).read_text())
        scenario = vector["scenarios"]["invalid_input"]
        if not scenario["applicable"]:
            continue
        tool_name = vector["tool"]
        error = assert_error(
            "invalid_input",
            getattr(service, tool_name),
            **scenario["input"],
        )
        assert error.to_envelope(service.context.request_id)["request_id"] == "invalid_vectors_001"
        assert AgentAuditEvent.objects.filter(
            request_id="invalid_vectors_001",
            tool_name=tool_name,
            outcome_code="invalid_input",
        ).exists()
        exercised.add(tool_name)

    applicable = {
        json.loads((manifest_dir / entry["path"]).read_text())["tool"]
        for entry in manifest["vectors"]
        if json.loads((manifest_dir / entry["path"]).read_text())["scenarios"][
            "invalid_input"
        ]["applicable"]
    }
    assert exercised == applicable
