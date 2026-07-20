# SiteHits

SiteHits is a small, cookieless, multi-site analytics service for internal use. It consists of a Django dashboard, a Django Ninja collection/reporting API, and one browser script that can be installed on any site.

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

SQLite is used for local development and the current StageOps deployment. Set
`DATABASE_URL` to PostgreSQL only for deployments that need higher write
concurrency.

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

Common query parameters are `site=all|<slug>`, `period=today|last24h|last7d|last30d|last90d`, and `granularity=auto|hourly|daily` for time series.

Each selected-site dashboard also provides an **Embed widget** action. It generates a public iframe showing aggregate distinct visitors, minute activity, and the top three countries for the last 60 minutes. The widget URL uses the site's public tracking key, refreshes every minute, and intentionally excludes paths, referrers, sessions, and custom-event details.

## Production configuration

Copy `.env.example` and supply real secrets. Important details:

- Use an independent `SITEHITS_HASH_SECRET`; changing it breaks hash continuity for that day.
- Set `SITEHITS_TRUST_PROXY_HEADERS=true` only when the reverse proxy overwrites untrusted forwarding headers.
- Provision a MaxMind GeoLite2 City database and set `SITEHITS_GEOIP_DB_PATH`. The checked-in deploy task installs and periodically runs `geoipupdate`; `manage.py check --deploy` fails if the configured database is missing, invalid, or the wrong MMDB type. Existing events are not location-backfilled because raw IP addresses are never stored.
- Run the collector over HTTPS. Configure the reverse proxy to limit request rates and cap `/api/events` bodies.
- Schedule `python manage.py purge_old_events --days 365` daily.

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

Both methods preserve the submitted website and resume at `/onboarding/`. New tracked sites are owned by the authenticated user; regular users can only open and query their own sites.

The included Dockerfile builds Tailwind/Chart.js assets, collects static files, applies migrations, and starts Gunicorn. Health checks should target `/health/`.

## Verification

```bash
uv run pytest
npm test
uv run python manage.py check
npm run build
```

The test suites cover origin validation, URL sanitization, privacy hashing, verified crawler collection, collector health, suspected automation, all reporting metrics/breakdowns, authentication, retention, initial/SPA pageviews, custom events, and session expiry.
