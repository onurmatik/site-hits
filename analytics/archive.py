from __future__ import annotations

import hashlib
import json
import logging
import threading
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

import boto3
import duckdb
from botocore.exceptions import ClientError
from django.conf import settings
from django.db import connection, models, transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDay
from django.utils import timezone
from psycopg.conninfo import make_conninfo

from websites.models import TrackedSite

from .automation import EXPLICIT_AUTOMATION_SCORE_THRESHOLD
from .models import (
    AnalyticsEvent,
    ArchivePartition,
    BotEvent,
    ColdDataTombstone,
    ColdDeletionJob,
    DailyAnalyticsRollup,
    DailyBotRollup,
    DailyDimensionRollup,
    DailyProductEventRollup,
    HistoricalReportCache,
)

logger = logging.getLogger("sitehits.archive")
ARCHIVE_SCHEMA_VERSION = 1
DELETE_BATCH_SIZE = 5_000
_COLD_QUERY_SLOT = threading.BoundedSemaphore(1)

ANALYTICS_COLUMNS = (
    "id",
    "site_id",
    "event_type",
    "event_name",
    "source",
    "occurred_at",
    "received_at",
    "visitor_hash",
    "session_id",
    "actor_hash",
    "idempotency_hash",
    "metric_value",
    "metric_unit",
    "path",
    "referrer_domain",
    "referrer_path",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "country_code",
    "country_name",
    "region_code",
    "region_name",
    "city_name",
    "device",
    "browser",
    "operating_system",
    "language",
    "client_timezone",
    "viewport_width",
    "viewport_height",
    "screen_width",
    "screen_height",
    "automation_score",
    "automation_reasons",
    "properties",
)
BOT_COLUMNS = (
    "id",
    "site_id",
    "occurred_at",
    "received_at",
    "path",
    "status_code",
    "provider",
    "crawler",
    "category",
    "verification",
)


class ArchiveConfigurationError(RuntimeError):
    pass


class ArchiveVerificationError(RuntimeError):
    pass


class HistoricalDataUnavailable(RuntimeError):
    pass


