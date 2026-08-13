import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import duckdb
import pytest
from botocore.exceptions import ClientError
from django.test import override_settings
from django.utils import timezone

from analytics import archive, cold_reporting
from analytics.archive import (
    ArchiveVerificationError,
    HistoricalDataUnavailable,
    _source_query,
    _write_rollups,
    archive_connection,
    archive_generation,
    delete_verified_source,
    eligible_partitions,
)
from analytics.cold_reporting import cached_historical_report
from analytics.models import (
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
    ProductEventDefinition,
)
from analytics.product_reporting import product_metrics
from analytics.reporting import bot_traffic, breakdown, overview, site_overviews, timeseries
from websites.models import TrackedSite


def _without_freshness(value):
    result = dict(value)
    result.pop("freshness", None)
    return result


def _mirror_database(path: Path, site_id: int, now: datetime) -> None:
    analytics_types = {
        "id": "INTEGER",
        "site_id": "INTEGER",
        "occurred_at": "TIMESTAMP",
        "received_at": "TIMESTAMP",
        "metric_value": "NUMERIC",
        "viewport_width": "INTEGER",
        "viewport_height": "INTEGER",
        "screen_width": "INTEGER",
        "screen_height": "INTEGER",
        "automation_score": "INTEGER",
    }
    bot_types = {
        "id": "INTEGER",
        "site_id": "INTEGER",
        "occurred_at": "TIMESTAMP",
        "received_at": "TIMESTAMP",
        "status_code": "INTEGER",
    }
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE analytics_analyticsevent ("
        + ", ".join(
            f'"{column}" {analytics_types.get(column, "TEXT")}'
            for column in archive.ANALYTICS_COLUMNS
        )
        + ")"
    )
    connection.execute(
        "CREATE TABLE analytics_botevent ("
        + ", ".join(f'"{column}" {bot_types.get(column, "TEXT")}' for column in archive.BOT_COLUMNS)
        + ")"
    )
    analytics_rows = []
    bot_rows = []
    for day in range(1, 401):
        occurred_at = now - timedelta(days=day, hours=day % 5)
        common = {
            "id": day,
            "site_id": site_id,
            "event_type": "pageview",
            "event_name": "",
            "source": "browser",
            "occurred_at": occurred_at.isoformat(),
            "received_at": occurred_at.isoformat(),
            "visitor_hash": f"visitor-{day % 17}",
            "session_id": f"session-{day}",
            "actor_hash": "",
            "idempotency_hash": "",
            "metric_value": None,
            "metric_unit": "",
            "path": f"/page-{day % 7}",
            "referrer_domain": "search.example" if day % 3 else "",
            "referrer_path": "",
            "utm_source": "newsletter" if day % 11 == 0 else "",
            "utm_medium": "email" if day % 11 == 0 else "",
            "utm_campaign": "archive" if day % 11 == 0 else "",
            "utm_term": "",
            "utm_content": "",
            "country_code": "US",
            "country_name": "United States",
            "region_code": "NY",
            "region_name": "New York",
            "city_name": "New York",
            "device": "desktop",
            "browser": "Firefox",
            "operating_system": "Linux",
            "language": "en",
            "client_timezone": "America/New_York",
            "viewport_width": 1440,
            "viewport_height": 900,
            "screen_width": 1440,
            "screen_height": 900,
            "automation_score": 0,
            "automation_reasons": "[]",
            "properties": json.dumps({"fixture_day": day}),
        }
        analytics_rows.append(tuple(common[column] for column in archive.ANALYTICS_COLUMNS))
        if day % 10 == 0:
            product = dict(common)
            product.update(
                {
                    "id": 10_000 + day,
                    "event_type": "custom",
                    "event_name": "purchase",
                    "source": "server",
                    "visitor_hash": "",
                    "actor_hash": f"actor-{day % 13}",
                    "metric_value": str(Decimal("12.340000")),
                    "metric_unit": "TRY",
                    "path": "",
                    "properties": json.dumps({"plan": "pro"}, sort_keys=True),
                }
            )
            analytics_rows.append(tuple(product[column] for column in archive.ANALYTICS_COLUMNS))
        if day % 4 == 0:
            bot = {
                "id": day,
                "site_id": site_id,
                "occurred_at": occurred_at.isoformat(),
                "received_at": occurred_at.isoformat(),
                "path": f"/page-{day % 7}",
                "status_code": 200,
                "provider": "OpenAI",
                "crawler": "GPTBot",
                "category": "training",
                "verification": "user_agent",
            }
            bot_rows.append(tuple(bot[column] for column in archive.BOT_COLUMNS))
    analytics_placeholders = ", ".join("?" for _ in archive.ANALYTICS_COLUMNS)
    bot_placeholders = ", ".join("?" for _ in archive.BOT_COLUMNS)
    connection.executemany(
        f"INSERT INTO analytics_analyticsevent VALUES ({analytics_placeholders})",
        analytics_rows,
    )
    connection.executemany(
        f"INSERT INTO analytics_botevent VALUES ({bot_placeholders})",
        bot_rows,
    )
    connection.commit()
    connection.close()


