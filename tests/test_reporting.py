from datetime import timedelta

import pytest
from django.utils import timezone

from analytics.models import AnalyticsEvent
from analytics.reporting import breakdown, overview, timeseries
from websites.models import TrackedSite


def make_event(site, *, minutes, session, visitor, path="/", event_type="pageview", **kwargs):
    defaults = {
        "event_name": "",
        "referrer_domain": "",
        "country_name": "Türkiye",
        "country_code": "TR",
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
