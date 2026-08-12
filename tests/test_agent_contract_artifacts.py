import copy
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from agent_runtime import SiteHitsService
from agent_runtime.errors import APPLICATION_ERROR_CODES

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "agent" / "contract.yaml"
CONTRACT_SCHEMA_PATH = ROOT / "agent" / "contract.schema.json"
VECTOR_SCHEMA_PATH = ROOT / "agent" / "conformance" / "vector.schema.json"
MANIFEST_PATH = ROOT / "agent" / "conformance" / "1.0.0" / "manifest.json"
SOURCES_PATH = ROOT / "release" / "sources.yaml"


def _load(path):
    return json.loads(path.read_text())


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _iter_references(value):
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str):
            yield reference
        for child in value.values():
            yield from _iter_references(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_references(child)


def _assert_local_references_resolve(document):
    definitions = document.get("$defs", {})
    for reference in _iter_references(document):
        assert reference.startswith("#/$defs/"), f"Unsupported reference: {reference}"
        target = reference.removeprefix("#/$defs/")
        assert "/" not in target and target in definitions, f"Dangling reference: {reference}"


@pytest.fixture(scope="module")
def contract():
    return _load(CONTRACT_PATH)


@pytest.fixture(scope="module")
def vectors():
    manifest = _load(MANIFEST_PATH)
    return manifest, {
        item["id"]: _load(MANIFEST_PATH.parent / item["path"]) for item in manifest["vectors"]
    }


def test_contract_validates_against_strict_schema_and_all_refs_resolve(contract):
    schema = _load(CONTRACT_SCHEMA_PATH)
    vector_schema = _load(VECTOR_SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(contract)
    _assert_local_references_resolve(schema)
    _assert_local_references_resolve(vector_schema)
    _assert_local_references_resolve(contract)
    for tool in contract["tools"].values():
        Draft202012Validator.check_schema(
            {**tool["input_schema"], "$defs": contract["$defs"]}
        )
        Draft202012Validator.check_schema(
            {**tool["output_schema"], "$defs": contract["$defs"]}
        )

    dangling = copy.deepcopy(contract)
    del dangling["$defs"]["site"]
    with pytest.raises(AssertionError, match=r"Dangling reference: #/\$defs/site"):
        _assert_local_references_resolve(dangling)


def test_contract_referential_integrity_and_metadata_completeness(contract):
    tools = contract["tools"]
    assert len(tools) == 22
    assert contract["bootstrap"]["tool"] == "get_account_capabilities"
    assert tools[contract["bootstrap"]["tool"]]["required_scopes"] == ["read"]
    assert tools[contract["bootstrap"]["tool"]]["required_capabilities"] == []
    assert tools[contract["bootstrap"]["tool"]]["side_effect"] == "read_only"
    assert tools["get_integration_status"]["resource_type"] == "integration"
    assert tools["get_measurement_config"]["resource_type"] == "measurement_configuration"
    assert contract["compatibility"]["window_status"] == "not_applicable_initial_release"
    assert contract["compatibility"]["previous_major_minimum_days"] is None
    assert contract["retention"] == {
        "audit_days": 90,
        "idempotency_records": {
            "create_site_days": 90,
            "expiry_behavior": contract["retention"]["idempotency_records"]["expiry_behavior"],
        },
    }
    assert set(contract["error_codes"]) == APPLICATION_ERROR_CODES
    for name, tool in tools.items():
        assert tool["title"] and tool["description"]
        assert tool["exposure"] == "public", name
        assert tool["authentication"] == "required", name
        assert set(tool["required_scopes"]) <= {"read", "write"}, name
        assert set(tool["required_capabilities"]) <= set(contract["capabilities"]), name
        assert tool["resource_type"] in contract["resources"], name
        assert set(tool["errors"]) <= set(contract["error_codes"]), name
        assert "permission_denied" in tool["errors"], name
        assert "internal_error" in tool["errors"], name
        assert set(tool["data"]["input"] + tool["data"]["output"]) <= set(
            contract["data_classifications"]
        ), name
        assert tool["audit"] in contract["audit_profiles"], name
        assert tool["input_schema"]["$ref"].startswith("#/$defs/"), name
        assert tool["output_schema"]["$ref"].startswith("#/$defs/"), name

    assert "rate_limited" in tools["create_site"]["errors"]
    assert all(
        "rate_limited" not in tool["errors"]
        for name, tool in tools.items()
        if name != "create_site"
    )
    assert "capacity_reached" not in tools["create_measurement_event"]["errors"]


def test_scope_minimization_and_deterministic_mapping_snapshot(contract):
    snapshot = {}
    for name, tool in contract["tools"].items():
        if tool["side_effect"] == "read_only":
            assert tool["required_scopes"] == ["read"]
        else:
            assert tool["required_scopes"] == ["read", "write"]
        snapshot[name] = {
            "readOnlyHint": tool["side_effect"] == "read_only",
            "destructiveHint": tool["destructive"],
            "openWorldHint": tool["open_world"],
            "securitySchemes": [{"type": "oauth2", "scopes": tool["required_scopes"]}],
        }
    assert all(not row["openWorldHint"] for row in snapshot.values())
    assert snapshot["delete_site"] == {
        "readOnlyHint": False,
        "destructiveHint": True,
        "openWorldHint": False,
        "securitySchemes": [{"type": "oauth2", "scopes": ["read", "write"]}],
    }
    assert snapshot["render_tracking_setup"]["readOnlyHint"] is True


def test_service_public_surface_signatures_match_contract(contract):
    service_methods = {
        name
        for name, member in inspect.getmembers(SiteHitsService, inspect.isfunction)
        if not name.startswith("_")
    }
    assert service_methods == set(contract["tools"])
    for name, tool in contract["tools"].items():
        signature = inspect.signature(getattr(SiteHitsService, name))
        parameters = {
            parameter_name: parameter
            for parameter_name, parameter in signature.parameters.items()
            if parameter_name != "self"
        }
        schema_name = tool["input_schema"]["$ref"].rsplit("/", 1)[-1]
        input_schema = contract["$defs"][schema_name]
        assert set(parameters) == set(input_schema.get("properties", {})), name
        required = set(input_schema.get("required", []))
        assert {
            parameter_name
            for parameter_name, parameter in parameters.items()
            if parameter.default is inspect.Parameter.empty
        } == required, name
        for parameter_name, property_schema in input_schema.get("properties", {}).items():
            if "default" in property_schema:
                assert parameters[parameter_name].default == property_schema["default"], (
                    name,
                    parameter_name,
                )


def test_no_secret_or_internal_database_id_contract_surface(contract):
    serialized = json.dumps(contract).lower()
    assert "include_credentials" not in serialized
    assert "credentials:read" not in serialized
    for secret_name in ("access_token", "refresh_token", "authorization_code"):
        assert secret_name not in json.dumps(contract["$defs"])
    site_properties = contract["$defs"]["site"]["properties"]
    event_properties = contract["$defs"]["event_definition"]["properties"]
    assert "id" not in site_properties
    assert "id" not in event_properties
    assert contract["$defs"]["application_error"]["properties"]["request_id"] == {
        "type": "string",
        "pattern": "^[A-Za-z0-9_-]{1,64}$",
    }


def test_approval_is_single_agent_assertion_and_contract_change_is_split(contract):
    tools = contract["tools"]
    approval_tools = {
        "delete_site",
        "delete_measurement_event",
        "change_measurement_event_contract",
        "clear_activation",
    }
    for name, tool in tools.items():
        if name in approval_tools:
            assert tool["approval"]["confirmation_owner"] == "agent"
            assert tool["approval"]["input_field"] == "approval"
        else:
            assert tool["approval"] == {"policy": "none"}
    delete_schema = contract["$defs"]["delete_site_input"]
    assert "confirm_site_slug" not in delete_schema["properties"]
    metadata_schema = contract["$defs"]["update_measurement_event_input"]
    assert not {"aggregation", "unit", "approval"} & set(metadata_schema["properties"])
    contract_schema = contract["$defs"]["change_measurement_event_contract_input"]
    assert {"aggregation", "unit"} <= set(contract_schema["required"])
    assert "approval" in contract_schema["properties"]
    assert "approval" not in contract_schema["required"]


def test_every_tool_has_versioned_complete_service_vector(contract, vectors):
    manifest, by_id = vectors
    vector_schema = _load(VECTOR_SCHEMA_PATH)
    Draft202012Validator.check_schema(vector_schema)
    validator = Draft202012Validator(vector_schema)
    assert manifest["agent_contract_version"] == contract["agent_contract_version"]
    assert len(manifest["vectors"]) == len(contract["tools"])
    assert {document["tool"] for document in by_id.values()} == set(contract["tools"])
    expected_categories = set(vector_schema["properties"]["scenarios"]["required"])
    harness_source = (ROOT / manifest["execution_harness"]).read_text()
    for item in manifest["vectors"]:
        path = MANIFEST_PATH.parent / item["path"]
        payload = path.read_bytes()
        assert hashlib.sha256(payload).hexdigest() == item["sha256"]
        document = by_id[item["id"]]
        validator.validate(document)
        assert set(document["scenarios"]) == expected_categories
        assert set(document["execution_coverage"]) == expected_categories
        tool = contract["tools"][document["tool"]]
        input_validator = Draft202012Validator(
            {"$ref": tool["input_schema"]["$ref"], "$defs": contract["$defs"]}
        )
        for category, scenario in document["scenarios"].items():
            coverage = document["execution_coverage"][category]
            for node_id in coverage["tests"]:
                assert node_id.startswith(f"{manifest['execution_harness']}::")
                assert f"def {node_id.rsplit('::', 1)[-1]}(" in harness_source
            if not scenario["applicable"]:
                assert len(scenario["reason"]) >= 12
                assert coverage["mode"] in {
                    "shared_service_mechanism",
                    "contract_inapplicability",
                }
                continue
            assert coverage["mode"] != "contract_inapplicability"
            if category == "invalid_input":
                declared = set(
                    contract["$defs"][tool["input_schema"]["$ref"].rsplit("/", 1)[-1]].get(
                        "properties", {}
                    )
                )
                assert set(scenario["input"]) <= declared, document["tool"]
                if input_validator.is_valid(scenario["input"]):
                    assert "service_business_validation" in scenario["expected"].get(
                        "assertions", []
                    ), document["tool"]
            else:
                assert input_validator.is_valid(scenario["input"]), (
                    document["tool"],
                    category,
                    list(input_validator.iter_errors(scenario["input"])),
                )
            expected = scenario["expected"]
            if expected.get("error_code"):
                assert expected["error_code"] in tool["errors"]
            if expected.get("result_schema_ref"):
                assert expected["result_schema_ref"] == tool["output_schema"]["$ref"]
        assert document["defaults"]["audit"]["input_recording"] == "keyed_hash"
        assert document["defaults"]["audit"]["profile"] == tool["audit"]
        assert "database_id" in document["defaults"]["audit"]["forbidden_fields"]


def test_semver_diff_classifier_covers_referenced_schemas_and_semantic_roots(contract):
    module = _load_module("sitehits_semver_diff", ROOT / "agent" / "tools" / "semver_diff.py")

    changed = copy.deepcopy(contract)
    changed["tools"]["get_site"]["description"] += " Clarified."
    assert module.classify_contract_change(contract, changed)["classification"] == "patch"

    changed = copy.deepcopy(contract)
    changed["$defs"]["site"]["properties"]["optional_note"] = {"type": "string"}
    assert module.classify_contract_change(contract, changed)["classification"] == "major"

    changed = copy.deepcopy(contract)
    changed["$defs"]["site_lookup_input"]["properties"]["optional_note"] = {"type": "string"}
    assert module.classify_contract_change(contract, changed)["classification"] == "minor"

    changed = copy.deepcopy(contract)
    changed["$defs"]["list_sites_input"]["properties"]["include_inactive"]["default"] = True
    assert module.classify_contract_change(contract, changed)["classification"] == "major"

    changed = copy.deepcopy(contract)
    changed["$defs"]["site_lookup_input"]["required"].append("new_required")
    changed["$defs"]["site_lookup_input"]["properties"]["new_required"] = {"type": "string"}
    assert module.classify_contract_change(contract, changed)["classification"] == "major"

    changed = copy.deepcopy(contract)
    changed["$defs"]["site_lookup_input"]["required"].remove("site_slug")
    assert module.classify_contract_change(contract, changed)["classification"] == "minor"

    changed = copy.deepcopy(contract)
    changed["$defs"]["site"]["required"].remove("timezone")
    assert module.classify_contract_change(contract, changed)["classification"] == "major"

    changed = copy.deepcopy(contract)
    changed["$defs"]["period"]["enum"].remove("last90d")
    assert module.classify_contract_change(contract, changed)["classification"] == "major"

    changed = copy.deepcopy(contract)
    changed["$defs"]["application_error"]["properties"]["request_id"]["maxLength"] = 32
    assert module.classify_contract_change(contract, changed)["classification"] == "major"

    changed = copy.deepcopy(contract)
    changed["server_instructions"]["rules"][0]["text"] += " Normative change."
    assert module.classify_contract_change(contract, changed)["classification"] == "major"

    for root_field in (
        "identity",
        "limits",
        "authorization",
        "data_classifications",
        "audit_profiles",
        "retention",
        "compatibility",
    ):
        changed = copy.deepcopy(contract)
        changed[root_field]["test_semantic_change"] = True
        assert module.classify_contract_change(contract, changed)["classification"] == "major"


def test_release_descriptor_is_reproducible_and_content_addresses_bundle(tmp_path):
    module = _load_module(
        "sitehits_contract_release", ROOT / "agent" / "tools" / "contract_release.py"
    )
    commit = "0123456789abcdef0123456789abcdef01234567"
    first = module.build_descriptor(
        contract_path=CONTRACT_PATH,
        vector_manifest_path=MANIFEST_PATH,
        git_commit=commit,
    )
    second = module.build_descriptor(
        contract_path=CONTRACT_PATH,
        vector_manifest_path=MANIFEST_PATH,
        git_commit=commit,
    )
    assert first == second
    assert module.canonical_json(first) == module.canonical_json(second)
    assert first["git_commit"] == commit
    assert first["contract_sha256"] == hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest()
    assert first["conformance_vectors"] == sorted(first["conformance_vectors"])

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    manifest = _load(MANIFEST_PATH)
    (bundle / "manifest.json").write_bytes(MANIFEST_PATH.read_bytes())
    schema_target = bundle.parent / "vector.schema.json"
    schema_target.write_bytes(VECTOR_SCHEMA_PATH.read_bytes())
    (bundle / "vectors").mkdir()
    for item in manifest["vectors"]:
        source = MANIFEST_PATH.parent / item["path"]
        target = bundle / item["path"]
        target.write_bytes(source.read_bytes())
    target = bundle / manifest["vectors"][0]["path"]
    target.write_text(target.read_text() + "\n")
    with pytest.raises(ValueError, match="digest mismatch"):
        module.build_descriptor(
            contract_path=CONTRACT_PATH,
            vector_manifest_path=bundle / "manifest.json",
            git_commit=commit,
        )

    target.write_bytes((MANIFEST_PATH.parent / manifest["vectors"][0]["path"]).read_bytes())
    manifest["execution_harness"] = "tests/changed_harness.py"
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    changed_manifest = module.build_descriptor(
        contract_path=CONTRACT_PATH,
        vector_manifest_path=bundle / "manifest.json",
        git_commit=commit,
    )
    assert changed_manifest["conformance_vectors_sha256"] != first["conformance_vectors_sha256"]

    (bundle / "manifest.json").write_bytes(MANIFEST_PATH.read_bytes())
    schema_target.write_text(schema_target.read_text() + "\n")
    changed_schema = module.build_descriptor(
        contract_path=CONTRACT_PATH,
        vector_manifest_path=bundle / "manifest.json",
        git_commit=commit,
    )
    assert changed_schema["conformance_vectors_sha256"] != first["conformance_vectors_sha256"]

    schema_target.write_bytes(VECTOR_SCHEMA_PATH.read_bytes())
    target.unlink()
    with pytest.raises(ValueError, match="Missing declared vector"):
        module.build_descriptor(
            contract_path=CONTRACT_PATH,
            vector_manifest_path=bundle / "manifest.json",
            git_commit=commit,
        )

    target.write_bytes((MANIFEST_PATH.parent / manifest["vectors"][0]["path"]).read_bytes())
    rogue = bundle / "vectors" / "undeclared.json"
    rogue.write_text("{}\n")
    with pytest.raises(ValueError, match="Undeclared vector"):
        module.build_descriptor(
            contract_path=CONTRACT_PATH,
            vector_manifest_path=bundle / "manifest.json",
            git_commit=commit,
        )
    rogue.unlink()

    escaped_manifest = _load(MANIFEST_PATH)
    escaped_manifest["vectors"][0]["path"] = "../outside.json"
    (bundle / "manifest.json").write_text(json.dumps(escaped_manifest))
    with pytest.raises(ValueError, match="Invalid or duplicate vector path"):
        module.build_descriptor(
            contract_path=CONTRACT_PATH,
            vector_manifest_path=bundle / "manifest.json",
            git_commit=commit,
        )


def test_release_sources_use_annotated_tag_payload_and_digest_resolution():
    sources = _load(SOURCES_PATH)
    agent_contract = sources["descriptors"]["agent_contract"]
    assert agent_contract["authoritative_store"] == "git_annotated_tag_payload"
    assert agent_contract["immutable_ref_pattern"] == "agent-contract-v{agent_contract_version}"
    assert "tag target" in agent_contract["consumer_resolution"]
    assert "digest" in agent_contract["consumer_resolution"]
