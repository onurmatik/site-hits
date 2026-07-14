import pytest

from websites.models import TrackedSite


@pytest.mark.django_db
def test_generated_tracking_keys_use_sitehits_prefixes():
    site = TrackedSite.objects.create(
        name="Example",
        slug="example",
        allowed_domains=["example.com"],
    )

    assert site.public_key.startswith("sh_")
    assert site.bot_key.startswith("shb_")
    assert site.bot_key != site.public_key
