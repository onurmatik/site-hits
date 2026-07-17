import logging
from datetime import timedelta, timezone as datetime_timezone

from django.db.models import Q
from django.utils import timezone

from websites.models import TrackedSite

from .bots import classify_crawler
from .models import BotEvent
from .privacy import hostname_allowed, sanitized_page


logger = logging.getLogger("sitehits.bot_collector")
COLLECTOR_HEARTBEAT_INTERVAL = timedelta(minutes=5)


class BotAuthenticationError(ValueError):
    pass


class BotIngestionError(ValueError):
    pass


def _authenticated_site(request):
    scheme, separator, key = request.headers.get("Authorization", "").partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not key:
        logger.warning("Bot collector request is missing a valid bearer credential.")
        raise BotAuthenticationError("A bot tracking key is required.")
    try:
        return TrackedSite.objects.get(bot_key=key, is_active=True)
    except TrackedSite.DoesNotExist as exc:
        logger.warning("Bot collector request used an invalid or inactive credential.")
        raise BotAuthenticationError("The bot tracking key is invalid.") from exc


def _record_collector_activity(site, occurred_at, *, accepted=False):
    queryset = TrackedSite.objects.filter(pk=site.pk)
    if accepted:
        queryset.update(
            bot_collector_last_seen_at=occurred_at,
            bot_collector_last_event_at=occurred_at,
        )
        return

    cutoff = occurred_at - COLLECTOR_HEARTBEAT_INTERVAL
    queryset.filter(
        Q(bot_collector_last_seen_at__isnull=True)
        | Q(bot_collector_last_seen_at__lt=cutoff)
    ).update(bot_collector_last_seen_at=occurred_at)


def ingest_bot_event(request, payload):
    site = _authenticated_site(request)
    try:
        page_host, path, _ = sanitized_page(payload.url)
    except ValueError as exc:
        raise BotIngestionError("Page URL is invalid.") from exc
    if not hostname_allowed(page_host, site.allowed_domains):
        logger.warning(
            "Bot collector request used a hostname outside the site's allowlist.",
            extra={"site_slug": site.slug, "page_host": page_host},
        )
        raise BotIngestionError("Page URL is not allowed for this site.")

    now = timezone.now()
    _record_collector_activity(site, now)
    crawler = classify_crawler(payload.user_agent)
    if crawler is None:
        return None

    occurred_at = payload.timestamp or now
    if occurred_at.tzinfo is None:
        raise BotIngestionError("Event timestamp must include a timezone.")
    occurred_at = occurred_at.astimezone(datetime_timezone.utc)
    if occurred_at > now + timedelta(minutes=5) or occurred_at < now - timedelta(days=7):
        raise BotIngestionError("Event timestamp is outside the accepted window.")

    event = BotEvent.objects.create(
        site=site,
        occurred_at=occurred_at,
        path=path,
        status_code=payload.status_code,
        provider=crawler.provider,
        crawler=crawler.crawler,
        category=crawler.category,
        verification=BotEvent.Verification.USER_AGENT,
    )
    _record_collector_activity(site, now, accepted=True)
    logger.info(
        "Bot collector accepted a known crawler request.",
        extra={"site_slug": site.slug, "crawler": crawler.crawler, "path": path},
    )
    return event
