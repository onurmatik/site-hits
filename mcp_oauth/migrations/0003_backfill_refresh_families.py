from importlib import import_module

from django.db import migrations

# Keep the historical data function frozen in the migration module where it
# was authored while giving PostgreSQL a transaction boundary after the 0002
# nullable-FK DDL and before the 0004 constraint tightening.
backfill_refresh_families = import_module(
    "mcp_oauth.migrations.0002_oauthrefreshfamily_and_more"
).backfill_refresh_families


class Migration(migrations.Migration):
    dependencies = [
        ("mcp_oauth", "0002_oauthrefreshfamily_and_more"),
    ]

    operations = [
        migrations.RunPython(
            backfill_refresh_families,
            migrations.RunPython.noop,
        ),
    ]
