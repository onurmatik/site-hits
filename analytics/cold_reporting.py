from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from .archive import (
    HistoricalDataUnavailable,
    _canonical_select,
    _configure_s3,
    _database_attachment,
    _s3_url,
    _sql_literal,
    _utc_literal,
    archive_connection,
    archive_generation,
    cold_query_slot,
    release_cold_query_slot,
)
from .automation import (
    AUTOMATION_REASON_LABELS,
    EXPLICIT_AUTOMATION_SCORE_THRESHOLD,
    HIGH_VOLUME_PAGEVIEW_THRESHOLD,
    RAPID_BURST_PAGEVIEW_THRESHOLD,
    RAPID_BURST_WINDOW_SECONDS,
    SESSION_CHURN_THRESHOLD,
)
from .models import (
    ActivationDefinition,
    ArchivePartition,
    ColdDataTombstone,
    HistoricalReportCache,
    ProductEventDefinition,
)

HISTORICAL_PERIODS = {"last180d", "last365d"}


def _site_ids_sql(site_ids: list[int]) -> str:
    if not site_ids:
        return "NULL"
    return ", ".join(str(int(site_id)) for site_id in site_ids)


def _partitions(stream: str, site_ids: list[int], start: datetime, end: datetime):
    return list(
        ArchivePartition.objects.filter(
            site_id_snapshot__in=site_ids,
            stream=stream,
            status__in=[
                ArchivePartition.Status.DELETING,
                ArchivePartition.Status.SOURCE_DELETED,
            ],
            range_start__lt=end,
            range_end__gt=start,
        )
        .exclude(object_key="")
        .order_by("site_id_snapshot", "range_start")
    )


def _register_combined_view(con, name, stream, site_ids, start, end):
    attached = {
        row[0] for row in con.execute("SELECT database_name FROM duckdb_databases()").fetchall()
    }
    if "source" not in attached:
        source_type, source_value = _database_attachment()
        con.load_extension(source_type)
        con.execute(
            f"ATTACH {_sql_literal(source_value)} AS source "
            f"(TYPE {source_type}, READ_ONLY)"
        )
    partitions = _partitions(stream, site_ids, start, end)
    site_filter = f"site_id IN ({_site_ids_sql(site_ids)})"
    time_filter = (
        f"occurred_at >= {_utc_literal(start)} AND occurred_at < {_utc_literal(end)}"
    )
    archived_ranges = [
        "(site_id = "
        f"{partition.site_id_snapshot} AND occurred_at >= {_utc_literal(partition.range_start)} "
        f"AND occurred_at < {_utc_literal(partition.range_end)})"
        for partition in partitions
    ]
    raw_filter = f"{site_filter} AND {time_filter}"
    if archived_ranges:
        raw_filter += " AND NOT (" + " OR ".join(archived_ranges) + ")"
    table = (
        "source.analytics_analyticsevent"
        if stream == ArchivePartition.Stream.ANALYTICS
        else "source.analytics_botevent"
    )
    raw_query = _canonical_select(stream, relation=table, where=raw_filter)
    union_queries = [raw_query]
    if partitions:
        if not settings.SITEHITS_ARCHIVE_QUERY_ENABLED:
            raise HistoricalDataUnavailable("Historical archive queries are disabled.")
        _configure_s3(con)
        urls = ", ".join(_sql_literal(_s3_url(partition.object_key)) for partition in partitions)
        cold_filter = f"{site_filter} AND {time_filter}"
        if stream == ArchivePartition.Stream.ANALYTICS:
            tombstones = list(
                ColdDataTombstone.objects.filter(
                    site_id_snapshot__in=site_ids,
                    kind=ColdDataTombstone.Kind.ACTOR,
                ).exclude(actor_hash="").values_list("site_id_snapshot", "actor_hash")
            )
            if tombstones:
                con.execute("CREATE TEMP TABLE cold_tombstones(site_id BIGINT, actor_hash VARCHAR)")
                con.executemany("INSERT INTO cold_tombstones VALUES (?, ?)", tombstones)
                cold_filter += (
                    " AND NOT EXISTS (SELECT 1 FROM cold_tombstones t "
                    "WHERE t.site_id = archived.site_id AND t.actor_hash = archived.actor_hash)"
                )
        cold_query = _canonical_select(
            stream,
            relation=f"read_parquet([{urls}], union_by_name=true) AS archived",
            where=cold_filter,
        )
        union_queries.append(cold_query)
    con.execute(f"CREATE TEMP VIEW {name} AS " + " UNION ALL ".join(union_queries))


