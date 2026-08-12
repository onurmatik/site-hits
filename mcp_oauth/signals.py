"""Revocation hooks for ordinary Django model lifecycle operations."""

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import OAuthApplication, OAuthConsent
from .services import (
    revoke_application_oauth_credentials,
    revoke_consent_oauth_credentials,
    revoke_user_oauth_credentials,
)


@receiver(
    post_save,
    sender=get_user_model(),
    dispatch_uid="mcp_oauth.revoke_deactivated_user",
    weak=False,
)
def revoke_deactivated_user(sender, instance, raw=False, **kwargs):
    if not raw and instance.pk is not None and not instance.is_active:
        revoke_user_oauth_credentials(instance)


@receiver(
    post_save,
    sender=OAuthApplication,
    dispatch_uid="mcp_oauth.revoke_application_credentials",
    weak=False,
)
def revoke_application_credentials(sender, instance, raw=False, **kwargs):
    if not raw and instance.pk is not None and instance.revoked_at is not None:
        revoke_application_oauth_credentials(
            instance,
            revoked_at=instance.revoked_at,
        )


@receiver(
    post_save,
    sender=OAuthConsent,
    dispatch_uid="mcp_oauth.revoke_consent_credentials",
    weak=False,
)
def revoke_consent_credentials(sender, instance, raw=False, **kwargs):
    if not raw and instance.pk is not None and instance.revoked_at is not None:
        revoke_consent_oauth_credentials(
            instance,
            revoked_at=instance.revoked_at,
        )
