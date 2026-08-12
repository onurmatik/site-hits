from __future__ import annotations

import os
import re
import shlex
from io import BytesIO
from pathlib import Path

from fabric import Connection, task
from invoke import Collection

DEPLOY_DIR = Path(__file__).resolve().parent


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


load_env(DEPLOY_DIR / "deploy.env")
load_env(DEPLOY_DIR.parent / ".env-prod")

PROJECT_NAME = os.environ.get("PROJECT_NAME", "sitehits")
DOMAIN = os.environ.get("DOMAIN", "sitehits.io")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "onurmatik/site-hits")
DEPLOY_HOST = os.environ.get("DEPLOY_HOST", "46.225.14.95")
KEY_FILENAME = os.environ.get("KEY_FILENAME", "hetzner-stage")
DEPLOY_USER = os.environ.get("DEPLOY_USER", "root")
APP_USER = os.environ.get("APP_USER", "ubuntu")
RELEASE_GIT_COMMIT = os.environ.get("SITEHITS_MCP_GIT_COMMIT", "").strip()

PROJECT_DIR = f"/srv/apps/{PROJECT_NAME}"
REPO_URL = f"https://github.com/{GITHUB_REPO}.git"
GEOIP_DB_PATH = "/var/lib/GeoIP/GeoLite2-City.mmdb"
GEOIP_CONFIG_PATH = "/etc/GeoIP.conf"
SYSTEMD_UNITS = (
    "sitehits-web.service",
    "sitehits-mcp.service",
    "sitehits-mcp-cleanup.service",
    "sitehits-mcp-cleanup.timer",
    "sitehits-mcp-cleanup-health.service",
    "sitehits-mcp-cleanup-health.timer",
    "sitehits-mcp-alert@.service",
)
NGINX_MCP_SNIPPET = "/etc/nginx/snippets/sitehits-mcp.locations.conf"
RUNTIME_ENV_KEYS = (
    "DATABASE_URL",
    "ALLOWED_HOSTS",
    "CSRF_TRUSTED_ORIGINS",
    "SITEHITS_BASE_URL",
    "SITEHITS_HASH_SECRET",
    "SITEHITS_TIME_ZONE",
    "SITEHITS_TRUST_PROXY_HEADERS",
    "SITEHITS_TRUSTED_PROXY_IPS",
    "SITEHITS_GEOIP_DB_PATH",
    "OPENAI_API_KEY",
    "SITEHITS_GOAL_PLANNING_MODEL",
    "SITEHITS_GOAL_PLANNING_TIMEOUT_SECONDS",
    "SITEHITS_GOAL_PLANNING_RATE_LIMIT",
    "SITEHITS_MCP_TOKEN_SECRET",
    "SITEHITS_MCP_ISSUER_URL",
    "SITEHITS_MCP_RESOURCE_URL",
    "SITEHITS_MCP_DOCUMENTATION_URL",
    "SITEHITS_MCP_SKILL_UPDATE_URL",
    "SITEHITS_MCP_IMAGE_REF",
    "SITEHITS_MCP_CORS_ORIGINS",
    "SITEHITS_MCP_ALERT_WEBHOOK_URL",
    "WEB_CONCURRENCY",
    "MCP_WEB_CONCURRENCY",
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "DJANGO_EMAIL_BACKEND",
    "AWS_SES_ACCESS_KEY_ID",
    "AWS_SES_SECRET_ACCESS_KEY",
    "AWS_SES_REGION_NAME",
    "AWS_SES_REGION_ENDPOINT",
    "USE_SES_V2",
    "AWS_SES_CONFIGURATION_SET",
    "DEFAULT_FROM_EMAIL",
)


def quote(value: str) -> str:
    return shlex.quote(value)


def app_run(connection: Connection, command: str, *, warn: bool = False):
    snippet = f"cd {quote(PROJECT_DIR)} && {command}"
    return connection.sudo(
        f"bash -lc {quote(snippet)}",
        user=APP_USER,
        warn=warn,
    )


