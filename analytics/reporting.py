from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Case, CharField, Count, F, Max, Min, Q, Value, When
from django.db.models.functions import Cast, Concat, TruncDay, TruncHour, TruncMinute
from django.utils import timezone

from websites.models import TrackedSite

from .models import AnalyticsEvent, BotEvent


VALID_PERIODS = {"today", "last24h", "last7d", "last30d", "last90d"}
VALID_GRANULARITIES = {"auto", "hourly", "daily"}
BREAKDOWN_CONFIG = {
    "pages": ("path", Q(event_type="pageview"), False),
    "referrers": ("referrer_domain", Q(event_type="pageview"), False),
    "countries": ("country_name", Q(), True),
    "regions": ("region_name", Q(), True),
    "cities": ("city_name", Q(), True),
    "devices": ("device", Q(), True),
    "browsers": ("browser", Q(), True),
    "os": ("operating_system", Q(), True),
    "campaigns": ("utm_campaign", ~Q(utm_campaign=""), True),
    "events": ("event_name", Q(event_type="custom") & ~Q(event_name=""), False),
}
BOT_CATEGORY_LABELS = {
    BotEvent.Category.ANSWER: "AI answers",
    BotEvent.Category.INDEXING: "Indexing",
    BotEvent.Category.TRAINING: "Training",
    BotEvent.Category.OTHER: "Other",
}


def scoped_identifier(field):
    return Concat(Cast("site_id", CharField()), Value(":"), field)


@dataclass(frozen=True)
class PeriodRange:
    start: datetime
    end: datetime
    previous_start: datetime
    previous_end: datetime
    timezone: ZoneInfo


def report_timezone(site):
    return ZoneInfo(site.timezone if site else settings.TIME_ZONE)


def resolve_period(period, site=None, now=None):
    if period not in VALID_PERIODS:
        raise ValueError("Unknown period.")
    now = now or timezone.now()
    tzinfo = report_timezone(site)
    local_now = now.astimezone(tzinfo)
    if period == "today":
        start = datetime.combine(local_now.date(), time.min, tzinfo=tzinfo)
    else:
        durations = {
            "last24h": timedelta(hours=24),
            "last7d": timedelta(days=7),
            "last30d": timedelta(days=30),
            "last90d": timedelta(days=90),
        }
        start = local_now - durations[period]
    end = local_now
    duration = end - start
    return PeriodRange(start, end, start - duration, start, tzinfo)


def selected_site(site_slug, sites=None):
    if site_slug == "all":
        return None
    queryset = sites if sites is not None else TrackedSite.objects.filter(is_active=True)
    try:
        return queryset.get(slug=site_slug, is_active=True)
    except TrackedSite.DoesNotExist as exc:
        raise ValueError("Unknown site.") from exc


def event_queryset(site, start, end, sites=None):
    queryset = AnalyticsEvent.objects.filter(occurred_at__gte=start, occurred_at__lt=end)
    if site:
        return queryset.filter(site=site)
    if sites is not None:
        return queryset.filter(site__in=sites)
    return queryset.filter(site__is_active=True)


def bot_event_queryset(site, start, end, sites=None):
    queryset = BotEvent.objects.filter(occurred_at__gte=start, occurred_at__lt=end)
    if site:
        return queryset.filter(site=site)
    if sites is not None:
        return queryset.filter(site__in=sites)
    return queryset.filter(site__is_active=True)


def metric_values(queryset):
    visitors = queryset.values("site_id", "visitor_hash").distinct().count()
    pageviews = queryset.filter(event_type="pageview").count()
    session_rows = list(
        queryset.values("site_id", "session_id").annotate(
            event_count=Count("id"),
            pageview_count=Count("id", filter=Q(event_type="pageview")),
            first_seen=Min("occurred_at"),
            last_seen=Max("occurred_at"),
        )
    )
    sessions = len(session_rows)
    bounces = sum(
        row["event_count"] == 1 and row["pageview_count"] == 1 for row in session_rows
    )
    durations = [
        max(0, (row["last_seen"] - row["first_seen"]).total_seconds())
        for row in session_rows
    ]
    return {
        "visitors": visitors,
        "sessions": sessions,
        "pageviews": pageviews,
        "bounce_rate": round((bounces / sessions * 100) if sessions else 0, 1),
        "avg_session_duration": round(sum(durations) / sessions) if sessions else 0,
    }


