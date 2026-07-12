# SiteHits

SiteHits is a small, cookieless, multi-site analytics service for internal use. It consists of a Django dashboard, a Django Ninja collection/reporting API, and one browser script that can be installed on any site.

## What it collects

- Page path, referrer hostname/path, UTM campaign fields, language, timezone, viewport and screen dimensions.
- Coarse country, device, browser and operating-system labels derived while receiving the event.
- Named custom events with at most 10 short scalar properties.
- A pseudonymous daily visitor hash and a tab-session identifier.

SiteHits never stores the raw IP address or raw user-agent. It drops arbitrary query strings, fragments and advertising click IDs. The visitor hash rotates daily and is scoped to one tracked site, so returning visitors across days and people crossing between domains are intentionally not linked.

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

Open [http://localhost:8000/admin/](http://localhost:8000/admin/), create a tracked site, then copy the generated install snippet. The dashboard is at [http://localhost:8000/dashboard/all](http://localhost:8000/dashboard/all).

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

## Reporting

Authenticated superusers can access:

- `GET /api/analytics/overview`
- `GET /api/analytics/timeseries`
- `GET /api/analytics/breakdowns/{pages|referrers|countries|devices|browsers|os|campaigns|events}`

Common query parameters are `site=all|<slug>`, `period=today|last24h|last7d|last30d|last90d`, and `granularity=auto|hourly|daily` for time series.

## Production configuration

Copy `.env.example` and supply real secrets. Important details:

- Use an independent `SITEHITS_HASH_SECRET`; changing it breaks hash continuity for that day.
- Set `SITEHITS_TRUST_PROXY_HEADERS=true` only when the reverse proxy overwrites untrusted forwarding headers.
- Mount a MaxMind GeoLite2 Country database and set `SITEHITS_GEOIP_DB_PATH`. Without it, collection continues and country remains `Unknown`.
- Run the collector over HTTPS. Configure the reverse proxy to limit request rates and cap `/api/events` bodies.
- Schedule `python manage.py purge_old_events --days 365` daily.

The included Dockerfile builds Tailwind/Chart.js assets, collects static files, applies migrations, and starts Gunicorn. Health checks should target `/health/`.

## Verification

```bash
uv run pytest
npm test
uv run python manage.py check
npm run build
```

The test suites cover origin validation, URL sanitization, privacy hashing, bot suppression, all reporting metrics/breakdowns, authentication, retention, initial/SPA pageviews, custom events, and session expiry.
