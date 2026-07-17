from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from analytics.models import AnalyticsEvent, BotEvent
from analytics.reporting import (
    bot_traffic,
    breakdown,
    last_hour_widget,
    overview,
    site_overviews,
    timeseries,
)
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


def make_bot_event(
    site,
    *,
    minutes,
    provider,
    crawler,
    category,
    path="/",
    status_code=200,
):
    return BotEvent.objects.create(
        site=site,
        occurred_at=timezone.now() - timedelta(minutes=minutes),
        provider=provider,
        crawler=crawler,
        category=category,
        path=path,
        status_code=status_code,
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
def test_site_overviews_separates_each_sites_metrics(analytics_data):
    site, other = analytics_data

    result = site_overviews("last7d")

    by_slug = {row["slug"]: row for row in result["sites"]}
    assert by_slug[site.slug]["current"] == {
        "visitors": 2,
        "sessions": 2,
        "pageviews": 2,
        "bounce_rate": 50.0,
        "avg_session_duration": 900,
    }
    assert by_slug[other.slug]["current"] == {
        "visitors": 1,
        "sessions": 1,
        "pageviews": 1,
        "bounce_rate": 100.0,
        "avg_session_duration": 0,
    }


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
def test_bot_traffic_reports_categories_providers_paths_and_scope(tracked_site):
    other = TrackedSite.objects.create(
        name="Other",
        slug="other-bots",
        allowed_domains=["other.example"],
    )
    make_bot_event(
        tracked_site,
        minutes=30,
        provider="OpenAI",
        crawler="ChatGPT-User",
        category="answer",
        path="/pricing",
    )
    make_bot_event(
        tracked_site,
        minutes=20,
        provider="OpenAI",
        crawler="GPTBot",
        category="training",
        path="/missing",
        status_code=404,
    )
    make_bot_event(
        other,
        minutes=10,
        provider="Google",
        crawler="Googlebot",
        category="indexing",
    )

    result = bot_traffic(tracked_site.slug, "last7d")

    assert result["total"] == 2
    assert {row["key"]: row["count"] for row in result["categories"]} == {
        "answer": 1,
        "indexing": 0,
        "training": 1,
        "other": 0,
    }
    assert result["providers"] == [
        {"label": "OpenAI", "count": 2, "percentage": 100.0}
    ]
    assert any(
        row["path"] == "/missing" and row["status_code"] == 404
        for row in result["pages"]
    )
    assert result["verification"] == {"ip_verified": 0, "user_agent": 2}
    assert result["collector"]["state"] == "never_seen"
    assert result["suspected_automation"] == {
        "visitors": 0,
        "sessions": 0,
        "pageviews": 0,
        "reasons": [],
        "pages": [],
    }
    assert bot_traffic("all", "last7d")["total"] == 3


@pytest.mark.django_db
def test_suspected_automation_combines_explicit_and_behavioral_signals(tracked_site):
    make_event(
        tracked_site,
        minutes=5,
        session="webdriver-session",
        visitor="webdriver-visitor",
        path="/automated",
        automation_score=100,
        automation_reasons=["webdriver"],
    )
    for index in range(20):
        make_event(
            tracked_site,
            minutes=4,
            session=f"churn-session-{index}",
            visitor="churn-visitor",
            path="/destinations",
        )

    result = bot_traffic(tracked_site.slug, "last7d")["suspected_automation"]

    assert result["visitors"] == 2
    assert result["sessions"] == 21
    assert result["pageviews"] == 21
    assert {row["key"] for row in result["reasons"]} == {
        "webdriver",
        "rapid_navigation_burst",
        "session_churn",
    }
    assert result["pages"][0] == {"path": "/destinations", "count": 20}
    assert overview(tracked_site.slug, "last7d")["current"]["pageviews"] == 20


@pytest.mark.django_db
def test_bot_report_exposes_recent_collector_health(tracked_site):
    now = timezone.now()
    tracked_site.bot_collector_last_seen_at = now
    tracked_site.bot_collector_last_event_at = now - timedelta(minutes=2)
    tracked_site.save(
        update_fields=["bot_collector_last_seen_at", "bot_collector_last_event_at"]
    )

    collector = bot_traffic(tracked_site.slug, "last7d")["collector"]

    assert collector["state"] == "active"
    assert collector["last_seen_at"] == now.isoformat()


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
