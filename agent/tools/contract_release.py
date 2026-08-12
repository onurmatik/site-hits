"""Generate the canonical annotated-tag payload for an Agent Contract release.

The generated JSON is used verbatim as the annotated ``agent-contract-vX.Y.Z``
tag message. The tag targets ``git_commit``; consumers verify the target and
digests, and may materialize the payload as ``release/contract-release.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bundle_sha256(manifest_path: Path, manifest: dict[str, object]) -> str:
    vector_directory = manifest_path.parent
    vectors_root = (vector_directory / "vectors").resolve()
    declared_paths: set[str] = set()
    digest = hashlib.sha256()
    bundle_files: list[tuple[str, bytes]] = [("manifest.json", manifest_path.read_bytes())]
    schema_reference = manifest.get("vector_schema")
    if not isinstance(schema_reference, str):
        raise TypeError("Conformance manifest must declare vector_schema")
    if schema_reference != "../vector.schema.json":
        raise ValueError("Invalid vector_schema path")
    schema_path = (vector_directory / schema_reference).resolve()
    allowed_root = vector_directory.parent.resolve()
    if schema_path.parent != allowed_root or not schema_path.is_file():
        raise ValueError("vector_schema must resolve to the versioned conformance schema")
    bundle_files.append((f"schema/{schema_path.name}", schema_path.read_bytes()))
    for entry in sorted(manifest["vectors"], key=lambda item: item["path"]):
        relative = entry["path"]
        if relative in declared_paths or relative.startswith("/") or ".." in Path(relative).parts:
            raise ValueError(f"Invalid or duplicate vector path: {relative}")
        declared_paths.add(relative)
        vector_path = (vector_directory / relative).resolve()
        if not vector_path.is_relative_to(vectors_root):
            raise ValueError(f"Vector path escapes vectors directory: {relative}")
        if not vector_path.is_file():
            raise ValueError(f"Missing declared vector file: {relative}")
        payload = vector_path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
            raise ValueError(f"Vector digest mismatch: {relative}")
        bundle_files.append((relative, payload))
    actual_paths = {
        path.relative_to(vector_directory).as_posix()
        for path in (vector_directory / "vectors").rglob("*")
        if path.is_file()
    }
    if actual_paths != declared_paths:
        undeclared = sorted(actual_paths - declared_paths)
        raise ValueError(f"Undeclared vector files: {', '.join(undeclared)}")
    for relative, payload in sorted(bundle_files):
        encoded_path = relative.encode()
        digest.update(len(encoded_path).to_bytes(4, "big"))
        digest.update(encoded_path)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def build_descriptor(
    *,
    contract_path: str | Path,
    vector_manifest_path: str | Path,
    git_commit: str,
) -> dict[str, object]:
    contract_path = Path(contract_path)
    vector_manifest_path = Path(vector_manifest_path)
    if not COMMIT_PATTERN.fullmatch(git_commit):
        raise ValueError("git_commit must be a lowercase 40-character SHA-1 object ID")
    contract = json.loads(contract_path.read_text())
    manifest = json.loads(vector_manifest_path.read_text())
    if manifest["agent_contract_version"] != contract["agent_contract_version"]:
        raise ValueError("Contract and conformance manifest versions do not match")
    vector_ids = sorted(vector["id"] for vector in manifest["vectors"])
    return {
        "schema_version": 1,
        "agent_contract_version": contract["agent_contract_version"],
        "git_commit": git_commit,
        "contract_sha256": _sha256(contract_path),
        "contract_schema_version": contract["contract_schema_version"],
        "compatibility_strategy": contract["compatibility"]["strategy"],
        "conformance_vectors_sha256": _bundle_sha256(vector_manifest_path, manifest),
        "conformance_vectors": vector_ids,
    }


def canonical_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", default="agent/contract.yaml")
    parser.add_argument("--vectors", default="agent/conformance/1.0.0/manifest.json")
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    rendered = canonical_json(
        build_descriptor(
            contract_path=args.contract,
            vector_manifest_path=args.vectors,
            git_commit=args.git_commit,
        )
    )
    if args.output:
        Path(args.output).write_text(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