def _metric_values(con, start, end, *, group_by_site=False):
    grouping = "site_id," if group_by_site else ""
    select_group = "site_id," if group_by_site else ""
    order_group = "GROUP BY site_id" if group_by_site else ""
    visitor_identity = (
        "visitor_hash"
        if group_by_site
        else "cast(site_id AS VARCHAR) || ':' || visitor_hash"
    )
    query = f"""
        WITH filtered AS (
            SELECT * FROM analytics_events
            WHERE occurred_at >= {_utc_literal(start)}
              AND occurred_at < {_utc_literal(end)}
              AND source = 'browser'
              AND automation_score < {EXPLICIT_AUTOMATION_SCORE_THRESHOLD}
        ), sessions AS (
            SELECT {grouping} session_id, count(*) AS event_count,
                   count(*) FILTER (WHERE event_type = 'pageview') AS pageview_count,
                   min(occurred_at) AS first_seen, max(occurred_at) AS last_seen
            FROM filtered GROUP BY {grouping} session_id
        ), traffic AS (
            SELECT {select_group}
                   count(DISTINCT CASE WHEN visitor_hash <> '' THEN {visitor_identity} END) AS visitors,
                   count(*) FILTER (WHERE event_type = 'pageview') AS pageviews
            FROM filtered {order_group}
        ), session_metrics AS (
            SELECT {select_group} count(*) AS sessions,
                   sum(CASE WHEN event_count = 1 AND pageview_count = 1 THEN 1 ELSE 0 END) AS bounces,
                   sum(greatest(0, epoch(last_seen - first_seen))) AS duration_seconds
            FROM sessions {order_group}
        )
        SELECT {select_group} visitors, sessions, pageviews,
               CASE WHEN sessions = 0 THEN 0 ELSE round(bounces * 100.0 / sessions, 1) END,
               CASE WHEN sessions = 0 THEN 0 ELSE round(duration_seconds / sessions) END
        FROM traffic JOIN session_metrics USING ({'site_id' if group_by_site else 'visitors'})
    """
    if not group_by_site:
        query = query.replace("FROM traffic JOIN session_metrics USING (visitors)",
                              "FROM traffic CROSS JOIN session_metrics")
    rows = con.execute(query).fetchall()

    def render(row, offset=0):
        return {
            "visitors": int(row[offset] or 0),
            "sessions": int(row[offset + 1] or 0),
            "pageviews": int(row[offset + 2] or 0),
            "bounce_rate": float(row[offset + 3] or 0),
            "avg_session_duration": int(row[offset + 4] or 0),
        }

    if group_by_site:
        return {int(row[0]): render(row, 1) for row in rows}
    return render(rows[0]) if rows else render((0, 0, 0, 0, 0))


def historical_overview(site_ids, ranges, *, group_by_site=False):
    if not site_ids:
        return ({}, {}) if group_by_site else (_empty_metrics(), _empty_metrics())
    if not cold_query_slot(settings.SITEHITS_HISTORICAL_QUERY_TIMEOUT_SECONDS):
        raise HistoricalDataUnavailable("Historical query capacity is busy.")
    try:
        with archive_connection() as con:
            _register_combined_view(
                con,
                "analytics_events",
                ArchivePartition.Stream.ANALYTICS,
                site_ids,
                ranges.previous_start,
                ranges.end,
            )
            return (
                _metric_values(con, ranges.start, ranges.end, group_by_site=group_by_site),
                _metric_values(
                    con,
                    ranges.previous_start,
                    ranges.previous_end,
                    group_by_site=group_by_site,
                ),
            )
    finally:
        release_cold_query_slot()


def _empty_metrics():
    return {
        "visitors": 0,
        "sessions": 0,
        "pageviews": 0,
        "bounce_rate": 0,
        "avg_session_duration": 0,
    }