def ensure_runtime_env(connection: Connection) -> None:
    env_path = PROJECT_DIR + "/.env"
    if connection.run(f"test -f {quote(env_path)}", warn=True, hide=True).failed:
        script = f"""
umask 077
{{
  printf 'DJANGO_DEBUG=false\\n'
  printf 'DJANGO_SECRET_KEY=%s\\n' "$(openssl rand -hex 48)"
  printf 'ALLOWED_HOSTS={DOMAIN}\\n'
  printf 'CSRF_TRUSTED_ORIGINS=https://{DOMAIN}\\n'
  printf 'SITEHITS_BASE_URL=https://{DOMAIN}\\n'
  printf 'SITEHITS_HASH_SECRET=%s\\n' "$(openssl rand -hex 48)"
  printf 'SITEHITS_MCP_TOKEN_SECRET=%s\\n' "$(openssl rand -hex 48)"
  printf 'SITEHITS_GEOIP_DB_PATH={GEOIP_DB_PATH}\\n'
  printf 'SITEHITS_TIME_ZONE=Europe/Istanbul\\n'
  printf 'SITEHITS_TRUST_PROXY_HEADERS=true\\n'
  printf 'SITEHITS_MAX_EVENT_BYTES=16384\\n'
}} > .env
""".strip()
        app_run(connection, f"bash -lc {quote(script)}")
        return

    script = f"""
if ! grep -q '^SITEHITS_MCP_TOKEN_SECRET=' .env; then
  printf 'SITEHITS_MCP_TOKEN_SECRET=%s\\n' "$(openssl rand -hex 48)" >> .env
fi
if grep -q '^SITEHITS_GEOIP_DB_PATH=' .env; then
  sed -i 's|^SITEHITS_GEOIP_DB_PATH=.*$|SITEHITS_GEOIP_DB_PATH={GEOIP_DB_PATH}|' .env
else
  printf 'SITEHITS_GEOIP_DB_PATH={GEOIP_DB_PATH}\\n' >> .env
fi
chmod 600 .env
""".strip()
    app_run(connection, f"bash -lc {quote(script)}")


def sync_runtime_env(connection: Connection) -> None:
    updates = {
        key: os.environ.get(key, "").strip()
        for key in RUNTIME_ENV_KEYS
        if os.environ.get(key, "").strip()
    }
    if not updates:
        return
    if any("\n" in value or "\r" in value for value in updates.values()):
        raise RuntimeError("Runtime environment values must be single-line strings.")

    payload = "".join(f"{key}={value}\n" for key, value in updates.items())
    temporary_path = connection.run("mktemp", hide=True).stdout.strip()
    staged_path = PROJECT_DIR + "/.env.deploy"
    try:
        connection.put(BytesIO(payload.encode()), remote=temporary_path)
        connection.sudo(
            f"install -o {quote(APP_USER)} -g {quote(APP_USER)} -m 600 "
            f"{quote(temporary_path)} {quote(staged_path)}"
        )
        app_run(
            connection,
            f"python3 .deploy/sync_env.py .env {quote(Path(staged_path).name)}",
        )
    finally:
        connection.run(f"rm -f {quote(temporary_path)}", warn=True, hide=True)
        app_run(connection, f"rm -f {quote(Path(staged_path).name)}", warn=True)


def install_stage1_topology(connection: Connection) -> None:
    """Install the digest-pinned Stage 1 process topology without mutable checkout runtime."""

    if connection.run("command -v docker", warn=True, hide=True).failed:
        raise RuntimeError("Docker is required for the immutable GHCR Stage 1 runtime.")
    app_run(
        connection,
        "set -a && . ./.env && set +a && deploy/validate-image-ref.sh",
    )
    app_run(
        connection,
        "set -a && . ./.env && set +a && "
        "python3 deploy/send-mcp-alert.py --check",
    )
    for unit in SYSTEMD_UNITS:
        source = f"{PROJECT_DIR}/deploy/systemd/{unit}"
        destination = f"/etc/systemd/system/{unit}"
        connection.sudo(f"install -o root -g root -m 644 {quote(source)} {quote(destination)}")
    connection.sudo(
        "install -o root -g root -m 644 "
        f"{quote(PROJECT_DIR + '/deploy/nginx/sitehits-mcp.locations.conf')} "
        f"{quote(NGINX_MCP_SNIPPET)}"
    )
    connection.sudo("systemctl daemon-reload")
    if connection.sudo(
        "nginx -T 2>/dev/null | grep -Fq 'location = /mcp {'",
        warn=True,
        hide=True,
    ).failed:
        raise RuntimeError(
            f"Include {NGINX_MCP_SNIPPET} in the canonical TLS server before deployment."
        )
    connection.sudo("nginx -t")
    connection.sudo("systemctl reload nginx")
    connection.sudo(
        f"systemctl disable --now app@{PROJECT_NAME}.socket app@{PROJECT_NAME}.service",
        warn=True,
    )
    # A persistent health timer can fire immediately on an older host. Keep
    # both timers stopped until the new schema is installed and a successful
    # cleanup run has seeded the durable health record.
    connection.sudo(
        "systemctl disable --now sitehits-mcp-cleanup.timer "
        "sitehits-mcp-cleanup-health.timer",
        warn=True,
    )
    connection.sudo("systemctl enable sitehits-web.service sitehits-mcp.service")
    # The clean-cut migration adds enforced refresh-family bindings. Stop both
    # request paths before the web unit applies migrations, then start the exact
    # same digest for web and MCP.
    connection.sudo("systemctl stop sitehits-mcp.service sitehits-web.service", warn=True)
    connection.sudo("systemctl start sitehits-web.service")
    connection.sudo("systemctl start sitehits-mcp-cleanup.service")
    connection.sudo("systemctl start sitehits-mcp.service")
    connection.sudo(
        "systemctl enable --now sitehits-mcp-cleanup.timer "
        "sitehits-mcp-cleanup-health.timer"
    )


