# SiteHits

SiteHits is a small, cookieless, multi-site analytics service for internal use. It consists of a Django dashboard, a Django Ninja collection/reporting API, and one browser script that can be installed on any site.

It also exposes an authenticated Python MCP server at `/mcp`. The server focuses on structured analytics reads and site/measurement CRUD so a calling agent can do its own evaluation. A separate, optional Django-template UI resource presents tracking setup without making the data tools depend on UI.

## What it collects

- Page path, referrer hostname/path, UTM campaign fields, language, timezone, viewport and screen dimensions.
- Approximate country, region, city, device, browser and operating-system labels derived while receiving the event.
- Named custom events with at most 10 short scalar properties.
- A pseudonymous daily visitor hash and a tab-session identifier.
- Known server-side bot requests, classified by provider and purpose, with sanitized path and optional HTTP status.

SiteHits never stores the raw IP address or raw user-agent. Bot user-agents are matched during ingestion and only the crawler/provider classification is retained. It drops arbitrary query strings, fragments and advertising click IDs. The visitor hash rotates daily and is scoped to one tracked site, so returning visitors across days and people crossing between domains are intentionally not linked.

## Local setup

Prerequisites: Python 3.11+, `uv`, Node.js 24+.

```bash
uv sync
npm install
npm run build
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

Open [http://localhost:8000/](http://localhost:8000/) and enter a website to test the public onboarding flow. Local magic-link emails are printed to the server console by default. Django admin remains available at [http://localhost:8000/admin/](http://localhost:8000/admin/) for staff users, and the dashboard is at [http://localhost:8000/dashboard/all](http://localhost:8000/dashboard/all).

SQLite is used only for local development. Stage 1 production requires
PostgreSQL 17 through `DATABASE_URL`; production startup fails closed for any
other database engine because SQLite cannot provide the accepted concurrency
evidence or row-lock semantics.

## Install on a site

The Django admin generates the exact values. The shape is:

```html
<script
  defer
  src="https://sitehits.io/js/script.js"
  data-site-key="sh_..."
  data-api-url="https://sitehits.io/api/events"
></script>
```

Install it in the real global template and separately check auth/error pages that do not extend that template. If the host application has a CSP, allow the SiteHits origin in both `script-src` and `connect-src`.

Programmatic custom event:

```js
window.sitehits("event", "signup", { plan: "pro" });
```

Declarative event:

```html
<button data-sitehits-event="cta_click" data-sitehits-location="hero">Start</button>
```

The tracker captures initial pageviews and SPA navigation through `pushState`, `replaceState`, and `popstate`. It uses only `sessionStorage`; session IDs rotate after 30 minutes of inactivity.

### Track activation and product metrics

Each tracked site can define an event catalog and one activation funnel from its **Product metrics** settings. Server-side events use a separate private key and are idempotent:

```http
POST /api/server-events
Authorization: Bearer shs_...
Content-Type: application/json

{
  "event_id": "purchase:stable-logical-id",
  "event_name": "purchase",
  "actor_id": "123",
  "timestamp": "2026-07-20T12:00:00Z",
  "value": "1499.00",
  "unit": "TRY",
  "properties": {"plan": "pro"}
}
```

SiteHits immediately HMAC-hashes both `actor_id` and `event_id`; raw identifiers are not stored. Use an internal PK or UUID rather than email or other PII. Repeating the same `event_id` returns `duplicate=true` without creating a second event. Actor-linked events can be removed through `POST /api/server-events/forget-actor` with the same bearer key.

Authenticated browser traffic can be linked to the same actor with a server-generated, one-hour HS256 JWT passed as `data-actor-token`. The Product metrics page generates a site-specific implementation instruction containing the exact claims, event catalog, reliability rules, and tests for an agent working in Django or another framework. Existing snippets remain anonymous and require no change.

### Track bots from the server

AI assistants and crawlers often skip JavaScript, so bot traffic uses a separate server-side collector. Every tracked site has a private `shb_...` bot key shown on its installation page. Keep that key in server environment variables and send a best-effort request from middleware after the response is known:

```http
POST /api/bot-events
Authorization: Bearer shb_...
Content-Type: application/json

