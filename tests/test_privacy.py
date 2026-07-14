from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import override_settings

from analytics.models import AnalyticsEvent
from analytics.privacy import (
    daily_visitor_hash,
    hostname_allowed,
    location_for_ip,
    sanitized_page,
    sanitized_properties,
)
from websites.models import TrackedSite


@override_settings(SITEHITS_HASH_SECRET="test-hash-secret")
def test_visitor_hash_is_stable_per_site_and_day_and_rotates(tracked_site):
    other = TrackedSite.objects.create(
        name="Other", slug="other", allowed_domains=["other.example"]
    )
    first = daily_visitor_hash(tracked_site, "203.0.113.8", "Browser/1", date(2026, 7, 10))
    same = daily_visitor_hash(tracked_site, "203.0.113.8", "Browser/1", date(2026, 7, 10))
    next_day = daily_visitor_hash(
        tracked_site, "203.0.113.8", "Browser/1", date(2026, 7, 11)
    )
    other_site = daily_visitor_hash(other, "203.0.113.8", "Browser/1", date(2026, 7, 10))

    assert first == same
    assert first != next_day
    assert first != other_site


def test_event_model_has_no_raw_ip_or_user_agent_fields():
    fields = {field.name for field in AnalyticsEvent._meta.fields}
    assert "ip" not in fields
    assert "ip_address" not in fields
    assert "user_agent" not in fields


@override_settings(SITEHITS_GEOIP_DB_PATH="/tmp/GeoLite2-City.mmdb")
def test_location_for_ip_extracts_country_region_and_city():
    response = SimpleNamespace(
        country=SimpleNamespace(iso_code="TR", name="Türkiye"),
        subdivisions=SimpleNamespace(most_specific=SimpleNamespace(iso_code="34", name="İstanbul")),
        city=SimpleNamespace(name="İstanbul"),
    )
    reader = MagicMock()
    reader.city.return_value = response

    with patch("analytics.privacy.geoip_reader", return_value=reader):
        location = location_for_ip("203.0.113.8")

    assert location == {
        "country_code": "TR",
        "country_name": "Türkiye",
        "region_code": "34",
        "region_name": "İstanbul",
        "city_name": "İstanbul",
    }
    reader.city.assert_called_once_with("203.0.113.8")


@override_settings(SITEHITS_GEOIP_DB_PATH="")
def test_location_for_ip_returns_empty_location_without_database():
    assert location_for_ip("203.0.113.8") == {
        "country_code": "",
        "country_name": "",
        "region_code": "",
        "region_name": "",
        "city_name": "",
    }


def test_page_sanitization_keeps_path_and_utm_only():
    host, path, utm = sanitized_page(
        "https://example.com/private?token=secret&email=a@example.com"
        "&utm_source=newsletter&utm_campaign=launch#section"
    )
    assert host == "example.com"
    assert path == "/private"
    assert utm["utm_source"] == "newsletter"
    assert utm["utm_campaign"] == "launch"
    assert "secret" not in repr((host, path, utm))
    assert "a@example.com" not in repr((host, path, utm))


def test_hostname_patterns_and_property_limits():
    assert hostname_allowed("example.com", ["example.com"])
    assert hostname_allowed("app.example.org", ["*.example.org"])
    assert not hostname_allowed("example.org", ["*.example.org"])
    assert sanitized_properties({"plan-name": "pro"}) == {"plan-name": "pro"}
