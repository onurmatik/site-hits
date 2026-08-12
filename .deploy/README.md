# SiteHits deployment

From this directory, deploy the exact release commit with:

```bash
export SITEHITS_MCP_GIT_COMMIT=<full-lowercase-40-character-release-sha>
python3 -m fabric deploy
```

The task refuses a branch name or abbreviated SHA. The checkout containing the
systemd/Nginx/config artifacts is detached at that exact commit, while the web
and MCP processes run the matching immutable GHCR image digest.

The first deployment creates `/srv/apps/sitehits/.env` with private runtime
secrets. Later deployments preserve that file, rebuild frontend assets, apply
database migrations, and collect static files. Stage 1 requires separate web
and MCP processes: `config.asgi:application` listens on loopback port 8000 and
`mcp_gateway.mcp_asgi:application` listens on loopback port 8001. The public
reverse proxy routes only `/mcp` to the latter.

The ignored local `.env-prod` holds production-only secrets. The Fabric task
merges its supported non-empty values into the preserved
`/srv/apps/sitehits/.env` with mode `0600` on every deploy. It never replaces
unrelated runtime settings. The runtime env must include:

```text
OPENAI_API_KEY=...
AWS_SES_ACCESS_KEY_ID=...
AWS_SES_SECRET_ACCESS_KEY=...
AWS_SES_REGION_NAME=...
DEFAULT_FROM_EMAIL=SiteHits <hello@sitehits.io>
GOOGLE_OAUTH_CLIENT_ID=...
GOOGLE_OAUTH_CLIENT_SECRET=...
SITEHITS_TRUSTED_PROXY_IPS=127.0.0.1,::1
SITEHITS_MCP_CORS_ORIGINS=https://chatgpt.com,https://codex.openai.com
SITEHITS_MCP_IMAGE_REF=ghcr.io/onurmatik/site-hits@sha256:<release-digest>
DATABASE_URL=postgresql://sitehits:<password>@127.0.0.1:5432/sitehits
SITEHITS_MCP_ALERT_WEBHOOK_URL=https://<monitoring-receiver>/sitehits-mcp
```

The checked-in systemd units refuse mutable image tags and start both processes
from this exact GHCR digest. Uvicorn must receive the same direct-peer list through
`--forwarded-allow-ips`; wildcard proxy trust is forbidden. Install the checked-in
`deploy/systemd/sitehits-web.service`, `deploy/systemd/sitehits-mcp.service`,
cleanup timer, and Nginx location include as one topology change.
`DATABASE_URL` must resolve to the provisioned PostgreSQL 17 instance; the
Stage 1 acceptance workflow rejects SQLite as concurrency evidence.
Onur owns the Stage 1 cleanup alert. The hourly health timer and cleanup unit
both trigger the external HTTPS webhook through `sitehits-mcp-alert@.service`.

`OPENAI_API_KEY` enables the AI-assisted Product metrics Describe → Review
flow. Optional `SITEHITS_GOAL_PLANNING_MODEL`,
`SITEHITS_GOAL_PLANNING_TIMEOUT_SECONDS`, and
`SITEHITS_GOAL_PLANNING_RATE_LIMIT` values are merged by the same task.

The SES region must be the region where the `sitehits.io` identity is verified.
The Google OAuth web client must authorize this exact redirect URI:

```text
https://sitehits.io/accounts/google/login/callback/
```

Country, region, and city analytics use the MaxMind GeoLite2 City database.
Create a MaxMind license key and add these deployment-only values to the
ignored local `.env-prod` file:

```text
MAXMIND_ACCOUNT_ID=...
MAXMIND_LICENSE_KEY=...
```

The deploy task installs `geoipupdate`, writes its root-only configuration,
downloads `/var/lib/GeoIP/GeoLite2-City.mmdb`, enables the packaged periodic
update timer, and sets `SITEHITS_GEOIP_DB_PATH` in the preserved runtime env.
Deployment stops if the database cannot be downloaded or read, so location
analytics cannot silently fall back to `Unknown` in production.
