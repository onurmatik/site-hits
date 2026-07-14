from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from analytics.models import AnalyticsEvent
from analytics.reporting import breakdown, last_hour_widget, overview, timeseries
from websites.models import TrackedSite


def make_event(site, *, minutes, session, visitor, path="/", event_type="pageview", **kwargs):
    defaults = {
        "event_name": "",
        "referrer_domain": "",
        "country_name": "Türkiye",
        "country_code": "TR",
        "region_name": "İstanbul",
        "region_code": "34",
        "city_name": "İstanbul",
        "device": "desktop",
        "browser": "Chrome",
        "operating_system": "macOS",
        "utm_campaign": "",
    }
    defaults.update(kwargs)
    return AnalyticsEvent.objects.create(
        site=site,
        event_type=event_type,
        occurred_at=timezone.now() - timedelta(minutes=minutes),
        visitor_hash=visitor,
        session_id=session,
        path=path,
        **defaults,
    )


@pytest.fixture
def analytics_data(db, tracked_site):
    other = TrackedSite.objects.create(
        name="Other", slug="other", allowed_domains=["other.example"]
    )
    make_event(
        tracked_site,
        minutes=60,
        session="site-a-session-1",
        visitor="site-a-visitor-1",
        path="/pricing",
        referrer_domain="google.com",
        utm_campaign="launch",
    )
    make_event(
        tracked_site,
        minutes=30,
        session="site-a-session-1",
        visitor="site-a-visitor-1",
        path="/pricing",
        event_type="custom",
        event_name="signup",
    )
    make_event(
        tracked_site,
        minutes=20,
        session="site-a-session-2",
        visitor="site-a-visitor-2",
        path="/",
        device="mobile",
    )
    make_event(
        other,
        minutes=10,
        session="site-a-session-2",
        visitor="site-a-visitor-2",
        path="/home",
    )
    return tracked_site, other


@pytest.mark.django_db
def test_site_and_all_site_overview_metrics(analytics_data):
    site, _ = analytics_data
    site_result = overview(site.slug, "last7d")["current"]
    assert site_result == {
        "visitors": 2,
        "sessions": 2,
        "pageviews": 2,
        "bounce_rate": 50.0,
        "avg_session_duration": 900,
    }

    all_result = overview("all", "last7d")["current"]
    assert all_result["visitors"] == 3
    assert all_result["sessions"] == 3
    assert all_result["pageviews"] == 3


@pytest.mark.django_db
def test_timeseries_and_every_breakdown(analytics_data):
    site, _ = analytics_data
    series = timeseries(site.slug, "last7d", "daily")
    assert series["granularity"] == "daily"
    assert sum(row["pageviews"] for row in series["data"]) == 2

    all_series = timeseries("all", "last7d", "daily")
    assert sum(row["sessions"] for row in all_series["data"]) == 3

    expected = {
        "pages": "/pricing",
        "referrers": "google.com",
        "countries": "Türkiye",
        "regions": "İstanbul, Türkiye",
        "cities": "İstanbul, Türkiye",
        "devices": "desktop",
        "browsers": "Chrome",
        "os": "macOS",
        "campaigns": "launch",
        "events": "signup",
    }
    for dimension, top_label in expected.items():
        result = breakdown(site.slug, "last7d", dimension)
        assert result["data"]
        assert any(row["label"] == top_label for row in result["data"])


@pytest.mark.django_db
def test_city_breakdown_groups_missing_city_as_unknown(tracked_site):
    make_event(
        tracked_site,
        minutes=10,
        session="unknown-city-1",
        visitor="unknown-city-visitor-1",
        city_name="",
        region_name="",
    )
    make_event(
        tracked_site,
        minutes=5,
        session="unknown-city-2",
        visitor="unknown-city-visitor-2",
        country_code="DE",
        country_name="Germany",
        city_name="",
        region_name="",
    )

    result = breakdown(tracked_site.slug, "last7d", "cities")

    assert result["data"] == [
        {"label": "Unknown", "count": 2, "percentage": 100.0}
    ]


@pytest.mark.django_db
def test_last_hour_widget_uses_sixty_minute_buckets_and_distinct_visitors(
    analytics_data,
):
    site, _ = analytics_data

    snapshot = last_hour_widget(site)

    assert snapshot["visitors"] == 2
    assert len(snapshot["minutes"]) == 60
    assert len(snapshot["axis_labels"]) == 5
    assert sum(row["visitors"] for row in snapshot["minutes"]) == 2
    assert snapshot["countries"] == [
        {"code": "TR", "name": "Türkiye", "visitors": 2}
    ]


@pytest.mark.django_db
def test_public_widget_is_frameable_and_contains_only_aggregate_data(
    client,
    tracked_site,
):
    make_event(
        tracked_site,
        minutes=5,
        session="widget-session",
        visitor="widget-visitor",
        path="/private-path",
        country_name="Germany",
        country_code="DE",
    )

    response = client.get(reverse("site-widget", args=[tracked_site.public_key]))

    assert response.status_code == 200
    assert "X-Frame-Options" not in response
    assert "frame-ancestors *" in response["Content-Security-Policy"]
    assert response["Referrer-Policy"] == "no-referrer"
    assert b"Visitors in the last 60 minutes" in response.content
    assert b"Germany" in response.content
    assert b"/private-path" not in response.content
    assert response.content.count(b"sh-widget-bar") >= 60


@pytest.mark.django_db
def test_dashboard_uses_downward_site_menu_with_new_site_action(client, superuser, tracked_site):
    other = TrackedSite.objects.create(
        name="Other", slug="other", allowed_domains=["other.example"]
    )
    client.force_login(superuser)

    response = client.get(
        reverse("dashboard-site", args=[tracked_site.slug]),
        {"period": "last30d", "granularity": "daily"},
    )

    assert response.status_code == 200
    assert b'id="site-menu-trigger"' in response.content
    assert b'aria-expanded="false"' in response.content
    assert b'id="site-menu-options" hidden' in response.content
    assert b'id="site-selector"' not in response.content
    assert (
        f'{reverse("dashboard-site", args=[other.slug])}?period=last30d&amp;granularity=daily'.encode()
        in response.content
    )
    assert b'aria-current="page"' in response.content
    assert b'id="new-site-trigger"' in response.content
    assert b'id="new-site-dialog"' in response.content
    assert f'action="{reverse("start-onboarding")}"'.encode() in response.content
    assert b'name="flow" value="dashboard-new-site"' in response.content
    assert b"New site" in response.content
    assert b'id="embed-widget-trigger"' in response.content
    assert b'id="embed-widget-dialog"' in response.content
    assert b'id="copy-embed-widget"' in response.content
    assert b'id="embed-widget-agent-instruction"' in response.content
    assert b'id="copy-embed-widget-agent"' in response.content
    assert b"Instruction for your agent" in response.content
    assert b"Preserve the iframe exactly" in response.content
    assert tracked_site.public_key.encode() in response.content

    all_sites = client.get(reverse("dashboard-all"))
    assert b'id="embed-widget-trigger"' not in all_sites.content