@pytest.mark.django_db(transaction=True)
def test_400_day_reports_match_across_hot_cold_boundary(tmp_path, monkeypatch):
    site = TrackedSite.objects.create(
        name="Historical",
        slug="historical",
        allowed_domains=["historical.example"],
        timezone="America/New_York",
    )
    ProductEventDefinition.objects.create(
        site=site,
        event_name="purchase",
        display_name="Purchases",
        description="Completed purchases.",
        aggregation=ProductEventDefinition.Aggregation.SUM,
        unit="TRY",
    )
    now = timezone.now().replace(microsecond=0)
    source_path = tmp_path / "source.sqlite3"
    _mirror_database(source_path, site.pk, now)
    attachment = lambda: ("sqlite", str(source_path))
    monkeypatch.setattr(archive, "_database_attachment", attachment)
    monkeypatch.setattr(cold_reporting, "_database_attachment", attachment)

    scoped_sites = TrackedSite.objects.filter(pk=site.pk)

    def reports():
        HistoricalReportCache.objects.all().delete()
        return {
            "overview": _without_freshness(overview(site.slug, "last365d", scoped_sites)),
            "overview_all": _without_freshness(overview("all", "last365d", scoped_sites)),
            "sites": _without_freshness(site_overviews("last365d", scoped_sites)),
            "timeseries": _without_freshness(
                timeseries(site.slug, "last365d", "daily", scoped_sites)
            ),
            "breakdown": _without_freshness(
                breakdown(site.slug, "last365d", "pages", 8, scoped_sites)
            ),
            "bots": _without_freshness(bot_traffic(site.slug, "last365d", 8, scoped_sites)),
            "product": _without_freshness(product_metrics(site.slug, "last365d", scoped_sites)),
        }

    before = reports()
    range_start = now - timedelta(days=401)
    range_end = now - timedelta(days=90)
    analytics_partition = ArchivePartition.objects.create(
        site=site,
        site_id_snapshot=site.pk,
        stream=ArchivePartition.Stream.ANALYTICS,
        range_start=range_start,
        range_end=range_end,
        timezone=site.timezone,
    )
    bot_partition = ArchivePartition.objects.create(
        site=site,
        site_id_snapshot=site.pk,
        stream=ArchivePartition.Stream.BOTS,
        range_start=range_start,
        range_end=range_end,
        timezone=site.timezone,
    )
    for partition in (analytics_partition, bot_partition):
        parquet_path = tmp_path / f"{partition.stream}.parquet"
        with archive_connection(attach_source=True) as con:
            con.execute(
                f"COPY ({_source_query(partition)}) TO ? "
                "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)",
                [str(parquet_path)],
            )
            partition.row_count = con.execute(
                "SELECT count(*) FROM read_parquet(?)", [str(parquet_path)]
            ).fetchone()[0]
        partition.object_key = str(parquet_path)
        partition.status = ArchivePartition.Status.SOURCE_DELETED
        partition.verified_at = now
        partition.source_deleted_at = now
        partition.save()
    mirror = sqlite3.connect(source_path)
    for table in ("analytics_analyticsevent", "analytics_botevent"):
        mirror.execute(
            f"DELETE FROM {table} WHERE occurred_at >= ? AND occurred_at < ?",
            (range_start.isoformat(), range_end.isoformat()),
        )
    mirror.commit()
    mirror.close()
    monkeypatch.setattr(cold_reporting, "_s3_url", lambda key: key)
    monkeypatch.setattr(cold_reporting, "_configure_s3", lambda con: None)
    with override_settings(SITEHITS_ARCHIVE_QUERY_ENABLED=True):
        after = reports()
        ColdDataTombstone.objects.create(
            site=site,
            site_id_snapshot=site.pk,
            kind=ColdDataTombstone.Kind.ACTOR,
            actor_hash="actor-10",
        )
        HistoricalReportCache.objects.all().delete()
        filtered_product = product_metrics(site.slug, "last365d", scoped_sites)

    assert after == before
    assert before["overview"]["current"]["pageviews"] > 0
    assert before["overview"]["previous"]["pageviews"] > 0
    assert before["timeseries"]["granularity"] == "daily"
    assert before["product"]["metrics"][0]["value_sum"] is not None
    assert (
        filtered_product["metrics"][0]["event_count"]
        < after["product"]["metrics"][0]["event_count"]
    )


