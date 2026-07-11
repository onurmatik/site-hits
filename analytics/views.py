from pathlib import Path

from django.contrib.staticfiles import finders
from django.http import FileResponse, Http404
from django.views.decorators.http import require_GET


@require_GET
def tracker_script(request):
    resolved = finders.find("analytics/script.js")
    if not resolved:
        raise Http404("Tracker script is unavailable.")
    response = FileResponse(Path(resolved).open("rb"), content_type="text/javascript; charset=utf-8")
    response["Cache-Control"] = "public, max-age=300, stale-while-revalidate=86400"
    response["X-Content-Type-Options"] = "nosniff"
    return response

