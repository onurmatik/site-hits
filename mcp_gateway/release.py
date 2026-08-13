"""Deterministic Stage 1 MCP release descriptor generation.

The generator is deliberately side-effect free unless ``--output`` is supplied.
Production callers must provide the exact native source-tree identity and GitHub
Release smoke evidence; the repository does not contain a placeholder
``mcp-release.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_contract_identity import (
    PINNED_AGENT_CONTRACT_DESCRIPTOR_SHA256,
    PINNED_AGENT_CONTRACT_SHA256,
)

from .registry import (
    build_deployment_registry,
    canonical_registry_json,
    registry_sha256,
)
from .release_identity import SERVER_VERSION

ROOT = Path(__file__).resolve().parents[1]
ISSUER = "https://sitehits.io"
RESOURCE = "https://sitehits.io/mcp"
DEFAULT_CLIENT_COMPATIBILITY_PATH = (
    Path(__file__).resolve().parents[1] / "integration" / "client-compatibility.yaml"
)
REQUIRED_ACCEPTANCE_CLIENTS = (
    "ChatGPT",
    "Codex",
    "Claude/Claude Desktop",
    "Claude Code",
)
REQUIRED_DIAGNOSTIC_CLIENTS = ("MCP Inspector",)
REQUIRED_SMOKE_FLOWS = {
    "discovery",
    "oauth",
    "initialize",
    "tools/list",
    "get_account_capabilities",
    "refresh",
    "revoke",
    "audit",
    "cleanup",
    "rollback",
}

SEMVER_PATTERN = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
UTC_TIMESTAMP_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z$"
)
EVIDENCE_URI_PATTERN = re.compile(
    r"^https://github\.com/onurmatik/site-hits/releases/download/[^/]+/[^/]+$"
)


def _load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict):
        raise TypeError(f"Expected a JSON object in {path}")
    return value


def _digest_bytes(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _framed_tree_digest(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(
                candidate
                for candidate in path.rglob("*")
                if candidate.is_file()
                and "__pycache__" not in candidate.parts
                and candidate.suffix != ".pyc"
                and candidate.name != ".credentials.env"
            )
        elif path.is_file():
            files.append(path)
    for path in sorted(set(files), key=lambda item: item.relative_to(ROOT).as_posix()):
        relative = path.relative_to(ROOT).as_posix().encode()
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def dependency_lock_digest() -> str:
    return _digest_bytes((ROOT / "uv.lock").read_bytes())


def deploy_contract_digest() -> str:
    return _framed_tree_digest(
        [
            ROOT / ".deploy",
            ROOT / "deploy" / "nginx",
            ROOT / "deploy" / "systemd",
            ROOT / "deploy" / "send-mcp-alert.py",
            ROOT / "scripts" / "install_sitehits_mcp_nginx.py",
            ROOT / "scripts" / "start.sh",
        ]
    )


def canonical_json(value: object) -> str:
    """Return the single canonical serialization used by release digests."""

    return json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"


def canonical_tool_registry(contract: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the exact registry payload shared with the runtime server."""

    registry = build_deployment_registry(contract=contract)
    return json.loads(canonical_registry_json(registry))


def canonical_tool_registry_digest(contract: dict[str, Any]) -> str:
    registry = build_deployment_registry(contract=contract)
    return f"sha256:{registry_sha256(registry)}"


