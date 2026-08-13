from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from analytics.archive import (
    ArchiveConfigurationError,
    compact_eligible_partitions,
    process_pending_cold_deletions,
    prune_archive_manifests,
    rebuild_changed_timezone_rollups,
    transition_cold_archive_storage,
)


class Command(BaseCommand):
    help = "Compact verified event months to Parquet and process archive retention work."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=12)

    def handle(self, *args, **options):
        if not settings.SITEHITS_ARCHIVE_ENABLED:
            self.stdout.write("Archive maintenance skipped because archiving is disabled.")
            return
        limit = options["limit"]
        if limit < 1 or limit > 120:
            raise CommandError("--limit must be between 1 and 120.")
        try:
            compacted = compact_eligible_partitions(limit=limit)
            rebuilt = rebuild_changed_timezone_rollups(limit=limit)
            deletions = process_pending_cold_deletions(limit=limit)
            transitioned = transition_cold_archive_storage(limit=limit)
            pruned = prune_archive_manifests()
        except ArchiveConfigurationError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                "Archive maintenance completed: "
                f"compaction={compacted}, timezone_rollups={rebuilt}, "
                f"cold_deletions={deletions}, "
                f"storage_transition={transitioned}, pruned={pruned}."
            )
        )