{
  "url": "https://example.com/docs/get-started",
  "user_agent": "GPTBot/1.2",
  "status_code": 200,
  "timestamp": "2026-07-14T12:00:00Z"
}
```

Only known crawler tokens are stored as verified bot requests. Successful responses include a backward-compatible classification: `{"accepted": true, "classification": "known_crawler"}` or `{"accepted": false, "classification": "unrecognized"}`. The latter is a healthy collector response and creates no bot row. SiteHits records a throttled collector heartbeat for valid key/domain calls, so the dashboard can distinguish an active collector from one that has never checked in.

Do not await analytics when the runtime provides `waitUntil`; collector failures must never delay or break the page response. Log network failures and non-2xx responses without logging the private key, full URL, or user-agent. Obvious static assets and internal API routes can be excluded, while `robots.txt`, `llms.txt`, sitemap XML, and Markdown content should remain trackable. Existing collectors do not need a payload change; response inspection is an optional observability upgrade.

### Suspected automation

The browser tracker reports a privacy-safe `navigator.webdriver` boolean and SiteHits also checks for explicit headless user-agent tokens. These high-confidence events are separated from regular visitor metrics. The bot report additionally applies conservative daily-visitor heuristics for high request volume, rapid navigation bursts, and repeated session churn. Heuristic results are labeled **suspected automation**, remain distinct from verified crawler identity, and may overlap regular traffic metrics. Because the hosted tracker is updated centrally, installed browser snippets do not need to change.

## Reporting

Authenticated users can access analytics for their own tracked sites. Superusers retain access to every site:

- `GET /api/analytics/overview`
- `GET /api/analytics/timeseries`
- `GET /api/analytics/bots`
- `GET /api/analytics/product-metrics` (requires one selected site)
- `GET /api/analytics/breakdowns/{pages|referrers|countries|regions|cities|devices|browsers|os|campaigns|events}`

Common query parameters are `site=all|<slug>`,
`period=today|last24h|last7d|last30d|last90d|last180d|last365d`, and
`granularity=auto|hourly|daily` for time series. Six-month and one-year responses use daily
timeseries buckets and may include an optional `freshness` object with `source=hot|hybrid|cache`,
`generated_at`, and `is_stale`. If neither a fresh result nor a generation-safe cached result can
be produced, the API returns `historical_data_unavailable` instead of a misleading zero result.

### Historical analytics storage

SiteHits keeps raw events in PostgreSQL for at least 90 days and compacts only complete local
calendar months whose UTC end is older than that boundary. This produces an effective hot window
of 90–121 days. Verified monthly archives are ZSTD Parquet objects in private, versioned,
SSE-KMS S3 storage. Additive daily rollups and hourly report caches remain in PostgreSQL; exact
historical visitors, sessions, bounce, duration, activation, and suspected-automation metrics are
computed by a per-worker in-memory DuckDB query over hot PostgreSQL plus cold Parquet.

Objects remain directly queryable for two years, transition to Glacier for the third year, and are
permanently deleted with every object version after three years, all using manifest event-time
boundaries. See [the analytics archive runbook](docs/runbooks/analytics-archive.md) for rollout,
IAM, timers, deletion behavior, and recovery procedures.

Each selected-site dashboard also provides an **Embed widget** action. It generates a public iframe showing aggregate distinct visitors, minute activity, and the top three countries for the last 60 minutes. The widget URL uses the site's public tracking key, refreshes every minute, and intentionally excludes paths, referrers, sessions, and custom-event details.

## MCP and agent plugin

SiteHits is an OAuth-protected Streamable HTTP MCP resource at `https://sitehits.io/mcp`.
Clients discover the authorization server from the `401` challenge and public protected-resource
metadata. Authentication uses Authorization Code with PKCE `S256`; dynamic registration accepts
public clients and issued access tokens are bound to the canonical MCP resource.

