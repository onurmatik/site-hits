"""Fail when the persistent Stage 1 cleanup schedule is unhealthy."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from mcp_gateway.management.commands.cleanup_mcp_oauth import (
    CLEANUP_RETENTION_DAYS_BY_TARGET,
)
from mcp_oauth.models import OAuthCleanupRun

MAX_SUCCESS_AGE = timedelta(hours=36)
MAX_ELIGIBLE_LAG = timedelta(hours=36)


def _parse_utc(value):
    if not isinstance(value, str):
        raise TypeError("cleanup eligibility timestamp must be a string")
    parsed = datetime.fromisoformat(value)
    if not timezone.is_aware(parsed):
        raise ValueError("cleanup eligibility timestamp must be timezone-aware")
    return parsed


def _eligible_lags_by_type(run):
    if not isinstance(run.details, dict):
        return None
    evidence = run.details.get("eligibility_by_type")
    if not isinstance(evidence, dict) or set(evidence) != set(CLEANUP_RETENTION_DAYS_BY_TARGET):
        return None
    if run.finished_at is None:
        return None

    lags = {}
    try:
        for target, retention_days in CLEANUP_RETENTION_DAYS_BY_TARGET.items():
            item = evidence[target]
            if not isinstance(item, dict) or item.get("retention_days") != retention_days:
                return None
            cutoff = _parse_utc(item.get("cutoff"))
            if cutoff != run.started_at - timedelta(days=retention_days):
                return None
            oldest_source_at = item.get("oldest_source_at")
            eligible_since = item.get("eligible_since")
            if oldest_source_at is None or eligible_since is None:
                if oldest_source_at is not None or eligible_since is not None:
                    return None
                expected_lag = 0
            else:
                oldest_source = _parse_utc(oldest_source_at)
                eligible_at = _parse_utc(eligible_since)
                if eligible_at != oldest_source + timedelta(days=retention_days):
                    return None
                expected_lag = max(
                    0,
                    int((run.finished_at - eligible_at).total_seconds()),
                )
            if item.get("lag_seconds") != expected_lag:
                return None
            lags[target] = expected_lag
    except (TypeError, ValueError):
        return None
    return lags


def _eligible_lag(run):
    lags = _eligible_lags_by_type(run)
    if lags is None:
        return None
    return timedelta(seconds=max(lags.values(), default=0))


def cleanup_health(now=None):
    now = now or timezone.now()
    runs = list(OAuthCleanupRun.objects.order_by("-started_at")[:2])
    reasons: list[str] = []
    latest = runs[0] if runs else None
    latest_success = (
        OAuthCleanupRun.objects.filter(status=OAuthCleanupRun.Status.SUCCEEDED)
        .order_by("-finished_at")
        .first()
    )
    if latest_success is None or latest_success.finished_at is None:
        reasons.append("cleanup_has_no_success")
    elif now - latest_success.finished_at > MAX_SUCCESS_AGE:
        reasons.append("cleanup_success_is_stale")
    latest_lags = _eligible_lags_by_type(latest_success) if latest_success is not None else None
    if latest_success is not None and latest_lags is None:
        reasons.append("cleanup_eligibility_evidence_invalid")
    if latest is not None and (latest.status == OAuthCleanupRun.Status.FAILED or latest.errors > 0):
        reasons.append("latest_cleanup_failed")
    consecutive_lagged = len(runs) == 2 and all(
        run.status == OAuthCleanupRun.Status.SUCCEEDED
        and (lag := _eligible_lag(run)) is not None
        and lag > MAX_ELIGIBLE_LAG
        for run in runs
    )
    if consecutive_lagged:
        reasons.append("cleanup_lag_repeated")
    return {
        "healthy": not reasons,
        "checked_at": now.isoformat().replace("+00:00", "Z"),
        "reasons": reasons,
        "latest_run_id": str(latest.job_id) if latest else None,
        "latest_success_at": (
            latest_success.finished_at.isoformat().replace("+00:00", "Z")
            if latest_success is not None and latest_success.finished_at is not None
            else None
        ),
        "consecutive_lagged_runs": consecutive_lagged,
        "latest_eligible_lag_seconds_by_type": latest_lags,
    }


class Command(BaseCommand):
    help = "Check Stage 1 OAuth cleanup success, errors, and repeated lag."

    def handle(self, *args, **options):
        payload = cleanup_health()
        rendered = json.dumps(payload, sort_keys=True)
        if not payload["healthy"]:
            self.stderr.write(rendered)
            raise CommandError("MCP/OAuth cleanup health check failed")
        self.stdout.write(rendered)
