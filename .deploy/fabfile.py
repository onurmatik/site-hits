from __future__ import annotations

import os
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

PROJECT_DIR = f"/srv/apps/{PROJECT_NAME}"
VENV_DIR = f"{PROJECT_DIR}/venv"
REPO_URL = f"https://github.com/{GITHUB_REPO}.git"
GEOIP_DB_PATH = "/var/lib/GeoIP/GeoLite2-Country.mmdb"
GEOIP_CONFIG_PATH = "/etc/GeoIP.conf"


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
  printf 'SITEHITS_GEOIP_DB_PATH={GEOIP_DB_PATH}\\n'
  printf 'SITEHITS_TIME_ZONE=Europe/Istanbul\\n'
  printf 'SITEHITS_TRUST_PROXY_HEADERS=true\\n'
  printf 'SITEHITS_MAX_EVENT_BYTES=16384\\n'
}} > .env
""".strip()
        app_run(connection, f"bash -lc {quote(script)}")
        return

    script = f"""
if grep -q '^SITEHITS_GEOIP_DB_PATH=' .env; then
  sed -i 's|^SITEHITS_GEOIP_DB_PATH=.*$|SITEHITS_GEOIP_DB_PATH={GEOIP_DB_PATH}|' .env
else
  printf 'SITEHITS_GEOIP_DB_PATH={GEOIP_DB_PATH}\\n' >> .env
fi
chmod 600 .env
""".strip()
    app_run(connection, f"bash -lc {quote(script)}")


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
            "EditionIDs GeoLite2-Country\n"
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
    """Deploy SiteHits from GitHub to hetzner-stage."""
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
        app_run(connection, "git fetch origin main --prune")
        app_run(connection, "git checkout main")
        app_run(connection, "git reset --hard origin/main")
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

    ensure_geoip_database(connection)
    ensure_runtime_env(connection)

    if connection.run(f"test -x {quote(VENV_DIR + '/bin/python')}", warn=True, hide=True).failed:
        app_run(connection, f"python3 -m venv {quote(VENV_DIR)}")

    app_run(connection, f"{quote(VENV_DIR + '/bin/pip')} install --upgrade pip")
    app_run(connection, f"{quote(VENV_DIR + '/bin/pip')} install -r requirements.txt")
    app_run(connection, "npm ci")
    app_run(connection, "npm run build")
    app_run(connection, f"{quote(VENV_DIR + '/bin/python')} manage.py collectstatic --noinput")
    app_run(connection, f"{quote(VENV_DIR + '/bin/python')} manage.py migrate --noinput")
    app_run(connection, f"{quote(VENV_DIR + '/bin/python')} manage.py check --deploy")

    connection.sudo(
        f"systemctl reset-failed app@{PROJECT_NAME}.service app@{PROJECT_NAME}.socket",
        warn=True,
    )
    connection.sudo(f"systemctl restart app@{PROJECT_NAME}.socket", warn=True)


ns = Collection(deploy)