def historical_timeseries(site_ids, ranges, granularity):
    if not site_ids:
        return []
    unit = "hour" if granularity == "hourly" else "day"
    tzname = str(ranges.timezone)
    if not cold_query_slot(settings.SITEHITS_HISTORICAL_QUERY_TIMEOUT_SECONDS):
        raise HistoricalDataUnavailable("Historical query capacity is busy.")
    try:
        with archive_connection() as con:
            _register_combined_view(
                con,
                "analytics_events",
                ArchivePartition.Stream.ANALYTICS,
                site_ids,
                ranges.start,
                ranges.end,
            )
            rows = con.execute(
                f"""
                SELECT date_trunc('{unit}', timezone({_sql_literal(tzname)}, occurred_at)) bucket,
                       count(DISTINCT CASE WHEN visitor_hash <> '' THEN
                           cast(site_id AS VARCHAR) || ':' || visitor_hash END) visitors,
                       count(DISTINCT cast(site_id AS VARCHAR) || ':' || session_id) sessions,
                       count(*) FILTER (WHERE event_type = 'pageview') pageviews
                FROM analytics_events
                WHERE source = 'browser'
                  AND automation_score < {EXPLICIT_AUTOMATION_SCORE_THRESHOLD}
                  AND occurred_at >= {_utc_literal(ranges.start)}
                  AND occurred_at < {_utc_literal(ranges.end)}
                GROUP BY bucket ORDER BY bucket
                """
            ).fetchall()
        tzinfo = ZoneInfo(tzname)
        return [
            {
                "bucket": row[0].replace(tzinfo=tzinfo).isoformat(),
                "visitors": int(row[1]),
                "sessions": int(row[2]),
                "pageviews": int(row[3]),
            }
            for row in rows
        ]
    finally:
        release_cold_query_slot()


def _breakdown_label(dimension):
    fields = {
        "pages": "path",
        "referrers": "referrer_domain",
        "countries": "country_name",
        "regions": "region_name",
        "cities": "city_name",
        "devices": "device",
        "browsers": "browser",
        "os": "operating_system",
        "campaigns": "utm_campaign",
        "events": "event_name",
    }
    field = fields[dimension]
    empty = "Direct" if dimension == "referrers" else "Unknown"
    if dimension == "regions":
        return (
            "CASE WHEN region_name = '' THEN 'Unknown' ELSE region_name || "
            "CASE WHEN country_name = '' THEN '' ELSE ', ' || country_name END END"
        )
    if dimension == "cities":
        return (
            "CASE WHEN city_name = '' THEN 'Unknown' ELSE city_name || "
            "CASE WHEN region_name = '' OR region_name = city_name THEN '' "
            "ELSE ', ' || region_name END || CASE WHEN country_name = '' THEN '' "
            "ELSE ', ' || country_name END END"
        )
    return f"CASE WHEN {field} = '' THEN {_sql_literal(empty)} ELSE {field} END"


def historical_breakdown(site_ids, ranges, dimension, limit):
    if not site_ids:
        return []
    filters = {
        "pages": "event_type = 'pageview'",
        "referrers": "event_type = 'pageview'",
        "countries": "source = 'browser'",
        "regions": "source = 'browser'",
        "cities": "source = 'browser'",
        "devices": "source = 'browser'",
        "browsers": "source = 'browser'",
        "os": "source = 'browser'",
        "campaigns": "source = 'browser' AND utm_campaign <> ''",
        "events": "event_type = 'custom' AND event_name <> ''",
    }
    distinct_sessions = dimension in {
        "countries",
        "regions",
        "cities",
        "devices",
        "browsers",
        "os",
        "campaigns",
    }
    counter = (
        "count(DISTINCT cast(site_id AS VARCHAR) || ':' || session_id)"
        if distinct_sessions
        else "count(*)"
    )
    row_limit = max(1, min(limit, 50))
    if not cold_query_slot(settings.SITEHITS_HISTORICAL_QUERY_TIMEOUT_SECONDS):
        raise HistoricalDataUnavailable("Historical query capacity is busy.")
    try:
        with archive_connection() as con:
            _register_combined_view(
                con,
                "analytics_events",
                ArchivePartition.Stream.ANALYTICS,
                site_ids,
                ranges.start,
                ranges.end,
            )
            rows = con.execute(
                f"""
                SELECT {_breakdown_label(dimension)} AS label_value, {counter} AS count_value
                FROM analytics_events
                WHERE automation_score < {EXPLICIT_AUTOMATION_SCORE_THRESHOLD}
                  AND occurred_at >= {_utc_literal(ranges.start)}
                  AND occurred_at < {_utc_literal(ranges.end)}
                  AND {filters[dimension]}
                GROUP BY label_value ORDER BY count_value DESC, label_value LIMIT {row_limit}
                """
            ).fetchall()
        total = sum(int(row[1]) for row in rows)
        return [
            {
                "label": row[0] or "Unknown",
                "count": int(row[1]),
                "percentage": round(int(row[1]) / total * 100, 1) if total else 0,
            }
            for row in rows
        ]
    finally:
        release_cold_query_slot()


