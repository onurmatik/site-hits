from html import escape
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import connection
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import slugify
from django.views.decorators.cache import cache_page
from django.views.decorators.clickjacking import xframe_options_exempt

from analytics.reporting import last_hour_widget
from websites.models import TrackedSite


ONBOARDING_SESSION_KEY = "sitehits_onboarding_website"
NEW_SITE_FORM_SESSION_KEY = "sitehits_new_site_form"


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


def _bot_tracking_settings(site):
    return (
        f"SITEHITS_BOT_ENDPOINT={settings.SITEHITS_BASE_URL}/api/bot-events\n"
        f"SITEHITS_BOT_KEY={site.bot_key}"
    )


def _bot_tracking_agent_instruction(site):
    endpoint = f"{settings.SITEHITS_BASE_URL}/api/bot-events"
    return (
        f"Add server-side SiteHits bot tracking to {site.name}'s backend middleware. "
        "Keep the existing browser tracker unchanged. For each document or crawler-facing "
        "request, send a best-effort POST after the response is known (or schedule it with "
        "waitUntil when the runtime provides it); never delay the page response. Exclude APIs, "
        "framework internals, and obvious static assets, but keep robots.txt, llms.txt, "
        "llms-full.txt, sitemap XML files, and Markdown content trackable. POST to "
        f"{endpoint} with Authorization: Bearer {site.bot_key} and Content-Type: "
        "application/json. The JSON body must contain url and user_agent, and may contain "
        "status_code and an ISO-8601 timestamp. Keep the bot key server-side and do not expose "
        "it in browser code. Ignore collector failures so analytics can never break requests."
    )


def _site_for_details(user, details):
    candidate_sites = TrackedSite.objects.filter(is_active=True)
    if not user.is_superuser:
        candidate_sites = candidate_sites.filter(owner=user)
    existing_site = next(
        (
            site
            for site in candidate_sites
            if details["hostname"] in site.allowed_domains
        ),
        None,
    )
    if existing_site:
        return existing_site

    site = TrackedSite(
        owner=user,
        name=details["name"],
        slug=_unique_site_slug(details["name"]),
        allowed_domains=[details["hostname"]],
        timezone=settings.TIME_ZONE,
    )
    site.full_clean()
    site.save()
    return site


def _dashboard_return_url(request):
    candidate = request.POST.get("return_to", "")
    if (
        candidate.startswith("/dashboard/")
        and url_has_allowed_host_and_scheme(
            candidate,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        )
    ):
        return candidate
    return reverse("dashboard-all")


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
    dashboard_add = (
        request.user.is_authenticated
        and request.POST.get("flow") == "dashboard-new-site"
    )
    try:
        details = _website_details(website)
    except ValidationError as exc:
        if dashboard_add:
            request.session[NEW_SITE_FORM_SESSION_KEY] = {
                "website": website,
                "error": exc.messages[0],
            }
            return redirect(_dashboard_return_url(request))
        return home(request, website=website, error=exc.messages[0], status=400)

    if dashboard_add:
        site = _site_for_details(request.user, details)
        request.session.pop(NEW_SITE_FORM_SESSION_KEY, None)
        return redirect("onboarding-install", site_slug=site.slug)

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
        site = _site_for_details(request.user, details)
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
        {
            "site": site,
            "tracking_snippet": _tracking_snippet(site),
            "bot_tracking_settings": _bot_tracking_settings(site),
            "bot_tracking_agent_instruction": _bot_tracking_agent_instruction(site),
        },
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
    new_site_form = request.session.pop(NEW_SITE_FORM_SESSION_KEY, {})
    report_now = timezone.localtime(timezone.now(), ZoneInfo(settings.TIME_ZONE))
    widget_url = ""
    widget_embed_code = ""
    widget_agent_instruction = ""
    bot_setup_url = ""
    if selected:
        bot_setup_url = reverse("onboarding-install", args=[selected.slug])
        widget_url = request.build_absolute_uri(
            reverse("site-widget", args=[selected.public_key])
        )
        widget_title = escape(f"Last hour traffic for {selected.name}", quote=True)
        widget_embed_code = (
            f'<iframe src="{widget_url}" title="{widget_title}" width="400" '
            'height="600" style="width:100%;max-width:400px;border:0" '
            'loading="lazy" referrerpolicy="no-referrer"></iframe>'
        )
        widget_agent_instruction = (
            f"Add the following SiteHits last-hour traffic widget to {selected.name}'s "
            "public page where the traffic snapshot should appear. Preserve the iframe "
            "exactly, keep it responsive, and do not expose any additional analytics data "
            "or change existing behavior:\n\n"
            f"{widget_embed_code}"
        )
    return render(
        request,
        "dashboard/dashboard.html",
        {
            "sites": sites,
            "selected_site": selected,
            "site_slug": site_slug,
            "period": period,
            "granularity": granularity,
            "new_site_website": new_site_form.get("website", ""),
            "new_site_error": new_site_form.get("error", ""),
            "report_timezone": settings.TIME_ZONE.replace("_", " ").replace("/", " / "),
            "report_local_time": report_now.strftime("%I:%M %p").lstrip("0"),
            "widget_url": widget_url,
            "widget_embed_code": widget_embed_code,
            "widget_agent_instruction": widget_agent_instruction,
            "bot_setup_url": bot_setup_url,
            "breakdowns": [
                ("pages", "Top pages", "Views"),
                ("referrers", "Top referrers", "Views"),
                ("countries", "Countries", "Sessions"),
                ("regions", "Regions", "Sessions"),
                ("cities", "Cities", "Sessions"),
                ("devices", "Devices", "Sessions"),
                ("campaigns", "Campaigns", "Sessions"),
                ("events", "Custom events", "Fired"),
            ],
        },
    )


@xframe_options_exempt
@cache_page(60)
def site_widget(request, public_key):
    site = get_object_or_404(
        TrackedSite.objects.filter(is_active=True),
        public_key=public_key,
    )
    response = render(
        request,
        "dashboard/widget.html",
        {
            "site": site,
            "snapshot": last_hour_widget(site),
            "sitehits_url": settings.SITEHITS_BASE_URL,
        },
    )
    response["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors *"
    )
    response["Referrer-Policy"] = "no-referrer"
    return response
