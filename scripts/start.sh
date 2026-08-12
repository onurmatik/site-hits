#!/bin/sh
set -eu

process="${1:-${SITEHITS_PROCESS:-web}}"

case "$process" in
  web)
    application="config.asgi:application"
    bind_host="${WEB_HOST:-0.0.0.0}"
    bind_port="${PORT:-8000}"
    workers="${WEB_CONCURRENCY:-2}"
    if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
      python manage.py migrate --noinput
    fi
    ;;
  mcp)
    application="mcp_gateway.mcp_asgi:application"
    bind_host="${SITEHITS_MCP_HOST:-127.0.0.1}"
    bind_port="${SITEHITS_MCP_PORT:-8001}"
    workers="${MCP_WEB_CONCURRENCY:-2}"
    ;;
  *)
    echo "usage: scripts/start.sh [web|mcp]" >&2
    exit 64
    ;;
esac

set -- uvicorn "$application" \
  --host "$bind_host" \
  --port "$bind_port" \
  --workers "$workers" \
  --no-access-log

case "${SITEHITS_TRUST_PROXY_HEADERS:-false}" in
  1|true|TRUE|yes|YES)
    trusted_proxy_ips="${SITEHITS_TRUSTED_PROXY_IPS:-127.0.0.1,::1}"
    if [ -z "$trusted_proxy_ips" ] || [ "$trusted_proxy_ips" = "*" ]; then
      echo "trusted proxy headers require an explicit SITEHITS_TRUSTED_PROXY_IPS list" >&2
      exit 78
    fi
    set -- "$@" --proxy-headers --forwarded-allow-ips "$trusted_proxy_ips"
    ;;
  *)
    set -- "$@" --no-proxy-headers
    ;;
esac

exec "$@"