def _suspected_automation(con, ranges, limit):
    explicit = con.execute(
        f"""
        SELECT site_id, visitor_hash, automation_reasons
        FROM analytics_events
        WHERE occurred_at >= {_utc_literal(ranges.start)}
          AND occurred_at < {_utc_literal(ranges.end)}
          AND automation_score >= {EXPLICIT_AUTOMATION_SCORE_THRESHOLD}
        """
    ).fetchall()
    suspected_by_site: dict[int, set[str]] = {}
    visitors_by_reason: dict[str, set[str]] = {}
    for site_id, visitor_hash, reasons_json in explicit:
        suspected_by_site.setdefault(int(site_id), set()).add(visitor_hash)
        try:
            reasons = json.loads(reasons_json) if reasons_json else ["webdriver"]
        except (TypeError, json.JSONDecodeError):
            reasons = ["webdriver"]
        for reason in reasons:
            if reason in AUTOMATION_REASON_LABELS:
                visitors_by_reason.setdefault(reason, set()).add(f"{site_id}:{visitor_hash}")
    candidates = con.execute(
        f"""
        SELECT site_id, visitor_hash, count(*) pageviews,
               count(DISTINCT session_id) sessions,
               epoch(max(occurred_at) - min(occurred_at)) active_seconds
        FROM analytics_events
        WHERE occurred_at >= {_utc_literal(ranges.start)}
          AND occurred_at < {_utc_literal(ranges.end)}
          AND event_type = 'pageview'
        GROUP BY site_id, visitor_hash
        HAVING pageviews >= {RAPID_BURST_PAGEVIEW_THRESHOLD}
            OR sessions >= {SESSION_CHURN_THRESHOLD}
        """
    ).fetchall()
    for site_id, visitor_hash, pageviews, sessions, active_seconds in candidates:
        reasons = []
        if pageviews >= HIGH_VOLUME_PAGEVIEW_THRESHOLD:
            reasons.append("high_request_volume")
        if pageviews >= RAPID_BURST_PAGEVIEW_THRESHOLD and active_seconds <= RAPID_BURST_WINDOW_SECONDS:
            reasons.append("rapid_navigation_burst")
        if sessions >= SESSION_CHURN_THRESHOLD:
            reasons.append("session_churn")
        if not reasons:
            continue
        suspected_by_site.setdefault(int(site_id), set()).add(visitor_hash)
        for reason in reasons:
            visitors_by_reason.setdefault(reason, set()).add(f"{site_id}:{visitor_hash}")
    pairs = [
        f"(site_id = {site_id} AND visitor_hash IN ("
        + ", ".join(_sql_literal(value) for value in sorted(values))
        + "))"
        for site_id, values in suspected_by_site.items()
        if values
    ]
    if not pairs:
        return {"visitors": 0, "sessions": 0, "pageviews": 0, "reasons": [], "pages": []}
    predicate = " OR ".join(pairs)
    totals = con.execute(
        f"""
        SELECT count(DISTINCT cast(site_id AS VARCHAR) || ':' || session_id),
               count(*) FILTER (WHERE event_type = 'pageview')
        FROM analytics_events WHERE {predicate}
        """
    ).fetchone()
    pages = con.execute(
        f"""
        SELECT path, count(*) count FROM analytics_events
        WHERE event_type = 'pageview' AND ({predicate})
        GROUP BY path ORDER BY count DESC, path LIMIT {max(1, min(limit, 50))}
        """
    ).fetchall()
    reasons = sorted(
        (
            {
                "key": reason,
                "label": AUTOMATION_REASON_LABELS[reason],
                "visitors": len(visitor_keys),
            }
            for reason, visitor_keys in visitors_by_reason.items()
        ),
        key=lambda row: (-row["visitors"], row["label"]),
    )
    return {
        "visitors": sum(len(values) for values in suspected_by_site.values()),
        "sessions": int(totals[0] or 0),
        "pageviews": int(totals[1] or 0),
        "reasons": reasons,
        "pages": [{"path": row[0], "count": int(row[1])} for row in pages],
    }


