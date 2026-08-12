"""Canonical Agent Contract access for the transport-neutral runtime."""

import json
from dataclasses import dataclass
from functools import cache, lru_cache
from pathlib import Path
from typing import Any

from django.conf import settings
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError as JSONSchemaValidationError

from .errors import APPLICATION_ERROR_CODES, invalid_input

SUPPORTED_AGENT_CONTRACT_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class ToolContract:
    name: str
    required_scopes: tuple[str, ...]
    required_capability: str | None
    ownership: str
    resource_type: str
    approval_required: bool
    approval_action: str | None
    approval_resource_id_template: str | None
    limit_name: str | None
    declared_errors: frozenset[str]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


@lru_cache(maxsize=1)
def load_contract() -> dict[str, Any]:
    path = Path(settings.BASE_DIR) / "agent" / "contract.yaml"
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("The canonical Agent Contract could not be loaded.") from exc
    if contract.get("agent_contract_version") != SUPPORTED_AGENT_CONTRACT_VERSION:
        raise RuntimeError("The Agent Contract version is not supported by this runtime.")
    schema_path = path.with_name("contract.schema.json")
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(contract)
    except (OSError, json.JSONDecodeError, JSONSchemaValidationError) as exc:
        raise RuntimeError("The canonical Agent Contract does not satisfy its schema.") from exc
    if set(contract["error_codes"]) != APPLICATION_ERROR_CODES:
        raise RuntimeError("Runtime application error codes differ from the Agent Contract.")
    return contract


def contract_version() -> str:
    return str(load_contract()["agent_contract_version"])


def audit_retention_days() -> int:
    return int(load_contract()["retention"]["audit_days"])


def idempotency_retention_days() -> int:
    return int(load_contract()["retention"]["idempotency_records"]["create_site_days"])


def _schema(document: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **schema,
        "$defs": document["$defs"],
    }


@cache
def get_tool_contract(tool_name: str) -> ToolContract:
    contract = load_contract()
    try:
        tool = contract["tools"][tool_name]
    except KeyError as exc:
        raise RuntimeError(f"Unknown canonical Agent Contract tool: {tool_name}") from exc
    capabilities = tuple(tool["required_capabilities"])
    if len(capabilities) > 1:
        raise RuntimeError("The runtime supports at most one product capability per tool.")
    approval = tool["approval"]
    approval_required = approval["policy"] == "explicit_intent"

    # The current Contract has one capacity resource. Derive its enforcement point
    # from canonical tool semantics rather than maintaining a second tool-name map.
    limit_names = tuple(contract["limits"])
    is_capacity_creation = (
        tool["resource_type"] == "site"
        and tool["side_effect"] == "reversible_write"
        and tool["idempotency"]["mode"] == "key"
    )
    limit_name = limit_names[0] if is_capacity_creation and len(limit_names) == 1 else None
    if limit_name is not None and "capacity_reached" not in tool["errors"]:
        raise RuntimeError("A capacity-enforced tool must declare capacity_reached.")

    return ToolContract(
        name=tool_name,
        required_scopes=tuple(tool["required_scopes"]),
        required_capability=capabilities[0] if capabilities else None,
        ownership=tool["ownership"],
        resource_type=tool["resource_type"],
        approval_required=approval_required,
        approval_action=approval.get("action") if approval_required else None,
        approval_resource_id_template=(
            approval.get("resource_id_template") if approval_required else None
        ),
        limit_name=limit_name,
        declared_errors=frozenset(tool["errors"]),
        input_schema=_schema(contract, tool["input_schema"]),
        output_schema=_schema(contract, tool["output_schema"]),
    )


def validate_tool_input(spec: ToolContract, value: dict[str, object]) -> None:
    validator = Draft202012Validator(spec.input_schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(value), key=lambda item: list(item.absolute_path))
    if not errors:
        return
    error = errors[0]
    field = ".".join(str(part) for part in error.absolute_path) or "$"
    raise invalid_input(
        "The tool input does not match the Agent Contract.",
        fields={field: ["Value does not satisfy the declared schema."]},
    )


def validate_tool_output(spec: ToolContract, value: object) -> None:
    validator = Draft202012Validator(spec.output_schema, format_checker=FormatChecker())
    if next(validator.iter_errors(value), None) is not None:
        # Output validation failures are implementation faults. Never expose schema,
        # provider, or returned-data details to the caller.
        raise RuntimeError("The operation returned an invalid Contract result.")


def clear_contract_caches() -> None:
    """Test/support hook for deterministic Contract drift checks."""

    get_tool_contract.cache_clear()
    load_contract.cache_clear()
