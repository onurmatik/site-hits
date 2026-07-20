import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from analytics.models import ActivationDefinition, AnalyticsEvent, ProductEventDefinition
from analytics.product_ingestion import actor_hash_for_site, actor_token_for_site
from analytics.product_reporting import product_metrics
from analytics.reporting import overview
from websites.models import TrackedSite


def browser_payload(site, **overrides):
    payload = {
        "site_key": site.public_key,
        "event_type": "custom",
        "event_name": "pricing_viewed",
        "session_id": "session-12345678",
        "url": "https://example.com/pricing",
        "referrer": "",
        "timestamp": timezone.now().isoformat(),
        "language": "en-US",
        "timezone": "Europe/Istanbul",
        "viewport": {"width": 1280, "height": 720},
        "screen": {"width": 1440, "height": 900},
        "properties": {},
    }
    payload.update(overrides)
    return payload


def server_event(client, site, **overrides):
    payload = {
        "event_id": "event-logical-id-1",
        "event_name": "purchase",
        "actor_id": "user-123",
        "timestamp": timezone.now().isoformat(),
        "value": "1499.50",
        "unit": "TRY",
        "path": "/checkout/success",
        "properties": {"plan": "pro"},
    }
    payload.update(overrides)
    return client.post(
        "/api/server-events",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {site.server_event_key}",
    )


@pytest.mark.django_db
def test_server_event_is_private_hashed_idempotent_and_site_scoped(client, tracked_site):
    ProductEventDefinition.objects.create(
        site=tracked_site,
        event_name="purchase",
        display_name="Revenue",
        description="Fire after confirmed payment.",
        aggregation="sum",
        unit="TRY",
    )

    response = server_event(client, tracked_site)

    assert response.status_code == 202
    assert response.json() == {"accepted": True, "duplicate": False}
    event = AnalyticsEvent.objects.get()
    assert event.source == AnalyticsEvent.Source.SERVER
    assert event.actor_hash == actor_hash_for_site(tracked_site, "user-123")
    assert event.metric_value == Decimal("1499.500000")
    assert event.metric_unit == "TRY"
    assert event.path == "/checkout/success"
    assert "user-123" not in repr(event.__dict__)
    assert "event-logical-id-1" not in repr(event.__dict__)

    duplicate = server_event(
        client,
        tracked_site,
        value=None,
        unit="",
        timestamp=(timezone.now() - timedelta(days=8)).isoformat(),
    )
    assert duplicate.status_code == 202
    assert duplicate.json() == {"accepted": True, "duplicate": True}
    assert AnalyticsEvent.objects.count() == 1
    assert overview(tracked_site.slug, "last7d")["current"] == {
        "visitors": 0,
        "sessions": 0,
        "pageviews": 0,
        "bounce_rate": 0,
        "avg_session_duration": 0,
    }

    tracked_site.refresh_from_db()
    assert tracked_site.server_event_collector_last_seen_at is not None
    assert tracked_site.server_event_collector_last_event_at is not None

    other = TrackedSite.objects.create(
        name="Other",
        slug="other-product",
        allowed_domains=["other.example"],
    )
    assert actor_hash_for_site(other, "user-123") != event.actor_hash


@pytest.mark.django_db
def test_server_event_validates_auth_metric_contract_actor_and_timestamp(client, tracked_site):
    purchase = ProductEventDefinition.objects.create(
        site=tracked_site,
        event_name="purchase",
        display_name="Revenue",
        description="Confirmed revenue.",
        aggregation="sum",
        unit="TRY",
    )
    unique = ProductEventDefinition.objects.create(
        site=tracked_site,
        event_name="tos_accepted",
        display_name="TOS accepted",
        description="Persisted TOS acceptance.",
        aggregation="unique_actors",
    )

    unauthorized = client.post(
        "/api/server-events",
        data=json.dumps({"event_id": "1", "event_name": "purchase"}),
        content_type="application/json",
    )
    assert unauthorized.status_code == 401
    assert server_event(client, tracked_site, value=None, unit="").status_code == 400
    assert server_event(client, tracked_site, unit="USD").status_code == 400
    assert server_event(
        client,
        tracked_site,
        event_id="tos-1",
        event_name=unique.event_name,
        actor_id="",
        value=None,
        unit="",
    ).status_code == 400
    assert server_event(
        client,
        tracked_site,
        timestamp=(timezone.now() - timedelta(days=8)).isoformat(),
    ).status_code == 400
    assert AnalyticsEvent.objects.count() == 0
    assert purchase.site == tracked_site


