# SiteHits MCP deployment handoff

This document describes runtime requirements only. It does not authorize a
production deployment and does not replace or modify SiteHits' existing Fabric
deployment contract.

## Runtime inputs

- Process entrypoint: `/bin/sh scripts/start.sh mcp` through the project's active
  virtual environment.
- Default loopback bind: `127.0.0.1:8001`.
- Canonical issuer: `https://sitehits.io`.
- Canonical resource: `https://sitehits.io/mcp`.
- Database: PostgreSQL 17 for accepted concurrency semantics.
- Required configuration is documented in `.env.example`; credentials must use
  the deployment process's existing secrets mechanism.

## Public application routes

The application implements these routes:

```text
/.well-known/oauth-authorization-server
/.well-known/oauth-protected-resource
/.well-known/oauth-protected-resource/mcp
/oauth/register/
/oauth/authorize/
/oauth/token/
/oauth/revoke/
/mcp
```

How the MCP process and these routes are exposed is owned by a separate,
explicit deployment task. Agentic implementation and distribution stages must
not edit `.deploy/`, systemd, nginx, backup, migration-order, scheduler or
rollback files.

## Application operations

Before live acceptance, the existing deployment process must have applied the
checked-in Django migrations. Credential cleanup is available as:

```bash
python manage.py cleanup_mcp_oauth
python manage.py check_mcp_oauth_cleanup_health
```

Scheduling these commands and connecting alerts are operations handoff items;
this repository does not install timers or alert transports as part of MCP
implementation.

## Acceptance after a separate deployment

1. Confirm public health remains successful.
2. Confirm an unauthenticated `/mcp` request returns `401` with canonical
   `WWW-Authenticate` protected-resource discovery.
3. Run OAuth code, refresh and revoke flows.
4. Run client-native discovery, `tools/list`, and the read-only bootstrap tool
   with ChatGPT, Codex, Claude/Claude Desktop and Claude Code.
5. Verify audit records contain no raw credential material.

Do not commit `release/mcp-release.json` as a mutable placeholder. Seal it only
after the separately deployed candidate has immutable real-client evidence.
Never unconsume an authorization code or reactivate a revoked credential during
deployment recovery. If credential schema is not backward-compatible, do not downgrade code or reverse
state transitions.
