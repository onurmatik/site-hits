from django.db.models.signals import post_save, pre_delete, pre_save
from django.dispatch import receiver

from websites.models import TrackedSite

from .archive import invalidate_historical_cache
from .models import (
    ColdDataTombstone,
    ColdDeletionJob,
    DailyAnalyticsRollup,
    DailyBotRollup,
    DailyDimensionRollup,
    DailyProductEventRollup,
)


@receiver(
    pre_save,
    sender=TrackedSite,
    dispatch_uid="analytics.capture_site_timezone",
    weak=False,
)
def capture_site_timezone(sender, instance, **kwargs):
    if instance.pk is None:
        instance._sitehits_previous_timezone = None
        return
    instance._sitehits_previous_timezone = (
        sender.objects.filter(pk=instance.pk).values_list("timezone", flat=True).first()
    )


@receiver(
    post_save,
    sender=TrackedSite,
    dispatch_uid="analytics.invalidate_timezone_rollups",
    weak=False,
)
def invalidate_timezone_rollups(sender, instance, created, **kwargs):
    previous = getattr(instance, "_sitehits_previous_timezone", None)
    if created or previous is None or previous == instance.timezone:
        return
    DailyAnalyticsRollup.objects.filter(site=instance).delete()
    DailyDimensionRollup.objects.filter(site=instance).delete()
    DailyProductEventRollup.objects.filter(site=instance).delete()
    DailyBotRollup.objects.filter(site=instance).delete()
    invalidate_historical_cache(instance.pk)


@receiver(
    pre_delete,
    sender=TrackedSite,
    dispatch_uid="analytics.queue_cold_site_deletion",
    weak=False,
)
def queue_cold_site_deletion(sender, instance, **kwargs):
    if instance.pk is None:
        return
    ColdDataTombstone.objects.get_or_create(
        site_id_snapshot=instance.pk,
        kind=ColdDataTombstone.Kind.SITE,
        actor_hash="",
        status=ColdDataTombstone.Status.PENDING,
        defaults={"site": None},
    )
    ColdDeletionJob.objects.get_or_create(
        site_id_snapshot=instance.pk,
        kind=ColdDeletionJob.Kind.SITE,
        actor_hash="",
        status=ColdDeletionJob.Status.ACCEPTED,
        defaults={"site": None},
    )