def metric_values_by_site(queryset, site_ids):
    values = {
        site_id: {
            "visitors": 0,
            "sessions": 0,
            "pageviews": 0,
            "bounce_rate": 0,
            "avg_session_duration": 0,
        }
        for site_id in site_ids
    }
    for row in (
        queryset.values("site_id")
        .annotate(
            visitors=Count("visitor_hash", distinct=True),
            pageviews=Count("id", filter=Q(event_type="pageview")),
        )
        .order_by()
    ):
        values[row["site_id"]]["visitors"] = row["visitors"]
        values[row["site_id"]]["pageviews"] = row["pageviews"]

    session_rows = queryset.values("site_id", "session_id").annotate(
        event_count=Count("id"),
        pageview_count=Count("id", filter=Q(event_type="pageview")),
        first_seen=Min("occurred_at"),
        last_seen=Max("occurred_at"),
    )
    durations = {site_id: [] for site_id in site_ids}
    bounces = {site_id: 0 for site_id in site_ids}
    for row in session_rows:
        site_id = row["site_id"]
        values[site_id]["sessions"] += 1
        bounces[site_id] += row["event_count"] == 1 and row["pageview_count"] == 1
        durations[site_id].append(
            max(0, (row["last_seen"] - row["first_seen"]).total_seconds())
        )

    for site_id, metrics in values.items():
        sessions = metrics["sessions"]
        metrics["bounce_rate"] = round(
            (bounces[site_id] / sessions * 100) if sessions else 0,
            1,
        )
        metrics["avg_session_duration"] = (
            round(sum(durations[site_id]) / sessions) if sessions else 0
        )
    return values


def delta(current, previous):
    if previous == 0:
        return 0 if current == 0 else None
    return round((current - previous) / previous * 100, 1)


def overview(site_slug, period, sites=None):
    site = selected_site(site_slug, sites)
    ranges = resolve_period(period, site)
    current = metric_values(event_queryset(site, ranges.start, ranges.end, sites))
    previous = metric_values(
        event_queryset(site, ranges.previous_start, ranges.previous_end, sites)
    )
    return {
        "site": site_slug,
        "period": period,
        "timezone": str(ranges.timezone),
        "current": current,
        "previous": previous,
        "deltas": {key: delta(current[key], previous[key]) for key in current},
    }


def site_overviews(period, sites=None):
    if sites is None:
        sites = TrackedSite.objects.filter(is_active=True)
    site_list = list(sites)
    site_ids = [site.pk for site in site_list]
    ranges = resolve_period(period)
    current = metric_values_by_site(
        event_queryset(None, ranges.start, ranges.end, sites),
        site_ids,
    )
    previous = metric_values_by_site(
        event_queryset(None, ranges.previous_start, ranges.previous_end, sites),
        site_ids,
    )
    return {
        "site": "all",
        "period": period,
        "timezone": str(ranges.timezone),
        "sites": [
            {
                "slug": site.slug,
                "name": site.name,
                "domains": site.allowed_domains,
                "current": current[site.pk],
                "previous": previous[site.pk],
                "deltas": {
                    key: delta(current[site.pk][key], previous[site.pk][key])
                    for key in current[site.pk]
                },
            }
            for site in site_list
        ],
    }


def timeseries(site_slug, period, granularity, sites=None):
    if granularity not in VALID_GRANULARITIES:
        raise ValueError("Unknown granularity.")
    site = selected_site(site_slug, sites)
    ranges = resolve_period(period, site)
    resolved = granularity
    if granularity == "auto":
        resolved = "hourly" if period in {"today", "last24h"} else "daily"
    trunc = TruncHour("occurred_at", tzinfo=ranges.timezone) if resolved == "hourly" else TruncDay(
        "occurred_at", tzinfo=ranges.timezone
    )
    rows = (
        event_queryset(site, ranges.start, ranges.end, sites)
        .annotate(bucket=trunc)
        .values("bucket")
        .annotate(
            visitors=Count(scoped_identifier("visitor_hash"), distinct=True),
            sessions=Count(scoped_identifier("session_id"), distinct=True),
            pageviews=Count("id", filter=Q(event_type="pageview")),
        )
        .order_by("bucket")
    )
    return {
        "site": site_slug,
        "period": period,
        "granularity": resolved,
        "timezone": str(ranges.timezone),
        "data": [
            {
                "bucket": row["bucket"].isoformat(),
                "visitors": row["visitors"],
                "sessions": row["sessions"],
                "pageviews": row["pageviews"],
            }
            for row in rows
        ],
    }


def breakdown(site_slug, period, dimension, limit=8, sites=None):
    if dimension not in BREAKDOWN_CONFIG:
        raise ValueError("Unknown breakdown dimension.")
    site = selected_site(site_slug, sites)
    ranges = resolve_period(period, site)
    field, filters, distinct_sessions = BREAKDOWN_CONFIG[dimension]
    empty_label = "Direct" if dimension == "referrers" else "Unknown"
    if dimension == "regions":
        label = Case(
            When(region_name="", then=Value(empty_label)),
            default=Concat(
                "region_name",
                Case(
                    When(country_name="", then=Value("")),
                    default=Concat(Value(", "), "country_name"),
                    output_field=CharField(),
                ),
            ),
            output_field=CharField(),
        )
    elif dimension == "cities":
        label = Case(
            When(city_name="", then=Value(empty_label)),
            default=Concat(
                "city_name",
                Case(
                    When(
                        Q(region_name="") | Q(region_name=F("city_name")),
                        then=Value(""),
                    ),
                    default=Concat(Value(", "), "region_name"),
                    output_field=CharField(),
                ),
                Case(
                    When(country_name="", then=Value("")),
                    default=Concat(Value(", "), "country_name"),
                    output_field=CharField(),
                ),
            ),
            output_field=CharField(),
        )
    else:
        label = Case(
            When(**{field: ""}, then=Value(empty_label)),
            default=field,
            output_field=CharField(),
        )
    queryset = event_queryset(site, ranges.start, ranges.end, sites).filter(filters)
    counter = (
        Count(scoped_identifier("session_id"), distinct=True)
        if distinct_sessions
        else Count("id")
    )
    rows = list(
        queryset.annotate(label=label)
        .values("label")
        .annotate(count=counter)
        .order_by("-count", "label")[: max(1, min(limit, 50))]
    )
    total = sum(row["count"] for row in rows)
    return {
        "site": site_slug,
        "period": period,
        "dimension": dimension,
        "data": [
            {
                "label": row["label"] or "Unknown",
                "count": row["count"],
                "percentage": round(row["count"] / total * 100, 1) if total else 0,
            }
            for row in rows
        ],
    }


