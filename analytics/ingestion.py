from datetime import timedelta, timezone as datetime_timezone

from django.utils import timezone

from websites.models import TrackedSite

from .automation import assess_browser_automation
from .models import AnalyticsEvent
from .product_ingestion import actor_hash_from_token, validate_metric_contract
from .privacy import (
    EVENT_NAME_PATTERN,
    client_ip,
    daily_visitor_hash,
    device_details,
    hostname_allowed,
    location_for_ip,
    origin_hostname,
    sanitized_page,
    sanitized_properties,
    sanitized_referrer,
)


class IngestionError(ValueError):
    pass


def ingest_event(request, payload):
    try:
        site = TrackedSite.objects.get(public_key=payload.site_key, is_active=True)
    except TrackedSite.DoesNotExist as exc:
        raise IngestionError("Unknown or inactive site key.") from exc

    origin_host = origin_hostname(request.headers.get("Origin", ""))
    if not origin_host or not hostname_allowed(origin_host, site.allowed_domains):
        raise IngestionError("Origin is not allowed for this site.")

    try:
        page_host, path, utm = sanitized_page(payload.url)
    except ValueError as exc:
        raise IngestionError("Page URL is invalid.") from exc
    if not hostname_allowed(page_host, site.allowed_domains):
        raise IngestionError("Page URL is not allowed for this site.")

    if payload.event_type == AnalyticsEvent.EventType.CUSTOM:
        if not payload.event_name or not EVENT_NAME_PATTERN.fullmatch(payload.event_name):
            raise IngestionError("Custom event name is invalid.")
    elif payload.event_name:
        raise IngestionError("Pageviews cannot include an event name.")

    now = timezone.now()
    occurred_at = payload.timestamp
    if occurred_at.tzinfo is None:
        raise IngestionError("Event timestamp must include a timezone.")
    occurred_at = occurred_at.astimezone(datetime_timezone.utc)
    if occurred_at > now + timedelta(minutes=5) or occurred_at < now - timedelta(days=7):
        raise IngestionError("Event timestamp is outside the accepted window.")

    user_agent = request.headers.get("User-Agent", "")
    device = device_details(user_agent)
    if device["is_bot"]:
        return None
    automation = assess_browser_automation(user_agent, payload.automation)

    ip_address = client_ip(request)
    location = location_for_ip(ip_address)
    referrer_domain, referrer_path = sanitized_referrer(payload.referrer)
    properties = sanitized_properties(payload.properties)
    actor_hash = actor_hash_from_token(site, payload.actor_token)
    metric_unit = validate_metric_contract(
        site,
        payload.event_name,
        payload.value,
        payload.unit,
        actor_hash=actor_hash,
    ) if payload.event_type == AnalyticsEvent.EventType.CUSTOM else ""
    if payload.event_type == AnalyticsEvent.EventType.PAGEVIEW and (payload.value is not None or payload.unit):
        raise IngestionError("Pageviews cannot include a metric value.")

    return AnalyticsEvent.objects.create(
        site=site,
        event_type=payload.event_type,
        event_name=payload.event_name,
        source=AnalyticsEvent.Source.BROWSER,
        occurred_at=occurred_at,
        visitor_hash=daily_visitor_hash(site, ip_address, user_agent, now.date()),
        session_id=payload.session_id,
        actor_hash=actor_hash,
        metric_value=payload.value,
        metric_unit=metric_unit,
        path=path,
        referrer_domain=referrer_domain,
        referrer_path=referrer_path,
        **location,
        device=device["device"],
        browser=device["browser"],
        operating_system=device["operating_system"],
        language=payload.language,
        client_timezone=payload.timezone,
        viewport_width=payload.viewport.width,
        viewport_height=payload.viewport.height,
        screen_width=payload.screen.width,
        screen_height=payload.screen.height,
        automation_score=automation.score,
        automation_reasons=list(automation.reasons),
        properties=properties,
        **utm,
    )
