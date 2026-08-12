from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("mcp_oauth", "0004_refresh_family_constraints"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="oauthapplication",
            name="mcp_oauth_app_dcr_only",
        ),
        migrations.AddConstraint(
            model_name="oauthapplication",
            constraint=models.CheckConstraint(
                condition=models.Q(registration_source__in=("dcr", "cimd")),
                name="mcp_oauth_app_source_supported",
            ),
        ),
        migrations.AddConstraint(
            model_name="oauthapplication",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(registration_source="dcr", cimd_expires_at__isnull=True)
                    | models.Q(registration_source="cimd", cimd_expires_at__isnull=False)
                ),
                name="mcp_oauth_app_cimd_expiry_ck",
            ),
        ),
    ]