def bot_traffic(site_slug, period, limit=8, sites=None):
    site = selected_site(site_slug, sites)
    ranges = resolve_period(period, site)
    queryset = bot_event_queryset(site, ranges.start, ranges.end, sites)
    total = queryset.count()
    category_counts = {
        row["category"]: row["count"]
        for row in queryset.values("category").annotate(count=Count("id"))
    }
    row_limit = max(1, min(limit, 50))
    providers = list(
        queryset.values("provider")
        .annotate(count=Count("id"))
        .order_by("-count", "provider")[:row_limit]
    )
    pages = list(
        queryset.values("path", "status_code")
        .annotate(count=Count("id"))
        .order_by("-count", "path", "status_code")[:row_limit]
    )

    return {
        "site": site_slug,
        "period": period,
        "timezone": str(ranges.timezone),
        "total": total,
        "categories": [
            {
                "key": key,
                "label": label,
                "count": category_counts.get(key, 0),
                "percentage": (
                    round(category_counts.get(key, 0) / total * 100, 1) if total else 0
                ),
            }
            for key, label in BOT_CATEGORY_LABELS.items()
        ],
        "providers": [
            {
                "label": row["provider"],
                "count": row["count"],
                "percentage": round(row["count"] / total * 100, 1) if total else 0,
            }
            for row in providers
        ],
        "pages": [
            {
                "path": row["path"],
                "status_code": row["status_code"],
                "count": row["count"],
                "percentage": round(row["count"] / total * 100, 1) if total else 0,
            }
            for row in pages
        ],
        "verification": {
            "ip_verified": queryset.filter(
                verification=BotEvent.Verification.IP_VERIFIED
            ).count(),
            "user_agent": queryset.filter(
                verification=BotEvent.Verification.USER_AGENT
            ).count(),
        },
    }


def last_hour_widget(site, now=None):
    """Return a public, aggregate-only snapshot for a site's embed widget."""
    tzinfo = report_timezone(site)
    current_minute = (now or timezone.now()).astimezone(tzinfo).replace(
        second=0,
        microsecond=0,
    )
    end = current_minute + timedelta(minutes=1)
    start = end - timedelta(hours=1)
    queryset = AnalyticsEvent.objects.filter(
        site=site,
        occurred_at__gte=start,
        occurred_at__lt=end,
    )

    minute_rows = (
        queryset.annotate(bucket=TruncMinute("occurred_at", tzinfo=tzinfo))
        .values("bucket")
        .annotate(visitors=Count("visitor_hash", distinct=True))
        .order_by("bucket")
    )
    visitors_by_minute = {
        row["bucket"].astimezone(tzinfo).replace(second=0, microsecond=0): row[
            "visitors"
        ]
        for row in minute_rows
    }
    minutes = [
        {
            "bucket": start + timedelta(minutes=index),
            "visitors": visitors_by_minute.get(
                start + timedelta(minutes=index),
                0,
            ),
        }
        for index in range(60)
    ]
    max_visitors = max((minute["visitors"] for minute in minutes), default=0)
    for minute in minutes:
        minute["height"] = (
            round(minute["visitors"] / max_visitors * 100) if max_visitors else 0
        )
        minute["label"] = minute["bucket"].strftime("%H:%M")

    country_rows = (
        queryset.values("country_code", "country_name")
        .annotate(visitors=Count("visitor_hash", distinct=True))
        .order_by("-visitors", "country_name")[:3]
    )
    countries = [
        {
            "code": row["country_code"].upper() or "--",
            "name": row["country_name"] or "Unknown",
            "visitors": row["visitors"],
        }
        for row in country_rows
    ]
    axis_indexes = (0, 15, 30, 45, 59)

    return {
        "visitors": queryset.values("visitor_hash").distinct().count(),
        "minutes": minutes,
        "axis_labels": [minutes[index]["label"] for index in axis_indexes],
        "countries": countries,
        "timezone": str(tzinfo),
    }
