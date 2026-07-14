from unittest.mock import MagicMock, patch

from django.test import override_settings

from analytics.checks import check_geoip_database


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