def _validate_client_compatibility(
    matrix: dict[str, Any],
) -> list[dict[str, str]]:
    required_matrix_keys = {
        "schema_version",
        "matrix_version",
        "registration_policy",
        "clients",
    }
    if set(matrix) != required_matrix_keys:
        missing = sorted(required_matrix_keys - set(matrix))
        unexpected = sorted(set(matrix) - required_matrix_keys)
        raise ValueError(
            "Client compatibility keys differ; "
            f"missing={missing}, unexpected={unexpected}"
        )
    if matrix["schema_version"] != 1:
        raise ValueError("Client compatibility schema_version must be 1")
    if not isinstance(matrix["matrix_version"], str) or not SEMVER_PATTERN.fullmatch(
        matrix["matrix_version"]
    ):
        raise ValueError("Client compatibility matrix_version must be SemVer")
    policy = matrix["registration_policy"]
    policy_keys = {
        "preferred",
        "fallback",
        "owner_role",
        "review_interval_days",
        "review_triggers",
        "removal_evidence",
    }
    if not isinstance(policy, dict) or set(policy) != policy_keys:
        raise ValueError("Client compatibility registration_policy is not canonical")
    if (
        policy["preferred"] != "cimd"
        or policy["fallback"] != "dcr"
        or policy["owner_role"] != "repository-maintainer"
        or policy["review_interval_days"] != 90
    ):
        raise ValueError("Client compatibility must be CIMD-first with managed DCR fallback")
    expected_triggers = {
        "mcp-authorization-spec-change",
        "required-client-registration-or-callback-change",
        "public-plugin-submission",
        "oauth-security-incident",
    }
    if (
        not isinstance(policy["review_triggers"], list)
        or set(policy["review_triggers"]) != expected_triggers
        or len(policy["review_triggers"]) != len(expected_triggers)
    ):
        raise ValueError("Client compatibility DCR review triggers are incomplete")
    removal = policy["removal_evidence"]
    if not isinstance(removal, dict) or set(removal) != {
        "all_supported_clients_non_dcr",
        "successful_dcr_usage_zero_days",
        "minimum_zero_use_days",
        "status",
    }:
        raise ValueError("Client compatibility DCR removal evidence is not canonical")
    if (
        not isinstance(removal["all_supported_clients_non_dcr"], bool)
        or not isinstance(removal["successful_dcr_usage_zero_days"], int)
        or removal["successful_dcr_usage_zero_days"] < 0
        or removal["minimum_zero_use_days"] != 90
        or removal["status"] not in {"not-eligible", "eligible"}
    ):
        raise ValueError("Client compatibility DCR removal evidence is invalid")

    record_keys = {
        "client",
        "display_name",
        "surface",
        "tier",
        "tested_version",
        "transport",
        "registration_method",
        "fallback_registration_method",
        "callback_profile",
        "support_status",
        "tested_at",
        "evidence",
        "release_acceptance",
    }
    records = matrix["clients"]
    if not isinstance(records, list) or not records:
        raise ValueError("Client compatibility clients must be a non-empty array")
    for record in records:
        if not isinstance(record, dict) or set(record) != record_keys:
            raise ValueError("Each client compatibility record must use the canonical fields")
        nullable_evidence_fields = {"tested_version", "tested_at", "evidence"}
        for field in record_keys - nullable_evidence_fields:
            if not isinstance(record[field], str) or not record[field]:
                raise ValueError(f"Client compatibility {field} must be a non-empty string")
        evidence_values = tuple(record[field] for field in nullable_evidence_fields)
        if any(value is not None for value in evidence_values) and not all(
            isinstance(value, str) and value for value in evidence_values
        ):
            raise ValueError("Client compatibility evidence fields must be all null or all set")
        if record["transport"] != "streamable-http":
            raise ValueError("Every Stage 1 client must use Streamable HTTP")
        if (
            record["registration_method"] != "cimd"
            or record["fallback_registration_method"] != "dcr"
        ):
            raise ValueError("Every Stage 1 client must declare CIMD and DCR acceptance")
        if record["tier"] not in {"primary", "cross-agent", "diagnostic"}:
            raise ValueError("Client compatibility tier is unsupported")
        if record["support_status"] not in {
            "pending-acceptance",
            "pending-diagnostic",
            "supported",
            "experimental",
            "excluded",
        }:
            raise ValueError("Client compatibility support_status is unsupported")
        if record["release_acceptance"] not in {"required", "diagnostic"}:
            raise ValueError("Client compatibility release_acceptance is unsupported")

    slugs = [record["client"] for record in records]
    names = [record["display_name"] for record in records]
    if len(slugs) != len(set(slugs)) or len(names) != len(set(names)):
        raise ValueError("Client compatibility client identifiers must be unique")
    acceptance = tuple(
        record["display_name"]
        for record in records
        if record["release_acceptance"] == "required"
    )
    diagnostics = tuple(
        record["display_name"]
        for record in records
        if record["release_acceptance"] == "diagnostic"
    )
    if acceptance != REQUIRED_ACCEPTANCE_CLIENTS:
        raise ValueError(
            "Client compatibility must require the primary and cross-agent baseline in order"
        )
    if diagnostics != REQUIRED_DIAGNOSTIC_CLIENTS:
        raise ValueError("Client compatibility must retain MCP Inspector as diagnostic")
    return records


