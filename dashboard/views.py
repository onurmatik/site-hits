import hashlib
from html import escape
import json
import secrets
import time
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import IntegrityError, connection, transaction
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import slugify
from django.views.decorators.cache import cache_page
from django.views.decorators.clickjacking import xframe_options_exempt

from analytics.reporting import last_hour_widget
from analytics.models import ActivationDefinition, ProductEventDefinition
from websites.models import TrackedSite

from .forms import (
    ActivationDefinitionForm,
    GoalClarificationForm,
    GoalPlanConfirmForm,
    GoalTrackingIntentForm,
    ProductEventDefinitionFormSet,
)
from .goal_planning import (
    GoalPlanningService,
    GoalPlanningServiceError,
    ReconciledGoalPlan,
)
from .product_tracking import product_tracking_agent_instruction, server_event_settings


ONBOARDING_SESSION_KEY = "sitehits_onboarding_website"
NEW_SITE_FORM_SESSION_KEY = "sitehits_new_site_form"
GOAL_DRAFTS_SESSION_KEY = "sitehits_product_metric_drafts"
GOAL_RATE_SESSION_KEY = "sitehits_product_metric_draft_attempts"
GOAL_DRAFT_TTL_SECONDS = 30 * 60
GOAL_MAX_SESSION_DRAFTS = 5


class GoalCatalogChanged(RuntimeError):
    pass


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
        "it in browser code. Treat HTTP 202 with accepted=false as a healthy unrecognized "
        "user-agent response. Log network failures and non-2xx responses with only the HTTP "
        "status, request path, and returned error message; never log the bot key, full URL, or "
        "user-agent. Collector failures must remain best-effort and never break requests."
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
            "product_metrics_settings_url": reverse(
                "product-metrics-settings",
                args=[site.slug],
            ),
        },
    )


def _visible_site(request, site_slug):
    sites = TrackedSite.objects.all()
    if not request.user.is_superuser:
        sites = sites.filter(owner=request.user)
    return get_object_or_404(sites, slug=site_slug)


def _product_catalog_snapshot(site, *, lock=False):
    definitions = ProductEventDefinition.objects.filter(site=site).order_by("event_name", "pk")
    activation = ActivationDefinition.objects.filter(site=site).select_related(
        "start_event",
        "goal_event",
    )
    if lock:
        definitions = definitions.select_for_update()
        activation = activation.select_for_update()
    definitions = list(definitions)
    activation = activation.first()
    payload = {
        "events": [
            {
                "id": definition.pk,
                "event_name": definition.event_name,
                "display_name": definition.display_name,
                "description": definition.description,
                "aggregation": definition.aggregation,
                "unit": definition.unit,
                "updated_at": definition.updated_at.isoformat(),
            }
            for definition in definitions
        ],
        "activation": (
            {
                "id": activation.pk,
                "start_event_id": activation.start_event_id,
                "goal_event_id": activation.goal_event_id,
                "updated_at": activation.updated_at.isoformat(),
            }
            if activation
            else None
        ),
    }
    serialized = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    fingerprint = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return definitions, activation, fingerprint


def _goal_session_drafts(request):
    now = int(time.time())
    stored = request.session.get(GOAL_DRAFTS_SESSION_KEY, {})
    if not isinstance(stored, dict):
        stored = {}
    drafts = {
        draft_id: draft
        for draft_id, draft in stored.items()
        if isinstance(draft_id, str)
        and isinstance(draft, dict)
        and isinstance(draft.get("created_at"), int)
        and now - draft["created_at"] <= GOAL_DRAFT_TTL_SECONDS
    }
    if drafts != stored:
        request.session[GOAL_DRAFTS_SESSION_KEY] = drafts
    return drafts


def _goal_draft_for_request(request, site, draft_id):
    if not isinstance(draft_id, str) or not draft_id:
        return None
    draft = _goal_session_drafts(request).get(draft_id)
    if not draft:
        return None
    if draft.get("site_id") != site.pk or draft.get("user_id") != request.user.pk:
        return None
    return draft


def _store_goal_draft(
    request,
    site,
    *,
    intent,
    plan,
    catalog_fingerprint,
    clarification_answer="",
):
    drafts = _goal_session_drafts(request)
    if len(drafts) >= GOAL_MAX_SESSION_DRAFTS:
        oldest = min(drafts, key=lambda draft_id: drafts[draft_id]["created_at"])
        drafts.pop(oldest, None)
    draft_id = secrets.token_urlsafe(24)
    drafts[draft_id] = {
        "site_id": site.pk,
        "user_id": request.user.pk,
        "created_at": int(time.time()),
        "intent": intent,
        "clarification_answer": clarification_answer,
        "catalog_fingerprint": catalog_fingerprint,
        "plan": plan.model_dump(mode="json"),
    }
    request.session[GOAL_DRAFTS_SESSION_KEY] = drafts
    return draft_id


