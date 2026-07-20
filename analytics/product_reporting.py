from datetime import timedelta
from statistics import median

from django.db.models import Avg, Count, Min, Q, Sum
from django.utils import timezone

from .automation import EXPLICIT_AUTOMATION_SCORE_THRESHOLD
from .models import ActivationDefinition, AnalyticsEvent, ProductEventDefinition
from .reporting import event_queryset, resolve_period, selected_site


def _decimal_string(value):
    if value is None:
        return None
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _rate(converted, eligible):
    return round(converted / eligible * 100, 1) if eligible else None


def _collector_health(site):
    last_seen_at = site.server_event_collector_last_seen_at
    if last_seen_at is None:
        state = "never_seen"
    elif last_seen_at >= timezone.now() - timedelta(hours=24):
        state = "active"
    else:
        state = "stale"
    return {
        "state": state,
        "last_seen_at": last_seen_at.isoformat() if last_seen_at else None,
        "last_event_at": (
            site.server_event_collector_last_event_at.isoformat()
            if site.server_event_collector_last_event_at
            else None
        ),
    }


def _activation_metrics(site, ranges):
    try:
        definition = ActivationDefinition.objects.select_related(
            "start_event",
            "goal_event",
        ).get(site=site)
    except ActivationDefinition.DoesNotExist:
        return None

    start_rows = (
        AnalyticsEvent.objects.filter(
            site=site,
            event_type=AnalyticsEvent.EventType.CUSTOM,
            event_name=definition.start_event.event_name,
            automation_score__lt=EXPLICIT_AUTOMATION_SCORE_THRESHOLD,
        )
        .exclude(actor_hash="")
        .values("actor_hash")
        .annotate(started_at=Min("occurred_at"))
        .filter(started_at__gte=ranges.start, started_at__lt=ranges.end)
    )
    cohorts = {row["actor_hash"]: row["started_at"] for row in start_rows}
    first_goals = {}
    if cohorts:
        goal_rows = (
            AnalyticsEvent.objects.filter(
                site=site,
                event_type=AnalyticsEvent.EventType.CUSTOM,
                event_name=definition.goal_event.event_name,
                actor_hash__in=cohorts,
                automation_score__lt=EXPLICIT_AUTOMATION_SCORE_THRESHOLD,
            )
            .values("actor_hash", "occurred_at")
            .order_by("actor_hash", "occurred_at")
        )
        for row in goal_rows:
            started_at = cohorts[row["actor_hash"]]
            if (
                row["actor_hash"] not in first_goals
                and row["occurred_at"] >= started_at
            ):
                first_goals[row["actor_hash"]] = row["occurred_at"]

    now = timezone.now()
    eligible_24h = 0
    converted_24h = 0
    eligible_7d = 0
    converted_7d = 0
    durations = []
    for actor_hash, started_at in cohorts.items():
        goal_at = first_goals.get(actor_hash)
        if goal_at:
            durations.append(max(0, (goal_at - started_at).total_seconds()))
        if started_at <= now - timedelta(hours=24):
            eligible_24h += 1
            converted_24h += bool(goal_at and goal_at <= started_at + timedelta(hours=24))
        if started_at <= now - timedelta(days=7):
            eligible_7d += 1
            converted_7d += bool(goal_at and goal_at <= started_at + timedelta(days=7))

    started = len(cohorts)
    return {
        "start_event": definition.start_event.event_name,
        "start_label": definition.start_event.display_name,
        "goal_event": definition.goal_event.event_name,
        "goal_label": definition.goal_event.display_name,
        "started": started,
        "activated": len(first_goals),
        "eligible_24h": eligible_24h,
        "converted_24h": converted_24h,
        "rate_24h": _rate(converted_24h, eligible_24h),
        "pending_24h": started - eligible_24h,
        "eligible_7d": eligible_7d,
        "converted_7d": converted_7d,
        "rate_7d": _rate(converted_7d, eligible_7d),
        "pending_7d": started - eligible_7d,
        "median_activation_seconds": round(median(durations)) if durations else None,
    }


def product_metrics(site_slug, period, sites=None):
    if site_slug == "all":
        raise ValueError("Product metrics require a selected site.")
    site = selected_site(site_slug, sites)
    ranges = resolve_period(period, site)
    definitions = list(ProductEventDefinition.objects.filter(site=site))
    definition_names = [definition.event_name for definition in definitions]
    queryset = event_queryset(site, ranges.start, ranges.end, sites).filter(
        event_type=AnalyticsEvent.EventType.CUSTOM,
        event_name__in=definition_names,
    )
    rows = (
        {
            row["event_name"]: row
            for row in queryset.values("event_name").annotate(
                event_count=Count("id"),
                unique_actors=Count(
                    "actor_hash",
                    distinct=True,
                    filter=~Q(actor_hash=""),
                ),
                identified_events=Count("id", filter=~Q(actor_hash="")),
                value_sum=Sum("metric_value"),
                value_average=Avg("metric_value"),
            )
        }
        if definitions
        else {}
    )

    metrics = []
    for definition in definitions:
        row = rows.get(definition.event_name, {})
        event_count = row.get("event_count", 0)
        unique_actors = row.get("unique_actors", 0)
        identified_events = row.get("identified_events", 0)
        values = {
            ProductEventDefinition.Aggregation.COUNT: event_count,
            ProductEventDefinition.Aggregation.UNIQUE_ACTORS: unique_actors,
            ProductEventDefinition.Aggregation.SUM: _decimal_string(row.get("value_sum")),
            ProductEventDefinition.Aggregation.AVERAGE: _decimal_string(
                row.get("value_average")
            ),
        }
        metrics.append(
            {
                "event_name": definition.event_name,
                "display_name": definition.display_name,
                "description": definition.description,
                "aggregation": definition.aggregation,
                "unit": definition.unit,
                "primary_value": values[definition.aggregation],
                "event_count": event_count,
                "unique_actors": unique_actors,
                "identified_events": identified_events,
                "identified_rate": round(identified_events / event_count * 100, 1)
                if event_count
                else None,
                "value_sum": _decimal_string(row.get("value_sum")),
                "value_average": _decimal_string(row.get("value_average")),
            }
        )

    incomplete = [
        metric["display_name"]
        for metric in metrics
        if metric["event_count"] and metric["identified_rate"] != 100.0
    ]
    warnings = []
    if incomplete:
        warnings.append(
            "Some events are missing a verified actor: " + ", ".join(incomplete)
        )

    return {
        "site": site.slug,
        "period": period,
        "timezone": str(ranges.timezone),
        "collector": _collector_health(site),
        "activation": _activation_metrics(site, ranges),
        "metrics": metrics,
        "warnings": warnings,
    }