@pytest.mark.django_db
def test_forget_actor_deletes_only_that_sites_linked_events(client, tracked_site):
    actor_hash = actor_hash_for_site(tracked_site, "user-123")
    AnalyticsEvent.objects.create(
        site=tracked_site,
        event_type="custom",
        event_name="tos_accepted",
        source="server",
        occurred_at=timezone.now(),
        actor_hash=actor_hash,
        path="",
    )
    AnalyticsEvent.objects.create(
        site=tracked_site,
        event_type="custom",
        event_name="other",
        source="server",
        occurred_at=timezone.now(),
        actor_hash=actor_hash_for_site(tracked_site, "other-user"),
        path="",
    )

    response = client.post(
        "/api/server-events/forget-actor",
        data=json.dumps({"actor_id": "user-123"}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {tracked_site.server_event_key}",
    )

    assert response.status_code == 200
    assert response.json() == {"deleted_events": 1}
    assert list(AnalyticsEvent.objects.values_list("event_name", flat=True)) == ["other"]


@pytest.mark.django_db
def test_browser_actor_token_links_events_and_invalid_tokens_stay_anonymous(client, tracked_site):
    valid_token = actor_token_for_site(tracked_site, "user-123")
    identified = client.post(
        "/api/events",
        data=json.dumps(browser_payload(tracked_site, actor_token=valid_token)),
        content_type="application/json",
        HTTP_ORIGIN="https://example.com",
        HTTP_USER_AGENT="Mozilla/5.0 Chrome/126.0",
        REMOTE_ADDR="203.0.113.8",
    )
    assert identified.status_code == 202
    assert AnalyticsEvent.objects.get().actor_hash == actor_hash_for_site(
        tracked_site, "user-123"
    )

    AnalyticsEvent.objects.all().delete()
    expired = actor_token_for_site(
        tracked_site,
        "user-123",
        now=timezone.now() - timedelta(hours=2),
    )
    anonymous = client.post(
        "/api/events",
        data=json.dumps(browser_payload(tracked_site, actor_token=expired)),
        content_type="application/json",
        HTTP_ORIGIN="https://example.com",
        HTTP_USER_AGENT="Mozilla/5.0 Chrome/126.0",
        REMOTE_ADDR="203.0.113.8",
    )
    assert anonymous.status_code == 202
    assert AnalyticsEvent.objects.get().actor_hash == ""

    AnalyticsEvent.objects.all().delete()
    other = TrackedSite.objects.create(
        name="Other browser",
        slug="other-browser",
        allowed_domains=["other.example"],
    )
    wrong_site_token = actor_token_for_site(other, "user-123")
    for token in (wrong_site_token, f"{valid_token}tampered"):
        response = client.post(
            "/api/events",
            data=json.dumps(browser_payload(tracked_site, actor_token=token)),
            content_type="application/json",
            HTTP_ORIGIN="https://example.com",
            HTTP_USER_AGENT="Mozilla/5.0 Chrome/126.0",
            REMOTE_ADDR="203.0.113.8",
        )
        assert response.status_code == 202
    assert set(AnalyticsEvent.objects.values_list("actor_hash", flat=True)) == {""}


def create_product_event(site, event_name, actor_hash, occurred_at, **kwargs):
    return AnalyticsEvent.objects.create(
        site=site,
        event_type="custom",
        event_name=event_name,
        source="server",
        occurred_at=occurred_at,
        actor_hash=actor_hash,
        path="",
        **kwargs,
    )


@pytest.mark.django_db
def test_product_metrics_reports_mature_activation_cohorts_and_numeric_kpis(tracked_site):
    started = ProductEventDefinition.objects.create(
        site=tracked_site,
        event_name="signup_completed",
        display_name="Signed up",
        description="Account creation committed.",
        aggregation="unique_actors",
    )
    activated = ProductEventDefinition.objects.create(
        site=tracked_site,
        event_name="first_project_created",
        display_name="First project",
        description="First project committed.",
        aggregation="unique_actors",
    )
    ProductEventDefinition.objects.create(
        site=tracked_site,
        event_name="purchase",
        display_name="Revenue",
        description="Payment confirmed.",
        aggregation="sum",
        unit="TRY",
    )
    ActivationDefinition.objects.create(
        site=tracked_site,
        start_event=started,
        goal_event=activated,
    )
    now = timezone.now()
    actor_a = actor_hash_for_site(tracked_site, "a")
    actor_b = actor_hash_for_site(tracked_site, "b")
    actor_c = actor_hash_for_site(tracked_site, "c")
    create_product_event(tracked_site, started.event_name, actor_a, now - timedelta(days=10))
    create_product_event(tracked_site, started.event_name, actor_a, now - timedelta(days=9))
    create_product_event(
        tracked_site,
        activated.event_name,
        actor_a,
        now - timedelta(days=10) + timedelta(hours=1),
    )
    create_product_event(
        tracked_site,
        activated.event_name,
        actor_b,
        now - timedelta(days=11),
    )
    create_product_event(tracked_site, started.event_name, actor_b, now - timedelta(days=10))
    create_product_event(tracked_site, activated.event_name, actor_b, now - timedelta(days=8))
    create_product_event(tracked_site, started.event_name, actor_c, now - timedelta(hours=2))
    create_product_event(tracked_site, started.event_name, "", now - timedelta(days=10))
    create_product_event(
        tracked_site,
        "purchase",
        actor_a,
        now - timedelta(days=1),
        metric_value=Decimal("100.00"),
        metric_unit="TRY",
    )
    create_product_event(
        tracked_site,
        "purchase",
        "",
        now - timedelta(hours=12),
        metric_value=Decimal("50.00"),
        metric_unit="TRY",
    )

    result = product_metrics(tracked_site.slug, "last30d")

    funnel = result["activation"]
    assert funnel["started"] == 3
    assert funnel["activated"] == 2
    assert funnel["eligible_24h"] == 2
    assert funnel["rate_24h"] == 50.0
    assert funnel["pending_24h"] == 1
    assert funnel["eligible_7d"] == 2
    assert funnel["rate_7d"] == 100.0
    assert funnel["pending_7d"] == 1
    assert funnel["median_activation_seconds"] == 88200

    revenue = next(metric for metric in result["metrics"] if metric["event_name"] == "purchase")
    assert revenue["primary_value"] == "150"
    assert revenue["event_count"] == 2
    assert revenue["unique_actors"] == 1
    assert revenue["identified_rate"] == 50.0
    assert revenue["value_average"] == "75"
    assert result["warnings"] == [
        "Some events are missing a verified actor: Revenue, Signed up"
    ]


@pytest.mark.django_db
def test_product_metrics_api_requires_selected_owned_site(client, tracked_site):
    owner = get_user_model().objects.create_user("owner", email="owner@example.com")
    tracked_site.owner = owner
    tracked_site.save(update_fields=["owner"])
    client.force_login(owner)

    selected = client.get(
        "/api/analytics/product-metrics",
        {"site": tracked_site.slug, "period": "last7d"},
    )
    aggregate = client.get(
        "/api/analytics/product-metrics",
        {"site": "all", "period": "last7d"},
    )

    assert selected.status_code == 200
    assert selected.json()["site"] == tracked_site.slug
    assert aggregate.status_code == 400


@pytest.mark.django_db
def test_product_metric_settings_builds_catalog_activation_and_agent_prompt(
    client, tracked_site, superuser, settings
):
    settings.SITEHITS_BASE_URL = "https://stats.example"
    client.force_login(superuser)
    url = reverse("product-metrics-settings", args=[tracked_site.slug])

    saved_events = client.post(
        url,
        {
            "action": "events",
            "events-TOTAL_FORMS": "2",
            "events-INITIAL_FORMS": "0",
            "events-MIN_NUM_FORMS": "0",
            "events-MAX_NUM_FORMS": "1000",
            "events-0-event_name": "signup_completed",
            "events-0-display_name": "Signed up",
            "events-0-description": "Fire after account creation commits.",
            "events-0-aggregation": "unique_actors",
            "events-0-unit": "",
            "events-1-event_name": "first_project_created",
            "events-1-display_name": "First project",
            "events-1-description": "Fire after the first project commits.",
            "events-1-aggregation": "unique_actors",
            "events-1-unit": "",
        },
    )
    assert saved_events.status_code == 302
    definitions = list(ProductEventDefinition.objects.filter(site=tracked_site))
    assert len(definitions) == 2

    by_name = {definition.event_name: definition for definition in definitions}
    saved_activation = client.post(
        url,
        {
            "action": "activation",
            "activation-enabled": "on",
            "activation-start_event": by_name["signup_completed"].pk,
            "activation-goal_event": by_name["first_project_created"].pk,
        },
    )
    assert saved_activation.status_code == 302

    page = client.get(url)
    assert page.status_code == 200
    assert b"signup_completed" in page.content
    assert b"first_project_created" in page.content
    assert b"https://stats.example/api/server-events" in page.content
    assert tracked_site.server_event_key.encode() in page.content
    assert b"data-actor-token" in page.content
    assert b"durably successful" in page.content
    assert ActivationDefinition.objects.filter(site=tracked_site).exists()

    dashboard = client.get(reverse("dashboard-site", args=[tracked_site.slug]))
    assert b'id="product-metrics"' in dashboard.content
    assert url.encode() in dashboard.content


@pytest.mark.django_db
def test_product_metric_settings_are_owner_scoped(client, tracked_site):
    other = get_user_model().objects.create_user("other", email="other@example.com")
    client.force_login(other)

    response = client.get(reverse("product-metrics-settings", args=[tracked_site.slug]))

    assert response.status_code == 404
