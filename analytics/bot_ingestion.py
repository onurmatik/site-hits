from datetime import timedelta, timezone as datetime_timezone

from django.utils import timezone

from websites.models import TrackedSite

from .bots import classify_crawler
from .models import BotEvent
from .privacy import hostname_allowed, sanitized_page


class BotAuthenticationError(ValueError):
    pass


class BotIngestionError(ValueError):
    pass


def _authenticated_site(request):
    scheme, separator, key = request.headers.get("Authorization", "").partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not key:
        raise BotAuthenticationError("A bot tracking key is required.")
    try:
        return TrackedSite.objects.get(bot_key=key, is_active=True)
    except TrackedSite.DoesNotExist as exc:
        raise BotAuthenticationError("The bot tracking key is invalid.") from exc


def ingest_bot_event(request, payload):
    site = _authenticated_site(request)
    try:
        page_host, path, _ = sanitized_page(payload.url)
    except ValueError as exc:
        raise BotIngestionError("Page URL is invalid.") from exc
    if not hostname_allowed(page_host, site.allowed_domains):
        raise BotIngestionError("Page URL is not allowed for this site.")

    crawler = classify_crawler(payload.user_agent)
    if crawler is None:
        return None

    now = timezone.now()
    occurred_at = payload.timestamp or now
    if occurred_at.tzinfo is None:
        raise BotIngestionError("Event timestamp must include a timezone.")
    occurred_at = occurred_at.astimezone(datetime_timezone.utc)
    if occurred_at > now + timedelta(minutes=5) or occurred_at < now - timedelta(days=7):
        raise BotIngestionError("Event timestamp is outside the accepted window.")

    return BotEvent.objects.create(
        site=site,
        occurred_at=occurred_at,
        path=path,
        status_code=payload.status_code,
        provider=crawler.provider,
        crawler=crawler.crawler,
        category=crawler.category,
        verification=BotEvent.Verification.USER_AGENT,
    )
