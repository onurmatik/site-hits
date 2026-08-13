"""Delete terminal MCP/OAuth metadata after the accepted retention window."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Exists, Min, OuterRef, Q, QuerySet
from django.utils import timezone

from mcp_gateway.models import (
    MCPAccessToken,
    MCPOAuthAccessToken,
    MCPOAuthAuthorizationCode,
    MCPOAuthAuthorizationRequest,
    MCPOAuthClient,
    MCPOAuthRefreshToken,
)
from mcp_oauth.models import (
    OAuthAccessToken,
    OAuthApplication,
    OAuthCleanupRun,
    OAuthConsent,
    OAuthGrant,
    OAuthRateLimitBucket,
    OAuthRefreshFamily,
    OAuthRefreshToken,
    OAuthSecurityEvent,
)

CREDENTIAL_METADATA_RETENTION_DAYS = 30
AUDIT_METADATA_RETENTION_DAYS = 90
DEFAULT_BATCH_SIZE = 500
DEFAULT_MAX_BATCHES = 20
MAX_BATCH_SIZE = 5_000
MAX_BATCHES = 1_000

CLEANUP_RETENTION_DAYS_BY_TARGET = {
    "stale_dcr_applications": CREDENTIAL_METADATA_RETENTION_DAYS,
    "oauth_grants": CREDENTIAL_METADATA_RETENTION_DAYS,
    "oauth_access_tokens": CREDENTIAL_METADATA_RETENTION_DAYS,
    "oauth_refresh_tokens": CREDENTIAL_METADATA_RETENTION_DAYS,
    "oauth_refresh_families": CREDENTIAL_METADATA_RETENTION_DAYS,
    "oauth_consents": CREDENTIAL_METADATA_RETENTION_DAYS,
    "oauth_security_events": AUDIT_METADATA_RETENTION_DAYS,
    "oauth_cleanup_runs": AUDIT_METADATA_RETENTION_DAYS,
    "oauth_rate_limit_buckets": CREDENTIAL_METADATA_RETENTION_DAYS,
    "legacy_authorization_requests": CREDENTIAL_METADATA_RETENTION_DAYS,
    "legacy_authorization_codes": CREDENTIAL_METADATA_RETENTION_DAYS,
    "legacy_oauth_access_tokens": CREDENTIAL_METADATA_RETENTION_DAYS,
    "legacy_oauth_refresh_tokens": CREDENTIAL_METADATA_RETENTION_DAYS,
    "legacy_access_tokens": CREDENTIAL_METADATA_RETENTION_DAYS,
    "legacy_stale_dcr_clients": CREDENTIAL_METADATA_RETENTION_DAYS,
}


@dataclass(frozen=True)
class CleanupTarget:
    name: str
    queryset: QuerySet
    eligibility_fields: tuple[str, ...]
    retention_days: int
    cutoff: datetime


def _terminal_filter(fields: tuple[str, ...], cutoff) -> Q:
    query = Q()
    for field in fields:
        query |= Q(**{f"{field}__lte": cutoff})
    return query


def _target(name, queryset, fields, *, now):
    retention_days = CLEANUP_RETENTION_DAYS_BY_TARGET[name]
    return CleanupTarget(
        name=name,
        queryset=queryset,
        eligibility_fields=fields,
        retention_days=retention_days,
        cutoff=now - timedelta(days=retention_days),
    )


def _credential_target(name, model, fields, *, now):
    cutoff = now - timedelta(days=CLEANUP_RETENTION_DAYS_BY_TARGET[name])
    return _target(
        name,
        # Every predicate targets columns on this model; no join can multiply
        # rows. Keeping this queryset non-DISTINCT is required because
        # PostgreSQL forbids SELECT ... FOR UPDATE on a DISTINCT query.
        model.objects.filter(_terminal_filter(fields, cutoff)),
        fields,
        now=now,
    )


def _stale_application_target(now):
    name = "stale_dcr_applications"
    cutoff = now - timedelta(days=CLEANUP_RETENTION_DAYS_BY_TARGET[name])
    grants = OAuthGrant.objects.filter(application_id=OuterRef("pk"))
    access_tokens = OAuthAccessToken.objects.filter(application_id=OuterRef("pk"))
    refresh_tokens = OAuthRefreshToken.objects.filter(application_id=OuterRef("pk"))
    queryset = OAuthApplication.objects.annotate(
        _has_grant=Exists(grants),
        _has_access_token=Exists(access_tokens),
        _has_refresh_token=Exists(refresh_tokens),
    ).filter(
        registration_source=OAuthApplication.RegistrationSource.DCR,
        created__lte=cutoff,
        last_used_at__isnull=True,
        _has_grant=False,
        _has_access_token=False,
        _has_refresh_token=False,
    )
    return _target(
        name,
        queryset,
        ("created", "last_used_at"),
        now=now,
    )


def _consent_target(now):
    name = "oauth_consents"
    cutoff = now - timedelta(days=CLEANUP_RETENTION_DAYS_BY_TARGET[name])
    queryset = OAuthConsent.objects.filter(
        Q(revoked_at__lte=cutoff) | Q(decision=OAuthConsent.Decision.DENIED, created_at__lte=cutoff)
    )
    return _target(
        name,
        queryset,
        ("revoked_at", "created_at"),
        now=now,
    )


def _legacy_stale_client_target(now):
    name = "legacy_stale_dcr_clients"
    cutoff = now - timedelta(days=CLEANUP_RETENTION_DAYS_BY_TARGET[name])
    authorization_requests = MCPOAuthAuthorizationRequest.objects.filter(
        client_id=OuterRef("pk"),
        created_at__gt=cutoff,
    )
    authorization_codes = MCPOAuthAuthorizationCode.objects.filter(client_id=OuterRef("pk"))
    access_tokens = MCPOAuthAccessToken.objects.filter(client_id=OuterRef("pk"))
    refresh_tokens = MCPOAuthRefreshToken.objects.filter(client_id=OuterRef("pk"))
    queryset = MCPOAuthClient.objects.annotate(
        _recent_authorization_request=Exists(authorization_requests),
        _has_authorization_code=Exists(authorization_codes),
        _has_access_token=Exists(access_tokens),
        _has_refresh_token=Exists(refresh_tokens),
    ).filter(
        created_at__lte=cutoff,
        _recent_authorization_request=False,
        _has_authorization_code=False,
        _has_access_token=False,
        _has_refresh_token=False,
    )
    return _target(name, queryset, ("created_at",), now=now)


def cleanup_targets(now=None):
    now = now or timezone.now()
    targets = [
        # Evaluate never-authorized DCR applications before deleting terminal
        # grant/token metadata so a credential-issuing client is not mistaken
        # for a never-used registration within the same run.
        _stale_application_target(now),
        _credential_target(
            "oauth_grants",
            OAuthGrant,
            ("expires", "consumed_at", "replayed_at"),
            now=now,
        ),
        _credential_target(
            "oauth_access_tokens",
            OAuthAccessToken,
            ("expires", "revoked_at"),
            now=now,
        ),
        _credential_target(
            "oauth_refresh_tokens",
            OAuthRefreshToken,
            ("family_expires_at", "revoked", "family_revoked_at"),
            now=now,
        ),
        _credential_target(
            "oauth_refresh_families",
            OAuthRefreshFamily,
            ("expires_at", "revoked_at"),
            now=now,
        ),
        _consent_target(now),
        _credential_target(
            "oauth_security_events",
            OAuthSecurityEvent,
            ("created_at",),
            now=now,
        ),
        _credential_target(
            "oauth_cleanup_runs",
            OAuthCleanupRun,
            ("started_at",),
            now=now,
        ),
        _credential_target(
            "oauth_rate_limit_buckets",
            OAuthRateLimitBucket,
            ("window_ends_at",),
            now=now,
        ),
        _credential_target(
            "legacy_authorization_requests",
            MCPOAuthAuthorizationRequest,
            ("expires_at", "resolved_at"),
            now=now,
        ),
        _credential_target(
            "legacy_authorization_codes",
            MCPOAuthAuthorizationCode,
            ("expires_at", "consumed_at"),
            now=now,
        ),
        _credential_target(
            "legacy_oauth_access_tokens",
            MCPOAuthAccessToken,
            ("expires_at", "revoked_at"),
            now=now,
        ),
        _credential_target(
            "legacy_oauth_refresh_tokens",
            MCPOAuthRefreshToken,
            ("expires_at", "used_at", "revoked_at"),
            now=now,
        ),
        _credential_target(
            "legacy_access_tokens",
            MCPAccessToken,
            ("expires_at", "revoked_at"),
            now=now,
        ),
        _legacy_stale_client_target(now),
    ]
    if {target.name for target in targets} != set(CLEANUP_RETENTION_DAYS_BY_TARGET):
        raise RuntimeError("Cleanup target retention registry drifted from runtime targets.")
    return targets


def _start_cleanup_run(*, now, batch_size, max_batches, dry_run, targets):
    if dry_run:
        return None
    return OAuthCleanupRun.objects.create(
        status=OAuthCleanupRun.Status.RUNNING,
        started_at=now,
        details={
            "operator": "management_command",
            "scheduler": "external_operations",
            "batch_size": batch_size,
            "max_batches": max_batches,
            "retention_days_by_type": {target.name: target.retention_days for target in targets},
        },
    )


def _finish_cleanup_run(
    run,
    *,
    status,
    deleted,
    errors,
    duration_seconds,
    finished_at,
    oldest_eligible_at,
    details,
):
    if run is None:
        return
    run.status = status
    run.finished_at = finished_at
    run.deleted = deleted
    run.errors = errors
    run.duration_ms = max(0, round(duration_seconds * 1_000))
    run.oldest_eligible_at = oldest_eligible_at
    run.details = {**run.details, **details}
    run.save(
        update_fields=[
            "status",
            "finished_at",
            "deleted",
            "errors",
            "duration_ms",
            "oldest_eligible_at",
            "details",
        ]
    )


def _oldest_eligible_source(target: CleanupTarget):
    timestamps = []
    for field in target.eligibility_fields:
        result = target.queryset.filter(**{f"{field}__lte": target.cutoff}).aggregate(
            oldest=Min(field)
        )["oldest"]
        if result is not None:
            timestamps.append(result)
    return min(timestamps, default=None)


def _eligibility_evidence(*, targets, oldest_sources, observed_at):
    evidence = {}
    eligible_since_values = []
    for target in targets:
        oldest_source = oldest_sources.get(target.name)
        eligible_since = (
            oldest_source + timedelta(days=target.retention_days)
            if oldest_source is not None
            else None
        )
        if eligible_since is not None:
            eligible_since_values.append(eligible_since)
        evidence[target.name] = {
            "retention_days": target.retention_days,
            "cutoff": _utc(target.cutoff),
            "oldest_source_at": _utc(oldest_source) if oldest_source is not None else None,
            "eligible_since": _utc(eligible_since) if eligible_since is not None else None,
            "lag_seconds": (
                max(0, int((observed_at - eligible_since).total_seconds()))
                if eligible_since is not None
                else 0
            ),
        }
    return min(eligible_since_values, default=None), evidence


def _delete_bounded(target: CleanupTarget, *, batch_size: int, max_batches: int):
    deleted = 0
    batches = 0
    for _ in range(max_batches):
        with transaction.atomic():
            candidates = target.queryset.order_by("pk")
            if connection.features.has_select_for_update:
                candidates = candidates.select_for_update(
                    skip_locked=connection.features.has_select_for_update_skip_locked
                )
            primary_keys = list(candidates.values_list("pk", flat=True)[:batch_size])
            if not primary_keys:
                break
            _, detail = target.queryset.model.objects.filter(pk__in=primary_keys).delete()
            deleted += detail.get(target.queryset.model._meta.label, 0)
            batches += 1
    return deleted, batches, target.queryset.exists()


def _utc(value):
    return value.isoformat().replace("+00:00", "Z")


class Command(BaseCommand):
    help = (
        "Delete expired or terminal MCP/OAuth metadata and stale DCR clients "
        "after their target-specific fixed Stage 1 retention windows."
    )

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
        parser.add_argument("--max-batches", type=int, default=DEFAULT_MAX_BATCHES)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        max_batches = options["max_batches"]
        dry_run = options["dry_run"]
        if not 1 <= batch_size <= MAX_BATCH_SIZE:
            raise CommandError(f"batch-size must be between 1 and {MAX_BATCH_SIZE}")
        if not 1 <= max_batches <= MAX_BATCHES:
            raise CommandError(f"max-batches must be between 1 and {MAX_BATCHES}")

        started = time.monotonic()
        now = timezone.now()
        targets = cleanup_targets(now)
        cleanup_run = _start_cleanup_run(
            now=now,
            batch_size=batch_size,
            max_batches=max_batches,
            dry_run=dry_run,
            targets=targets,
        )
        oldest_sources = {}
        oldest_eligible_at = None
        eligibility_by_type = {}
        try:
            oldest_sources = {target.name: _oldest_eligible_source(target) for target in targets}
            deleted_by_type = {}
            batches_by_type = {}
            truncated = []
            eligible_by_type = {}
            for target in targets:
                eligible_by_type[target.name] = target.queryset.count()
                if dry_run:
                    deleted_by_type[target.name] = 0
                    batches_by_type[target.name] = 0
                    continue
                deleted, batches, has_more = _delete_bounded(
                    target,
                    batch_size=batch_size,
                    max_batches=max_batches,
                )
                deleted_by_type[target.name] = deleted
                batches_by_type[target.name] = batches
                if has_more:
                    truncated.append(target.name)
        except Exception as exc:
            duration = time.monotonic() - started
            finished_at = timezone.now()
            oldest_eligible_at, eligibility_by_type = _eligibility_evidence(
                targets=targets,
                oldest_sources=oldest_sources,
                observed_at=finished_at,
            )
            _finish_cleanup_run(
                cleanup_run,
                status=OAuthCleanupRun.Status.FAILED,
                deleted={},
                errors=1,
                duration_seconds=duration,
                finished_at=finished_at,
                oldest_eligible_at=oldest_eligible_at,
                details={
                    "error_code": "cleanup_failed",
                    "eligibility_by_type": eligibility_by_type,
                },
            )
            failure = {
                "job": "cleanup_mcp_oauth",
                "runs": 1,
                "deleted": 0,
                "errors": 1,
                "duration_seconds": round(duration, 6),
                "last_success_at": None,
                "error_code": "cleanup_failed",
            }
            self.stderr.write(json.dumps(failure, sort_keys=True))
            raise CommandError("MCP/OAuth cleanup failed") from exc

        duration = time.monotonic() - started
        finished_at = timezone.now()
        oldest_eligible_at, eligibility_by_type = _eligibility_evidence(
            targets=targets,
            oldest_sources=oldest_sources,
            observed_at=finished_at,
        )
        _finish_cleanup_run(
            cleanup_run,
            status=OAuthCleanupRun.Status.SUCCEEDED,
            deleted=deleted_by_type,
            errors=0,
            duration_seconds=duration,
            finished_at=finished_at,
            oldest_eligible_at=oldest_eligible_at,
            details={
                "dry_run": dry_run,
                "eligible_by_type": eligible_by_type,
                "batches_by_type": batches_by_type,
                "truncated": sorted(truncated),
                "eligibility_by_type": eligibility_by_type,
            },
        )
        payload = {
            "job": "cleanup_mcp_oauth",
            "runs": 1,
            "dry_run": dry_run,
            "retention_days_by_type": {target.name: target.retention_days for target in targets},
            "cutoff_by_type": {target.name: _utc(target.cutoff) for target in targets},
            "eligible": sum(eligible_by_type.values()),
            "eligible_by_type": eligible_by_type,
            "deleted": sum(deleted_by_type.values()),
            "deleted_by_type": deleted_by_type,
            "batches_by_type": batches_by_type,
            "errors": 0,
            "duration_seconds": round(duration, 6),
            "oldest_eligible_at": (
                _utc(oldest_eligible_at) if oldest_eligible_at is not None else None
            ),
            "oldest_eligible_age_seconds": max(
                (item["lag_seconds"] for item in eligibility_by_type.values()),
                default=0,
            ),
            "eligibility_by_type": eligibility_by_type,
            "last_success_at": _utc(finished_at),
            "cleanup_run_id": str(cleanup_run.job_id) if cleanup_run else None,
            "truncated": sorted(truncated),
        }
        self.stdout.write(json.dumps(payload, sort_keys=True))
