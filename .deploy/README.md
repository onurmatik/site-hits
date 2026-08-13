# SiteHits deployment

From this directory, deploy the current `main` branch with:

```bash
python3 -m fabric deploy
```

The Fabric task uses the existing native production topology. It updates the
checkout to `origin/main`, installs Python dependencies into
`/srv/apps/sitehits/venv`, builds frontend assets, applies migrations, collects
static files, runs Django's deployment checks, and refreshes
`app@sitehits.socket`. The dedicated MCP process and scheduled jobs run from the
same checkout and virtualenv through native systemd units. Docker and an image
registry are not part of this flow.

The first deployment creates `/srv/apps/sitehits/.env` with private runtime
secrets. Later deployments preserve that file. The ignored local `.env-prod`
holds production-only settings; Fabric merges only supported, non-empty values
and keeps the remote file at mode `0600`.

The runtime env must include the production database and application settings,
plus any enabled integrations:

```text
DATABASE_URL=postgresql://sitehits:<password>@127.0.0.1:5432/sitehits
OPENAI_API_KEY=...
AWS_SES_ACCESS_KEY_ID=...
AWS_SES_SECRET_ACCESS_KEY=...
AWS_SES_REGION_NAME=...
DEFAULT_FROM_EMAIL=SiteHits <hello@sitehits.io>
GOOGLE_OAUTH_CLIENT_ID=...
GOOGLE_OAUTH_CLIENT_SECRET=...
```

`OPENAI_API_KEY` enables the AI-assisted Product metrics Describe → Review
flow. Optional `SITEHITS_GOAL_PLANNING_MODEL`,
`SITEHITS_GOAL_PLANNING_TIMEOUT_SECONDS`, and
`SITEHITS_GOAL_PLANNING_RATE_LIMIT` values are merged by the same task.

Historical analytics are also native. Deployment pre-installs DuckDB's
`httpfs` and `postgres` extensions for the application user, then installs and
enables these systemd timers:

- `sitehits-archive-maintenance.timer` (daily)
- `sitehits-historical-cache.timer` (hourly)

Deployment also installs the native `sitehits-mcp.service`, OAuth cleanup and
health timers, and the checked-in nginx `/mcp` routing include. Missing units or
routes are created idempotently by the same Fabric entrypoint; they are not
manual prerequisites. Before migrations, a custom-format PostgreSQL backup is
written under `/srv/backups/sitehits/`.

Production archive settings live in the ignored `.env-prod` file. Start the
archive rollout in shadow mode by setting
`SITEHITS_ARCHIVE_ENABLED=true` while keeping
`SITEHITS_ARCHIVE_QUERY_ENABLED=false` and
`SITEHITS_ARCHIVE_DELETE_SOURCE=false`. Configure the bucket, prefix, region,
KMS key, retention values, and the host's AWS credential chain in `.env-prod`.

The SES region must be the region where the `sitehits.io` identity is verified.
The Google OAuth web client must authorize this exact redirect URI:

```text
https://sitehits.io/accounts/google/login/callback/
```

Country, region, and city analytics use the MaxMind GeoLite2 City database.
Create a MaxMind license key and add these deployment-only values to the ignored
local `.env-prod` file:

```text
MAXMIND_ACCOUNT_ID=...
MAXMIND_LICENSE_KEY=...
```

The deploy task installs `geoipupdate`, writes its root-only configuration,
downloads `/var/lib/GeoIP/GeoLite2-City.mmdb`, enables the packaged periodic
update timer, and sets `SITEHITS_GEOIP_DB_PATH` in the preserved runtime env.
Deployment stops if the database cannot be downloaded or read.
