# SiteHits MCP/OAuth Stage 1 operations and release runbook

This runbook implements ADR `0002-mcp-oauth-v1`. It does not replace the
Agent Contract or authorize a production deployment. The production identity
is byte-exact:

```text
issuer   = https://sitehits.io
resource = https://sitehits.io/mcp
```

Issuer or resource changes require a separate migration release. Do not
normalize case, ports, paths, percent encoding, or trailing slashes.

## Production topology

- PostgreSQL 17 is the only Stage 1 acceptance database. SQLite is useful for
  local unit tests but is not concurrency evidence.
- Install `deploy/systemd/sitehits-web.service` and
  `deploy/systemd/sitehits-mcp.service` as separate processes. They share the
  same environment file, database, product account model, and service layer.
  Both container processes bind only to loopback; public traffic reaches them
  exclusively through the canonical TLS reverse proxy.
- Set `SITEHITS_MCP_IMAGE_REF` to the exact
  `ghcr.io/onurmatik/site-hits@sha256:<digest>` sealed into the release descriptor.
  The units validate this form, pull that digest, and never execute a mutable tag
  or the checkout's virtual environment.
- `config.asgi:application` is Django-only. The dedicated resource process uses
  `mcp_gateway.mcp_asgi:application`; never remount MCP into the web ASGI app.
- Include `deploy/nginx/sitehits-mcp.locations.conf` inside the existing
  `sitehits.io` TLS server. Only the trusted loopback proxy may set forwarded
  scheme, host, or source-address headers.
- Set `SITEHITS_TRUST_PROXY_HEADERS=true` and list only direct proxy peer IPs in
  `SITEHITS_TRUSTED_PROXY_IPS`. Uvicorn must receive the same list via
  `--forwarded-allow-ips`; never use `*`.
- OAuth/DCR abuse limits use PostgreSQL-backed counters and atomic increments.
  A local-memory cache is not an accepted production rate-limit store.
- Client registration is CIMD-first. Outbound metadata retrieval is performed
  only by `django-embedded-mcp`: lowercase ASCII HTTPS client IDs with a path and
  no userinfo/query/fragment/dot segments, one-time DNS resolution, public-address
  enforcement, IP-pinned TLS/SNI, no redirects, 3-second shared deadline, 8 KiB
  body cap, and bounded concurrency. Validated `Cache-Control` and `Age` values are
  clamped to a 5-minute/1-hour freshness window. Expired metadata is never authorized
  when refresh fails. DCR remains the measured compatibility fallback.
- Enable `sitehits-mcp-cleanup.timer`. Its service runs bounded OAuth metadata
  cleanup and the existing Agent Contract audit/idempotency cleanup daily.

## Migration and clean-cut rollout

There are no external MCP consumers and no legacy compatibility window.
Provisional root OAuth endpoints and legacy static bearer behavior are removed
in the same clean-cut release; do not advertise them as aliases.

1. Record the current `agent-contract-v1.0.0` tag target and SHA-256 of
   `release/contract-release.json`. Take a PostgreSQL 17 backup.
2. Verify migrations are additive: create models, columns, constraints, and
   cleanup indexes before new code depends on them. Do not combine destructive
   cleanup with this deployment.
   The clean-cut refresh-family binding is enforced in the additive migration;
   stop both old request processes before applying it so an old worker cannot
   insert an unbound refresh token during the transition.
   This release therefore requires an explicit maintenance/drain boundary:
   stop every old web/MCP writer before `0002` and keep traffic drained through
   `0004`. Do not claim zero-downtime migration. A future zero-downtime rollout
   would require a separate dual-write expand/contract release.
3. Run `manage.py migrate --plan`, then apply migrations on staging. Exercise
   concurrent code exchange, refresh rotation/replay, revoke, rate-limit
   consumption, and cleanup against PostgreSQL 17.