def historical_bot_traffic(site_ids, ranges, limit):
    if not site_ids:
        return {
            "total": 0,
            "categories": {},
            "providers": [],
            "pages": [],
            "verification": {"ip_verified": 0, "user_agent": 0},
            "suspected_automation": {
                "visitors": 0,
                "sessions": 0,
                "pageviews": 0,
                "reasons": [],
                "pages": [],
            },
        }
    row_limit = max(1, min(limit, 50))
    if not cold_query_slot(settings.SITEHITS_HISTORICAL_QUERY_TIMEOUT_SECONDS):
        raise HistoricalDataUnavailable("Historical query capacity is busy.")
    try:
        with archive_connection() as con:
            _register_combined_view(
                con,
                "bot_events",
                ArchivePartition.Stream.BOTS,
                site_ids,
                ranges.start,
                ranges.end,
            )
            _register_combined_view(
                con,
                "analytics_events",
                ArchivePartition.Stream.ANALYTICS,
                site_ids,
                ranges.start,
                ranges.end,
            )
            total = int(con.execute("SELECT count(*) FROM bot_events").fetchone()[0])
            categories = dict(con.execute(
                "SELECT category, count(*) FROM bot_events GROUP BY category"
            ).fetchall())
            providers = con.execute(
                f"SELECT provider, count(*) count FROM bot_events GROUP BY provider "
                f"ORDER BY count DESC, provider LIMIT {row_limit}"
            ).fetchall()
            pages = con.execute(
                f"SELECT path, status_code, count(*) count FROM bot_events "
                f"GROUP BY path, status_code ORDER BY count DESC, path, status_code "
                f"LIMIT {row_limit}"
            ).fetchall()
            verification = dict(con.execute(
                "SELECT verification, count(*) FROM bot_events GROUP BY verification"
            ).fetchall())
            automation = _suspected_automation(con, ranges, limit)
        return {
            "total": total,
            "categories": {key: int(value) for key, value in categories.items()},
            "providers": [
                {
                    "label": row[0],
                    "count": int(row[1]),
                    "percentage": round(int(row[1]) / total * 100, 1) if total else 0,
                }
                for row in providers
            ],
            "pages": [
                {
                    "path": row[0],
                    "status_code": row[1],
                    "count": int(row[2]),
                    "percentage": round(int(row[2]) / total * 100, 1) if total else 0,
                }
                for row in pages
            ],
            "verification": {
                "ip_verified": int(verification.get("ip_verified", 0)),
                "user_agent": int(verification.get("user_agent", 0)),
            },
            "suspected_automation": automation,
        }
    finally:
        release_cold_query_slot()


def _decimal_string(value):
    if value is None:
        return None
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


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


def historical_product_metrics(site, ranges):
    definitions = list(ProductEventDefinition.objects.filter(site=site))
    definition_names = [definition.event_name for definition in definitions]
    data_start = timezone.now() - timedelta(days=settings.SITEHITS_ARCHIVE_RETENTION_DAYS)
    if not cold_query_slot(settings.SITEHITS_HISTORICAL_QUERY_TIMEOUT_SECONDS):
        raise HistoricalDataUnavailable("Historical query capacity is busy.")
    try:
        with archive_connection() as con:
            _register_combined_view(
                con,
                "analytics_events",
                ArchivePartition.Stream.ANALYTICS,
                [site.pk],
                data_start,
                ranges.end,
            )
            names = ", ".join(_sql_literal(name) for name in definition_names) or "NULL"
            rows = con.execute(
                f"""
                SELECT event_name, count(*) event_count,
                       count(DISTINCT CASE WHEN actor_hash <> '' THEN actor_hash END) unique_actors,
                       count(*) FILTER (WHERE actor_hash <> '') identified_events,
                       sum(metric_value) value_sum, avg(metric_value) value_average
                FROM analytics_events
                WHERE occurred_at >= {_utc_literal(ranges.start)}
                  AND occurred_at < {_utc_literal(ranges.end)}
                  AND automation_score < {EXPLICIT_AUTOMATION_SCORE_THRESHOLD}
                  AND event_type = 'custom' AND event_name IN ({names})
                GROUP BY event_name
                """
            ).fetchall()
            by_name = {
                row[0]: {
                    "event_count": int(row[1]),
                    "unique_actors": int(row[2]),
                    "identified_events": int(row[3]),
                    "value_sum": row[4],
                    "value_average": row[5],
                }
                for row in rows
            }
            activation = _historical_activation(con, site, ranges)
    finally:
        release_cold_query_slot()
    metrics = []
    for definition in definitions:
        row = by_name.get(definition.event_name, {})
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
        warnings.append("Some events are missing a verified actor: " + ", ".join(incomplete))
    return {
        "site": site.slug,
        "period": ranges.period if hasattr(ranges, "period") else "",
        "timezone": str(ranges.timezone),
        "collector": _collector_health(site),
        "activation": activation,
        "metrics": metrics,
        "warnings": warnings,
    }