This release is CIMD-first with DCR retained as a compatibility fallback. URL-shaped public client
IDs are fetched through the shared `django-embedded-mcp` SSRF-safe adapter with DNS-pinned TLS,
bounded responses, and validated cache lifetimes; expired metadata fails closed if it cannot be
refreshed. Django OAuth Toolkit 3.4 remains the grant/token core and its
independent CIMD resolver is disabled to avoid parallel policy paths. DCR requires an explicit
`web` or `native` application profile and remains subject to the role-owned 90-day review/removal
gate in `agent/decisions/0002-mcp-oauth-v1.yaml`.

For Codex, install the plugin and start its native OAuth flow:

```bash
codex mcp login --scopes read,write sitehits
```

Claude/Claude Desktop and Claude Code are required cross-agent acceptance clients. Configure and
authenticate Claude Code with its native user-scope commands:

```bash
claude mcp add --transport http --scope user sitehits https://sitehits.io/mcp
claude mcp login sitehits
```

The production release descriptor pins `integration/client-compatibility.yaml` and immutable CIMD
plus DCR-fallback evidence for ChatGPT, Codex, Claude/Claude Desktop, and Claude Code. MCP Inspector
remains diagnostic only.

The consent page uses the existing SiteHits sign-in. An authenticated MCP client has the same
resource ownership boundary as its linked Django user: superusers can access every site and regular
users only their own. OAuth scopes are an additional coarse permission layer:

- `read` for sites, reports, measurement configuration, and redacted tracking setup;
- `write` for site, measurement-event, and activation mutations.

Production is OAuth-only. Stage 1 is a clean cut with no external compatibility window; legacy
static MCP bearer tokens are not part of the production protocol.

The Streamable HTTP endpoint is `https://sitehits.io/mcp`. Its tools cover:

- site list/get/create/update/delete;
- overview, site comparison, time series, breakdown, bot, and product-metric reads for any supported period;
- product-event catalog and activation CRUD;
- browser snippet, bot middleware instruction, and product-event integration instruction retrieval.

Private bot and product keys are never returned by MCP tools. Tracking setup returns environment
variable names and redacted placeholders; real values stay in SiteHits or an authorized secret
manager. `get_tracking_setup` is a UI-less structured tool.
`render_tracking_setup` links the same result to an optional
`text/html;profile=mcp-app` resource rendered from a Django template and the compiled Tailwind
stylesheet.

Draft Skill and plugin packaging remains in `plugins/sitehits/`, but Stage 1 intentionally does not
promote or distribute it yet. Agentic Product Lifecycle requires the sealed MCP descriptor and
skill-independent production acceptance before Skill Distribution, followed by Plugin Distribution.
The public `/agent-manifest.json` and read-only `get_integration_status` tool expose independent
server, Agent Contract, skill, and plugin versions without making those later artifacts an MCP
acceptance dependency.

## Production configuration

Copy `.env.example` and supply real secrets. Important details:

- Use an independent `SITEHITS_HASH_SECRET`; changing it breaks hash continuity for that day.
- Set `SITEHITS_TRUST_PROXY_HEADERS=true` only behind the managed reverse proxy and restrict
  `SITEHITS_TRUSTED_PROXY_IPS` to its direct peer addresses. Uvicorn ignores forwarded headers from
  every other peer.
- Provision a MaxMind GeoLite2 City database and set `SITEHITS_GEOIP_DB_PATH`. The checked-in deploy task installs and periodically runs `geoipupdate`; `manage.py check --deploy` fails if the configured database is missing, invalid, or the wrong MMDB type. Existing events are not location-backfilled because raw IP addresses are never stored.
- Production must use PostgreSQL 17, matching the concurrency acceptance suite. `manage.py check --deploy` opens the configured database and rejects any other engine or major version.
- Run the collector over HTTPS. Configure the reverse proxy to limit request rates and cap `/api/events` bodies.
- Install the daily `sitehits-archive-maintenance.timer`, hourly
  `sitehits-historical-cache.timer`, and existing daily metadata cleanup timer. The
  `purge_old_events` command now removes only expired audit/idempotency metadata; raw analytics
  deletion is permitted only through a verified archive manifest.
- Set `SITEHITS_MCP_TOKEN_SECRET` to an independent long-lived HMAC key for security-event and
  rate-limit pseudonyms. OAuth credentials are stored with one-way SHA-256 digests and do not
  depend on this key.
