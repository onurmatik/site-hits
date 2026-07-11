import pytest
from django.contrib.auth import get_user_model


@pytest.mark.django_db
def test_analytics_api_requires_superuser(client, tracked_site, superuser):
    anonymous = client.get("/api/analytics/overview")
    assert anonymous.status_code == 401

    regular = get_user_model().objects.create_user("viewer", password="pass")
    client.force_login(regular)
    forbidden = client.get("/api/analytics/overview")
    assert forbidden.status_code == 403

    client.force_login(superuser)
    allowed = client.get("/api/analytics/overview")
    assert allowed.status_code == 200


@pytest.mark.django_db
def test_dashboard_login_and_site_routes(client, tracked_site, superuser):
    anonymous = client.get("/dashboard/all")
    assert anonymous.status_code == 302
    assert "/accounts/login/" in anonymous.url

    client.force_login(superuser)
    all_sites = client.get("/dashboard/all?period=last7d&granularity=daily")
    site = client.get(f"/dashboard/{tracked_site.slug}")
    assert all_sites.status_code == 200
    assert b"All sites" in all_sites.content
    assert b'data-breakdown="pages"' in all_sites.content
    assert b'data-breakdown="events"' in all_sites.content
    assert site.status_code == 200
    assert tracked_site.name.encode() in site.content


@pytest.mark.django_db
def test_tracker_and_health_are_public(client):
    health = client.get("/health/")
    tracker = client.get("/js/script.js")
    content = b"".join(tracker.streaming_content).decode()
    assert health.json() == {"status": "ok"}
    assert tracker.status_code == 200
    assert "sessionStorage" in content
    assert "pushState" in content
    assert "replaceState" in content
    assert "localStorage" not in content
    assert "document.cookie" not in content