def _delete_goal_draft(request, draft_id):
    drafts = _goal_session_drafts(request)
    if draft_id in drafts:
        drafts.pop(draft_id, None)
        request.session[GOAL_DRAFTS_SESSION_KEY] = drafts


def _goal_rate_limit_allows_request(request):
    limit = settings.SITEHITS_GOAL_PLANNING_RATE_LIMIT
    if limit <= 0:
        return True
    now = int(time.time())
    attempts = request.session.get(GOAL_RATE_SESSION_KEY, [])
    if not isinstance(attempts, list):
        attempts = []
    attempts = [
        attempt
        for attempt in attempts
        if isinstance(attempt, int) and now - attempt < 60 * 60
    ]
    if len(attempts) >= limit:
        request.session[GOAL_RATE_SESSION_KEY] = attempts
        return False
    attempts.append(now)
    request.session[GOAL_RATE_SESSION_KEY] = attempts
    return True


def _activation_summary(plan):
    if not plan or not plan.activation:
        return None
    labels = {event.event_name: event.display_name for event in plan.events}
    return {
        "start": labels[plan.activation.start_event],
        "goal": labels[plan.activation.goal_event],
    }


def _render_product_metrics(
    request,
    site,
    *,
    step="describe",
    event_formset=None,
    activation_form=None,
    goal_intent_form=None,
    goal_clarification_form=None,
    goal_plan=None,
    goal_draft_id="",
    goal_intent="",
    goal_error="",
    saved="",
    advanced_open=False,
    status=200,
):
    definitions = ProductEventDefinition.objects.filter(site=site)
    activation = ActivationDefinition.objects.filter(site=site).first()
    if event_formset is None:
        event_formset = ProductEventDefinitionFormSet(
            queryset=definitions,
            prefix="events",
            site=site,
        )
    if activation_form is None:
        activation_form = ActivationDefinitionForm(
            instance=activation or ActivationDefinition(site=site),
            prefix="activation",
            site=site,
        )
    if goal_intent_form is None:
        goal_intent_form = GoalTrackingIntentForm(
            initial={"intent": goal_intent} if goal_intent else None
        )
    if goal_clarification_form is None and goal_draft_id:
        goal_clarification_form = GoalClarificationForm(
            initial={"draft_id": goal_draft_id}
        )

    return render(
        request,
        "dashboard/product_metrics_settings.html",
        {
            "site": site,
            "definitions": definitions,
            "activation": activation,
            "event_formset": event_formset,
            "activation_form": activation_form,
            "server_event_settings": server_event_settings(site),
            "server_event_endpoint": (
                f"{settings.SITEHITS_BASE_URL}/api/server-events"
            ),
            "agent_instruction": product_tracking_agent_instruction(site),
            "step": step,
            "goal_intent_form": goal_intent_form,
            "goal_clarification_form": goal_clarification_form,
            "goal_confirm_form": (
                GoalPlanConfirmForm(initial={"draft_id": goal_draft_id})
                if goal_draft_id
                else None
            ),
            "goal_plan": goal_plan,
            "goal_activation_summary": _activation_summary(goal_plan),
            "goal_draft_id": goal_draft_id,
            "goal_intent": goal_intent,
            "goal_error": goal_error,
            "saved": saved,
            "advanced_open": advanced_open,
        },
        status=status,
    )


def _draft_product_metrics_plan(
    request,
    site,
    *,
    intent,
    planning_intent=None,
    clarification_answer="",
):
    intent_form = GoalTrackingIntentForm(initial={"intent": intent})
    if not settings.OPENAI_API_KEY:
        return _render_product_metrics(
            request,
            site,
            goal_intent_form=intent_form,
            goal_error=(
                "AI-assisted setup is not configured yet. Use Advanced setup or try again "
                "after the server key is configured."
            ),
            status=503,
        )
    if not _goal_rate_limit_allows_request(request):
        return _render_product_metrics(
            request,
            site,
            goal_intent_form=intent_form,
            goal_error="Too many drafts were requested. Try again in a little while.",
            status=429,
        )

    definitions, _activation, fingerprint = _product_catalog_snapshot(site)
    try:
        plan = GoalPlanningService(
            model=settings.SITEHITS_GOAL_PLANNING_MODEL,
            api_key=settings.OPENAI_API_KEY,
            timeout=settings.SITEHITS_GOAL_PLANNING_TIMEOUT_SECONDS,
        ).plan(planning_intent or intent, definitions)
    except GoalPlanningServiceError as exc:
        status_by_code = {
            "invalid_intent": 400,
            "invalid_catalog": 409,
            "invalid_plan": 502,
            "empty_response": 502,
        }
        return _render_product_metrics(
            request,
            site,
            goal_intent_form=intent_form,
            goal_error=str(exc),
            status=status_by_code.get(exc.code, 503),
        )

    draft_id = _store_goal_draft(
        request,
        site,
        intent=intent,
        plan=plan,
        catalog_fingerprint=fingerprint,
        clarification_answer=clarification_answer,
    )
    review_url = reverse("product-metrics-settings", args=[site.slug])
    return redirect(f"{review_url}?step=review&draft={draft_id}")


