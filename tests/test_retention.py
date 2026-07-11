from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from analytics.models import AnalyticsEvent


@pytest.mark.django_db
def test_retention_command_deletes_only_expired_events(tracked_site):
    common = {
        "site": tracked_site,
        "event_type": "pageview",
        "visitor_hash": "visitor",
        "session_id": "session",
        "path": "/",
    }
    AnalyticsEvent.objects.create(
        **common, occurred_at=timezone.now() - timedelta(days=366)
    )
    AnalyticsEvent.objects.create(**common, occurred_at=timezone.now() - timedelta(days=10))
    call_command("purge_old_events", days=365)
    assert AnalyticsEvent.objects.count() == 1

