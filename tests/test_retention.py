from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from agent_runtime import RequestContext, SiteHitsService
from analytics.models import AgentAuditEvent, AgentIdempotencyRecord, AnalyticsEvent, BotEvent


@pytest.mark.django_db
def test_retention_command_preserves_events_for_verified_archive_cleanup(tracked_site):
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
    user = tracked_site.owner or get_user_model().objects.create_user("retention-owner")
    tracked_site.owner = user
    tracked_site.save(update_fields=["owner"])
    service = SiteHitsService(
        RequestContext(
            authenticated_actor_id=str(user.pk),
            authenticated_client_id="retention-client",
            granted_scopes=frozenset({"read", "write"}),
        )
    )
    service.create_site(
        name="Retention",
        allowed_domains=["retention.example"],
        idempotency_key="retention-site-key",
    )
    AgentAuditEvent.objects.filter(tool_name="create_site").update(
        created_at=timezone.now() - timedelta(days=91)
    )
    AgentIdempotencyRecord.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    service.list_sites()
    call_command("purge_old_events", days=365)
    assert AnalyticsEvent.objects.count() == 2
    assert BotEvent.objects.count() == 2
    assert AgentAuditEvent.objects.count() == 1
    assert AgentIdempotencyRecord.objects.count() == 0