@pytest.mark.django_db
def test_only_complete_old_local_months_are_eligible(tracked_site):
    tracked_site.timezone = "UTC"
    tracked_site.save(update_fields=["timezone"])
    now = datetime(2026, 8, 13, 12, tzinfo=UTC)
    AnalyticsEvent.objects.create(
        site=tracked_site,
        event_type="pageview",
        occurred_at=datetime(2026, 4, 15, 12, tzinfo=UTC),
        visitor_hash="old",
        session_id="old",
        path="/old",
    )
    AnalyticsEvent.objects.create(
        site=tracked_site,
        event_type="pageview",
        occurred_at=datetime(2026, 5, 1, 1, tzinfo=UTC),
        visitor_hash="warm",
        session_id="warm",
        path="/warm",
    )

    partitions = list(eligible_partitions(now=now))

    assert [(item.range_start.month, item.range_end.month) for item in partitions] == [(4, 5)]


@pytest.mark.django_db
def test_source_deletion_requires_verified_manifest(tracked_site):
    event = AnalyticsEvent.objects.create(
        site=tracked_site,
        event_type="pageview",
        occurred_at=timezone.now() - timedelta(days=120),
        visitor_hash="visitor",
        session_id="session",
        path="/",
    )
    partition = ArchivePartition.objects.create(
        site=tracked_site,
        site_id_snapshot=tracked_site.pk,
        stream=ArchivePartition.Stream.ANALYTICS,
        range_start=event.occurred_at - timedelta(days=1),
        range_end=event.occurred_at + timedelta(days=1),
        timezone=tracked_site.timezone,
    )
    with override_settings(SITEHITS_ARCHIVE_DELETE_SOURCE=True):
        with pytest.raises(ArchiveVerificationError):
            delete_verified_source(partition)
        assert AnalyticsEvent.objects.filter(pk=event.pk).exists()
        partition.status = ArchivePartition.Status.VERIFIED
        partition.save(update_fields=["status", "updated_at"])
        assert delete_verified_source(partition) == 1
    partition.refresh_from_db()
    assert partition.status == ArchivePartition.Status.SOURCE_DELETED