4. Build the image once, publish it to
   `ghcr.io/onurmatik/site-hits`, and record the immutable
   `sha256:<64-hex>` digest. Set `SITEHITS_MCP_GIT_COMMIT` to the exact full
   source commit used for that image; the deployment task must detach the
   topology/config checkout at this commit rather than a mutable branch. Deploy
   that commit and digest to staging.
5. Install/reload the two process units. After migrations succeed, run the
   cleanup service once to seed its durable success record; only then enable the
   persistent cleanup and hourly health timers. Route only `/mcp` to the
   dedicated MCP process on port 8001; public discovery, `/oauth/`, consent, and
   ordinary product routes remain on the Django web process on port 8000.
6. Run native OAuth through discovery, initialize, `tools/list`,
   `get_account_capabilities`, refresh, and revoke with exact tested versions
   of ChatGPT, Codex, Claude/Claude Desktop, and Claude Code. Record a real-client
   CIMD result and a separately exercised DCR fallback result for every required
   matrix entry. Run MCP Inspector separately as diagnostics.
   Claude Code must use its native user-scope HTTP registration and login flow:

   ```bash
   claude mcp add --transport http --scope user sitehits https://sitehits.io/mcp
   claude mcp login sitehits
   ```

   The successful Claude Code callback is lowercase
   `http://localhost:<ephemeral-port>/callback`; do not substitute an IP host,
   HTTPS localhost, or another path in its acceptance record.
7. Verify correlation IDs, OAuth/tool audit mapping, digest-only credential
   storage, redaction, cleanup output, and alerts. Promote the identical image
   digest to production and repeat the real-client smoke.

## Retention and cleanup health

- Agent and OAuth audit retention is 90 days. The existing
  `purge_old_events` command obtains Agent Contract audit retention from the
  canonical Contract.
- Expired, consumed, used, or revoked OAuth credential metadata is deleted only
  after 30 days. A DCR client older than 30 days may be deleted only when it has
  never issued a grant/token and its durable `last_used_at` marker remains null.
  Deleting old credential rows must never make a previously used client stale.
- `cleanup_mcp_oauth` emits one JSON metric record containing `runs`,
  `eligible`, `deleted`, `errors`, `duration_seconds`,
  `oldest_eligible_age_seconds`, `last_success_at`, per-type batches, and a
  `truncated` list. Each database transaction is bounded by batch size.
- Alert when the timer has no success for 36 hours, any run reports errors, or
  the oldest eligible record remains past the cleanup lag objective for two
  consecutive runs. A non-empty `truncated` list should schedule another
  bounded run rather than increasing the transaction without review.
- Onur owns this alert. `sitehits-mcp-cleanup-health.timer` evaluates it hourly;
  both cleanup execution and health failures trigger
  `sitehits-mcp-alert@.service`, which calls the configured external HTTPS
  `SITEHITS_MCP_ALERT_WEBHOOK_URL` without placing that URL in process argv.

## DCR fallback review and removal

The repository-maintainer role owns DCR fallback. Review it at least every 90
days and before an MCP authorization-spec change, required-client registration
or callback change, public plugin submission, or OAuth security incident.

Do not remove DCR until every supported primary and cross-agent surface has
current CIMD or pre-registration acceptance, no supported matrix record requires
DCR, and production telemetry shows 90 consecutive days with zero successful
DCR use. Any successful DCR use or compatibility regression resets the window.
Update `integration/client-compatibility.yaml`, create a new ADR and server
release, and preserve a rollback path for any removal.

## Seal the immutable release descriptor

Do not commit `release/mcp-release.json` before real evidence exists. Store a
smoke evidence object outside the source tree with these exact keys:

