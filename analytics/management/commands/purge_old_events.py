from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from agent_runtime.contract import audit_retention_days
from analytics.models import AgentAuditEvent, AgentIdempotencyRecord, AnalyticsEvent, BotEvent


class Command(BaseCommand):
    help = "Delete analytics events older than the configured retention window."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=365)

    def handle(self, *args, **options):
        days = options["days"]
        if days < 1:
            raise ValueError("Retention days must be positive.")
        now = timezone.now()
        cutoff = now - timedelta(days=days)
        audit_cutoff = now - timedelta(days=audit_retention_days())
        analytics_deleted, _ = AnalyticsEvent.objects.filter(occurred_at__lt=cutoff).delete()
        bots_deleted, _ = BotEvent.objects.filter(occurred_at__lt=cutoff).delete()
        audit_deleted, _ = AgentAuditEvent.objects.filter(created_at__lt=audit_cutoff).delete()
        idempotency_deleted, _ = AgentIdempotencyRecord.objects.filter(
            expires_at__lte=now
        ).delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {analytics_deleted + bots_deleted} analytics event rows before "
                f"{cutoff}, {audit_deleted} agent audit rows before {audit_cutoff}, and "
                f"{idempotency_deleted} expired agent idempotency rows."
            )
        )
