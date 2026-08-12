from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from mcp_gateway.models import MCPAccessToken


class Command(BaseCommand):
    help = "Issue a deprecated SiteHits MCP migration token for an existing Django user."

    def add_arguments(self, parser):
        parser.add_argument("user", help="Username (or email when it is the login field).")
        parser.add_argument("--name", default="MCP client", help="Human-readable token name.")
        parser.add_argument(
            "--expires-in-days",
            type=int,
            help="Optional positive token lifetime in days.",
        )

    def handle(self, *args, **options):
        if not settings.SITEHITS_MCP_ALLOW_LEGACY_TOKENS:
            raise CommandError(
                "Legacy MCP tokens are disabled. Use OAuth, or explicitly enable "
                "SITEHITS_MCP_ALLOW_LEGACY_TOKENS for a time-bounded migration."
            )
        user_model = get_user_model()
        identifier = options["user"]
        try:
            user = user_model._default_manager.get_by_natural_key(identifier)
        except user_model.DoesNotExist as exc:
            raise CommandError(f"User {identifier!r} does not exist.") from exc

        expires_in_days = options["expires_in_days"]
        if expires_in_days is not None and expires_in_days <= 0:
            raise CommandError("--expires-in-days must be positive.")
        expires_at = (
            timezone.now() + timedelta(days=expires_in_days)
            if expires_in_days is not None
            else None
        )
        token, raw_token = MCPAccessToken.issue(
            user=user,
            name=options["name"],
            expires_at=expires_at,
        )
        self.stdout.write(self.style.SUCCESS(f"Issued token {token.pk} for {user}."))
        self.stdout.write(raw_token)
        self.stdout.write("Store this value now; SiteHits does not retain the plaintext token.")
