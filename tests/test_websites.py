import pytest

from websites.models import TrackedSite


@pytest.mark.django_db
def test_generated_public_key_uses_sitehits_prefix():
    site = TrackedSite.objects.create(
        name="Example",
        slug="example",
        allowed_domains=["example.com"],
    )

    assert site.public_key.startswith("sh_")
