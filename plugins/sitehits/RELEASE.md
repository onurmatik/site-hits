# SiteHits plugin release policy

SiteHits versions the deployable layers independently:

- `server_version` identifies the server implementation and must match MCP `serverInfo.version`.
- `agent_contract_version` identifies the canonical, transport-neutral tool, authorization, approval, entitlement, error, and retry semantics. MCP maps that Contract without redefining it.
- `skill_version` identifies the standalone skill instructions and installers; it must match `skills/sitehits-analytics/VERSION`.
- `minimum_skill_version` identifies the oldest skill that can safely use the current contract.
- The plugin manifest version identifies the packaged skill, MCP config, and metadata release. It is not the Agent Contract version.

All version values use SemVer. Keep `minimum_skill_version` less than or equal to `skill_version`.

## Bump rules

| Change | Version bump |
| --- | --- |
| Server-only implementation fix with no contract change | `server_version` patch |
| Skill wording or installer fix | `skill_version` patch |
| Backward-compatible skill workflow | `skill_version` minor |
| New optional tool or output field | `agent_contract_version` minor |
| Tool removal or rename | `agent_contract_version` major |
| Optional input becomes required | `agent_contract_version` major |
| Output or tool semantics change | `agent_contract_version` major |
| A tool requires a stronger scope | `agent_contract_version` major |
| Support for an older skill ends | Raise `minimum_skill_version` |
| Packaged skill, MCP config, or plugin metadata changes | Plugin version bump |

## Backward-compatible release order

1. Make the server work with the currently supported skill and clients.
2. Add new tools and fields as optional behavior.
3. Update the public manifest and MCP server version, then deploy the server.
4. Verify the deployed server with the previous skill and a read-only OAuth call.
5. Publish the standalone skill and plugin, with matching skill `VERSION` and manifest values.
6. Keep `minimum_skill_version` at the oldest safe version while adoption and errors are monitored.
7. Raise the minimum only in a later release when older instructions can no longer operate safely.

Never publish a server and skill that each require the other new version; that creates an update deadlock.

## Breaking release order

This compatibility-window policy begins with the first public release. The current pre-public
Stage 1 clean cut has no external consumer and follows the MCP/OAuth ADR instead.

1. Add a parallel contract, tool name, or endpoint before removing the old one.
2. Run old and new contracts together for a documented compatibility window.
3. Deploy the server and verify both paths.
4. Publish the new skill and plugin, then mark the old path deprecated.
5. Monitor adoption before raising `minimum_skill_version`.
6. Remove the old path only after the published compatibility window ends.

## Deprecation and rollback

Publish the affected tools, scopes, replacement path, and removal date when deprecating behavior. Keep deprecated behavior available until that date and until adoption evidence supports removal. OAuth discovery is the primary plugin authentication path from plugin `0.2.0`; do not reintroduce static bearer-token configuration in distributed manifests.

Roll back the plugin or skill before rolling back a server capability they depend on. The server should remain backward-compatible throughout rollback. A skill must never download or overwrite itself; plugin updates and explicit standalone update requests remain the only supported update paths.

## Release gates

- Validate both Codex and Agent Plugins 1.0.0 manifests.
- Confirm `.mcp.json`, protected-resource metadata, OAuth requests, token audience, and documentation use `https://sitehits.io/mcp` exactly.
- Confirm the packaged tools, scopes, schemas, titles, and descriptions map exactly to the pinned Agent Contract.
- Confirm `serverInfo.version` and all public manifest versions are SemVer.
- Confirm `skills/sitehits-analytics/VERSION` matches public `skill_version`.
- Complete OAuth login and one read-only call with the bundled Codex CLI before publishing.
- Confirm normal skill workflows do not run compatibility checks or mutate installed files.
