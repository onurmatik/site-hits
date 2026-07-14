import pytest
from django.contrib.auth import get_user_model


@pytest.mark.django_db
def test_analytics_api_requires_authentication_and_scopes_regular_users(client, tracked_site, superuser):
    anonymous = client.get("/api/analytics/overview")
    assert anonymous.status_code == 401

    regular = get_user_model().objects.create_user("viewer", password="pass")
    client.force_login(regular)
    empty_allowed = client.get("/api/analytics/overview")
    other_forbidden = client.get(f"/api/analytics/overview?site={tracked_site.slug}")
    assert empty_allowed.status_code == 200
    assert other_forbidden.status_code == 400

    tracked_site.owner = regular
    tracked_site.save(update_fields=["owner"])
    own_allowed = client.get(f"/api/analytics/overview?site={tracked_site.slug}")
    assert own_allowed.status_code == 200

    client.force_login(superuser)
    allowed = client.get("/api/analytics/overview")
    assert allowed.status_code == 200


@pytest.mark.django_db
def test_dashboard_login_and_site_routes(client, tracked_site, superuser):
    anonymous = client.get("/dashboard/all")
    assert anonymous.status_code == 302
    assert "/accounts/signup/" in anonymous.url

    client.force_login(superuser)
    all_sites = client.get("/dashboard/all?period=last7d&granularity=daily")
    site = client.get(f"/dashboard/{tracked_site.slug}")
    assert all_sites.status_code == 200
    assert b"All sites" in all_sites.content
    assert b'data-breakdown="pages"' in all_sites.content
    assert b'data-breakdown="regions"' in all_sites.content
    assert b'data-breakdown="cities"' in all_sites.content
    assert b'data-breakdown="events"' in all_sites.content
    assert site.status_code == 200
    assert tracked_site.name.encode() in site.content


@pytest.mark.django_db
def test_regular_user_dashboard_only_exposes_owned_sites(client, tracked_site):
    regular = get_user_model().objects.create_user("owner", email="owner@example.com")
    tracked_site.owner = regular
    tracked_site.save(update_fields=["owner"])
    other = type(tracked_site).objects.create(
        name="Other",
        slug="other",
        allowed_domains=["other.example"],
    )
    client.force_login(regular)

    own = client.get(f"/dashboard/{tracked_site.slug}")
    forbidden = client.get(f"/dashboard/{other.slug}")
    aggregate = client.get("/dashboard/all")

    assert own.status_code == 200
    assert forbidden.status_code == 404
    assert aggregate.status_code == 200
    assert tracked_site.name.encode() in aggregate.content
    assert other.name.encode() not in aggregate.content


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


@pytest.mark.django_db
def test_logout_returns_to_public_home(client, superuser):
    client.force_login(superuser)

    response = client.post("/accounts/logout/")

    assert response.status_code == 302
    assert response.url == "/"