def _historical_activation(con, site, ranges):
    definition = (
        ActivationDefinition.objects.filter(site=site)
        .select_related("start_event", "goal_event")
        .first()
    )
    if definition is None:
        return None
    start_rows = con.execute(
        f"""
        SELECT actor_hash, min(occurred_at) started_at
        FROM analytics_events
        WHERE event_type = 'custom'
          AND event_name = {_sql_literal(definition.start_event.event_name)}
          AND actor_hash <> ''
          AND automation_score < {EXPLICIT_AUTOMATION_SCORE_THRESHOLD}
        GROUP BY actor_hash
        HAVING started_at >= {_utc_literal(ranges.start)}
           AND started_at < {_utc_literal(ranges.end)}
        """
    ).fetchall()
    cohorts = {row[0]: row[1] for row in start_rows}
    first_goals = {}
    if cohorts:
        actor_values = ", ".join(_sql_literal(value) for value in cohorts)
        goal_rows = con.execute(
            f"""
            SELECT actor_hash, occurred_at FROM analytics_events
            WHERE event_type = 'custom'
              AND event_name = {_sql_literal(definition.goal_event.event_name)}
              AND actor_hash IN ({actor_values})
              AND automation_score < {EXPLICIT_AUTOMATION_SCORE_THRESHOLD}
            ORDER BY actor_hash, occurred_at
            """
        ).fetchall()
        for actor_hash, occurred_at in goal_rows:
            if actor_hash not in first_goals and occurred_at >= cohorts[actor_hash]:
                first_goals[actor_hash] = occurred_at
    now = timezone.now()
    eligible_24h = converted_24h = eligible_7d = converted_7d = 0
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
        "rate_24h": round(converted_24h / eligible_24h * 100, 1) if eligible_24h else None,
        "pending_24h": started - eligible_24h,
        "eligible_7d": eligible_7d,
        "converted_7d": converted_7d,
        "rate_7d": round(converted_7d / eligible_7d * 100, 1) if eligible_7d else None,
        "pending_7d": started - eligible_7d,
        "median_activation_seconds": round(median(durations)) if durations else None,
    }


def cached_historical_report(
    *,
    report_type,
    site_selector,
    site,
    site_ids,
    period,
    parameters,
    producer,
):
    cache_parameters = {**parameters, "_site_ids": sorted(int(value) for value in site_ids)}
    canonical = json.dumps(
        cache_parameters,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    parameters_hash = hashlib.sha256(canonical.encode()).hexdigest()
    generation = archive_generation(site_ids)
    now = timezone.now()
    cache = HistoricalReportCache.objects.filter(
        site_selector=site_selector,
        report_type=report_type,
        period=period,
        parameters_hash=parameters_hash,
    ).first()
    if cache and cache.expires_at > now and cache.archive_generation == generation:
        result = dict(cache.result)
        result["freshness"] = {
            "source": "cache",
            "generated_at": cache.generated_at.isoformat(),
            "is_stale": False,
        }
        return result
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sitehits-cold-query")
    future = executor.submit(producer)
    try:
        result = future.result(timeout=settings.SITEHITS_HISTORICAL_QUERY_TIMEOUT_SECONDS)
    except Exception as exc:
        future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        if cache and cache.archive_generation == generation:
            stale = dict(cache.result)
            stale["freshness"] = {
                "source": "cache",
                "generated_at": cache.generated_at.isoformat(),
                "is_stale": True,
            }
            return stale
        if isinstance(exc, HistoricalDataUnavailable):
            raise
        raise HistoricalDataUnavailable("Historical analytics are temporarily unavailable.") from exc
    else:
        executor.shutdown(wait=True)
    generated_at = timezone.now()
    stored = dict(result)
    stored.pop("freshness", None)
    HistoricalReportCache.objects.update_or_create(
        site_selector=site_selector,
        report_type=report_type,
        period=period,
        parameters_hash=parameters_hash,
        defaults={
            "site": site,
            "parameters": cache_parameters,
            "result": stored,
            "archive_generation": generation,
            "generated_at": generated_at,
            "expires_at": generated_at + timedelta(seconds=settings.SITEHITS_HISTORICAL_CACHE_SECONDS),
        },
    )
    result["freshness"] = {
        "source": "hybrid",
        "generated_at": generated_at.isoformat(),
        "is_stale": False,
    }
    return result