@pytest.mark.django_db
def test_rollups_are_idempotent_and_store_dst_utc_boundaries():
    site = TrackedSite.objects.create(
        name="DST",
        slug="dst",
        allowed_domains=["dst.example"],
        timezone="America/New_York",
    )
    start = datetime(2025, 3, 9, 5, tzinfo=UTC)
    end = datetime(2025, 3, 10, 4, tzinfo=UTC)
    AnalyticsEvent.objects.create(
        site=site,
        event_type="pageview",
        source="browser",
        occurred_at=start + timedelta(hours=2),
        visitor_hash="visitor",
        session_id="session",
        actor_hash="actor",
        path="/dst",
        country_name="United States",
    )
    AnalyticsEvent.objects.create(
        site=site,
        event_type="custom",
        event_name="purchase",
        source="server",
        occurred_at=start + timedelta(hours=3),
        actor_hash="actor",
        metric_value=Decimal("10.500000"),
        metric_unit="TRY",
        path="",
    )
    BotEvent.objects.create(
        site=site,
        occurred_at=start + timedelta(hours=4),
        path="/dst",
        status_code=200,
        provider="OpenAI",
        crawler="GPTBot",
        category="training",
    )
    for _ in range(2):
        _write_rollups(site, start, end, ArchivePartition.Stream.ANALYTICS)
        _write_rollups(site, start, end, ArchivePartition.Stream.BOTS)

    daily = DailyAnalyticsRollup.objects.get(site=site)
    assert daily.event_count == 2
    assert daily.bucket_end - daily.bucket_start == timedelta(hours=23)
    assert DailyDimensionRollup.objects.filter(site=site, dimension="pages").count() == 1
    assert DailyProductEventRollup.objects.filter(site=site).count() == 1
    assert DailyBotRollup.objects.filter(site=site, dimension="provider").count() == 1


