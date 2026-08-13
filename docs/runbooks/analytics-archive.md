# Analytics archive runbook

## Scope and safety invariants

This subsystem moves complete site-local calendar months from PostgreSQL to ZSTD Parquet while
preserving exact historical reporting. PostgreSQL raw rows are never removed until the exported
object has been read back and its schema, row count, ID/time bounds, and deterministic fingerprint
match the source. A failed or partially exported manifest is retained for diagnosis and cannot
authorize deletion.

The default windows are:

- raw PostgreSQL: at least 90 days; complete-month eligibility makes the effective window 90–121
  days;
- directly queryable S3 Parquet: two years by `ArchivePartition.range_end`;
- Glacier: the third year;
- permanent deletion: three years by `ArchivePartition.range_end`, including all versions and
  delete markers.

S3 object age is not the retention authority. The application manifest's event-time range is. S3
Lifecycle is a secondary safety net only.

## Required S3 controls

Use a dedicated private bucket with Block Public Access, versioning, and default SSE-KMS enabled.
Do not enable Object Lock: actor and site erasure requires permanent version deletion. Restrict the
runtime role to the configured `SITEHITS_ARCHIVE_PREFIX` and grant only:

- `s3:ListBucket` and `s3:ListBucketVersions` with a prefix condition;
- `s3:GetObject`, `s3:GetObjectVersion`, `s3:PutObject`, `s3:DeleteObject`,
  `s3:DeleteObjectVersion`, and `s3:RestoreObject` on that prefix;
- `kms:Encrypt`, `kms:Decrypt`, `kms:GenerateDataKey`, and `kms:DescribeKey` for the archive key.

The production application rejects exported objects without SSE-KMS or a version ID. A Glacier
transition is a same-key copy to a new version; SiteHits verifies the new storage class before
permanently removing superseded versions.

Configure Lifecycle only as a delayed backstop for expiration/noncurrent-version cleanup. Do not
configure an independent age-based Glacier transition that can run earlier than the manifest
boundary, because actor erasure needs the application to know whether a restore is required.

## Configuration

The relevant environment variables are documented in `.env.example`. Production requires a
bucket, region, and KMS key when archiving is enabled. These invariants are checked at startup:

- `SITEHITS_ARCHIVE_HOT_DAYS >= 90`;
- `SITEHITS_ARCHIVE_QUERYABLE_DAYS >= 730`;
- retention is not shorter than the queryable window;
- `SITEHITS_HISTORICAL_QUERY_CONCURRENCY=1`;
- query and source-delete flags require archive export to be enabled.

DuckDB's `httpfs`, `postgres`, and `sqlite` extensions are installed into the image at build time.
Production containers do not download extensions at runtime. Every worker uses an in-memory
DuckDB connection; no shared `.duckdb` file exists.

## Rollout

Apply migration `analytics.0007_archive_rollups_and_cold_deletions`, then progress through these
gates. Keep each gate in production long enough to compare representative reports, including DST
sites and a hot/cold boundary.

1. Shadow export: set `SITEHITS_ARCHIVE_ENABLED=true`, keep query and delete flags false, and run
   `python manage.py maintain_event_archive --limit 12` daily.
2. Verification: compare overview, timeseries, every breakdown, bot, and product reports against
   PostgreSQL raw data. Investigate every `failed` manifest; do not manually change it to
   `verified`.
3. Historical reads: set `SITEHITS_ARCHIVE_QUERY_ENABLED=true`, run
   `python manage.py refresh_historical_reports`, then enable the hourly cache timer.
4. Source deletion: only after comparison is green, set
   `SITEHITS_ARCHIVE_DELETE_SOURCE=true`. The next daily maintenance run batch-deletes source rows
   for verified manifests.

Agent Contract 2.0.0 is sealed by the immutable `agent-contract-v2.0.0` annotated tag.
`SITEHITS_ALLOW_UNRELEASED_AGENT_CONTRACT=true` remains development-only; production verifies the
materialized release descriptor and pinned digests at startup.

## Scheduled operations

Install and enable:

- `deploy/systemd/sitehits-archive-maintenance.timer` daily. It compacts eligible months, retries
  cold erasure jobs, rebuilds invalidated rollups from queryable Parquet after site timezone
  changes, transitions third-year objects to Glacier, and removes expired manifests and every S3
  version;
- `deploy/systemd/sitehits-historical-cache.timer` hourly. It refreshes standard six-month and
  one-year overview, timeseries, breakdown, bot, and product reports;
- the existing cleanup timer daily. `purge_old_events` now cleans only audit and idempotency
  metadata and never removes analytics or bot events.

Alert on nonzero compaction/transition failures, repeated `historical_data_unavailable`, cache age
over one hour, a growing `failed` manifest count, and cold-deletion jobs that remain incomplete.
S3 failure must not affect reports whose full current/previous ranges remain inside PostgreSQL.

## Historical query behavior

Long-range queries have a 15-second synchronous budget and a one-hour cache target. A cache miss
may run one DuckDB cold query per worker. On timeout or S3 failure, SiteHits returns the last
successful cache only when its archive/tombstone generation still matches, marked
`is_stale=true`. With no safe cache it returns `historical_data_unavailable`; it never turns an
outage into zeros.

Raw rows and arbitrary user SQL are not exposed. DuckDB feeds only the existing overview,
timeseries, breakdown, bot, and product report shapes.

## Actor and site erasure

`forget-actor` deletes hot events and creates the tombstone/job atomically. The tombstone is applied
to every cold query immediately and invalidates historical caches. The daily worker rewrites each
affected Parquet partition, verifies the replacement, and permanently deletes all versions of the
old object.

If an affected object is in Glacier, the job moves to `waiting_restore`; its tombstone remains
active while S3 restores the current version. Later maintenance runs resume the rewrite and return
third-year replacement objects to Glacier. A site deletion needs no restore: it permanently deletes
all known versions directly and then removes its manifests. Query a forget request at the status
endpoint using the returned `request_id`.

## Validation and incident checks

Run the local acceptance suite:

```bash
uv run pytest tests/test_archive.py
```

The 400-day fixture covers site/all-site, DST, the hot/cold boundary, overview, daily timeseries,
breakdowns, bots, product metrics, cache generation, tombstones, Parquet type round-trips, and
Glacier state transitions.

For the real S3-compatible integration test, start a versioned MinIO bucket and set
`SITEHITS_TEST_MINIO_ENDPOINT`, optional `SITEHITS_TEST_MINIO_BUCKET`, AWS credentials, and region,
then run the same suite. The test performs an actual DuckDB export/readback and removes all test
versions afterward.

During an incident, inspect `ArchivePartition.status`, `error_message`, object version, fingerprint,
and row bounds before taking action. Never delete source rows or mark a manifest verified by hand.