- Configure the byte-exact public identity as `SITEHITS_BASE_URL=https://sitehits.io`,
  `SITEHITS_MCP_ISSUER_URL=https://sitehits.io`, and
  `SITEHITS_MCP_RESOURCE_URL=https://sitehits.io/mcp`. Startup rejects normalization candidates,
  origin drift, userinfo, query/fragment, and unexpected trailing slashes.
- The clean-cut Stage 1 release has no static-token issuance command, fallback verifier, or
  compatibility switch. Historical provisional rows remain cleanup-only until a later destructive
  migration.
- Set `SITEHITS_MCP_CORS_ORIGINS` to the explicit ChatGPT/Codex browser-origin allowlist. A wildcard
  is rejected outside local development. Authorization endpoints do not inherit MCP CORS.
- Set `SITEHITS_MCP_SKILL_UPDATE_URL=https://sitehits.io/INSTALL.md`; the URL remains a Stage 2
  production gate until its same-origin public redirect is deployed.
- OAuth lifetimes are fixed in code: authorization code 60 seconds, access token 15 minutes, and
  refresh family an absolute 30 days; environment overrides are intentionally unsupported.

### Separate web and MCP processes

The public origin is shared, but the runtimes are not. The reverse proxy sends only `/mcp` to the
dedicated MCP process on loopback port 8001. Discovery, `/oauth/`, consent, and ordinary SiteHits
routes stay on the Django web process on port 8000. Both use the same settings, PostgreSQL database,
account model, and service layer.

```bash
# Process 1: Django web and embedded OAuth provider
scripts/start.sh web

# Process 2: stateless Streamable HTTP MCP resource
scripts/start.sh mcp
```

Only the web process may run migrations (`RUN_MIGRATIONS=true`, the default). Production systemd and
Nginx examples are under `deploy/`; the complete rollout, cleanup, release, and rollback procedure is
in `docs/runbooks/mcp-stage1.md`.

### Passwordless and Google authentication

Anonymous onboarding uses a 10-minute `django-sesame` magic link. Local development uses Django's console backend; production defaults to `django_ses.SESBackend` and sends through the SES API. Configure `AWS_SES_ACCESS_KEY_ID`, `AWS_SES_SECRET_ACCESS_KEY`, and `AWS_SES_REGION_NAME` in the production environment, with the region matching the verified `sitehits.io` SES identity. `AWS_SES_REGION_ENDPOINT` is derived automatically and `AWS_SES_CONFIGURATION_SET` is optional. Mail is sent as `SiteHits <hello@sitehits.io>` unless `DEFAULT_FROM_EMAIL` overrides it.

Google sign-up/sign-in uses `django-allauth`. Set `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET`, then add this exact authorized redirect URI to the Google OAuth web client:

```text
https://sitehits.io/accounts/google/login/callback/
```

The checked-in Fabric deploy task reads these values from the ignored
`.env-prod` file, securely merges them into the server's preserved runtime
environment, and fails its production checks when either value is missing or
the client ID is not a Google web client ID.

AI-assisted Product metrics planning requires `OPENAI_API_KEY`. The Fabric
task also merges this key and optional `SITEHITS_GOAL_PLANNING_*` operational
overrides from `.env-prod` into the preserved runtime environment.

Both methods preserve the submitted website and resume at `/onboarding/`. New tracked sites are owned by the authenticated user; regular users can only open and query their own sites.

The native checkout contains both entrypoints; orchestration starts the existing web process with
`scripts/start.sh web` and a separate MCP process with `scripts/start.sh mcp`. Health checks for
the web process should target `/health/`; MCP acceptance uses authenticated initialize/bootstrap
smoke through the public reverse proxy.

## Verification

```bash
uv run pytest
npm test
uv run python manage.py check
npm run build
```

The test suites cover origin validation, URL sanitization, privacy hashing, verified crawler collection, collector health, suspected automation, all reporting metrics/breakdowns, authentication, retention, initial/SPA pageviews, custom events, and session expiry.
