import hashlib
import hmac
import logging
import re
from datetime import timedelta, timezone as datetime_timezone
from decimal import Decimal

import jwt
from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone

from websites.models import TrackedSite

from .models import ActivationDefinition, AnalyticsEvent, ProductEventDefinition
from .privacy import EVENT_NAME_PATTERN, sanitized_properties


logger = logging.getLogger("sitehits.product_collector")
COLLECTOR_HEARTBEAT_INTERVAL = timedelta(minutes=5)
ACTOR_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MAX_METRIC_VALUE = Decimal("100000000000000")


class ProductAuthenticationError(ValueError):
    pass


class ProductIngestionError(ValueError):
    pass


def actor_hash_for_site(site, actor_id):
    return hmac.new(
        site.server_event_key.encode(),
        str(actor_id).encode(),
        hashlib.sha256,
    ).hexdigest()


def idempotency_hash_for_site(site, event_id):
    return hmac.new(
        site.server_event_key.encode(),
        f"event:{event_id}".encode(),
        hashlib.sha256,
    ).hexdigest()


def actor_token_for_site(site, actor_id, *, now=None, max_age=timedelta(hours=1)):
    now = now or timezone.now()
    lifetime = min(max_age, timedelta(hours=1))
    return jwt.encode(
        {
            "iss": site.public_key,
            "aud": "sitehits",
            "sub": actor_hash_for_site(site, actor_id),
            "iat": int(now.timestamp()),
            "exp": int((now + lifetime).timestamp()),
        },
        site.server_event_key,
        algorithm="HS256",
    )


def actor_hash_from_token(site, token):
    if not token:
        return ""
    try:
        claims = jwt.decode(
            token,
            site.server_event_key,
            algorithms=["HS256"],
            audience="sitehits",
            issuer=site.public_key,
            options={"require": ["iss", "aud", "sub", "iat", "exp"]},
            leeway=30,
        )
        issued_at = int(claims["iat"])
        expires_at = int(claims["exp"])
        actor_hash = str(claims["sub"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        return ""
    if expires_at <= issued_at or expires_at - issued_at > 3600:
        return ""
    return actor_hash if ACTOR_HASH_PATTERN.fullmatch(actor_hash) else ""


def authenticated_product_site(request):
    scheme, separator, key = request.headers.get("Authorization", "").partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not key:
        logger.warning("Product collector request is missing a valid bearer credential.")
        raise ProductAuthenticationError("A server event key is required.")
    try:
        return TrackedSite.objects.get(server_event_key=key, is_active=True)
    except TrackedSite.DoesNotExist as exc:
        logger.warning("Product collector request used an invalid or inactive credential.")
        raise ProductAuthenticationError("The server event key is invalid.") from exc


def _record_collector_activity(site, occurred_at, *, accepted=False):
    queryset = TrackedSite.objects.filter(pk=site.pk)
    if accepted:
        queryset.update(
            server_event_collector_last_seen_at=occurred_at,
            server_event_collector_last_event_at=occurred_at,
        )
        return
    cutoff = occurred_at - COLLECTOR_HEARTBEAT_INTERVAL
    queryset.filter(
        Q(server_event_collector_last_seen_at__isnull=True)
        | Q(server_event_collector_last_seen_at__lt=cutoff)
    ).update(server_event_collector_last_seen_at=occurred_at)


def _occurred_at(value, now):
    occurred_at = value or now
    if occurred_at.tzinfo is None:
        raise ProductIngestionError("Event timestamp must include a timezone.")
    occurred_at = occurred_at.astimezone(datetime_timezone.utc)
    if occurred_at > now + timedelta(minutes=5) or occurred_at < now - timedelta(days=7):
        raise ProductIngestionError("Event timestamp is outside the accepted window.")
    return occurred_at


def _event_path(value):
    value = (value or "").strip()
    if not value:
        return ""
    if not value.startswith("/") or "?" in value or "#" in value or "://" in value:
        raise ProductIngestionError("Event path must be a query-free absolute path.")
    return value[:2048]


def validate_metric_contract(
    site,
    event_name,
    value,
    unit,
    *,
    actor_hash="",
    strict_actor=False,
):
    if not EVENT_NAME_PATTERN.fullmatch(event_name):
        raise ProductIngestionError("Custom event name is invalid.")
    definition = ProductEventDefinition.objects.filter(
        site=site,
        event_name=event_name,
    ).first()
    numeric = definition and definition.aggregation in {
        ProductEventDefinition.Aggregation.SUM,
        ProductEventDefinition.Aggregation.AVERAGE,
    }
    if numeric and value is None:
        raise ProductIngestionError("This metric requires a numeric value.")
    if value is not None:
        if not unit:
            raise ProductIngestionError("Numeric event values require a unit.")
        if (
            value.copy_abs() >= MAX_METRIC_VALUE
            or max(-value.as_tuple().exponent, 0) > 6
        ):
            raise ProductIngestionError("Metric value is outside the supported precision.")
    elif unit:
        raise ProductIngestionError("Metric unit requires a numeric value.")
    canonical_unit = unit
    if definition and definition.unit:
        if unit.casefold() != definition.unit.casefold():
            raise ProductIngestionError("Metric unit does not match its event definition.")
        canonical_unit = definition.unit
    requires_actor = bool(
        definition
        and (
            definition.aggregation == ProductEventDefinition.Aggregation.UNIQUE_ACTORS
            or ActivationDefinition.objects.filter(
                Q(start_event=definition) | Q(goal_event=definition)
            ).exists()
        )
    )
    if strict_actor and requires_actor and not actor_hash:
        raise ProductIngestionError("This event requires an actor_id.")
    return canonical_unit


def ingest_server_event(request, payload):
    site = authenticated_product_site(request)
    now = timezone.now()
    _record_collector_activity(site, now)
    idempotency_hash = idempotency_hash_for_site(site, payload.event_id)
    existing = AnalyticsEvent.objects.filter(
        site=site,
        idempotency_hash=idempotency_hash,
    ).first()
    if existing:
        return existing, True
    occurred_at = _occurred_at(payload.timestamp, now)
    actor_hash = actor_hash_for_site(site, payload.actor_id) if payload.actor_id else ""
    metric_unit = validate_metric_contract(
        site,
        payload.event_name,
        payload.value,
        payload.unit,
        actor_hash=actor_hash,
        strict_actor=True,
    )
    values = {
        "site": site,
        "event_type": AnalyticsEvent.EventType.CUSTOM,
        "event_name": payload.event_name,
        "source": AnalyticsEvent.Source.SERVER,
        "occurred_at": occurred_at,
        "actor_hash": actor_hash,
        "idempotency_hash": idempotency_hash,
        "metric_value": payload.value,
        "metric_unit": metric_unit,
        "path": _event_path(payload.path),
        "properties": sanitized_properties(payload.properties),
    }
    try:
        event = AnalyticsEvent.objects.create(**values)
    except IntegrityError:
        event = AnalyticsEvent.objects.get(site=site, idempotency_hash=idempotency_hash)
        return event, True
    _record_collector_activity(site, now, accepted=True)
    logger.info(
        "Product collector accepted a server event.",
        extra={"site_slug": site.slug, "event_name": payload.event_name},
    )
    return event, False


def forget_actor(request, actor_id):
    site = authenticated_product_site(request)
    actor_hash = actor_hash_for_site(site, actor_id)
    deleted, _ = AnalyticsEvent.objects.filter(site=site, actor_hash=actor_hash).delete()
    return deleted
