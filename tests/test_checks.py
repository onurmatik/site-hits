from unittest.mock import MagicMock, patch

from django.test import override_settings

from analytics.checks import check_geoip_database
from dashboard.checks import check_google_oauth


@override_settings(SITEHITS_GEOIP_DB_PATH="")
def test_deploy_check_requires_geoip_path():
    issues = check_geoip_database(None)

    assert [issue.id for issue in issues] == ["analytics.E001"]


def test_deploy_check_requires_existing_geoip_database(tmp_path):
    missing_path = tmp_path / "GeoLite2-City.mmdb"

    with override_settings(SITEHITS_GEOIP_DB_PATH=str(missing_path)):
        issues = check_geoip_database(None)

    assert [issue.id for issue in issues] == ["analytics.E002"]


def test_deploy_check_rejects_invalid_geoip_database(tmp_path):
    invalid_path = tmp_path / "GeoLite2-City.mmdb"
    invalid_path.write_bytes(b"not an mmdb database")

    with override_settings(SITEHITS_GEOIP_DB_PATH=str(invalid_path)):
        issues = check_geoip_database(None)

    assert [issue.id for issue in issues] == ["analytics.E003"]


def test_deploy_check_accepts_city_database(tmp_path):
    database_path = tmp_path / "GeoLite2-City.mmdb"
    database_path.touch()
    reader = MagicMock()
    reader.__enter__.return_value.metadata.return_value.database_type = "GeoLite2-City"

    with (
        override_settings(SITEHITS_GEOIP_DB_PATH=str(database_path)),
        patch("analytics.checks.Reader", return_value=reader),
    ):
        issues = check_geoip_database(None)

    assert issues == []


def test_deploy_check_rejects_wrong_mmdb_type(tmp_path):
    database_path = tmp_path / "GeoLite2-Country.mmdb"
    database_path.touch()
    reader = MagicMock()
    reader.__enter__.return_value.metadata.return_value.database_type = "GeoLite2-Country"

    with (
        override_settings(SITEHITS_GEOIP_DB_PATH=str(database_path)),
        patch("analytics.checks.Reader", return_value=reader),
    ):
        issues = check_geoip_database(None)

    assert [issue.id for issue in issues] == ["analytics.E004"]


@override_settings(SITEHITS_GOOGLE_CLIENT_ID="", SITEHITS_GOOGLE_CLIENT_SECRET="")
def test_deploy_check_requires_google_oauth_credentials():
    issues = check_google_oauth(None)

    assert [issue.id for issue in issues] == ["dashboard.E001"]


@override_settings(
    SITEHITS_GOOGLE_CLIENT_ID="not-a-google-client",
    SITEHITS_GOOGLE_CLIENT_SECRET="client-secret",
)
def test_deploy_check_rejects_invalid_google_client_id():
    issues = check_google_oauth(None)

    assert [issue.id for issue in issues] == ["dashboard.E002"]


@override_settings(
    SITEHITS_GOOGLE_CLIENT_ID="123-example.apps.googleusercontent.com",
    SITEHITS_GOOGLE_CLIENT_SECRET="client-secret",
)
def test_deploy_check_accepts_google_oauth_credentials():
    assert check_google_oauth(None) == []