```json
{
  "clients": {
    "ChatGPT": {
      "tested_version": "<exact-version>",
      "registration_method": "cimd",
      "registration_status": "passed",
      "fallback_registration_method": "dcr",
      "fallback_status": "passed"
    },
    "Codex": {
      "tested_version": "<exact-version>",
      "registration_method": "cimd",
      "registration_status": "passed",
      "fallback_registration_method": "dcr",
      "fallback_status": "passed"
    },
    "Claude/Claude Desktop": {
      "tested_version": "<exact-version>",
      "registration_method": "cimd",
      "registration_status": "passed",
      "fallback_registration_method": "dcr",
      "fallback_status": "passed"
    },
    "Claude Code": {
      "tested_version": "<exact-version>",
      "registration_method": "cimd",
      "registration_status": "passed",
      "fallback_registration_method": "dcr",
      "fallback_status": "passed"
    }
  },
  "diagnostic_clients": {
    "MCP Inspector": {
      "tested_version": "<exact-version>",
      "registration_method": "cimd",
      "registration_status": "diagnostic-passed",
      "fallback_registration_method": "dcr",
      "fallback_status": "diagnostic-passed"
    }
  },
  "flows": [
    "discovery",
    "oauth",
    "initialize",
    "tools/list",
    "get_account_capabilities",
    "refresh",
    "revoke",
    "audit",
    "cleanup",
    "rollback"
  ],
  "git_commit": "<full-lowercase-commit>",
  "image_digest": "sha256:<ghcr-image-digest>",
  "issuer": "https://sitehits.io",
  "resource": "https://sitehits.io/mcp",
  "tool_registry_sha256": "sha256:<canonical-registry-digest>",
  "client_compatibility_sha256": "sha256:<integration/client-compatibility.yaml-digest>",
  "tested_at": "<UTC-RFC3339-Z>",
  "evidence_uri": "https://github.com/onurmatik/site-hits/releases/download/<immutable-tag>/<evidence-file>"
}
```

The evidence bundle itself must contain request/response-safe proof of the four
required real-client flows over CIMD and DCR fallback, registry equality,
audit, refresh/revoke, cleanup health,
and rollback smoke without raw tokens, codes, verifiers, state, or secrets. The
release generator computes `smoke.evidence_sha256` from the exact bytes supplied
to `--smoke-evidence`; upload those unchanged bytes at `evidence_uri`.
Generate the descriptor twice and compare bytes:

```bash
python -m mcp_gateway.release \
  --server-version <semver> \
  --git-commit <full-commit> \
  --image-digest sha256:<digest> \
  --client-compatibility integration/client-compatibility.yaml \
  --smoke-evidence /secure/evidence/mcp-smoke.json \
  --output /tmp/mcp-release.first.json

python -m mcp_gateway.release \
  --server-version <semver> \
  --git-commit <full-commit> \
  --image-digest sha256:<digest> \
  --client-compatibility integration/client-compatibility.yaml \
  --smoke-evidence /secure/evidence/mcp-smoke.json \
  --output /tmp/mcp-release.second.json

cmp /tmp/mcp-release.first.json /tmp/mcp-release.second.json
```

Validate against `release/mcp-release.schema.json`, attach the exact descriptor
and evidence bundle to GitHub Release `sitehits-mcp-v<server-version>`, and
verify the GHCR digest before promotion. No `mcp_contract_version` exists.
The manual `MCP Stage 1 release seal` workflow performs this same two-pass
generation, schema validation, and immutable asset upload after the smoke
evidence asset exists; its protected production-release environment is the
normal sealing path.

## Rollback

1. Stop new MCP routing or return maintenance before changing the process.
2. If all applied schema remains backward-compatible, route port 8001 to the
   previous immutable GHCR image and matching config/descriptor. Do not move a
   mutable tag.
3. Never unconsume an authorization code, un-revoke a token/client, restore a
   replayed refresh family, or delete audit evidence to make rollback pass.
4. If the schema is not backward-compatible, do not downgrade code or reverse
   migrations. Keep traffic disabled as needed and roll forward a corrective
   image.
5. After rollback, repeat discovery, exact-resource, ChatGPT, Codex,
   Claude/Claude Desktop, and Claude Code OAuth over the required registration
   paths, initialize, registry, bootstrap, refresh, revoke, audit, and cleanup
   smoke.
   Publish immutable rollback evidence and record the release/operator/time.

Skill Distribution begins only after the sealed MCP descriptor, production
acceptance, cleanup health, and rollback smoke are all verified.