def _apply_goal_plan(site, plan, expected_fingerprint):
    with transaction.atomic():
        locked_site = TrackedSite.objects.select_for_update().get(pk=site.pk)
        definitions, activation, fingerprint = _product_catalog_snapshot(
            locked_site,
            lock=True,
        )
        if fingerprint != expected_fingerprint:
            raise GoalCatalogChanged

        by_name = {definition.event_name: definition for definition in definitions}
        for proposed in plan.events:
            definition = by_name.get(proposed.event_name)
            if definition is None:
                definition = ProductEventDefinition(
                    site=locked_site,
                    event_name=proposed.event_name,
                )
            definition.display_name = proposed.display_name
            definition.description = proposed.description
            definition.aggregation = proposed.aggregation
            definition.unit = proposed.unit
            definition.full_clean()
            definition.save()
            by_name[proposed.event_name] = definition

        if plan.activation:
            configured = activation or ActivationDefinition(site=locked_site)
            configured.start_event = by_name[plan.activation.start_event]
            configured.goal_event = by_name[plan.activation.goal_event]
            configured.full_clean()
            configured.save()


@login_required
def product_metrics_settings(request, site_slug):
    site = _visible_site(request, site_slug)
    definitions = ProductEventDefinition.objects.filter(site=site)
    activation = ActivationDefinition.objects.filter(site=site).first()
    saved = request.GET.get("saved", "")
    action = request.POST.get("action", "") if request.method == "POST" else ""

    if action == "events":
        event_formset = ProductEventDefinitionFormSet(
            request.POST,
            queryset=definitions,
            prefix="events",
            site=site,
        )
        activation_form = ActivationDefinitionForm(
            instance=activation or ActivationDefinition(site=site),
            prefix="activation",
            site=site,
        )
        if event_formset.is_valid():
            instances = event_formset.save(commit=False)
            for deleted in event_formset.deleted_objects:
                deleted.delete()
            for instance in instances:
                instance.site = site
                instance.full_clean()
                instance.save()
            destination = reverse("product-metrics-settings", args=[site.slug])
            return redirect(f"{destination}?saved=events#advanced-setup")
        return _render_product_metrics(
            request,
            site,
            event_formset=event_formset,
            activation_form=activation_form,
            goal_error="Check the highlighted Advanced setup fields.",
            advanced_open=True,
            status=400,
        )

    if action == "activation":
        event_formset = ProductEventDefinitionFormSet(
            queryset=definitions,
            prefix="events",
            site=site,
        )
        activation_form = ActivationDefinitionForm(
            request.POST,
            instance=activation or ActivationDefinition(site=site),
            prefix="activation",
            site=site,
        )
        if activation_form.is_valid():
            if not activation_form.cleaned_data["enabled"]:
                if activation:
                    activation.delete()
            else:
                configured = activation_form.save(commit=False)
                configured.site = site
                configured.full_clean()
                configured.save()
            destination = reverse("product-metrics-settings", args=[site.slug])
            return redirect(f"{destination}?saved=activation#advanced-setup")
        return _render_product_metrics(
            request,
            site,
            event_formset=event_formset,
            activation_form=activation_form,
            goal_error="Check the highlighted Advanced setup fields.",
            advanced_open=True,
            status=400,
        )

    if action == "goal_draft":
        intent_form = GoalTrackingIntentForm(request.POST)
        if not intent_form.is_valid():
            return _render_product_metrics(
                request,
                site,
                goal_intent_form=intent_form,
                goal_error="Describe the outcome you want SiteHits to track.",
                status=400,
            )
        return _draft_product_metrics_plan(
            request,
            site,
            intent=intent_form.cleaned_data["intent"],
        )

    if action == "goal_clarify":
        clarification_form = GoalClarificationForm(request.POST)
        draft_id = request.POST.get("draft_id", "")
        draft = _goal_draft_for_request(request, site, draft_id)
        if not draft:
            return _render_product_metrics(
                request,
                site,
                goal_error="That draft expired. Describe what you want to track again.",
                status=410,
            )
        try:
            plan = ReconciledGoalPlan.model_validate(draft["plan"])
        except (KeyError, TypeError, ValueError):
            _delete_goal_draft(request, draft_id)
            return _render_product_metrics(
                request,
                site,
                goal_error="That draft is no longer valid. Create a new tracking plan.",
                status=409,
            )
        if not clarification_form.is_valid():
            return _render_product_metrics(
                request,
                site,
                step="review",
                goal_plan=plan,
                goal_draft_id=draft_id,
                goal_intent=draft["intent"],
                goal_clarification_form=clarification_form,
                goal_error="Answer the clarification before continuing.",
                status=400,
            )
        answer = clarification_form.cleaned_data["clarification"]
        planning_intent = (
            f"{draft['intent']}\n\n"
            f"Clarification question: {plan.clarification}\n"
            f"Answer: {answer}"
        )
        return _draft_product_metrics_plan(
            request,
            site,
            intent=draft["intent"],
            planning_intent=planning_intent,
            clarification_answer=answer,
        )

    if action == "goal_confirm":
        confirm_form = GoalPlanConfirmForm(request.POST)
        if not confirm_form.is_valid():
            return _render_product_metrics(
                request,
                site,
                goal_error="The tracking-plan draft is missing or invalid.",
                status=400,
            )
        draft_id = confirm_form.cleaned_data["draft_id"]
        draft = _goal_draft_for_request(request, site, draft_id)
        if not draft:
            return _render_product_metrics(
                request,
                site,
                goal_error="That draft expired. Describe what you want to track again.",
                status=410,
            )
        try:
            plan = ReconciledGoalPlan.model_validate(draft["plan"])
        except (KeyError, TypeError, ValueError):
            _delete_goal_draft(request, draft_id)
            return _render_product_metrics(
                request,
                site,
                goal_error="That draft is no longer valid. Create a new tracking plan.",
                status=409,
            )
        if not plan.can_install:
            return _render_product_metrics(
                request,
                site,
                step="review",
                goal_plan=plan,
                goal_draft_id=draft_id,
                goal_intent=draft["intent"],
                goal_error="Resolve the review items before approving this plan.",
                status=409,
            )
        try:
            _apply_goal_plan(site, plan, draft["catalog_fingerprint"])
        except GoalCatalogChanged:
            return _render_product_metrics(
                request,
                site,
                step="review",
                goal_plan=plan,
                goal_draft_id=draft_id,
                goal_intent=draft["intent"],
                goal_error=(
                    "The event catalog changed after this draft was created. Draft the plan "
                    "again before approving it."
                ),
                status=409,
            )
        except (IntegrityError, ValidationError, KeyError):
            return _render_product_metrics(
                request,
                site,
                step="review",
                goal_plan=plan,
                goal_draft_id=draft_id,
                goal_intent=draft["intent"],
                goal_error="The plan could not be saved. Review it and try again.",
                status=409,
            )
        _delete_goal_draft(request, draft_id)
        destination = reverse("product-metrics-settings", args=[site.slug])
        return redirect(f"{destination}?step=install&saved=plan")

    step = request.GET.get("step", "describe")
    draft_id = request.GET.get("draft", "")
    if step in {"review", "describe"} and draft_id:
        draft = _goal_draft_for_request(request, site, draft_id)
        if not draft:
            return _render_product_metrics(
                request,
                site,
                goal_error="That draft expired. Describe what you want to track again.",
                saved=saved,
                status=410 if step == "review" else 200,
            )
        if step == "describe":
            return _render_product_metrics(
                request,
                site,
                goal_intent=draft["intent"],
                saved=saved,
            )
        try:
            plan = ReconciledGoalPlan.model_validate(draft["plan"])
        except (KeyError, TypeError, ValueError):
            _delete_goal_draft(request, draft_id)
            return _render_product_metrics(
                request,
                site,
                goal_error="That draft is no longer valid. Create a new tracking plan.",
                status=409,
            )
        return _render_product_metrics(
            request,
            site,
            step="review",
            goal_plan=plan,
            goal_draft_id=draft_id,
            goal_intent=draft["intent"],
            saved=saved,
        )

    if step == "review":
        return _render_product_metrics(
            request,
            site,
            goal_error="That draft expired. Describe what you want to track again.",
            saved=saved,
            status=410,
        )
    if step not in {"describe", "install"}:
        step = "describe"
    return _render_product_metrics(
        request,
        site,
        step=step,
        saved=saved,
        advanced_open=saved in {"events", "activation"},
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
    product_metrics_settings_url = ""
    if selected:
        bot_setup_url = reverse("onboarding-install", args=[selected.slug])
        product_metrics_settings_url = reverse(
            "product-metrics-settings",
            args=[selected.slug],
        )
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
            "product_metrics_settings_url": product_metrics_settings_url,
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
