from django.contrib.auth.decorators import user_passes_test
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render

from websites.models import TrackedSite


def health(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return JsonResponse({"status": "ok"})


@user_passes_test(lambda user: user.is_superuser, login_url="login")
def dashboard(request, site_slug):
    selected = None
    if site_slug != "all":
        selected = get_object_or_404(TrackedSite, slug=site_slug, is_active=True)
    period = request.GET.get("period", "last7d")
    if period not in {"today", "last24h", "last7d", "last30d", "last90d"}:
        period = "last7d"
    granularity = request.GET.get("granularity", "auto")
    if granularity not in {"auto", "hourly", "daily"}:
        granularity = "auto"
    return render(
        request,
        "dashboard/dashboard.html",
        {
            "sites": TrackedSite.objects.filter(is_active=True),
            "selected_site": selected,
            "site_slug": site_slug,
            "period": period,
            "granularity": granularity,
            "breakdowns": [
                ("pages", "Top pages", "Views"),
                ("referrers", "Top referrers", "Views"),
                ("countries", "Countries", "Sessions"),
                ("devices", "Devices", "Sessions"),
                ("campaigns", "Campaigns", "Sessions"),
                ("events", "Custom events", "Fired"),
            ],
        },
    )