def _validate_smoke_evidence(
    evidence: dict[str, Any],
    *,
    server_version: str,
    git_commit: str,
    source_tree_sha256: str,
    registry_digest: str,
    client_compatibility_digest: str,
    client_records: list[dict[str, str]],
) -> tuple[dict[str, str], list[dict[str, str]]]:
    required = {
        "clients",
        "diagnostic_clients",
        "flows",
        "git_commit",
        "source_tree_sha256",
        "issuer",
        "resource",
        "tool_registry_sha256",
        "client_compatibility_sha256",
        "tested_at",
        "evidence_uri",
    }
    if set(evidence) != required:
        missing = sorted(required - set(evidence))
        unexpected = sorted(set(evidence) - required)
        raise ValueError(f"Smoke evidence keys differ; missing={missing}, unexpected={unexpected}")
    clients = evidence["clients"]
    diagnostics = evidence["diagnostic_clients"]
    if not isinstance(clients, dict) or set(clients) != set(REQUIRED_ACCEPTANCE_CLIENTS):
        raise ValueError(
            "Smoke evidence must cover the exact primary and cross-agent baseline"
        )
    if not isinstance(diagnostics, dict) or set(diagnostics) != set(REQUIRED_DIAGNOSTIC_CLIENTS):
        raise ValueError("MCP Inspector must be recorded as a diagnostic client")
    records_by_name = {record["display_name"]: record for record in client_records}
    evidence_record_keys = {
        "tested_version",
        "registration_method",
        "registration_status",
        "fallback_registration_method",
        "fallback_status",
    }
    for name, client_evidence in {**clients, **diagnostics}.items():
        if not isinstance(client_evidence, dict) or set(client_evidence) != evidence_record_keys:
            raise ValueError(f"Smoke client {name} evidence is not canonical")
        matrix_record = records_by_name[name]
        diagnostic = matrix_record["release_acceptance"] == "diagnostic"
        expected_status = "diagnostic-passed" if diagnostic else "passed"
        if (
            not isinstance(client_evidence["tested_version"], str)
            or not client_evidence["tested_version"].strip()
            or client_evidence["registration_method"] != matrix_record["registration_method"]
            or client_evidence["registration_status"] != expected_status
            or client_evidence["fallback_registration_method"]
            != matrix_record["fallback_registration_method"]
            or client_evidence["fallback_status"] != expected_status
        ):
            raise ValueError(
                f"Smoke client {name} must pass exact CIMD and DCR fallback acceptance"
            )
    flows = evidence["flows"]
    if (
        not isinstance(flows, list)
        or len(flows) != len(set(flows))
        or set(flows) != REQUIRED_SMOKE_FLOWS
    ):
        raise ValueError("Smoke evidence does not cover the required Stage 1 flows")
    exact_values = {
        "git_commit": git_commit,
        "source_tree_sha256": source_tree_sha256,
        "issuer": ISSUER,
        "resource": RESOURCE,
        "tool_registry_sha256": registry_digest,
        "client_compatibility_sha256": client_compatibility_digest,
    }
    for field, expected in exact_values.items():
        if evidence[field] != expected:
            raise ValueError(f"Smoke evidence {field} does not match the release input")
    tested_at = evidence["tested_at"]
    if not isinstance(tested_at, str) or not UTC_TIMESTAMP_PATTERN.fullmatch(tested_at):
        raise ValueError("Smoke tested_at must be an RFC 3339 UTC timestamp ending in Z")
    datetime.fromisoformat(tested_at.removesuffix("Z") + "+00:00")
    evidence_uri = evidence["evidence_uri"]
    if not isinstance(evidence_uri, str) or not EVIDENCE_URI_PATTERN.fullmatch(evidence_uri):
        raise ValueError("Smoke evidence_uri must be an immutable GitHub Release asset URL")
    release_prefix = (
        "https://github.com/onurmatik/site-hits/releases/download/"
        f"sitehits-mcp-v{server_version}/"
    )
    if not evidence_uri.startswith(release_prefix):
        raise ValueError("Smoke evidence_uri must use the server version's immutable release tag")
    client = "; ".join(
        f"{name} {clients[name]['tested_version']}"
        for name in REQUIRED_ACCEPTANCE_CLIENTS
    )
    smoke = {
        "client": client,
        "tested_at": tested_at,
        "evidence_uri": evidence_uri,
    }
    all_evidence = {**clients, **diagnostics}
    acceptance = [
        {
            "client": record["client"],
            "surface": record["surface"],
            "tier": record["tier"],
            "tested_version": all_evidence[record["display_name"]]["tested_version"],
            "transport": record["transport"],
            "registration_method": record["registration_method"],
            "fallback_registration_method": record["fallback_registration_method"],
            "fallback_status": all_evidence[record["display_name"]]["fallback_status"],
            "callback_profile": record["callback_profile"],
            "status": all_evidence[record["display_name"]]["registration_status"],
            "tested_at": tested_at,
            "evidence_uri": evidence_uri,
        }
        for record in client_records
    ]
    return smoke, acceptance


