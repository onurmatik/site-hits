import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Tighten refresh-family constraints after the 0003 backfill commits."""

    dependencies = [
        ("mcp_oauth", "0003_backfill_refresh_families"),
    ]

    operations = [
        migrations.AlterField(
            model_name="oauthrefreshtoken",
            name="family_state",
            field=models.ForeignKey(
                editable=False,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="tokens",
                to="mcp_oauth.oauthrefreshfamily",
            ),
        ),
        migrations.AddConstraint(
            model_name="oauthrefreshtoken",
            constraint=models.UniqueConstraint(
                fields=("token_checksum",),
                name="mcp_oauth_refresh_sum_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="oauthrefreshtoken",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(token_family__isnull=False)
                    & models.Q(token_family=models.F("family_state_id"))
                ),
                name="mcp_oauth_refresh_family_identity_ck",
            ),
        ),
        migrations.AddIndex(
            model_name="oauthrefreshfamily",
            index=models.Index(
                fields=["application", "user", "revoked_at"],
                name="mcp_oauth_family_principal_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="oauthrefreshfamily",
            index=models.Index(
                fields=["expires_at", "revoked_at"],
                name="mcp_oauth_family_cleanup_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="oauthrefreshfamily",
            constraint=models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F("created_at")),
                name="mcp_oauth_family_expiry_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="oauthrefreshfamily",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(revoked_at__isnull=True)
                    | models.Q(revoked_at__gte=models.F("created_at"))
                ),
                name="mcp_oauth_family_revoke_ck",
            ),
        ),
    ]
