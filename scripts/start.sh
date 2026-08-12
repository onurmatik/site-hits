#!/bin/sh
set -eu

python manage.py migrate --noinput
exec uvicorn config.asgi:application \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-2}" \
  --proxy-headers
