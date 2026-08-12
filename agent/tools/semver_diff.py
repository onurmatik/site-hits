"""Classify semantic changes between two canonical SiteHits Agent Contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

MAJOR_ROOT_FIELDS = {
    "product",
    "identity",
    "scopes",
    "capabilities",
    "limits",
    "resources",
    "bootstrap",
    "authorization",
    "error_envelope",
    "data_classifications",
    "audit_profiles",
    "retention",
    "mapping",
    "compatibility",
}
TOOL_BREAKING_FIELDS = {
    "authentication",
    "required_scopes",
    "required_capabilities",
    "resource_type",
    "ownership",
    "side_effect",
    "destructive",
    "open_world",
    "approval",
    "idempotency",
    "errors",
    "data",
    "audit",
    "exposure",
}


def load_contract(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text())


def _resolve_schema(schema: Any, contract: dict[str, Any], seen: tuple[str, ...] = ()) -> Any:
    if not isinstance(schema, dict):
        if isinstance(schema, list):
            return [_resolve_schema(item, contract, seen) for item in schema]
        return schema
    reference = schema.get("$ref")
    if reference and reference.startswith("#/$defs/"):
        name = reference.rsplit("/", 1)[-1]
        if name in seen:
            return {"$ref": reference}
        return _resolve_schema(contract["$defs"][name], contract, (*seen, name))
    return {
        key: _resolve_schema(value, contract, seen)
        for key, value in schema.items()
        if key not in {"title", "description"}
    }


def _schema_change(old: dict[str, Any], new: dict[str, Any], *, direction: str) -> str:
    if old == new:
        return "none"
    rank = {"none": 0, "minor": 1, "major": 2}
    levels: list[str] = []
    if old.get("type") != new.get("type"):
        return "major"
    if old.get("enum") != new.get("enum"):
        return "major"
    if old.get("const") != new.get("const"):
        return "major"
    if old.get("default") != new.get("default"):
        return "major"
    old_required = set(old.get("required", []))
    new_required = set(new.get("required", []))
    if new_required - old_required:
        return "major"
    if old_required - new_required:
        if direction == "output":
            return "major"
        levels.append("minor")
    old_properties = old.get("properties", {})
    new_properties = new.get("properties", {})
    if set(old_properties) - set(new_properties):
        return "major"
    if new.get("additionalProperties") is False and old.get("additionalProperties") is not False:
        return "major"
    levels.extend(
        _schema_change(old_properties[name], new_properties[name], direction=direction)
        for name in set(old_properties) & set(new_properties)
    )
    added_properties = set(new_properties) - set(old_properties)
    if added_properties and direction == "output" and old.get("additionalProperties") is False:
        return "major"
    if added_properties:
        levels.append("minor")
    if "items" in old or "items" in new:
        if "items" not in old or "items" not in new:
            return "major"
        levels.append(_schema_change(old["items"], new["items"], direction=direction))
    for combinator in ("oneOf", "anyOf", "allOf"):
        if old.get(combinator) != new.get(combinator):
            return "major"
    ignored = {"properties", "required", "items", "title", "description"}
    if {key: value for key, value in old.items() if key not in ignored} != {
        key: value for key, value in new.items() if key not in ignored
    }:
        return "major"
    return max(levels or ["none"], key=rank.__getitem__)


def classify_contract_change(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    changes: list[str] = []
    level = "none"
    rank = {"none": 0, "patch": 1, "minor": 2, "major": 3}

    def promote(next_level: str, message: str) -> None:
        nonlocal level
        changes.append(message)
        if rank[next_level] > rank[level]:
            level = next_level

    old_tools = old.get("tools", {})
    new_tools = new.get("tools", {})
    for name in sorted(set(old_tools) - set(new_tools)):
        promote("major", f"removed tool: {name}")
    for name in sorted(set(new_tools) - set(old_tools)):
        promote("minor", f"added tool: {name}")
    for name in sorted(set(old_tools) & set(new_tools)):
        before = old_tools[name]
        after = new_tools[name]
        for field in sorted(TOOL_BREAKING_FIELDS):
            if before.get(field) != after.get(field):
                promote("major", f"changed {name}.{field}")
        for field in ("input_schema", "output_schema"):
            schema_level = _schema_change(
                _resolve_schema(before[field], old),
                _resolve_schema(after[field], new),
                direction="input" if field == "input_schema" else "output",
            )
            if schema_level != "none":
                promote(schema_level, f"changed {name}.{field}")
        for field in ("title", "description"):
            if before.get(field) != after.get(field):
                promote("patch", f"changed {name}.{field}")

    old_errors = old.get("error_codes", {})
    new_errors = new.get("error_codes", {})
    if set(old_errors) - set(new_errors):
        promote("major", "removed application error code")
    for code in sorted(set(old_errors) & set(new_errors)):
        if old_errors[code] != new_errors[code]:
            promote("major", f"changed error meaning: {code}")
    if set(new_errors) - set(old_errors):
        promote("minor", "added application error code")

    for field in sorted(MAJOR_ROOT_FIELDS):
        if old.get(field) != new.get(field):
            promote("major", f"changed root semantic field: {field}")
    if _resolve_schema(old.get("error_envelope", {}), old) != _resolve_schema(
        new.get("error_envelope", {}), new
    ):
        promote("major", "changed reachable application error envelope schema")
    old_instructions = old.get("server_instructions", {})
    new_instructions = new.get("server_instructions", {})
    if old_instructions.get("rules") != new_instructions.get("rules"):
        promote("major", "changed normative server instructions")
    elif old_instructions.get("summary") != new_instructions.get("summary"):
        promote("patch", "changed server instruction summary")

    return {"classification": level, "changes": changes}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("old")
    parser.add_argument("new")
    args = parser.parse_args()
    print(
        json.dumps(
            classify_contract_change(load_contract(args.old), load_contract(args.new)), indent=2
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
