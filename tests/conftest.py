import pytest

from websites.models import TrackedSite


@pytest.fixture
def tracked_site(db):
    return TrackedSite.objects.create(
        name="Example",
        slug="example",
        allowed_domains=["example.com", "*.example.org"],
        timezone="Europe/Istanbul",
    )


@pytest.fixture
def superuser(django_user_model):
    return django_user_model.objects.create_superuser(
        username="admin", email="admin@example.com", password="secret-pass"
    )

