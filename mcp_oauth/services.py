"""Transactional server-side OAuth revocation services."""

from django.db import transaction
from django.utils import timezone


def _revoke_refresh_families(queryset):
    family_state_ids = list(
        queryset.exclude(family_state=None)
        .order_by("family_state_id")
        .values_list("family_state_id", flat=True)
        .distinct()
    )
    from .models import OAuthRefreshFamily

    for family_state in OAuthRefreshFamily.objects.filter(
        pk__in=family_state_ids
    ).iterator():
        family_state.revoke()
    for refresh_token in queryset.filter(family_state=None).iterator():
        refresh_token.revoke_family()


def revoke_user_oauth_credentials(user, *, revoked_at=None):
    """Revoke OAuth state immediately when a user is deactivated."""

    from .models import (
        OAuthAccessToken,
        OAuthConsent,
        OAuthGrant,
        OAuthRefreshToken,
    )

    now = revoked_at or timezone.now()
    with transaction.atomic():
        OAuthGrant.objects.filter(
            user_id=user.pk,
            consumed_at__isnull=True,
        ).update(consumed_at=now)
        _revoke_refresh_families(
            OAuthRefreshToken.objects.filter(
                user_id=user.pk,
                family_revoked_at__isnull=True,
            )
        )
        OAuthAccessToken.objects.filter(
            user_id=user.pk,
            revoked_at__isnull=True,
        ).update(revoked_at=now)
        OAuthConsent.objects.filter(
            user_id=user.pk,
            revoked_at__isnull=True,
        ).update(revoked_at=now)


def revoke_application_oauth_credentials(application, *, revoked_at=None):
    """Revoke an application and every credential issued to it."""

    from .models import (
        OAuthAccessToken,
        OAuthApplication,
        OAuthConsent,
        OAuthGrant,
        OAuthRefreshToken,
    )

    now = timezone.now()
    application_revoked_at = revoked_at or now
    with transaction.atomic():
        OAuthApplication.objects.select_for_update().filter(pk=application.pk).update(
            revoked_at=application_revoked_at
        )
        OAuthGrant.objects.filter(
            application_id=application.pk,
            consumed_at__isnull=True,
        ).update(consumed_at=now)
        _revoke_refresh_families(
            OAuthRefreshToken.objects.filter(
                application_id=application.pk,
                family_revoked_at__isnull=True,
            )
        )
        OAuthAccessToken.objects.filter(
            application_id=application.pk,
            revoked_at__isnull=True,
        ).update(revoked_at=now)
        OAuthConsent.objects.filter(
            application_id=application.pk,
            revoked_at__isnull=True,
        ).update(revoked_at=now)


def revoke_consent_oauth_credentials(consent, *, revoked_at=None):
    """Withdraw consent and revoke all credentials authorized for its binding."""

    from .models import (
        OAuthAccessToken,
        OAuthConsent,
        OAuthGrant,
        OAuthRefreshToken,
    )

    now = timezone.now()
    consent_revoked_at = revoked_at or now
    binding = {
        "user_id": consent.user_id,
        "application_id": consent.application_id,
        "resource": [consent.resource],
    }
    with transaction.atomic():
        OAuthConsent.objects.select_for_update().filter(pk=consent.pk).update(
            revoked_at=consent_revoked_at
        )
        OAuthGrant.objects.filter(
            **binding,
            consumed_at__isnull=True,
        ).update(consumed_at=now)
        _revoke_refresh_families(
            OAuthRefreshToken.objects.filter(
                **binding,
                family_revoked_at__isnull=True,
            )
        )
        OAuthAccessToken.objects.filter(
            **binding,
            revoked_at__isnull=True,
        ).update(revoked_at=now)
