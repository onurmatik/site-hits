import pytest
from django.urls import reverse

from websites.models import TrackedSite


@pytest.mark.django_db
def test_anonymous_home_shows_fast_onboarding(client):
    response = client.get("/")

    assert response.status_code == 200
    assert b"Understand your traffic" in response.content
    assert b'name="website"' in response.content
    assert b"Start tracking" in response.content
    assert b"Live dashboard preview" in response.content


@pytest.mark.django_db
def test_superuser_home_redirects_to_dashboard(client, superuser):
    client.force_login(superuser)

    response = client.get("/")

    assert response.status_code == 302
    assert response.url == reverse("dashboard-all")


@pytest.mark.django_db
def test_start_onboarding_validates_and_preserves_website_for_login(client):
    invalid = client.post(reverse("start-onboarding"), {"website": "not a domain"})
    assert invalid.status_code == 400
    assert b"Enter a valid website address" in invalid.content

    response = client.post(
        reverse("start-onboarding"),
        {"website": "https://www.my-product.example/"},
    )

    assert response.status_code == 302
    assert response.url == f"{reverse('login')}?next={reverse('onboarding')}"
    assert client.session["sitehits_onboarding_website"] == {
        "hostname": "www.my-product.example",
        "name": "My Product",
    }


@pytest.mark.django_db
def test_onboarding_requires_superuser_and_creates_site(client, superuser, settings):
    settings.SITEHITS_BASE_URL = "https://stats.example"
    client.post(reverse("start-onboarding"), {"website": "my-product.example"})

    protected = client.get(reverse("onboarding"))
    assert protected.status_code == 302
    assert reverse("login") in protected.url

    client.force_login(superuser)
    confirm = client.get(reverse("onboarding"))
    assert confirm.status_code == 200
    assert b"Ready to track my-product.example" in confirm.content

    created = client.post(reverse("onboarding"))
    site = TrackedSite.objects.get()
    assert created.status_code == 302
    assert created.url == reverse("onboarding-install", args=[site.slug])
    assert site.name == "My Product"
    assert site.allowed_domains == ["my-product.example"]
    assert "sitehits_onboarding_website" not in client.session

    install = client.get(created.url)
    assert install.status_code == 200
    assert b"https://stats.example/js/script.js" in install.content
    assert site.public_key.encode() in install.content
    assert reverse("dashboard-site", args=[site.slug]).encode() in install.content


@pytest.mark.django_db
def test_login_keeps_anonymous_website_and_continues_onboarding(client, superuser):
    client.post(reverse("start-onboarding"), {"website": "kept.example"})

    response = client.post(
        reverse("login"),
        {
            "username": superuser.username,
            "password": "secret-pass",
            "next": reverse("onboarding"),
        },
    )

    assert response.status_code == 302
    assert response.url == reverse("onboarding")
    confirm = client.get(response.url)
    assert b"Ready to track kept.example" in confirm.content


@pytest.mark.django_db
def test_onboarding_reuses_existing_site_for_same_domain(client, tracked_site, superuser):
    client.force_login(superuser)
    client.post(reverse("start-onboarding"), {"website": "example.com"})

    response = client.post(reverse("onboarding"))

    assert response.status_code == 302
    assert response.url == reverse("onboarding-install", args=[tracked_site.slug])
    assert TrackedSite.objects.count() == 1


@pytest.mark.django_db
def test_superuser_can_start_over_with_a_different_website(client, superuser):
    client.force_login(superuser)
    client.post(reverse("start-onboarding"), {"website": "first.example"})

    response = client.get(f"{reverse('home')}?start=over")

    assert response.status_code == 200
    assert b"Dashboard" in response.content
    assert "sitehits_onboarding_website" not in client.session
