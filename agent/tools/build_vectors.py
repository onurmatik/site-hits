"""Build a deterministic, versioned Agent Contract service conformance bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

CATEGORIES = (
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

SAMPLE_INPUTS: dict[str, dict[str, Any]] = {
    "get_account_capabilities": {},
    "list_sites": {"include_inactive": False},
    "get_site": {"site_slug": "example-site"},
    "create_site": {
        "name": "Example",
        "allowed_domains": ["example.com"],
        "timezone": "Europe/Istanbul",
        "idempotency_key": "site-create-001",
    },
    "update_site": {
        "site_slug": "example-site",
        "expected_revision": "rev-site-001",
        "name": "Example two",
    },
    "delete_site": {
        "site_slug": "example-site",
        "expected_revision": "rev-site-001",
        "approval": {
            "owner": "agent",
            "action": "delete_site",
            "resource_id": "example-site",
            "confirmed": True,
        },
    },
    "get_analytics_overview": {"site_slug": "example-site", "period": "last7d"},
    "get_sites_overview": {"period": "last7d"},
    "get_analytics_timeseries": {
        "site_slug": "example-site",
        "period": "last7d",
        "granularity": "daily",
    },
    "get_analytics_breakdown": {
        "site_slug": "example-site",
        "dimension": "pages",
        "period": "last7d",
        "limit": 8,
    },
    "get_bot_analytics": {"site_slug": "example-site", "period": "last7d", "limit": 8},
    "get_product_metrics": {"site_slug": "example-site", "period": "last7d"},
    "get_measurement_config": {"site_slug": "example-site"},
    "create_measurement_event": {
        "site_slug": "example-site",
        "event_name": "purchase:completed",
        "display_name": "Purchases",
        "description": "Completed purchases.",
        "aggregation": "count",
        "unit": "",
    },
    "update_measurement_event": {
        "site_slug": "example-site",
        "event_name": "purchase:completed",
        "expected_revision": "rev-event-001",
        "display_name": "Completed purchases",
    },
    "change_measurement_event_contract": {
        "site_slug": "example-site",
        "event_name": "purchase:completed",
        "expected_revision": "rev-event-001",
        "aggregation": "sum",
        "unit": "TRY",
        "approval": {
            "owner": "agent",
            "action": "change_measurement_event_contract",
            "resource_id": "example-site/purchase:completed",
            "confirmed": True,
        },
    },
    "delete_measurement_event": {
        "site_slug": "example-site",
        "event_name": "purchase:completed",
        "expected_revision": "rev-event-001",
        "approval": {
            "owner": "agent",
            "action": "delete_measurement_event",
            "resource_id": "example-site/purchase:completed",
            "confirmed": True,
        },
    },
    "set_activation": {
        "site_slug": "example-site",
        "start_event": "signup",
        "goal_event": "activated",
        "expected_revision": None,
    },
    "clear_activation": {
        "site_slug": "example-site",
        "expected_revision": "rev-activation-001",
        "approval": {
            "owner": "agent",
            "action": "clear_activation",
            "resource_id": "example-site",
            "confirmed": True,
        },
    },
    "get_tracking_setup": {"site_slug": "example-site", "section": "all"},
    "render_tracking_setup": {"site_slug": "example-site", "section": "all"},
    "get_integration_status": {"skill_version": "not-semver-diagnostic-input"},
}

INVALID_INPUTS: dict[str, dict[str, Any]] = {
    "get_account_capabilities": {"not_empty": True},
    "list_sites": {"include_inactive": "yes"},
    "get_site": {"site_slug": "Invalid Slug"},
    "create_site": {
        "name": "Example",
        "allowed_domains": ["https://example.com"],
        "idempotency_key": "short",
    },
    "update_site": {"site_slug": "example-site", "expected_revision": "rev-1"},
    "delete_site": {"site_slug": "Invalid Slug", "expected_revision": "rev-1"},
    "get_analytics_overview": {"period": "forever"},
    "get_sites_overview": {"period": "forever"},
    "get_analytics_timeseries": {"granularity": "weekly"},
    "get_analytics_breakdown": {"site_slug": "example-site", "dimension": "passwords"},
    "get_bot_analytics": {"limit": 0},
    "get_product_metrics": {"site_slug": "Invalid Slug"},
    "get_measurement_config": {"site_slug": "Invalid Slug"},
    "create_measurement_event": {
        "site_slug": "example-site",
        "event_name": "UPPER CASE",
        "display_name": "Invalid",
        "description": "",
    },
    "update_measurement_event": {
        "site_slug": "example-site",
        "event_name": "purchase",
        "expected_revision": "rev-1",
    },
    "change_measurement_event_contract": {
        "site_slug": "example-site",
        "event_name": "purchase",
        "expected_revision": "rev-1",
        "aggregation": "median",
        "unit": "TRY",
    },
    "delete_measurement_event": {
        "site_slug": "example-site",
        "event_name": "UPPER CASE",
        "expected_revision": "rev-1",
    },
    "set_activation": {
        "site_slug": "example-site",
        "start_event": "same",
        "goal_event": "same",
        "expected_revision": None,
    },
    "clear_activation": {"site_slug": "Invalid Slug", "expected_revision": "rev-1"},
    "get_tracking_setup": {"site_slug": "example-site", "section": "secrets"},
    "render_tracking_setup": {"site_slug": "example-site", "section": "secrets"},
    "get_integration_status": {"skill_version": "x" * 129},
}

APPROVAL_TOOLS = {
    "delete_site",
    "change_measurement_event_contract",
    "delete_measurement_event",
    "clear_activation",
}
IDEMPOTENCY_TOOLS = {"create_site", "create_measurement_event"}
NO_CAPABILITY_TOOLS = {"get_account_capabilities", "get_integration_status"}
NO_OWNERSHIP_TARGET_TOOLS = {"get_account_capabilities", "create_site", "get_integration_status"}
FILTERED_COLLECTION_TOOLS = {"list_sites", "get_sites_overview"}

EXECUTABLE_TESTS = {
    "success": {
        "mode": "per_tool_vector",
        "basis": "The harness calls all public service methods and validates each exact output schema and audit event.",
        "tests": [
            "tests/test_agent_runtime.py::test_all_public_tools_return_exact_contract_outputs_and_audit"
        ],
    },
    "invalid_input": {
        "mode": "per_tool_vector",
        "basis": "The harness invokes every applicable invalid_input vector through its named service method.",
        "tests": [
            "tests/test_agent_runtime.py::test_all_applicable_vector_inputs_have_executable_service_outcomes"
        ],
    },
    "missing_capability": {
        "mode": "shared_service_mechanism",
        "basis": "SiteHitsService._run applies the same CapabilityEvaluator gate before ownership and operation dispatch.",
        "tests": [
            "tests/test_agent_runtime.py::test_bootstrap_requires_no_product_capability_and_keeps_ownership_separate"
        ],
    },
    "ownership_isolation": {
        "mode": "shared_service_mechanism",
        "basis": "Central site lookup, visible-site filtering, and report dispatch implement target and inherited ownership.",
        "tests": [
            "tests/test_agent_runtime.py::test_ownership_isolation_and_reporting_use_resource_not_found",
            "tests/test_agent_runtime.py::test_collections_omit_foreign_and_system_owned_sites_without_global_access",
            "tests/test_agent_runtime.py::test_global_resource_access_includes_foreign_and_system_owned_sites",
        ],
    },
    "capability_plan_matrix": {
        "mode": "shared_service_mechanism",
        "basis": "Capability and limit evaluators receive actor context rather than a plan label.",
        "tests": [
            "tests/test_agent_runtime.py::test_site_capacity_is_plan_independent_atomic_and_audited"
        ],
    },
    "quota_concurrency": {
        "mode": "shared_service_mechanism",
        "basis": "Two concurrent create_site calls share an actor-level capacity guard and database actor-row lock; exactly one succeeds at a limit of one.",
        "tests": [
            "tests/test_agent_runtime.py::test_site_capacity_concurrency_allows_exactly_one_creation"
        ],
    },
    "idempotent_replay": {
        "mode": "shared_service_mechanism",
        "basis": "Explicit-key and natural-key replay mechanisms are independently exercised.",
        "tests": [
            "tests/test_agent_runtime.py::test_create_site_is_atomic_idempotent_and_audited",
            "tests/test_agent_runtime.py::test_measurement_event_natural_key_replay_and_conflict",
        ],
    },
    "idempotency_conflict": {
        "mode": "shared_service_mechanism",
        "basis": "Explicit-key and natural-key conflicts are exercised without exposing a raw idempotency key.",
        "tests": [
            "tests/test_agent_runtime.py::test_create_site_is_atomic_idempotent_and_audited",
            "tests/test_agent_runtime.py::test_measurement_event_natural_key_replay_and_conflict",
        ],
    },
    "approval": {
        "mode": "shared_service_mechanism",
        "basis": "The shared approval predicate rejects missing and wrong owner, action, resource, and confirmation bindings; representative site, event, and activation paths also succeed with exact assertions.",
        "tests": [
            "tests/test_agent_runtime.py::test_site_mutations_require_revision_and_agent_approval",
            "tests/test_agent_runtime.py::test_agent_approval_rejects_wrong_owner_action_resource_and_confirmation",
            "tests/test_agent_runtime.py::test_measurement_metadata_and_contract_changes_are_separate",
            "tests/test_agent_runtime.py::test_activation_references_and_deletion_use_stable_errors",
        ],
    },
    "uncertain_external_effect": {
        "mode": "contract_inapplicability",
        "basis": "Contract metadata and complete execution prove that no public tool performs an external effect.",
        "tests": [
            "tests/test_agent_runtime.py::test_all_public_tools_return_exact_contract_outputs_and_audit"
        ],
    },
    "redaction": {
        "mode": "shared_service_mechanism",
        "basis": "Exact output schemas exclude internal identifiers and tracking output explicitly verifies private-key redaction.",
        "tests": [
            "tests/test_agent_runtime.py::test_all_public_tools_return_exact_contract_outputs_and_audit",
            "tests/test_agent_runtime.py::test_tracking_setup_never_returns_private_credentials",
        ],
    },
    "async_status": {
        "mode": "contract_inapplicability",
        "basis": "Contract metadata and complete execution prove that all public operations complete synchronously.",
        "tests": [
            "tests/test_agent_runtime.py::test_all_public_tools_return_exact_contract_outputs_and_audit"
        ],
    },
    "internal_error": {
        "mode": "shared_service_mechanism",
        "basis": "SiteHitsService._run catches every unexpected post-dispatch exception and emits one redacted envelope.",
        "tests": ["tests/test_agent_runtime.py::test_unexpected_exception_is_redacted_and_audited"],
    },
}


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n").encode()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _na(reason: str) -> dict[str, object]:
    return {"applicable": False, "reason": reason}


def _expected(outcome: str, audit_code: str, **extra: object) -> dict[str, object]:
    return {"outcome": outcome, "audit_outcome_code": audit_code, **extra}


def _scenario_bundle(tool_name: str, tool: dict[str, Any]) -> dict[str, Any]:
    sample = SAMPLE_INPUTS[tool_name]
    result_ref = tool["output_schema"]["$ref"]
    scenarios: dict[str, Any] = {
        "success": {
            "applicable": True,
            "input": sample,
            "expected": _expected(
                "success",
                "success",
                result_schema_ref=result_ref,
                assertions=["matches_exact_output_schema"],
            ),
        },
        "invalid_input": {
            "applicable": True,
            "input": INVALID_INPUTS[tool_name],
            "expected": _expected("error", "invalid_input", error_code="invalid_input"),
        },
        "redaction": {
            "applicable": True,
            "input": sample,
            "expected": _expected(
                "success",
                "success",
                result_schema_ref=result_ref,
                assertions=[
                    "contains_no_secret",
                    "contains_no_internal_database_id",
                    "contains_no_internal_trace",
                    "instruction_like_output_is_data",
                ],
            ),
        },
        "internal_error": {
            "applicable": True,
            "input": sample,
            "context_override": {"inject_unexpected_service_failure": True},
            "expected": _expected(
                "error",
                "internal_error",
                error_code="internal_error",
                assertions=[
                    "contains_no_stack_trace",
                    "contains_no_internal_trace",
                    "contains_no_database_identifier",
                ],
            ),
        },
        "uncertain_external_effect": _na(
            "No SiteHits public tool has external_effect semantics."
        ),
        "async_status": _na(
            "The operation completes within one request and does not create a long-running operation."
        ),
    }
    if tool_name == "get_account_capabilities":
        scenarios["invalid_input"] = _na(
            "The bootstrap tool accepts an exact empty object and has no service-level input value to invalidate."
        )
    elif tool_name == "set_activation":
        scenarios["invalid_input"]["expected"]["assertions"] = ["service_business_validation"]
    if tool_name in NO_CAPABILITY_TOOLS:
        scenarios["missing_capability"] = _na(
            "This tool deliberately requires no product capability."
        )
        scenarios["capability_plan_matrix"] = _na(
            "This tool is plan-independent and deliberately requires no product capability."
        )
    else:
        required = tool["required_capabilities"]
        scenarios["missing_capability"] = {
            "applicable": True,
            "input": sample,
            "authorization_override": {"capabilities": []},
            "expected": _expected(
                "error",
                "feature_unavailable",
                error_code="feature_unavailable",
                assertions=[f"missing_{required[0]}"],
            ),
        }
        scenarios["capability_plan_matrix"] = {
            "applicable": True,
            "input": sample,
            "expected": _expected(
                "matrix",
                "success",
                cases=[
                    {"plan_label": "free", "capability_available": True, "outcome": "success"},
                    {"plan_label": "pro", "capability_available": True, "outcome": "success"},
                    {
                        "plan_label": "any",
                        "capability_available": False,
                        "outcome": "feature_unavailable",
                    },
                ],
                assertions=["plan_label_is_not_an_authorization_dimension"],
            ),
        }
    if tool_name in NO_OWNERSHIP_TARGET_TOOLS:
        scenarios["ownership_isolation"] = _na(
            "This call has no pre-existing actor-owned target resource."
        )
    elif tool_name in FILTERED_COLLECTION_TOOLS:
        scenarios["ownership_isolation"] = {
            "applicable": True,
            "input": sample,
            "context_override": {"ownership": {"include_foreign_resource_fixture": True}},
            "expected": _expected(
                "success",
                "success",
                result_schema_ref=result_ref,
                assertions=[
                    "foreign_resources_are_omitted",
                    "system_owned_resources_require_global_resource_access",
                ],
            ),
        }
    else:
        scenarios["ownership_isolation"] = {
            "applicable": True,
            "input": sample,
            "context_override": {"ownership": {"target_owned_by_authenticated_actor": False}},
            "expected": _expected(
                "error",
                "resource_not_found",
                error_code="resource_not_found",
                assertions=["does_not_reveal_resource_existence"],
            ),
        }
    if tool_name == "create_site":
        scenarios["quota_concurrency"] = {
            "applicable": True,
            "input": sample,
            "authorization_override": {
                "limits": {
                    "sites": {"used": 0, "limit": 1, "period": "permanent", "reset_at": None}
                }
            },
            "expected": _expected(
                "concurrent_results",
                "mixed",
                results=["success", "capacity_reached"],
                assertions=[
                    "mutation_and_capacity_consumption_are_atomic",
                    "exactly_one_creation_succeeds",
                ],
            ),
        }
    else:
        scenarios["quota_concurrency"] = _na(
            "This tool does not consume the Contract's sites capacity limit."
        )
    if tool_name in IDEMPOTENCY_TOOLS:
        scenarios["idempotent_replay"] = {
            "applicable": True,
            "input": sample,
            "expected": _expected(
                "success",
                "success",
                result_schema_ref=result_ref,
                assertions=[
                    "same_canonical_input_returns_original_logical_result",
                    "no_duplicate_resource_created",
                ],
            ),
        }
        conflict_input = (
            {**sample, "name": "Different"}
            if tool_name == "create_site"
            else {**sample, "display_name": "Different"}
        )
        conflict_assertion = (
            "error_exposes_only_opaque_idempotency_id"
            if tool_name == "create_site"
            else "error_exposes_only_natural_key"
        )
        scenarios["idempotency_conflict"] = {
            "applicable": True,
            "input": conflict_input,
            "expected": _expected(
                "error",
                "idempotency_conflict",
                error_code="idempotency_conflict",
                assertions=[conflict_assertion, "error_never_exposes_raw_idempotency_key"],
            ),
        }
        if tool_name == "create_site":
            scenarios["idempotent_replay"]["expected"]["assertions"].extend(
                ["guarantee_window_is_90_days", "same_key_is_fresh_after_expiry"]
            )
    else:
        scenarios["idempotent_replay"] = _na(
            "This tool uses safe reads or optimistic revision semantics rather than replay-result idempotency."
        )
        scenarios["idempotency_conflict"] = _na(
            "This tool has no explicit or natural replay key that can conflict."
        )
    if tool_name in APPROVAL_TOOLS:
        no_approval = {key: value for key, value in sample.items() if key != "approval"}
        scenarios["approval"] = {
            "applicable": True,
            "input": no_approval,
            "expected": _expected(
                "error",
                "confirmation_required",
                error_code="confirmation_required",
                assertions=[
                    "confirmation_owner_is_agent",
                    "approval_must_match_action_and_resource_id",
                    "no_second_confirmation_field",
                ],
            ),
        }
    else:
        scenarios["approval"] = _na(
            "This tool is read-only or reversible and requires no explicit-intent assertion."
        )
    return {category: scenarios[category] for category in CATEGORIES}


def _execution_coverage(scenarios: dict[str, Any]) -> dict[str, Any]:
    coverage: dict[str, Any] = {}
    for category, scenario in scenarios.items():
        category_coverage = {
            "mode": EXECUTABLE_TESTS[category]["mode"],
            "basis": EXECUTABLE_TESTS[category]["basis"],
            "tests": list(EXECUTABLE_TESTS[category]["tests"]),
        }
        if not scenario["applicable"]:
            category_coverage["mode"] = "contract_inapplicability"
            category_coverage["basis"] = (
                f"Contract-declared inapplicability for this tool: {scenario['reason']}"
            )
        coverage[category] = category_coverage
    return coverage


def build_bundle(contract_path: Path, output_dir: Path) -> dict[str, Any]:
    contract = json.loads(contract_path.read_text())
    contract_version = contract["agent_contract_version"]
    vector_dir = output_dir / "vectors"
    vector_dir.mkdir(parents=True, exist_ok=True)
    if contract_version == "1.0.0":
        vector_schema_reference = "../vector.schema.json"
    else:
        vector_schema_reference = "vector.schema.json"
        template_path = contract_path.parent / "conformance" / "vector.schema.json"
        vector_schema = json.loads(template_path.read_text())
        vector_schema["$id"] = (
            "https://sitehits.io/schemas/agent-contract-vector-"
            f"{contract_version}.json"
        )
        vector_schema["properties"]["agent_contract_version"] = {
            "const": contract_version
        }
        (output_dir / vector_schema_reference).write_bytes(
            _canonical_bytes(vector_schema)
        )
    entries = []
    for tool_name, tool in sorted(contract["tools"].items()):
        vector_id = f"{tool_name}-contract-{contract['agent_contract_version']}"
        scenarios = _scenario_bundle(tool_name, tool)
        document = {
            "schema_version": 1,
            "id": vector_id,
            "agent_contract_version": contract["agent_contract_version"],
            "tool": tool_name,
            "defaults": {
                "context": {
                    "authenticated_actor_id": "actor-owner",
                    "authenticated_client_id": "client-codex",
                    "tenant_id": None,
                    "ownership": {"target_owned_by_authenticated_actor": True},
                },
                "authorization_context": {
                    "authenticated": True,
                    "scopes": tool["required_scopes"],
                    "capabilities": tool["required_capabilities"],
                    "limits": {
                        "sites": {"used": 1, "limit": None, "period": "permanent", "reset_at": None}
                    },
                },
                "audit": {
                    "profile": tool["audit"],
                    "input_recording": "keyed_hash",
                    "required_fields": contract["audit_profiles"][tool["audit"]]["fields"],
                    "forbidden_fields": [
                        "password",
                        "access_token",
                        "refresh_token",
                        "authorization_code",
                        "private_tracking_key",
                        "trace_id",
                        "stack_trace",
                        "database_id",
                    ],
                },
            },
            "execution_coverage": _execution_coverage(scenarios),
            "scenarios": scenarios,
        }
        path = vector_dir / f"{tool_name}.json"
        payload = _canonical_bytes(document)
        path.write_bytes(payload)
        entries.append(
            {"id": vector_id, "path": f"vectors/{tool_name}.json", "sha256": _sha256(payload)}
        )
    manifest = {
        "schema_version": 1,
        "agent_contract_version": contract["agent_contract_version"],
        "vector_schema": vector_schema_reference,
        "execution_harness": "tests/test_agent_runtime.py",
        "vectors": entries,
    }
    (output_dir / "manifest.json").write_bytes(_canonical_bytes(manifest))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", default="agent/contract.yaml")
    parser.add_argument("--output-dir")
    args = parser.parse_args()
    contract_path = Path(args.contract)
    contract = json.loads(contract_path.read_text())
    output_dir = Path(args.output_dir or f"agent/conformance/{contract['agent_contract_version']}")
    build_bundle(contract_path, output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