def _sql_literal(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _utc_literal(value: datetime) -> str:
    rendered = value.astimezone(UTC).isoformat()
    return f"TIMESTAMPTZ {_sql_literal(rendered)}"


def _month_after(value: datetime) -> datetime:
    if value.month == 12:
        return value.replace(year=value.year + 1, month=1, day=1)
    return value.replace(month=value.month + 1, day=1)


def _local_month_range(site: TrackedSite, local_month: datetime) -> tuple[datetime, datetime]:
    tzinfo = ZoneInfo(site.timezone)
    start = datetime(local_month.year, local_month.month, 1, tzinfo=tzinfo)
    end = _month_after(start)
    return start.astimezone(UTC), end.astimezone(UTC)


def _archive_key(partition: ArchivePartition, *, generation: str | None = None) -> str:
    local_start = partition.range_start.astimezone(ZoneInfo(partition.timezone))
    root = "events" if partition.stream == ArchivePartition.Stream.ANALYTICS else "bot-events"
    generation = generation or str(partition.generation)
    return (
        f"{settings.SITEHITS_ARCHIVE_PREFIX}/{root}/schema_version="
        f"{partition.schema_version}/site_id={partition.site_id_snapshot}/"
        f"year={local_start.year:04d}/month={local_start.month:02d}/"
        f"part-{generation}.parquet"
    )


def _s3_url(object_key: str) -> str:
    return f"s3://{settings.SITEHITS_ARCHIVE_BUCKET}/{object_key}"


def _boto_client():
    kwargs: dict[str, object] = {"region_name": settings.SITEHITS_ARCHIVE_REGION}
    if settings.SITEHITS_ARCHIVE_ENDPOINT:
        kwargs["endpoint_url"] = settings.SITEHITS_ARCHIVE_ENDPOINT
    return boto3.client("s3", **kwargs)


def _database_attachment() -> tuple[str, str]:
    database = connection.settings_dict
    engine = database["ENGINE"]
    if engine == "django.db.backends.sqlite3":
        return "sqlite", str(Path(database["NAME"]).resolve())
    if engine != "django.db.backends.postgresql":
        raise ArchiveConfigurationError("Archive export supports PostgreSQL and local SQLite.")
    options = database.get("OPTIONS") or {}
    parameters = {
        "dbname": database.get("NAME"),
        "user": database.get("USER"),
        "password": database.get("PASSWORD"),
        "host": database.get("HOST"),
        "port": database.get("PORT"),
        "sslmode": options.get("sslmode"),
    }
    conninfo = make_conninfo(
        **{key: value for key, value in parameters.items() if value not in {None, ""}}
    )
    return "postgres", conninfo


def _configure_s3(con: duckdb.DuckDBPyConnection) -> None:
    con.load_extension("httpfs")
    options = [
        "TYPE s3",
        "PROVIDER credential_chain",
        f"REGION {_sql_literal(settings.SITEHITS_ARCHIVE_REGION)}",
    ]
    if settings.SITEHITS_ARCHIVE_ENDPOINT:
        parsed = urlsplit(settings.SITEHITS_ARCHIVE_ENDPOINT)
        endpoint = parsed.netloc or parsed.path
        options.extend(
            [
                f"ENDPOINT {_sql_literal(endpoint)}",
                f"USE_SSL {'true' if parsed.scheme == 'https' else 'false'}",
                "URL_STYLE 'path'",
            ]
        )
    if settings.SITEHITS_ARCHIVE_KMS_KEY_ID:
        options.append(f"KMS_KEY_ID {_sql_literal(settings.SITEHITS_ARCHIVE_KMS_KEY_ID)}")
    con.execute("CREATE OR REPLACE SECRET sitehits_archive (" + ", ".join(options) + ")")


@contextmanager
def archive_connection(*, attach_source: bool = False, needs_s3: bool = False):
    con = duckdb.connect(":memory:")
    try:
        con.execute("SET TimeZone='UTC'")
        con.execute("SET threads=2")
        con.execute("SET memory_limit='768MB'")
        if attach_source:
            source_type, source_value = _database_attachment()
            con.load_extension(source_type)
            con.execute(
                f"ATTACH {_sql_literal(source_value)} AS source (TYPE {source_type}, READ_ONLY)"
            )
        if needs_s3:
            _configure_s3(con)
        yield con
    finally:
        con.close()


def _canonical_select(stream: str, *, relation: str, where: str = "TRUE") -> str:
    columns = ANALYTICS_COLUMNS if stream == ArchivePartition.Stream.ANALYTICS else BOT_COLUMNS
    rendered = []
    for column in columns:
        if column in {"occurred_at", "received_at"}:
            rendered.append(f"CAST({column} AS TIMESTAMPTZ) AS {column}")
        elif column in {"automation_reasons", "properties"}:
            rendered.append(f"CAST({column} AS VARCHAR) AS {column}")
        elif column == "metric_value":
            rendered.append("CAST(metric_value AS DECIMAL(20,6)) AS metric_value")
        else:
            rendered.append(column)
    return f"SELECT {', '.join(rendered)} FROM {relation} WHERE {where}"


def _source_query(partition: ArchivePartition) -> str:
    table = (
        "source.analytics_analyticsevent"
        if partition.stream == ArchivePartition.Stream.ANALYTICS
        else "source.analytics_botevent"
    )
    where = (
        f"site_id = {int(partition.site_id_snapshot)} "
        f"AND occurred_at >= {_utc_literal(partition.range_start)} "
        f"AND occurred_at < {_utc_literal(partition.range_end)}"
    )
    return _canonical_select(partition.stream, relation=table, where=where)


def _fingerprint(con: duckdb.DuckDBPyConnection, query: str, columns: tuple[str, ...]):
    hash_expression = "hash(" + ", ".join(columns) + ")"
    row = con.execute(
        "SELECT count(*) AS row_count, min(id), max(id), min(occurred_at), "
        "max(occurred_at), coalesce(sum(" + hash_expression + "), 0)::VARCHAR, "
        "coalesce(bit_xor(" + hash_expression + "), 0)::VARCHAR "
        f"FROM ({query}) AS fingerprint_source"
    ).fetchone()
    material = json.dumps([str(value) if value is not None else None for value in row])
    return row, hashlib.sha256(material.encode()).hexdigest()


def _head_export(object_key: str) -> tuple[str, dict[str, object]]:
    response = _boto_client().head_object(
        Bucket=settings.SITEHITS_ARCHIVE_BUCKET,
        Key=object_key,
    )
    if not settings.DEBUG and response.get("ServerSideEncryption") != "aws:kms":
        raise ArchiveVerificationError("Archive object is not protected with SSE-KMS.")
    object_version = str(response.get("VersionId") or "")
    if not settings.DEBUG and not object_version:
        raise ArchiveVerificationError("Archive bucket versioning is not enabled.")
    return object_version, response


def _write_rollups(site: TrackedSite, start: datetime, end: datetime, stream: str) -> None:
    tzinfo = ZoneInfo(site.timezone)
    local_start = start.astimezone(tzinfo).date()
    local_end = end.astimezone(tzinfo).date()
    if stream == ArchivePartition.Stream.BOTS:
        _write_bot_rollups(site, start, end, local_start, local_end, tzinfo)
        return

    base = AnalyticsEvent.objects.filter(
        site=site,
        occurred_at__gte=start,
        occurred_at__lt=end,
    )
    valid = base.filter(automation_score__lt=EXPLICIT_AUTOMATION_SCORE_THRESHOLD)
    daily_rows = list(
        base.annotate(bucket=TruncDay("occurred_at", tzinfo=tzinfo))
        .values("bucket")
        .annotate(
            event_count=Count("id"),
            browser_event_count=Count("id", filter=Q(source=AnalyticsEvent.Source.BROWSER)),
            pageview_count=Count(
                "id",
                filter=Q(event_type=AnalyticsEvent.EventType.PAGEVIEW)
                & Q(automation_score__lt=EXPLICIT_AUTOMATION_SCORE_THRESHOLD),
            ),
            identified_event_count=Count("id", filter=~Q(actor_hash="")),
            automated_event_count=Count(
                "id",
                filter=Q(automation_score__gte=EXPLICIT_AUTOMATION_SCORE_THRESHOLD),
            ),
            metric_value_sum=Sum("metric_value"),
            metric_value_count=Count("metric_value"),
        )
        .order_by()
    )
    dimension_fields = {
        "pages": ("path", Q(event_type=AnalyticsEvent.EventType.PAGEVIEW)),
        "referrers": ("referrer_domain", Q(event_type=AnalyticsEvent.EventType.PAGEVIEW)),
        "countries": ("country_name", Q(source=AnalyticsEvent.Source.BROWSER)),
        "regions": ("region_name", Q(source=AnalyticsEvent.Source.BROWSER)),
        "cities": ("city_name", Q(source=AnalyticsEvent.Source.BROWSER)),
        "devices": ("device", Q(source=AnalyticsEvent.Source.BROWSER)),
        "browsers": ("browser", Q(source=AnalyticsEvent.Source.BROWSER)),
        "os": ("operating_system", Q(source=AnalyticsEvent.Source.BROWSER)),
        "campaigns": (
            "utm_campaign",
            Q(source=AnalyticsEvent.Source.BROWSER) & ~Q(utm_campaign=""),
        ),
        "events": (
            "event_name",
            Q(event_type=AnalyticsEvent.EventType.CUSTOM) & ~Q(event_name=""),
        ),
    }
    dimension_rows: list[DailyDimensionRollup] = []
    for dimension, (field, filters) in dimension_fields.items():
        rows = (
            valid.filter(filters)
            .annotate(bucket=TruncDay("occurred_at", tzinfo=tzinfo))
            .values("bucket", field)
            .annotate(event_count=Count("id"))
            .order_by()
        )
        for row in rows.iterator():
            label = row[field] or ("Direct" if dimension == "referrers" else "Unknown")
            dimension_rows.append(
                DailyDimensionRollup(
                    site=site,
                    day=row["bucket"].date(),
                    timezone=site.timezone,
                    bucket_start=row["bucket"],
                    bucket_end=row["bucket"] + timedelta(days=1),
                    dimension=dimension,
                    label=label,
                    event_count=row["event_count"],
                )
            )

    product_rows = list(
        valid.filter(event_type=AnalyticsEvent.EventType.CUSTOM)
        .exclude(event_name="")
        .annotate(bucket=TruncDay("occurred_at", tzinfo=tzinfo))
        .values("bucket", "event_name")
        .annotate(
            event_count=Count("id"),
            identified_event_count=Count("id", filter=~Q(actor_hash="")),
            value_sum=Sum("metric_value"),
            value_count=Count("metric_value"),
        )
        .order_by()
    )
    with transaction.atomic():
        DailyAnalyticsRollup.objects.filter(
            site=site, day__gte=local_start, day__lt=local_end
        ).delete()
        DailyDimensionRollup.objects.filter(
            site=site, day__gte=local_start, day__lt=local_end
        ).delete()
        DailyProductEventRollup.objects.filter(
            site=site, day__gte=local_start, day__lt=local_end
        ).delete()
        DailyAnalyticsRollup.objects.bulk_create(
            [
                DailyAnalyticsRollup(
                    site=site,
                    day=row["bucket"].date(),
                    timezone=site.timezone,
                    bucket_start=row["bucket"],
                    bucket_end=row["bucket"] + timedelta(days=1),
                    event_count=row["event_count"],
                    browser_event_count=row["browser_event_count"],
                    pageview_count=row["pageview_count"],
                    identified_event_count=row["identified_event_count"],
                    automated_event_count=row["automated_event_count"],
                    metric_value_sum=row["metric_value_sum"],
                    metric_value_count=row["metric_value_count"],
                )
                for row in daily_rows
            ]
        )
        DailyDimensionRollup.objects.bulk_create(dimension_rows)
        DailyProductEventRollup.objects.bulk_create(
            [
                DailyProductEventRollup(
                    site=site,
                    day=row["bucket"].date(),
                    timezone=site.timezone,
                    bucket_start=row["bucket"],
                    bucket_end=row["bucket"] + timedelta(days=1),
                    event_name=row["event_name"],
                    event_count=row["event_count"],
                    identified_event_count=row["identified_event_count"],
                    value_sum=row["value_sum"],
                    value_count=row["value_count"],
                )
                for row in product_rows
            ]
        )


def _write_bot_rollups(site, start, end, local_start, local_end, tzinfo) -> None:
    queryset = BotEvent.objects.filter(site=site, occurred_at__gte=start, occurred_at__lt=end)
    configurations = {
        "category": ("category", None),
        "provider": ("provider", None),
        "verification": ("verification", None),
        "page": ("path", "status_code"),
    }
    output: list[DailyBotRollup] = []
    for dimension, (field, secondary) in configurations.items():
        values = ["bucket", field] + ([secondary] if secondary else [])
        rows = (
            queryset.annotate(bucket=TruncDay("occurred_at", tzinfo=tzinfo))
            .values(*values)
            .annotate(event_count=Count("id"))
            .order_by()
        )
        for row in rows.iterator():
            output.append(
                DailyBotRollup(
                    site=site,
                    day=row["bucket"].date(),
                    timezone=site.timezone,
                    bucket_start=row["bucket"],
                    bucket_end=row["bucket"] + timedelta(days=1),
                    dimension=dimension,
                    label=str(row[field] or "Unknown"),
                    secondary_label=str(row[secondary] or "") if secondary else "",
                    event_count=row["event_count"],
                )
            )
    with transaction.atomic():
        DailyBotRollup.objects.filter(site=site, day__gte=local_start, day__lt=local_end).delete()
        DailyBotRollup.objects.bulk_create(output)


def _rollup_bucket(day: date, tzinfo: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=tzinfo)
    end = start + timedelta(days=1)
    return start.astimezone(UTC), end.astimezone(UTC)


def _queryable_archive_partitions(site: TrackedSite, stream: str):
    return list(
        ArchivePartition.objects.filter(
            site_id_snapshot=site.pk,
            stream=stream,
            storage_class=ArchivePartition.StorageClass.STANDARD,
            status__in=[
                ArchivePartition.Status.VERIFIED,
                ArchivePartition.Status.DELETING,
                ArchivePartition.Status.SOURCE_DELETED,
            ],
        )
        .exclude(object_key="")
        .order_by("range_start")
    )


def rebuild_site_rollups_from_archive(site: TrackedSite, stream: str) -> int:
    """Rebuild queryable archive rollups after a site timezone change."""
    partitions = _queryable_archive_partitions(site, stream)
    if not partitions:
        return 0
    urls = ", ".join(_sql_literal(_s3_url(item.object_key)) for item in partitions)
    tzinfo = ZoneInfo(site.timezone)
    timezone_literal = _sql_literal(site.timezone)
    relation = f"read_parquet([{urls}], union_by_name=true)"
    actor_hashes = list(
        ColdDataTombstone.objects.filter(
            site_id_snapshot=site.pk,
            kind=ColdDataTombstone.Kind.ACTOR,
        )
        .exclude(actor_hash="")
        .values_list("actor_hash", flat=True)
    )
    tombstone_filter = (
        " AND actor_hash NOT IN (" + ", ".join(_sql_literal(value) for value in actor_hashes) + ")"
        if actor_hashes and stream == ArchivePartition.Stream.ANALYTICS
        else ""
    )
    with archive_connection(needs_s3=True) as con:
        if stream == ArchivePartition.Stream.BOTS:
            configurations = {
                "category": ("category", None),
                "provider": ("provider", None),
                "verification": ("verification", None),
                "page": ("path", "status_code"),
            }
            output: list[DailyBotRollup] = []
            for dimension, (field, secondary) in configurations.items():
                secondary_select = f", {secondary}" if secondary else ""
                secondary_group = f", {secondary}" if secondary else ""
                rows = con.execute(
                    f"""
                    SELECT CAST(timezone({timezone_literal}, occurred_at) AS DATE) AS bucket_day,
                           {field}{secondary_select}, count(*) event_count
                    FROM {relation}
                    GROUP BY bucket_day, {field}{secondary_group}
                    """
                ).fetchall()
                for row in rows:
                    bucket_start, bucket_end = _rollup_bucket(row[0], tzinfo)
                    output.append(
                        DailyBotRollup(
                            site=site,
                            day=row[0],
                            timezone=site.timezone,
                            bucket_start=bucket_start,
                            bucket_end=bucket_end,
                            dimension=dimension,
                            label=str(row[1] or "Unknown"),
                            secondary_label=str(row[2] or "") if secondary else "",
                            event_count=int(row[3] if secondary else row[2]),
                        )
                    )
            with transaction.atomic():
                DailyBotRollup.objects.filter(site=site).delete()
                DailyBotRollup.objects.bulk_create(output)
            return len(output)

        base = f"SELECT * FROM {relation} WHERE TRUE{tombstone_filter}"
        daily_rows = con.execute(
            f"""
            SELECT CAST(timezone({timezone_literal}, occurred_at) AS DATE) AS bucket_day,
                   count(*) event_count,
                   count(*) FILTER (WHERE source = 'browser') browser_event_count,
                   count(*) FILTER (
                       WHERE event_type = 'pageview'
                         AND automation_score < {EXPLICIT_AUTOMATION_SCORE_THRESHOLD}
                   ) pageview_count,
                   count(*) FILTER (WHERE actor_hash <> '') identified_event_count,
                   count(*) FILTER (
                       WHERE automation_score >= {EXPLICIT_AUTOMATION_SCORE_THRESHOLD}
                   ) automated_event_count,
                   sum(metric_value) metric_value_sum,
                   count(metric_value) metric_value_count
            FROM ({base}) archived GROUP BY bucket_day
            """
        ).fetchall()
        dimension_fields = {
            "pages": ("path", "event_type = 'pageview'"),
            "referrers": ("referrer_domain", "event_type = 'pageview'"),
            "countries": ("country_name", "source = 'browser'"),
            "regions": ("region_name", "source = 'browser'"),
            "cities": ("city_name", "source = 'browser'"),
            "devices": ("device", "source = 'browser'"),
            "browsers": ("browser", "source = 'browser'"),
            "os": ("operating_system", "source = 'browser'"),
            "campaigns": ("utm_campaign", "source = 'browser' AND utm_campaign <> ''"),
            "events": ("event_name", "event_type = 'custom' AND event_name <> ''"),
        }
        dimension_rows: list[DailyDimensionRollup] = []
        for dimension, (field, filters) in dimension_fields.items():
            empty_label = "Direct" if dimension == "referrers" else "Unknown"
            rows = con.execute(
                f"""
                SELECT CAST(timezone({timezone_literal}, occurred_at) AS DATE) AS bucket_day,
                       CASE WHEN {field} = '' THEN {_sql_literal(empty_label)}
                            ELSE {field} END AS label_value,
                       count(*) event_count
                FROM ({base}) archived
                WHERE automation_score < {EXPLICIT_AUTOMATION_SCORE_THRESHOLD}
                  AND {filters}
                GROUP BY bucket_day, label_value
                """
            ).fetchall()
            for day, label, event_count in rows:
                bucket_start, bucket_end = _rollup_bucket(day, tzinfo)
                dimension_rows.append(
                    DailyDimensionRollup(
                        site=site,
                        day=day,
                        timezone=site.timezone,
                        bucket_start=bucket_start,
                        bucket_end=bucket_end,
                        dimension=dimension,
                        label=label or empty_label,
                        event_count=int(event_count),
                    )
                )
        product_rows = con.execute(
            f"""
            SELECT CAST(timezone({timezone_literal}, occurred_at) AS DATE) AS bucket_day,
                   event_name, count(*) event_count,
                   count(*) FILTER (WHERE actor_hash <> '') identified_event_count,
                   sum(metric_value) value_sum, count(metric_value) value_count
            FROM ({base}) archived
            WHERE automation_score < {EXPLICIT_AUTOMATION_SCORE_THRESHOLD}
              AND event_type = 'custom' AND event_name <> ''
            GROUP BY bucket_day, event_name
            """
        ).fetchall()
    analytics_output = []
    for row in daily_rows:
        bucket_start, bucket_end = _rollup_bucket(row[0], tzinfo)
        analytics_output.append(
            DailyAnalyticsRollup(
                site=site,
                day=row[0],
                timezone=site.timezone,
                bucket_start=bucket_start,
                bucket_end=bucket_end,
                event_count=int(row[1]),
                browser_event_count=int(row[2]),
                pageview_count=int(row[3]),
                identified_event_count=int(row[4]),
                automated_event_count=int(row[5]),
                metric_value_sum=row[6],
                metric_value_count=int(row[7]),
            )
        )
    product_output = []
    for row in product_rows:
        bucket_start, bucket_end = _rollup_bucket(row[0], tzinfo)
        product_output.append(
            DailyProductEventRollup(
                site=site,
                day=row[0],
                timezone=site.timezone,
                bucket_start=bucket_start,
                bucket_end=bucket_end,
                event_name=row[1],
                event_count=int(row[2]),
                identified_event_count=int(row[3]),
                value_sum=row[4],
                value_count=int(row[5]),
            )
        )
    with transaction.atomic():
        DailyAnalyticsRollup.objects.filter(site=site).delete()
        DailyDimensionRollup.objects.filter(site=site).delete()
        DailyProductEventRollup.objects.filter(site=site).delete()
        DailyAnalyticsRollup.objects.bulk_create(analytics_output)
        DailyDimensionRollup.objects.bulk_create(dimension_rows)
        DailyProductEventRollup.objects.bulk_create(product_output)
    return len(analytics_output) + len(dimension_rows) + len(product_output)


def rebuild_changed_timezone_rollups(*, limit: int = 12) -> dict[str, int]:
    """Repair rollups invalidated by a site timezone change using queryable Parquet."""
    result = {"rebuilt": 0, "failed": 0}
    candidates = (
        ArchivePartition.objects.filter(
            site__isnull=False,
            storage_class=ArchivePartition.StorageClass.STANDARD,
            status__in=[
                ArchivePartition.Status.VERIFIED,
                ArchivePartition.Status.DELETING,
                ArchivePartition.Status.SOURCE_DELETED,
            ],
        )
        .exclude(timezone=models.F("site__timezone"))
        .values_list("site_id", "stream")
        .distinct()[:limit]
    )
    for site_id, stream in candidates:
        site = TrackedSite.objects.get(pk=site_id)
        rollup_model = (
            DailyBotRollup if stream == ArchivePartition.Stream.BOTS else DailyAnalyticsRollup
        )
        if rollup_model.objects.filter(site=site, timezone=site.timezone).exists():
            continue
        try:
            result["rebuilt"] += rebuild_site_rollups_from_archive(site, stream)
        except Exception:
            result["failed"] += 1
            logger.exception(
                "Archive rollup timezone rebuild failed.",
                extra={"site_id": site_id, "stream": stream},
            )
    return result


def export_partition(
    partition: ArchivePartition, *, delete_source: bool = False
) -> ArchivePartition:
    if not settings.SITEHITS_ARCHIVE_ENABLED:
        raise ArchiveConfigurationError("Archive export is disabled.")
    if partition.site is None:
        raise ArchiveConfigurationError("Cannot export a partition after its site was deleted.")
    object_key = partition.object_key or _archive_key(partition)
    columns = (
        ANALYTICS_COLUMNS if partition.stream == ArchivePartition.Stream.ANALYTICS else BOT_COLUMNS
    )
    try:
        _write_rollups(
            partition.site,
            partition.range_start,
            partition.range_end,
            partition.stream,
        )
        with archive_connection(attach_source=True, needs_s3=True) as con:
            source_query = _source_query(partition)
            source_stats, source_fingerprint = _fingerprint(con, source_query, columns)
            if source_stats[0] == 0:
                partition.delete()
                return partition
            destination = _s3_url(object_key)
            partition.object_key = object_key
            partition.save(update_fields=["object_key", "updated_at"])
            con.execute(
                f"COPY ({source_query}) TO {_sql_literal(destination)} "
                "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000, "
                "OVERWRITE_OR_IGNORE true)"
            )
            source_after_stats, source_after_fingerprint = _fingerprint(
                con,
                source_query,
                columns,
            )
            if source_stats != source_after_stats or source_fingerprint != source_after_fingerprint:
                raise ArchiveVerificationError(
                    "Archive source changed while the partition was exported."
                )
            partition.status = ArchivePartition.Status.EXPORTED
            partition.exported_at = timezone.now()
            partition.error_message = ""
            partition.save(
                update_fields=[
                    "object_key",
                    "status",
                    "exported_at",
                    "error_message",
                    "updated_at",
                ]
            )
            object_version, head = _head_export(object_key)
            archived_query = _canonical_select(
                partition.stream,
                relation=f"read_parquet({_sql_literal(destination)}, union_by_name=true)",
            )
            archived_stats, archived_fingerprint = _fingerprint(con, archived_query, columns)
            if source_stats != archived_stats or source_fingerprint != archived_fingerprint:
                raise ArchiveVerificationError("Archive verification fingerprint mismatch.")
        partition.object_version = object_version
        partition.storage_class = str(
            head.get("StorageClass") or ArchivePartition.StorageClass.STANDARD
        )
        partition.transitioned_at = None
        partition.restore_requested_at = None
        partition.row_count = source_stats[0]
        partition.min_event_id = source_stats[1]
        partition.max_event_id = source_stats[2]
        partition.min_occurred_at = source_stats[3]
        partition.max_occurred_at = source_stats[4]
        partition.verification_sha256 = source_fingerprint
        partition.status = ArchivePartition.Status.VERIFIED
        partition.verified_at = timezone.now()
        partition.error_message = ""
        partition.save()
        invalidate_historical_cache(partition.site_id_snapshot)
        if delete_source:
            delete_verified_source(partition)
        return partition
    except Exception as exc:
        partition.status = ArchivePartition.Status.FAILED
        partition.error_message = str(exc)[:500]
        partition.save(update_fields=["status", "error_message", "updated_at"])
        logger.exception(
            "Archive partition export failed.",
            extra={
                "partition_generation": str(partition.generation),
                "site_id": partition.site_id_snapshot,
                "stream": partition.stream,
            },
        )
        raise


def delete_verified_source(partition: ArchivePartition) -> int:
    if not settings.SITEHITS_ARCHIVE_DELETE_SOURCE:
        raise ArchiveConfigurationError("Archive source deletion is disabled.")
    if partition.status not in {
        ArchivePartition.Status.VERIFIED,
        ArchivePartition.Status.DELETING,
    }:
        raise ArchiveVerificationError("Only verified archive partitions may delete source rows.")
    if partition.status != ArchivePartition.Status.DELETING:
        partition.status = ArchivePartition.Status.DELETING
        partition.save(update_fields=["status", "updated_at"])
    model = AnalyticsEvent if partition.stream == ArchivePartition.Stream.ANALYTICS else BotEvent
    deleted = 0
    while True:
        ids = list(
            model.objects.filter(
                site_id=partition.site_id_snapshot,
                occurred_at__gte=partition.range_start,
                occurred_at__lt=partition.range_end,
            )
            .order_by("id")
            .values_list("id", flat=True)[:DELETE_BATCH_SIZE]
        )
        if not ids:
            break
        with transaction.atomic():
            count, _ = model.objects.filter(id__in=ids).delete()
        deleted += count
    partition.status = ArchivePartition.Status.SOURCE_DELETED
    partition.source_deleted_at = timezone.now()
    partition.save(update_fields=["status", "source_deleted_at", "updated_at"])
    return deleted


def eligible_partitions(*, now: datetime | None = None):
    now = now or timezone.now()
    cutoff = now - timedelta(days=settings.SITEHITS_ARCHIVE_HOT_DAYS)
    for site in TrackedSite.objects.filter(is_active=True).iterator():
        tzinfo = ZoneInfo(site.timezone)
        for stream, model in (
            (ArchivePartition.Stream.ANALYTICS, AnalyticsEvent),
            (ArchivePartition.Stream.BOTS, BotEvent),
        ):
            earliest = (
                model.objects.filter(site=site)
                .order_by("occurred_at")
                .values_list("occurred_at", flat=True)
                .first()
            )
            if earliest is None:
                continue
            local = earliest.astimezone(tzinfo)
            month = datetime(local.year, local.month, 1, tzinfo=tzinfo)
            while True:
                range_start, range_end = _local_month_range(site, month)
                if range_end > cutoff:
                    break
                if model.objects.filter(
                    site=site,
                    occurred_at__gte=range_start,
                    occurred_at__lt=range_end,
                ).exists():
                    overlapping = ArchivePartition.objects.filter(
                        site_id_snapshot=site.pk,
                        stream=stream,
                        range_start__lt=range_end,
                        range_end__gt=range_start,
                    ).first()
                    if overlapping is not None and (
                        overlapping.range_start != range_start or overlapping.range_end != range_end
                    ):
                        month = _month_after(month)
                        continue
                    partition, _ = ArchivePartition.objects.get_or_create(
                        site_id_snapshot=site.pk,
                        stream=stream,
                        range_start=range_start,
                        range_end=range_end,
                        defaults={
                            "site": site,
                            "timezone": site.timezone,
                            "schema_version": ARCHIVE_SCHEMA_VERSION,
                        },
                    )
                    if partition.site_id is None:
                        partition.site = site
                        partition.save(update_fields=["site", "updated_at"])
                    yield partition
                month = _month_after(month)


def compact_eligible_partitions(*, limit: int = 12) -> dict[str, int]:
    counts = {"exported": 0, "deleted": 0, "skipped": 0, "failed": 0}
    processed = 0
    for partition in eligible_partitions():
        if processed >= limit:
            break
        processed += 1
        if partition.status == ArchivePartition.Status.SOURCE_DELETED:
            counts["skipped"] += 1
            continue
        if partition.status in {
            ArchivePartition.Status.VERIFIED,
            ArchivePartition.Status.DELETING,
        }:
            if settings.SITEHITS_ARCHIVE_DELETE_SOURCE:
                counts["deleted"] += delete_verified_source(partition)
            else:
                counts["skipped"] += 1
            continue
        try:
            export_partition(
                partition,
                delete_source=settings.SITEHITS_ARCHIVE_DELETE_SOURCE,
            )
            counts["exported"] += 1
        except Exception:  # noqa: BLE001 - one failed partition must not stop the bounded batch
            counts["failed"] += 1
    return counts


def invalidate_historical_cache(site_id: int | None = None) -> int:
    queryset = HistoricalReportCache.objects.all()
    if site_id is not None:
        queryset = queryset.filter(Q(site_id=site_id) | Q(site__isnull=True))
    deleted, _ = queryset.delete()
    return deleted


def archive_generation(site_ids: list[int] | None = None) -> str:
    queryset = ArchivePartition.objects.filter(
        status__in=[
            ArchivePartition.Status.VERIFIED,
            ArchivePartition.Status.DELETING,
            ArchivePartition.Status.SOURCE_DELETED,
        ]
    )
    if site_ids is not None:
        queryset = queryset.filter(site_id_snapshot__in=site_ids)
    values = list(
        queryset.order_by("site_id_snapshot", "stream", "range_start").values_list(
            "generation", "updated_at", "status"
        )
    )
    tombstones = ColdDataTombstone.objects.all()
    if site_ids is not None:
        tombstones = tombstones.filter(site_id_snapshot__in=site_ids)
    tombstone_values = list(
        tombstones.order_by("site_id_snapshot", "created_at", "id").values_list(
            "site_id_snapshot",
            "kind",
            "actor_hash",
            "status",
            "created_at",
            "processed_at",
        )
    )
    encoded = json.dumps(
        {
            "partitions": [[str(a), b.isoformat(), c] for a, b, c in values],
            "tombstones": [
                [
                    site_id,
                    kind,
                    actor_hash,
                    status,
                    created_at.isoformat(),
                    processed_at.isoformat() if processed_at else None,
                ]
                for site_id, kind, actor_hash, status, created_at, processed_at in tombstone_values
            ],
        },
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def cold_partition_urls(stream: str, *, site_ids: list[int], start: datetime, end: datetime):
    partitions = ArchivePartition.objects.filter(
        site_id_snapshot__in=site_ids,
        stream=stream,
        status__in=[
            ArchivePartition.Status.DELETING,
            ArchivePartition.Status.SOURCE_DELETED,
        ],
        range_start__lt=end,
        range_end__gt=start,
    ).exclude(object_key="")
    return [_s3_url(key) for key in partitions.values_list("object_key", flat=True)]


def create_cold_deletion(site: TrackedSite, actor_hash: str, deleted_hot_events: int):
    with transaction.atomic():
        tombstone = ColdDataTombstone.objects.create(
            site=site,
            site_id_snapshot=site.pk,
            kind=ColdDataTombstone.Kind.ACTOR,
            actor_hash=actor_hash,
        )
        job = ColdDeletionJob.objects.create(
            site=site,
            site_id_snapshot=site.pk,
            kind=ColdDeletionJob.Kind.ACTOR,
            actor_hash=actor_hash,
            deleted_hot_events=deleted_hot_events,
        )
        invalidate_historical_cache(site.pk)
    return job, tombstone


def _delete_all_object_versions(object_key: str) -> None:
    client = _boto_client()
    paginator = client.get_paginator("list_object_versions")
    for page in paginator.paginate(
        Bucket=settings.SITEHITS_ARCHIVE_BUCKET,
        Prefix=object_key,
    ):
        identifiers = [
            {"Key": item["Key"], "VersionId": item["VersionId"]}
            for item in [*(page.get("Versions") or []), *(page.get("DeleteMarkers") or [])]
            if item["Key"] == object_key
        ]
        if identifiers:
            client.delete_objects(
                Bucket=settings.SITEHITS_ARCHIVE_BUCKET,
                Delete={"Objects": identifiers, "Quiet": True},
            )


def _delete_object_versions_except(object_key: str, keep_version: str) -> None:
    """Permanently remove superseded versions after a verified same-key copy."""
    if not keep_version:
        return
    client = _boto_client()
    paginator = client.get_paginator("list_object_versions")
    for page in paginator.paginate(
        Bucket=settings.SITEHITS_ARCHIVE_BUCKET,
        Prefix=object_key,
    ):
        identifiers = [
            {"Key": item["Key"], "VersionId": item["VersionId"]}
            for item in [*(page.get("Versions") or []), *(page.get("DeleteMarkers") or [])]
            if item["Key"] == object_key and item["VersionId"] != keep_version
        ]
        if identifiers:
            client.delete_objects(
                Bucket=settings.SITEHITS_ARCHIVE_BUCKET,
                Delete={"Objects": identifiers, "Quiet": True},
            )


def transition_partition_to_glacier(partition: ArchivePartition) -> ArchivePartition:
    """Create and verify a Glacier version, then permanently remove older versions."""
    if partition.status != ArchivePartition.Status.SOURCE_DELETED:
        raise ArchiveVerificationError(
            "Only source-deleted archive partitions may transition to Glacier."
        )
    if not partition.object_key:
        raise ArchiveVerificationError("Archive partition has no object to transition.")
    if partition.storage_class == ArchivePartition.StorageClass.GLACIER:
        return partition

    client = _boto_client()
    copy_source: dict[str, str] = {
        "Bucket": settings.SITEHITS_ARCHIVE_BUCKET,
        "Key": partition.object_key,
    }
    if partition.object_version:
        copy_source["VersionId"] = partition.object_version
    copy_parameters: dict[str, object] = {
        "Bucket": settings.SITEHITS_ARCHIVE_BUCKET,
        "Key": partition.object_key,
        "CopySource": copy_source,
        "MetadataDirective": "COPY",
        "StorageClass": ArchivePartition.StorageClass.GLACIER,
    }
    if settings.SITEHITS_ARCHIVE_KMS_KEY_ID:
        copy_parameters.update(
            {
                "ServerSideEncryption": "aws:kms",
                "SSEKMSKeyId": settings.SITEHITS_ARCHIVE_KMS_KEY_ID,
            }
        )
    response = client.copy_object(**copy_parameters)
    new_version = str(response.get("VersionId") or "")
    head_parameters: dict[str, object] = {
        "Bucket": settings.SITEHITS_ARCHIVE_BUCKET,
        "Key": partition.object_key,
    }
    if new_version:
        head_parameters["VersionId"] = new_version
    head = client.head_object(**head_parameters)
    new_version = new_version or str(head.get("VersionId") or "")
    if head.get("StorageClass") != ArchivePartition.StorageClass.GLACIER:
        raise ArchiveVerificationError("S3 did not persist the Glacier storage class.")
    if not settings.DEBUG and not new_version:
        raise ArchiveVerificationError("Glacier transition did not create a versioned object.")

    _delete_object_versions_except(partition.object_key, new_version)
    partition.object_version = new_version
    partition.storage_class = ArchivePartition.StorageClass.GLACIER
    partition.transitioned_at = timezone.now()
    partition.restore_requested_at = None
    partition.save(
        update_fields=[
            "object_version",
            "storage_class",
            "transitioned_at",
            "restore_requested_at",
            "updated_at",
        ]
    )
    return partition


def transition_cold_archive_storage(
    *, now: datetime | None = None, limit: int = 20
) -> dict[str, int]:
    """Apply the exact manifest-based two-year queryable storage boundary."""
    now = now or timezone.now()
    queryable_cutoff = now - timedelta(days=settings.SITEHITS_ARCHIVE_QUERYABLE_DAYS)
    retention_cutoff = now - timedelta(days=settings.SITEHITS_ARCHIVE_RETENTION_DAYS)
    partitions = (
        ArchivePartition.objects.filter(
            status=ArchivePartition.Status.SOURCE_DELETED,
            storage_class=ArchivePartition.StorageClass.STANDARD,
            range_end__gt=retention_cutoff,
            range_end__lte=queryable_cutoff,
        )
        .exclude(object_key="")
        .order_by("range_end")[:limit]
    )
    result = {"transitioned": 0, "failed": 0}
    for partition in partitions:
        try:
            transition_partition_to_glacier(partition)
            result["transitioned"] += 1
        except Exception:
            result["failed"] += 1
            logger.exception(
                "Archive Glacier transition failed.",
                extra={"partition_generation": str(partition.generation)},
            )
    return result


def _glacier_partition_ready(partition: ArchivePartition) -> bool:
    client = _boto_client()
    parameters: dict[str, object] = {
        "Bucket": settings.SITEHITS_ARCHIVE_BUCKET,
        "Key": partition.object_key,
    }
    if partition.object_version:
        parameters["VersionId"] = partition.object_version
    head = client.head_object(**parameters)
    restore = str(head.get("Restore") or "")
    if 'ongoing-request="false"' in restore:
        return True
    try:
        client.restore_object(
            **parameters,
            RestoreRequest={
                "Days": 3,
                "GlacierJobParameters": {"Tier": "Standard"},
            },
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code == "ObjectAlreadyInActiveTierError":
            return True
        if code != "RestoreAlreadyInProgress":
            raise
    partition.restore_requested_at = timezone.now()
    partition.save(update_fields=["restore_requested_at", "updated_at"])
    return False


def _rewrite_actor_partition(partition: ArchivePartition, actor_hash: str) -> tuple[int, int]:
    if partition.stream != ArchivePartition.Stream.ANALYTICS:
        raise ArchiveVerificationError("Actor deletion only applies to analytics partitions.")
    old_key = partition.object_key
    old_url = _s3_url(old_key)
    new_generation = uuid4()
    new_key = _archive_key(partition, generation=str(new_generation))
    new_url = _s3_url(new_key)
    with archive_connection(needs_s3=True) as con:
        source = _canonical_select(
            partition.stream,
            relation=f"read_parquet({_sql_literal(old_url)}, union_by_name=true)",
        )
        old_stats, _ = _fingerprint(con, source, ANALYTICS_COLUMNS)
        filtered = (
            f"SELECT * FROM ({source}) archived WHERE actor_hash <> {_sql_literal(actor_hash)}"
        )
        con.execute(
            f"COPY ({filtered}) TO {_sql_literal(new_url)} "
            "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000, "
            "OVERWRITE_OR_IGNORE true)"
        )
        new_stats, new_fingerprint = _fingerprint(
            con,
            _canonical_select(
                partition.stream,
                relation=f"read_parquet({_sql_literal(new_url)}, union_by_name=true)",
            ),
            ANALYTICS_COLUMNS,
        )
    object_version, _ = _head_export(new_key)
    removed = int(old_stats[0]) - int(new_stats[0])
    partition.object_key = new_key
    partition.object_version = object_version
    partition.row_count = new_stats[0]
    partition.min_event_id = new_stats[1]
    partition.max_event_id = new_stats[2]
    partition.min_occurred_at = new_stats[3]
    partition.max_occurred_at = new_stats[4]
    partition.verification_sha256 = new_fingerprint
    partition.generation = new_generation
    partition.storage_class = ArchivePartition.StorageClass.STANDARD
    partition.transitioned_at = None
    partition.restore_requested_at = None
    partition.save()
    _delete_all_object_versions(old_key)
    return 1, removed


def process_cold_deletion(job: ColdDeletionJob) -> ColdDeletionJob:
    if job.status == ColdDeletionJob.Status.COMPLETED:
        return job
    job.status = ColdDeletionJob.Status.RUNNING
    if job.started_at is None:
        job.started_at = timezone.now()
    job.error_message = ""
    job.save(update_fields=["status", "started_at", "error_message", "updated_at"])
    try:
        rewritten = 0
        removed = 0
        if job.kind == ColdDeletionJob.Kind.SITE:
            partitions = list(
                ArchivePartition.objects.filter(site_id_snapshot=job.site_id_snapshot)
            )
            for partition in partitions:
                if partition.object_key:
                    _delete_all_object_versions(partition.object_key)
                removed += partition.row_count
                partition.delete()
            now = timezone.now()
            ColdDataTombstone.objects.filter(
                site_id_snapshot=job.site_id_snapshot,
                kind=ColdDataTombstone.Kind.SITE,
                status__in=[
                    ColdDataTombstone.Status.PENDING,
                    ColdDataTombstone.Status.FAILED,
                ],
            ).update(status=ColdDataTombstone.Status.PROCESSED, processed_at=now)
            job.status = ColdDeletionJob.Status.COMPLETED
            job.deleted_cold_events = removed
            job.rewritten_partitions = len(partitions)
            job.completed_at = now
            job.save()
            invalidate_historical_cache()
            return job
        reset_partitions = ArchivePartition.objects.filter(
            site_id_snapshot=job.site_id_snapshot,
            stream=ArchivePartition.Stream.ANALYTICS,
            status__in=[
                ArchivePartition.Status.PENDING,
                ArchivePartition.Status.EXPORTED,
                ArchivePartition.Status.FAILED,
            ],
        ).exclude(object_key="")
        for partition in reset_partitions.iterator():
            _delete_all_object_versions(partition.object_key)
            partition.object_key = ""
            partition.object_version = ""
            partition.status = ArchivePartition.Status.PENDING
            partition.error_message = ""
            partition.exported_at = None
            partition.save(
                update_fields=[
                    "object_key",
                    "object_version",
                    "status",
                    "error_message",
                    "exported_at",
                    "updated_at",
                ]
            )
        partitions = ArchivePartition.objects.filter(
            site_id_snapshot=job.site_id_snapshot,
            stream=ArchivePartition.Stream.ANALYTICS,
            status__in=[
                ArchivePartition.Status.VERIFIED,
                ArchivePartition.Status.DELETING,
                ArchivePartition.Status.SOURCE_DELETED,
            ],
        ).exclude(object_key="")
        glacier_partitions = list(
            partitions.filter(storage_class=ArchivePartition.StorageClass.GLACIER)
        )
        restore_states = [_glacier_partition_ready(partition) for partition in glacier_partitions]
        if not all(restore_states):
            job.status = ColdDeletionJob.Status.WAITING_RESTORE
            job.error_message = ""
            job.completed_at = None
            job.save(
                update_fields=[
                    "status",
                    "error_message",
                    "completed_at",
                    "updated_at",
                ]
            )
            return job
        queryable_cutoff = timezone.now() - timedelta(days=settings.SITEHITS_ARCHIVE_QUERYABLE_DAYS)
        for partition in partitions.iterator():
            changed, count = _rewrite_actor_partition(partition, job.actor_hash)
            rewritten += changed
            removed += count
            if partition.range_end <= queryable_cutoff:
                transition_partition_to_glacier(partition)
        if job.site is not None:
            rebuild_site_rollups_from_archive(
                job.site,
                ArchivePartition.Stream.ANALYTICS,
            )
        now = timezone.now()
        ColdDataTombstone.objects.filter(
            site_id_snapshot=job.site_id_snapshot,
            actor_hash=job.actor_hash,
            status__in=[
                ColdDataTombstone.Status.PENDING,
                ColdDataTombstone.Status.FAILED,
            ],
        ).update(status=ColdDataTombstone.Status.PROCESSED, processed_at=now)
        job.status = ColdDeletionJob.Status.COMPLETED
        job.rewritten_partitions = rewritten
        job.deleted_cold_events = removed
        job.completed_at = now
        job.save()
        invalidate_historical_cache(job.site_id_snapshot)
        return job
    except Exception as exc:
        job.status = ColdDeletionJob.Status.FAILED
        job.error_message = str(exc)[:500]
        job.completed_at = timezone.now()
        job.save()
        ColdDataTombstone.objects.filter(
            site_id_snapshot=job.site_id_snapshot,
            actor_hash=job.actor_hash,
            status=ColdDataTombstone.Status.PENDING,
        ).update(status=ColdDataTombstone.Status.FAILED, error_message=str(exc)[:500])
        logger.exception(
            "Cold actor deletion failed.",
            extra={"request_id": str(job.request_id), "site_id": job.site_id_snapshot},
        )
        return job


def process_pending_cold_deletions(*, limit: int = 20) -> dict[str, int]:
    result = {"completed": 0, "waiting_restore": 0, "failed": 0}
    stale_running = timezone.now() - timedelta(hours=1)
    jobs = ColdDeletionJob.objects.filter(
        Q(
            status__in=[
                ColdDeletionJob.Status.ACCEPTED,
                ColdDeletionJob.Status.WAITING_RESTORE,
                ColdDeletionJob.Status.FAILED,
            ]
        )
        | Q(status=ColdDeletionJob.Status.RUNNING, updated_at__lt=stale_running)
    ).order_by("created_at")[:limit]
    for job in jobs:
        processed = process_cold_deletion(job)
        if processed.status == ColdDeletionJob.Status.COMPLETED:
            key = "completed"
        elif processed.status == ColdDeletionJob.Status.WAITING_RESTORE:
            key = "waiting_restore"
        else:
            key = "failed"
        result[key] += 1
    return result


def prune_archive_manifests(*, now: datetime | None = None) -> int:
    cutoff = (now or timezone.now()) - timedelta(days=settings.SITEHITS_ARCHIVE_RETENTION_DAYS)
    deleted = 0
    partitions = ArchivePartition.objects.filter(range_end__lte=cutoff).exclude(object_key="")
    for partition in partitions.iterator():
        _delete_all_object_versions(partition.object_key)
        partition.delete()
        deleted += 1
    return deleted


def cold_query_slot(timeout: float | None = None):
    return _COLD_QUERY_SLOT.acquire(timeout=timeout)


def release_cold_query_slot() -> None:
    _COLD_QUERY_SLOT.release()