@pytest.mark.django_db(transaction=True)
def test_stale_cache_is_never_reused_after_actor_tombstone(tracked_site):
    kwargs = {
        "report_type": "overview",
        "site_selector": tracked_site.slug,
        "site": tracked_site,
        "site_ids": [tracked_site.pk],
        "period": "last365d",
        "parameters": {"site": tracked_site.slug},
    }
    first = cached_historical_report(
        **kwargs,
        producer=lambda: {"value": 1},
    )
    assert first["freshness"]["source"] == "hybrid"
    HistoricalReportCache.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    stale = cached_historical_report(
        **kwargs,
        producer=lambda: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    assert stale["freshness"]["is_stale"] is True
    ColdDataTombstone.objects.create(
        site=tracked_site,
        site_id_snapshot=tracked_site.pk,
        kind=ColdDataTombstone.Kind.ACTOR,
        actor_hash="a" * 64,
    )
    with pytest.raises(HistoricalDataUnavailable):
        cached_historical_report(
            **kwargs,
            producer=lambda: (_ for _ in ()).throw(RuntimeError("offline")),
        )


@pytest.mark.django_db
def test_timezone_change_invalidates_rollups_and_cache(tracked_site):
    now = timezone.now()
    DailyAnalyticsRollup.objects.create(
        site=tracked_site,
        day=now.date(),
        timezone=tracked_site.timezone,
        bucket_start=now,
        bucket_end=now + timedelta(days=1),
    )
    HistoricalReportCache.objects.create(
        site=tracked_site,
        site_selector=tracked_site.slug,
        report_type="overview",
        period="last365d",
        parameters_hash="a" * 64,
        parameters={},
        result={},
        archive_generation=archive_generation([tracked_site.pk]),
        generated_at=now,
        expires_at=now + timedelta(hours=1),
    )
    tracked_site.timezone = "UTC"
    tracked_site.save(update_fields=["timezone"])
    assert not DailyAnalyticsRollup.objects.filter(site=tracked_site).exists()
    assert not HistoricalReportCache.objects.filter(site=tracked_site).exists()


@pytest.mark.django_db(transaction=True)
def test_timezone_change_rollups_are_rebuilt_from_parquet(tmp_path, monkeypatch):
    site = TrackedSite.objects.create(
        name="Timezone rebuild",
        slug="timezone-rebuild",
        allowed_domains=["timezone-rebuild.example"],
        timezone="UTC",
    )
    now = timezone.now().replace(microsecond=0)
    source_path = tmp_path / "timezone-source.sqlite3"
    parquet_path = tmp_path / "timezone-events.parquet"
    bot_parquet_path = tmp_path / "timezone-bots.parquet"
    _mirror_database(source_path, site.pk, now)
    monkeypatch.setattr(archive, "_database_attachment", lambda: ("sqlite", str(source_path)))
    partition = ArchivePartition.objects.create(
        site=site,
        site_id_snapshot=site.pk,
        stream=ArchivePartition.Stream.ANALYTICS,
        range_start=now - timedelta(days=401),
        range_end=now - timedelta(days=90),
        timezone="UTC",
        object_key=str(parquet_path),
        status=ArchivePartition.Status.SOURCE_DELETED,
    )
    bot_partition = ArchivePartition.objects.create(
        site=site,
        site_id_snapshot=site.pk,
        stream=ArchivePartition.Stream.BOTS,
        range_start=now - timedelta(days=401),
        range_end=now - timedelta(days=90),
        timezone="UTC",
        object_key=str(bot_parquet_path),
        status=ArchivePartition.Status.SOURCE_DELETED,
    )
    with archive_connection(attach_source=True) as con:
        con.execute(
            f"COPY ({_source_query(partition)}) TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
            [str(parquet_path)],
        )
        con.execute(
            f"COPY ({_source_query(bot_partition)}) TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
            [str(bot_parquet_path)],
        )
    monkeypatch.setattr(archive, "_s3_url", lambda key: key)
    monkeypatch.setattr(archive, "_configure_s3", lambda con: None)

    site.timezone = "America/New_York"
    site.save(update_fields=["timezone"])
    result = archive.rebuild_changed_timezone_rollups()

    rows = list(DailyAnalyticsRollup.objects.filter(site=site))
    assert result["failed"] == 0
    assert result["rebuilt"] > 0
    assert rows
    assert {row.timezone for row in rows} == {"America/New_York"}
    assert any(
        row.bucket_end - row.bucket_start in {timedelta(hours=23), timedelta(hours=25)}
        for row in rows
    )
    assert DailyDimensionRollup.objects.filter(site=site).exists()
    assert DailyProductEventRollup.objects.filter(site=site).exists()
    assert DailyBotRollup.objects.filter(
        site=site,
        timezone="America/New_York",
    ).exists()


@pytest.mark.django_db
def test_site_delete_queues_fk_independent_cold_deletion(tracked_site):
    site_id = tracked_site.pk
    tracked_site.delete()
    tombstone = ColdDataTombstone.objects.get(
        site_id_snapshot=site_id,
        kind=ColdDataTombstone.Kind.SITE,
    )
    job = ColdDeletionJob.objects.get(
        site_id_snapshot=site_id,
        kind=ColdDeletionJob.Kind.SITE,
    )
    assert tombstone.site is None
    assert job.site is None


def test_parquet_zstd_round_trip_preserves_json_decimal_and_timestamp(tmp_path):
    target = tmp_path / "roundtrip.parquet"
    con = duckdb.connect(":memory:")
    try:
        con.execute(
            "CREATE TABLE sample(id BIGINT, occurred_at TIMESTAMPTZ, metric DECIMAL(20,6), "
            "properties VARCHAR)"
        )
        con.execute(
            "INSERT INTO sample VALUES (?, ?, ?, ?)",
            [
                1,
                datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
                Decimal("12.340000"),
                json.dumps({"nested": {"ok": True}}, sort_keys=True),
            ],
        )
        con.execute(
            "COPY sample TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
            [str(target)],
        )
        row = con.execute("SELECT * FROM read_parquet(?)", [str(target)]).fetchone()
        compression = {
            value[0]
            for value in con.execute(
                "SELECT DISTINCT compression FROM parquet_metadata(?)", [str(target)]
            ).fetchall()
        }
    finally:
        con.close()
    assert row[1].astimezone(UTC).isoformat() == "2026-01-02T03:04:05+00:00"
    assert row[2] == Decimal("12.340000")
    assert json.loads(row[3]) == {"nested": {"ok": True}}
    assert compression == {"ZSTD"}


@pytest.mark.django_db
def test_manifest_cutoff_transitions_only_third_year_partitions(tracked_site, monkeypatch):
    now = datetime(2026, 8, 13, 12, tzinfo=UTC)
    selected = []

    def record_transition(partition):
        selected.append(partition.object_key)
        partition.storage_class = ArchivePartition.StorageClass.GLACIER
        partition.save(update_fields=["storage_class", "updated_at"])
        return partition

    monkeypatch.setattr(archive, "transition_partition_to_glacier", record_transition)
    for age in (700, 800, 1200):
        ArchivePartition.objects.create(
            site=tracked_site,
            site_id_snapshot=tracked_site.pk,
            stream=ArchivePartition.Stream.ANALYTICS,
            range_start=now - timedelta(days=age + 30),
            range_end=now - timedelta(days=age),
            timezone="UTC",
            object_key=f"archive/{age}.parquet",
            status=ArchivePartition.Status.SOURCE_DELETED,
        )

    with override_settings(
        SITEHITS_ARCHIVE_QUERYABLE_DAYS=730,
        SITEHITS_ARCHIVE_RETENTION_DAYS=1095,
    ):
        result = archive.transition_cold_archive_storage(now=now)

    assert result == {"transitioned": 1, "failed": 0}
    assert selected == ["archive/800.parquet"]


@pytest.mark.django_db
def test_glacier_transition_verifies_new_version_and_removes_old_versions(
    tracked_site, monkeypatch
):
    now = timezone.now()
    partition = ArchivePartition.objects.create(
        site=tracked_site,
        site_id_snapshot=tracked_site.pk,
        stream=ArchivePartition.Stream.ANALYTICS,
        range_start=now - timedelta(days=800),
        range_end=now - timedelta(days=770),
        timezone="UTC",
        object_key="archive/events.parquet",
        object_version="v1",
        status=ArchivePartition.Status.SOURCE_DELETED,
    )

    class Paginator:
        def paginate(self, **kwargs):
            return [
                {
                    "Versions": [
                        {"Key": partition.object_key, "VersionId": "v2"},
                        {"Key": partition.object_key, "VersionId": "v1"},
                    ],
                    "DeleteMarkers": [],
                }
            ]

    class S3Client:
        deleted = []
        copy_parameters = None

        def copy_object(self, **kwargs):
            self.copy_parameters = kwargs
            return {"VersionId": "v2"}

        def head_object(self, **kwargs):
            return {"VersionId": "v2", "StorageClass": "GLACIER"}

        def get_paginator(self, name):
            assert name == "list_object_versions"
            return Paginator()

        def delete_objects(self, **kwargs):
            self.deleted.extend(kwargs["Delete"]["Objects"])

    client = S3Client()
    monkeypatch.setattr(archive, "_boto_client", lambda: client)
    with override_settings(
        SITEHITS_ARCHIVE_BUCKET="private-archive",
        SITEHITS_ARCHIVE_KMS_KEY_ID="kms-key",
        DEBUG=False,
    ):
        archive.transition_partition_to_glacier(partition)

    partition.refresh_from_db()
    assert partition.storage_class == ArchivePartition.StorageClass.GLACIER
    assert partition.object_version == "v2"
    assert partition.transitioned_at is not None
    assert client.copy_parameters["CopySource"]["VersionId"] == "v1"
    assert client.copy_parameters["ServerSideEncryption"] == "aws:kms"
    assert client.deleted == [{"Key": partition.object_key, "VersionId": "v1"}]


@pytest.mark.django_db
def test_actor_deletion_waits_for_glacier_restore_without_releasing_tombstone(
    tracked_site, monkeypatch
):
    now = timezone.now()
    tombstone = ColdDataTombstone.objects.create(
        site=tracked_site,
        site_id_snapshot=tracked_site.pk,
        kind=ColdDataTombstone.Kind.ACTOR,
        actor_hash="actor",
    )
    job = ColdDeletionJob.objects.create(
        site=tracked_site,
        site_id_snapshot=tracked_site.pk,
        kind=ColdDeletionJob.Kind.ACTOR,
        actor_hash="actor",
    )
    ArchivePartition.objects.create(
        site=tracked_site,
        site_id_snapshot=tracked_site.pk,
        stream=ArchivePartition.Stream.ANALYTICS,
        range_start=now - timedelta(days=150),
        range_end=now - timedelta(days=120),
        timezone="UTC",
        object_key="archive/glacier.parquet",
        object_version="v1",
        storage_class=ArchivePartition.StorageClass.GLACIER,
        status=ArchivePartition.Status.SOURCE_DELETED,
    )
    monkeypatch.setattr(archive, "_glacier_partition_ready", lambda partition: False)
    monkeypatch.setattr(
        archive,
        "_rewrite_actor_partition",
        lambda partition, actor_hash: pytest.fail("must not rewrite before restore"),
    )

    processed = archive.process_cold_deletion(job)

    processed.refresh_from_db()
    tombstone.refresh_from_db()
    assert processed.status == ColdDeletionJob.Status.WAITING_RESTORE
    assert processed.completed_at is None
    assert tombstone.status == ColdDataTombstone.Status.PENDING


@pytest.mark.skipif(
    not os.environ.get("SITEHITS_TEST_MINIO_ENDPOINT"),
    reason="Set SITEHITS_TEST_MINIO_ENDPOINT to run the MinIO archive integration test.",
)
@pytest.mark.django_db(transaction=True)
def test_minio_export_verifies_versioned_parquet(tmp_path, monkeypatch):
    endpoint = os.environ["SITEHITS_TEST_MINIO_ENDPOINT"]
    bucket = os.environ.get("SITEHITS_TEST_MINIO_BUCKET", "sitehits-archive-test")
    prefix = f"pytest/{uuid4()}"
    site = TrackedSite.objects.create(
        name="MinIO",
        slug=f"minio-{uuid4().hex[:8]}",
        allowed_domains=["minio.example"],
        timezone="UTC",
    )
    now = timezone.now().replace(microsecond=0)
    source_path = tmp_path / "minio-source.sqlite3"
    _mirror_database(source_path, site.pk, now)
    monkeypatch.setattr(archive, "_database_attachment", lambda: ("sqlite", str(source_path)))
    configuration = {
        "SITEHITS_ARCHIVE_ENABLED": True,
        "SITEHITS_ARCHIVE_DELETE_SOURCE": False,
        "SITEHITS_ARCHIVE_QUERY_ENABLED": False,
        "SITEHITS_ARCHIVE_BUCKET": bucket,
        "SITEHITS_ARCHIVE_PREFIX": prefix,
        "SITEHITS_ARCHIVE_REGION": os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        "SITEHITS_ARCHIVE_ENDPOINT": endpoint,
        "SITEHITS_ARCHIVE_KMS_KEY_ID": "",
        "DEBUG": True,
    }
    with override_settings(**configuration):
        client = archive._boto_client()
        try:
            client.head_bucket(Bucket=bucket)
        except ClientError:
            client.create_bucket(Bucket=bucket)
        client.put_bucket_versioning(
            Bucket=bucket,
            VersioningConfiguration={"Status": "Enabled"},
        )
        partition = ArchivePartition.objects.create(
            site=site,
            site_id_snapshot=site.pk,
            stream=ArchivePartition.Stream.ANALYTICS,
            range_start=now - timedelta(days=401),
            range_end=now - timedelta(days=90),
            timezone=site.timezone,
        )
        try:
            archive.export_partition(partition)
            partition.refresh_from_db()
            objects = client.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", [])
            head = client.head_object(Bucket=bucket, Key=partition.object_key)
            assert partition.status == ArchivePartition.Status.VERIFIED
            assert partition.object_version
            assert partition.row_count > 0
            assert partition.verification_sha256
            assert len(objects) == 1
            assert head["ContentLength"] > 0
        finally:
            if partition.object_key:
                archive._delete_all_object_versions(partition.object_key)
