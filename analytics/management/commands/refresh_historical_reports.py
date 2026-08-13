from django.conf import settings
from django.core.management.base import BaseCommand

from analytics.archive import HistoricalDataUnavailable
from analytics.product_reporting import product_metrics
from analytics.reporting import bot_traffic, breakdown, overview, site_overviews, timeseries
from websites.models import TrackedSite


class Command(BaseCommand):
    help = "Refresh the standard six-month and one-year historical report cache."

    def handle(self, *args, **options):
        if not settings.SITEHITS_ARCHIVE_QUERY_ENABLED:
            self.stdout.write("Historical cache refresh skipped because cold queries are disabled.")
            return
        sites = TrackedSite.objects.filter(is_active=True)
        refreshed = 0
        failed = 0
        for period in ("last180d", "last365d"):
            calls = [
                lambda p=period: overview("all", p, sites=sites),
                lambda p=period: site_overviews(p, sites=sites),
                lambda p=period: timeseries("all", p, "daily", sites=sites),
                lambda p=period: bot_traffic("all", p, sites=sites),
            ]
            for site in sites.iterator():
                scoped = TrackedSite.objects.filter(pk=site.pk)
                calls.extend(
                    [
                        lambda s=site, p=period, q=scoped: overview(s.slug, p, sites=q),
                        lambda s=site, p=period, q=scoped: timeseries(
                            s.slug, p, "daily", sites=q
                        ),
                        lambda s=site, p=period, q=scoped: bot_traffic(s.slug, p, sites=q),
                        lambda s=site, p=period, q=scoped: product_metrics(s.slug, p, sites=q),
                    ]
                )
                for dimension in (
                    "pages",
                    "referrers",
                    "countries",
                    "regions",
                    "cities",
                    "devices",
                    "browsers",
                    "os",
                    "campaigns",
                    "events",
                ):
                    calls.append(
                        lambda s=site, p=period, q=scoped, d=dimension: breakdown(
                            s.slug, p, d, sites=q
                        )
                    )
            for call in calls:
                try:
                    call()
                    refreshed += 1
                except HistoricalDataUnavailable:
                    failed += 1
        message = f"Historical cache refresh completed: refreshed={refreshed}, failed={failed}."
        rendered = self.style.SUCCESS(message) if not failed else self.style.WARNING(message)
        self.stdout.write(rendered)
