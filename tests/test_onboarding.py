from urllib.parse import urlsplit

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
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
def test_start_onboarding_validates_and_preserves_website_for_signup(client):
    invalid = client.post(reverse("start-onboarding"), {"website": "not a domain"})
    assert invalid.status_code == 400
    assert b"Enter a valid website address" in invalid.content

    response = client.post(
        reverse("start-onboarding"),
        {"website": "https://www.my-product.example/"},
    )

    assert response.status_code == 302
    assert response.url == f"{reverse('signup')}?next={reverse('onboarding')}"
    assert client.session["sitehits_onboarding_website"] == {
        "hostname": "www.my-product.example",
        "name": "My Product",
    }


@pytest.mark.django_db
def test_onboarding_requires_authentication_and_creates_owned_site(client, superuser, settings):
    settings.SITEHITS_BASE_URL = "https://stats.example"
    client.post(reverse("start-onboarding"), {"website": "my-product.example"})

    protected = client.get(reverse("onboarding"))
    assert protected.status_code == 302
    assert reverse("signup") in protected.url

    client.force_login(superuser)
    confirm = client.get(reverse("onboarding"))
    assert confirm.status_code == 200
    assert b"Ready to track my-product.example" in confirm.content

    created = client.post(reverse("onboarding"))
    site = TrackedSite.objects.get()
    assert created.status_code == 302
    assert created.url == reverse("onboarding-install", args=[site.slug])
    assert site.name == "My Product"
    assert site.owner == superuser
    assert site.allowed_domains == ["my-product.example"]
    assert "sitehits_onboarding_website" not in client.session

    install = client.get(created.url)
    assert install.status_code == 200
    assert b"https://stats.example/js/script.js" in install.content
    assert site.public_key.encode() in install.content
    assert reverse("dashboard-site", args=[site.slug]).encode() in install.content
    assert b"Instruction for your agent" in install.content
    assert b'id="agent-instruction"' in install.content
    assert install.content.count(b"data-copy-target=") == 2
    assert b'id="copy-status"' in install.content


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
def test_anonymous_onboarding_shows_signup_options(client):
    client.post(reverse("start-onboarding"), {"website": "new-product.example"})

    response = client.get(reverse("signup"), {"next": reverse("onboarding")})

    assert response.status_code == 200
    assert b"Create your SiteHits account" in response.content
    assert b"new-product.example" in response.content
    assert b"Continue with Google" in response.content
    assert b"Send magic link" in response.content
    assert b'name="password"' not in response.content


@pytest.mark.django_db
def test_magic_link_creates_user_and_continues_preserved_onboarding(client, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.SITEHITS_BASE_URL = "https://stats.example"
    client.post(reverse("start-onboarding"), {"website": "magic.example"})

    sent = client.post(
        reverse("signup"),
        {"email": "owner@example.com", "next": reverse("onboarding")},
    )

    assert sent.status_code == 200
    assert b"Check your inbox" in sent.content
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.subject == "Your SiteHits sign-in link"
    assert len(message.alternatives) == 1
    html = message.alternatives[0].content
    assert message.alternatives[0].mimetype == "text/html"
    assert "Sign in to SiteHits" in html
    assert "Passwordless access" in html
    assert "https://stats.example/static/sitehits-mark.svg" in html
    user = get_user_model().objects.get(email="owner@example.com")
    assert not user.has_usable_password()
    login_url = next(line for line in message.body.splitlines() if "magic-link" in line)
    assert login_url.replace("&", "&amp;") in html
    parsed = urlsplit(login_url)

    logged_in = client.get(f"{parsed.path}?{parsed.query}")

    assert logged_in.status_code == 302
    assert logged_in.url == reverse("onboarding")
    confirm = client.get(logged_in.url)
    assert b"Ready to track magic.example" in confirm.content


@pytest.mark.django_db
def test_google_signup_redirect_is_branded_when_unconfigured(client):
    response = client.get(reverse("google-start"), {"next": reverse("onboarding")})

    assert response.status_code == 302
    follow = client.get(response.url)
    assert follow.status_code == 200
    assert b"Google sign-in is not configured yet" in follow.content


@pytest.mark.django_db
def test_google_signup_starts_oauth_and_preserves_next(client, settings):
    settings.SITEHITS_GOOGLE_CLIENT_ID = "client-id"
    settings.SITEHITS_GOOGLE_CLIENT_SECRET = "client-secret"
    settings.SOCIALACCOUNT_PROVIDERS = {
        "google": {
            "APPS": [{"client_id": "client-id", "secret": "client-secret", "key": ""}],
            "SCOPE": ["profile", "email"],
            "AUTH_PARAMS": {"access_type": "online"},
            "OAUTH_PKCE_ENABLED": True,
        }
    }

    response = client.get(reverse("google-start"), {"next": reverse("onboarding")})

    assert response.status_code == 302
    assert response.url.startswith(reverse("google_login"))
    assert "next=%2Fonboarding%2F" in response.url
    oauth = client.get(response.url)
    assert oauth.status_code == 302
    assert oauth.url.startswith("https://accounts.google.com/")


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
