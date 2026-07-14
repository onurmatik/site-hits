from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from analytics.models import AnalyticsEvent, BotEvent


class Command(BaseCommand):
    help = "Delete analytics events older than the configured retention window."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=365)

    def handle(self, *args, **options):
        days = options["days"]
        if days < 1:
            raise ValueError("Retention days must be positive.")
        cutoff = timezone.now() - timedelta(days=days)
        analytics_deleted, _ = AnalyticsEvent.objects.filter(occurred_at__lt=cutoff).delete()
        bots_deleted, _ = BotEvent.objects.filter(occurred_at__lt=cutoff).delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {analytics_deleted + bots_deleted} event rows before {cutoff}."
            )
        )
