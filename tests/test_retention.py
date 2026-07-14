from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from analytics.models import AnalyticsEvent, BotEvent


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
    bot_common = {
        "site": tracked_site,
        "provider": "OpenAI",
        "crawler": "GPTBot",
        "category": "training",
        "path": "/",
    }
    BotEvent.objects.create(
        **bot_common,
        occurred_at=timezone.now() - timedelta(days=366),
    )
    BotEvent.objects.create(
        **bot_common,
        occurred_at=timezone.now() - timedelta(days=10),
    )
    call_command("purge_old_events", days=365)
    assert AnalyticsEvent.objects.count() == 1
    assert BotEvent.objects.count() == 1