def ensure_geoip_database(connection: Connection) -> None:
    account_id = os.environ.get("MAXMIND_ACCOUNT_ID", "").strip()
    license_key = os.environ.get("MAXMIND_LICENSE_KEY", "").strip()
    if bool(account_id) != bool(license_key):
        raise RuntimeError("MAXMIND_ACCOUNT_ID and MAXMIND_LICENSE_KEY must be supplied together.")

    server_is_configured = connection.run(
        "test -x /usr/bin/geoipupdate "
        f"&& test -f {quote(GEOIP_CONFIG_PATH)} "
        f"&& grep -Eq '^AccountID [1-9][0-9]*$' {quote(GEOIP_CONFIG_PATH)} "
        f"&& grep -Eq '^LicenseKey [^[:space:]]+$' {quote(GEOIP_CONFIG_PATH)}",
        warn=True,
        hide=True,
    ).ok
    if not account_id and not server_is_configured:
        raise RuntimeError(
            "GeoIP is not provisioned. Set MAXMIND_ACCOUNT_ID and "
            "MAXMIND_LICENSE_KEY in the ignored .env-prod file, then deploy again."
        )

    if connection.run("test -x /usr/bin/geoipupdate", warn=True, hide=True).failed:
        connection.sudo("apt-get update")
        connection.sudo("DEBIAN_FRONTEND=noninteractive apt-get install -y geoipupdate")

    if account_id:
        config = (
            f"AccountID {account_id}\n"
            f"LicenseKey {license_key}\n"
            "EditionIDs GeoLite2-City\n"
            "DatabaseDirectory /var/lib/GeoIP\n"
        )
        temporary_path = connection.run("mktemp", hide=True).stdout.strip()
        try:
            connection.put(BytesIO(config.encode()), remote=temporary_path)
            connection.sudo(
                f"install -o root -g root -m 600 {quote(temporary_path)} {quote(GEOIP_CONFIG_PATH)}"
            )
        finally:
            connection.run(f"rm -f {quote(temporary_path)}", warn=True, hide=True)

    connection.sudo("/usr/bin/geoipupdate")
    connection.sudo("systemctl enable --now geoipupdate.timer")
    connection.sudo(f"chown root:root {quote(GEOIP_DB_PATH)}")
    connection.sudo(f"chmod 644 {quote(GEOIP_DB_PATH)}")
    if connection.sudo(
        f"test -s {quote(GEOIP_DB_PATH)} && test -r {quote(GEOIP_DB_PATH)}",
        user=APP_USER,
        warn=True,
        hide=True,
    ).failed:
        raise RuntimeError(f"GeoIP database is missing or unreadable: {GEOIP_DB_PATH}")


@task
def deploy(_context):
    """Deploy one immutable SiteHits source commit to hetzner-stage."""
    if not re.fullmatch(r"[0-9a-f]{40}", RELEASE_GIT_COMMIT):
        raise RuntimeError(
            "SITEHITS_MCP_GIT_COMMIT must be the release's full lowercase commit SHA."
        )
    connection = Connection(
        host=DEPLOY_HOST,
        user=DEPLOY_USER,
        connect_kwargs={
            "key_filename": str(Path(f"~/.ssh/{KEY_FILENAME}").expanduser()),
        },
    )

    connection.run(f"mkdir -p {quote(PROJECT_DIR)}")
    connection.run(f"chown {quote(APP_USER)}:{quote(APP_USER)} {quote(PROJECT_DIR)}")

    if connection.run(f"test -d {quote(PROJECT_DIR + '/.git')}", warn=True, hide=True).ok:
        app_run(connection, "git fetch origin --tags --prune")
    else:
        is_empty = connection.run(
            f'test -z "$(find {quote(PROJECT_DIR)} -mindepth 1 -maxdepth 1 -print -quit)"',
            warn=True,
            hide=True,
        ).ok
        if not is_empty:
            raise RuntimeError(f"{PROJECT_DIR} exists and is not an empty Git checkout")
        connection.sudo(
            f"git clone {quote(REPO_URL)} {quote(PROJECT_DIR)}",
            user=APP_USER,
        )
        app_run(connection, "git fetch origin --tags --prune")

    commit_object = quote(f"{RELEASE_GIT_COMMIT}^{{commit}}")
    app_run(connection, f"git cat-file -e {commit_object}")
    app_run(connection, f"git checkout --detach {quote(RELEASE_GIT_COMMIT)}")
    app_run(
        connection,
        f"test \"$(git rev-parse HEAD)\" = {quote(RELEASE_GIT_COMMIT)}",
    )

    ensure_geoip_database(connection)
    ensure_runtime_env(connection)
    sync_runtime_env(connection)

    install_stage1_topology(connection)


ns = Collection(deploy)
