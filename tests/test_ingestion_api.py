import json
from unittest.mock import patch

import pytest
from django.utils import timezone

from analytics.models import AnalyticsEvent


def event_payload(site, **overrides):
    payload = {
        "site_key": site.public_key,
        "event_type": "pageview",
        "event_name": "",
        "session_id": "session-12345678",
        "url": "https://example.com/pricing?token=hidden&utm_source=google&utm_campaign=q3",
        "referrer": "https://search.example/results?q=private",
        "timestamp": timezone.now().isoformat(),
        "language": "en-US",
        "timezone": "Europe/Istanbul",
        "viewport": {"width": 1280, "height": 720},
        "screen": {"width": 1440, "height": 900},
        "properties": {},
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
def test_event_is_accepted_sanitized_and_cors_reflected(client, tracked_site):
    location = {
        "country_code": "TR",
        "country_name": "Türkiye",
        "region_code": "34",
        "region_name": "İstanbul",
        "city_name": "İstanbul",
    }
    with patch("analytics.ingestion.location_for_ip", return_value=location):
        response = client.post(
            "/api/events",
            data=json.dumps(event_payload(tracked_site)),
            content_type="application/json",
            HTTP_ORIGIN="https://example.com",
            HTTP_USER_AGENT="Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
            REMOTE_ADDR="203.0.113.8",
        )
    assert response.status_code == 202
    assert response.json() == {"accepted": True}
    assert response["Access-Control-Allow-Origin"] == "https://example.com"

    event = AnalyticsEvent.objects.get()
    assert event.path == "/pricing"
    assert event.utm_source == "google"
    assert event.utm_campaign == "q3"
    assert event.referrer_domain == "search.example"
    assert event.referrer_path == "/results"
    assert event.device == "desktop"
    assert event.country_code == "TR"
    assert event.region_name == "İstanbul"
    assert event.city_name == "İstanbul"
    assert "hidden" not in repr(event.__dict__)


@pytest.mark.django_db
def test_event_rejects_wrong_origin_page_host_and_event_name(client, tracked_site):
    common = {
        "content_type": "application/json",
        "HTTP_USER_AGENT": "Mozilla/5.0 Chrome/126.0",
        "REMOTE_ADDR": "203.0.113.8",
    }
    wrong_origin = client.post(
        "/api/events",
        data=json.dumps(event_payload(tracked_site)),
        HTTP_ORIGIN="https://evil.example",
        **common,
    )
    assert wrong_origin.status_code == 400

    wrong_page = client.post(
        "/api/events",
        data=json.dumps(event_payload(tracked_site, url="https://evil.example/")),
        HTTP_ORIGIN="https://example.com",
        **common,
    )
    assert wrong_page.status_code == 400

    bad_event = client.post(
        "/api/events",
        data=json.dumps(
            event_payload(
                tracked_site,
                event_type="custom",
                event_name="Invalid Event Name",
            )
        ),
        HTTP_ORIGIN="https://example.com",
        **common,
    )
    assert bad_event.status_code == 400
    assert AnalyticsEvent.objects.count() == 0


@pytest.mark.django_db
def test_preflight_and_payload_size_limit(client, tracked_site, settings):
    preflight = client.options("/api/events", HTTP_ORIGIN="https://example.com")
    assert preflight.status_code == 204
    assert preflight["Access-Control-Allow-Methods"] == "POST, OPTIONS"

    settings.SITEHITS_MAX_EVENT_BYTES = 32
    oversized = client.post(
        "/api/events",
        data=json.dumps(event_payload(tracked_site)),
        content_type="application/json",
        HTTP_ORIGIN="https://example.com",
    )
    assert oversized.status_code == 413


@pytest.mark.django_db
def test_bot_event_is_acknowledged_without_storage(client, tracked_site):
    response = client.post(
        "/api/events",
        data=json.dumps(event_payload(tracked_site)),
        content_type="application/json",
        HTTP_ORIGIN="https://example.com",
        HTTP_USER_AGENT="Googlebot/2.1 (+http://www.google.com/bot.html)",
        REMOTE_ADDR="203.0.113.8",
    )
    assert response.status_code == 202
    assert AnalyticsEvent.objects.count() == 0
