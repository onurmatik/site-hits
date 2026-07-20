# SiteHits deployment

From this directory, deploy the current `main` branch with:

```bash
python3 -m fabric deploy
```

The first deployment creates `/srv/apps/sitehits/.env` with private runtime
secrets. Later deployments preserve that file, rebuild frontend assets, apply
database migrations, collect static files, and refresh the cold-tier socket.

The ignored local `.env-prod` holds production-only secrets. The Fabric task
merges its supported non-empty values into the preserved
`/srv/apps/sitehits/.env` with mode `0600` on every deploy. It never replaces
unrelated runtime settings. The runtime env must include:

```text
AWS_SES_ACCESS_KEY_ID=...
AWS_SES_SECRET_ACCESS_KEY=...
AWS_SES_REGION_NAME=...
DEFAULT_FROM_EMAIL=SiteHits <hello@sitehits.io>
GOOGLE_OAUTH_CLIENT_ID=...
GOOGLE_OAUTH_CLIENT_SECRET=...
```

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
