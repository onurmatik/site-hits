from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import connection
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify

from websites.models import TrackedSite


ONBOARDING_SESSION_KEY = "sitehits_onboarding_website"


def _website_details(value):
    raw_value = (value or "").strip()
    if not raw_value:
        raise ValidationError("Enter your website address.")

    candidate = raw_value if "://" in raw_value else f"https://{raw_value}"
    try:
        URLValidator(schemes=["http", "https"])(candidate)
        parsed = urlsplit(candidate)
        port = parsed.port
    except (ValidationError, ValueError) as exc:
        raise ValidationError("Enter a valid website address.") from exc

    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValidationError("Enter a domain such as example.com.")

    hostname = parsed.hostname.rstrip(".").lower()
    if port and port not in {80, 443}:
        hostname = f"{hostname}:{port}"
    if ":" in hostname:
        raise ValidationError("Enter a hostname without a custom port.")

    label = hostname.removeprefix("www.").split(".")[0]
    name = label.replace("-", " ").replace("_", " ").strip().title() or "Website"
    return {"hostname": hostname, "name": name}


def _unique_site_slug(name):
    base = slugify(name)[:70] or "website"
    slug = base
    suffix = 2
    while TrackedSite.objects.filter(slug=slug).exists():
        slug = f"{base[: 79 - len(str(suffix))]}-{suffix}"
        suffix += 1
    return slug


def _tracking_snippet(site):
    base_url = settings.SITEHITS_BASE_URL
    return (
        f'<script defer src="{base_url}/js/script.js" '
        f'data-site-key="{site.public_key}" '
        f'data-api-url="{base_url}/api/events"></script>'
    )


def home(request, *, website="", error="", status=200):
    starting_over = request.GET.get("start") == "over"
    if request.user.is_authenticated and not starting_over:
        return redirect("dashboard-all")
    if starting_over:
        request.session.pop(ONBOARDING_SESSION_KEY, None)
    return render(
        request,
        "onboarding/landing.html",
        {"website": website, "website_error": error},
        status=status,
    )


def start_onboarding(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    website = request.POST.get("website", "")
    try:
        details = _website_details(website)
    except ValidationError as exc:
        return home(request, website=website, error=exc.messages[0], status=400)

    request.session[ONBOARDING_SESSION_KEY] = details
    if request.user.is_authenticated:
        return redirect("onboarding")
    signup_url = reverse("signup")
    return redirect(f"{signup_url}?next={reverse('onboarding')}")


@login_required
def onboarding(request):
    details = request.session.get(ONBOARDING_SESSION_KEY)
    if not details:
        return redirect("home")

    if request.method == "POST":
        candidate_sites = TrackedSite.objects.filter(is_active=True)
        if not request.user.is_superuser:
            candidate_sites = candidate_sites.filter(owner=request.user)
        existing_site = next(
            (
                site
                for site in candidate_sites
                if details["hostname"] in site.allowed_domains
            ),
            None,
        )
        if existing_site:
            site = existing_site
        else:
            site = TrackedSite(
                owner=request.user,
                name=details["name"],
                slug=_unique_site_slug(details["name"]),
                allowed_domains=[details["hostname"]],
                timezone=settings.TIME_ZONE,
            )
            site.full_clean()
            site.save()
        request.session.pop(ONBOARDING_SESSION_KEY, None)
        return redirect("onboarding-install", site_slug=site.slug)

    return render(request, "onboarding/confirm.html", {"website": details})


@login_required
def onboarding_install(request, site_slug):
    sites = TrackedSite.objects.all()
    if not request.user.is_superuser:
        sites = sites.filter(owner=request.user)
    site = get_object_or_404(sites, slug=site_slug)
    return render(
        request,
        "onboarding/install.html",
        {"site": site, "tracking_snippet": _tracking_snippet(site)},
    )


def health(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return JsonResponse({"status": "ok"})


@login_required
def dashboard(request, site_slug):
    sites = TrackedSite.objects.filter(is_active=True)
    if not request.user.is_superuser:
        sites = sites.filter(owner=request.user)
    selected = None
    if site_slug != "all":
        selected = get_object_or_404(sites, slug=site_slug)
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
            "sites": sites,
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
