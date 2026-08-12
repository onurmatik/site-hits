import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

django_application = get_asgi_application()

from mcp_gateway.http import build_application
from mcp_gateway.server import mcp

application = build_application(mcp, django_application)
