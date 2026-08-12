"""Deterministic MCP tool registry derived from the canonical Agent Contract."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from mcp.types import Tool, ToolAnnotations

TRACKING_SETUP_RESOURCE_URI = "ui://sitehits/tracking-setup-v1.html"


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    security_schemes: tuple[dict[str, Any], ...]
    annotations: ToolAnnotations
    exposure: str
    authentication: str
    required_scopes: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    ownership: str
    resource_type: str
    meta: dict[str, Any]

    def to_mcp_tool(self) -> Tool:
        security = deepcopy(list(self.security_schemes))
        meta = deepcopy(self.meta)
        # MCP's extension-safe metadata carries the same security declaration for
        # hosts whose protocol model does not yet have a top-level extension field.
        meta["securitySchemes"] = security
        return Tool(
            name=self.name,
            title=self.title,
            description=self.description,
            inputSchema=deepcopy(self.input_schema),
            outputSchema=deepcopy(self.output_schema),
            annotations=self.annotations.model_copy(deep=True),
            _meta=meta or None,
        )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": deepcopy(self.input_schema),
            "outputSchema": deepcopy(self.output_schema),
            "securitySchemes": deepcopy(list(self.security_schemes)),
            "annotations": self.annotations.model_dump(
                by_alias=True,
                mode="json",
                exclude_none=True,
            ),
            "exposure": self.exposure,
            "authentication": self.authentication,
            "requiredCapabilities": list(self.required_capabilities),
            "ownership": self.ownership,
            "resourceType": self.resource_type,
            "_meta": deepcopy(self.meta),
        }


def _expanded_schema(contract: Mapping[str, Any], schema: Mapping[str, Any]) -> dict[str, Any]:
    definitions = deepcopy(contract["$defs"])
    reference = schema.get("$ref")
    if set(schema) == {"$ref"} and isinstance(reference, str) and reference.startswith("#/$defs/"):
        root_name = reference.removeprefix("#/$defs/")
        try:
            root = deepcopy(definitions[root_name])
        except KeyError as exc:
            raise RuntimeError(f"Unresolved Agent Contract schema reference: {reference}") from exc
        return {**root, "$defs": definitions}
    return {**deepcopy(dict(schema)), "$defs": definitions}


def _idempotent_hint(tool: Mapping[str, Any]) -> bool:
    side_effect = tool["side_effect"]
    mode = tool["idempotency"]["mode"]
    if side_effect == "read_only":
        if mode != "not_required":
            raise RuntimeError("Read-only tools must use not_required idempotency mode.")
        return True
    if mode in {"key", "natural_key", "optimistic_revision"}:
        return True
    if mode == "not_required":
        return False
    raise RuntimeError(f"Unknown Agent Contract idempotency mode: {mode}")


def contract_annotations(tool: Mapping[str, Any]) -> ToolAnnotations:
    return ToolAnnotations(
        readOnlyHint=tool["side_effect"] == "read_only",
        destructiveHint=tool["destructive"],
        idempotentHint=_idempotent_hint(tool),
        openWorldHint=tool["open_world"],
    )


def build_registry(
    *,
    contract: Mapping[str, Any] | None = None,
    metadata_by_name: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[RegistryEntry, ...]:
    if contract is None:
        from agent_runtime.contract import load_contract

        document = load_contract()
    else:
        document = contract
    metadata = metadata_by_name or {}
    declared_names = set(document["tools"])
    unknown_metadata = set(metadata) - declared_names
    if unknown_metadata:
        raise RuntimeError(
            "Deployment metadata names undeclared Agent Contract tools: "
            + ", ".join(sorted(unknown_metadata))
        )

    normalized_names: dict[str, str] = {}
    entries: list[RegistryEntry] = []
    for name, tool in document["tools"].items():
        normalized = unicodedata.normalize("NFKC", name).casefold()
        previous = normalized_names.setdefault(normalized, name)
        if previous != name:
            raise RuntimeError(f"Normalized MCP tool-name collision: {previous}, {name}")

        authentication = tool["authentication"]
        required_scopes = tuple(tool["required_scopes"])
        if authentication == "none":
            if required_scopes:
                raise RuntimeError(f"Unauthenticated tool declares OAuth scopes: {name}")
            security: tuple[dict[str, Any], ...] = ()
        elif authentication == "required":
            if not required_scopes:
                raise RuntimeError(f"Authenticated tool has no OAuth scopes: {name}")
            security = ({"type": "oauth2", "scopes": list(required_scopes)},)
        else:
            raise RuntimeError(f"Unknown authentication policy for {name}: {authentication}")

        entries.append(
            RegistryEntry(
                name=name,
                title=tool["title"],
                description=tool["description"],
                input_schema=_expanded_schema(document, tool["input_schema"]),
                output_schema=_expanded_schema(document, tool["output_schema"]),
                security_schemes=security,
                annotations=contract_annotations(tool),
                exposure=tool["exposure"],
                authentication=authentication,
                required_scopes=required_scopes,
                required_capabilities=tuple(tool["required_capabilities"]),
                ownership=tool["ownership"],
                resource_type=tool["resource_type"],
                meta=deepcopy(dict(metadata.get(name, {}))),
            )
        )

    return tuple(sorted(entries, key=lambda entry: entry.name))


def deployment_tool_metadata() -> dict[str, dict[str, Any]]:
    """Return deterministic, environment-neutral metadata included in the release registry."""

    return {
        "render_tracking_setup": {
            "ui": {"resourceUri": TRACKING_SETUP_RESOURCE_URI},
            "openai/outputTemplate": TRACKING_SETUP_RESOURCE_URI,
            "openai/toolInvocation/invoking": "Preparing SiteHits tracking setup",
            "openai/toolInvocation/invoked": "SiteHits tracking setup is ready",
        }
    }


def build_deployment_registry(
    *,
    contract: Mapping[str, Any] | None = None,
) -> tuple[RegistryEntry, ...]:
    return build_registry(
        contract=contract,
        metadata_by_name=deployment_tool_metadata(),
    )


def canonical_registry_json(registry: tuple[RegistryEntry, ...]) -> str:
    payload = [entry.canonical_payload() for entry in registry]
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def registry_sha256(registry: tuple[RegistryEntry, ...]) -> str:
    return hashlib.sha256(canonical_registry_json(registry).encode("utf-8")).hexdigest()
