from pathlib import Path

from django.conf import settings
from django.core.checks import Error, Tags, register
from geoip2.database import Reader


SUPPORTED_DATABASE_TYPES = {"GeoLite2-City", "GeoIP2-City"}


@register(Tags.security, deploy=True)
def check_geoip_database(app_configs, **kwargs):
    path = settings.SITEHITS_GEOIP_DB_PATH
    if not path:
        return [
            Error(
                "SITEHITS_GEOIP_DB_PATH is not configured.",
                hint="Provision GeoLite2-City.mmdb before accepting analytics events.",
                id="analytics.E001",
            )
        ]

    database_path = Path(path)
    if not database_path.is_file():
        return [
            Error(
                f"The configured GeoIP database does not exist: {database_path}",
                hint="Run geoipupdate and verify SITEHITS_GEOIP_DB_PATH.",
                id="analytics.E002",
            )
        ]

    try:
        with Reader(str(database_path)) as reader:
            database_type = reader.metadata().database_type
    except Exception as exc:
        return [
            Error(
                f"The configured GeoIP database cannot be opened ({type(exc).__name__}).",
                hint="Replace it with a valid GeoLite2-City MMDB file.",
                id="analytics.E003",
            )
        ]

    if database_type not in SUPPORTED_DATABASE_TYPES:
        return [
            Error(
                f"The configured GeoIP database has unsupported type: {database_type}",
                hint="Configure a GeoLite2-City or GeoIP2-City MMDB file.",
                id="analytics.E004",
            )
        ]
    return []