def build_descriptor(
    *,
    server_version: str,
    contract_path: str | Path,
    contract_descriptor_path: str | Path,
    git_commit: str,
    source_tree_sha256: str,
    smoke_evidence_path: str | Path,
    client_compatibility_path: str | Path = DEFAULT_CLIENT_COMPATIBILITY_PATH,
) -> dict[str, Any]:
    """Build a sealed descriptor from immutable release evidence."""

    if not SEMVER_PATTERN.fullmatch(server_version):
        raise ValueError("server_version must be SemVer")
    if server_version != SERVER_VERSION:
        raise ValueError("server_version must match the deployed native runtime")
    if not COMMIT_PATTERN.fullmatch(git_commit):
        raise ValueError("git_commit must be a lowercase 40-character SHA-1 object ID")
    if not DIGEST_PATTERN.fullmatch(source_tree_sha256):
        raise ValueError("source_tree_sha256 must use sha256:<64 lowercase hex>")

    contract_path = Path(contract_path)
    contract_descriptor_path = Path(contract_descriptor_path)
    contract = _load_json(contract_path)
    upstream = _load_json(contract_descriptor_path)
    contract_digest = hashlib.sha256(contract_path.read_bytes()).hexdigest()
    descriptor_digest = hashlib.sha256(contract_descriptor_path.read_bytes()).hexdigest()
    if contract_digest != PINNED_AGENT_CONTRACT_SHA256:
        raise ValueError("Contract content is not the pinned Agent Contract release")
    if descriptor_digest != PINNED_AGENT_CONTRACT_DESCRIPTOR_SHA256:
        raise ValueError("Contract descriptor is not the pinned immutable artifact")
    if upstream.get("agent_contract_version") != contract.get("agent_contract_version"):
        raise ValueError("Contract and upstream descriptor versions do not match")
    if upstream.get("contract_sha256") != contract_digest:
        raise ValueError("Contract content does not match the upstream descriptor")
    registry_digest = canonical_tool_registry_digest(contract)
    client_compatibility_path = Path(client_compatibility_path)
    client_compatibility_bytes = client_compatibility_path.read_bytes()
    client_compatibility = json.loads(client_compatibility_bytes)
    if not isinstance(client_compatibility, dict):
        raise TypeError(f"Expected a JSON object in {client_compatibility_path}")
    client_records = _validate_client_compatibility(client_compatibility)
    client_compatibility_digest = _digest_bytes(client_compatibility_bytes)
    evidence_path = Path(smoke_evidence_path)
    evidence_bytes = evidence_path.read_bytes()
    evidence = json.loads(evidence_bytes)
    if not isinstance(evidence, dict):
        raise TypeError(f"Expected a JSON object in {evidence_path}")
    smoke, client_acceptance = _validate_smoke_evidence(
        evidence,
        server_version=server_version,
        git_commit=git_commit,
        source_tree_sha256=source_tree_sha256,
        registry_digest=registry_digest,
        client_compatibility_digest=client_compatibility_digest,
        client_records=client_records,
    )
    evidence_digest = _digest_bytes(evidence_bytes)
    smoke["evidence_sha256"] = evidence_digest
    for record in client_acceptance:
        record["evidence_sha256"] = evidence_digest
    version = contract["agent_contract_version"]
    return {
        "schema_version": 1,
        "server_version": server_version,
        "agent_contract": {
            "agent_contract_version": version,
            "descriptor_sha256": f"sha256:{descriptor_digest}",
            "contract_sha256": f"sha256:{contract_digest}",
            "supported_versions": [version],
        },
        "git_commit": git_commit,
        "source_tree_sha256": source_tree_sha256,
        "dependency_lock_sha256": dependency_lock_digest(),
        "deploy_contract_sha256": deploy_contract_digest(),
        "tool_registry_sha256": registry_digest,
        "client_compatibility_sha256": client_compatibility_digest,
        "oauth": {"issuer": ISSUER, "resource": RESOURCE},
        "client_acceptance": client_acceptance,
        "smoke": smoke,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-version", required=True)
    parser.add_argument("--contract", default="agent/contract.yaml")
    parser.add_argument("--contract-descriptor", default="release/contract-release.json")
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--source-tree-sha256", required=True)
    parser.add_argument("--smoke-evidence", required=True)
    parser.add_argument(
        "--client-compatibility",
        default=str(DEFAULT_CLIENT_COMPATIBILITY_PATH),
    )
    parser.add_argument("--output")
    args = parser.parse_args()
    rendered = canonical_json(
        build_descriptor(
            server_version=args.server_version,
            contract_path=args.contract,
            contract_descriptor_path=args.contract_descriptor,
            git_commit=args.git_commit,
            source_tree_sha256=args.source_tree_sha256,
            smoke_evidence_path=args.smoke_evidence,
            client_compatibility_path=args.client_compatibility,
        )
    )
    if args.output:
        Path(args.output).write_text(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
