from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlsplit
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Q
from django.utils import timezone
from django_embedded_mcp.redirects import (
    LOOPBACK_HOSTS,
    redirect_uri_matches,
    validate_registered_redirect_uri,
)
from django_embedded_mcp.cimd import validate_cimd_client_id
from django_embedded_mcp.refresh import (
    RefreshFamilyDecisionCode,
    RefreshFamilyPolicy,
    RefreshFamilyState,
    RefreshMemberState,
)
from oauth2_provider.models import (
    AbstractAccessToken,
    AbstractApplication,
    AbstractGrant,
    AbstractIDToken,
    AbstractRefreshToken,
)


def refresh_family_expiry():
    """Return the fixed Stage 1 absolute refresh-family deadline."""

    return timezone.now() + timedelta(days=30)


def _loopback_redirect_match(registered_uri: str, requested_uri: str) -> bool:
    return redirect_uri_matches(
        registered_uri,
        requested_uri,
        allow_localhost=True,
    )


def _validate_redirect_uri(uri: str) -> None:
    validate_registered_redirect_uri(uri, allow_localhost=True)


class OAuthApplication(AbstractApplication):
    """Public DCR or CIMD client used by the SiteHits authorization server."""

    metadata = models.JSONField(default=dict, blank=True)
    allowed_scopes = models.JSONField(default=list, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_used_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        swappable = "OAUTH2_PROVIDER_APPLICATION_MODEL"
        indexes = [
            models.Index(
                fields=["registration_source", "last_used_at"],
                name="mcp_oauth_app_cleanup_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(client_type=AbstractApplication.CLIENT_PUBLIC),
                name="mcp_oauth_app_public_only",
            ),
            models.CheckConstraint(
                condition=Q(
                    authorization_grant_type=(
                        AbstractApplication.GRANT_AUTHORIZATION_CODE
                    )
                ),
                name="mcp_oauth_app_auth_code_only",
            ),
            models.CheckConstraint(
                condition=Q(skip_authorization=False),
                name="mcp_oauth_app_consent_required",
            ),
            models.CheckConstraint(
                condition=Q(client_secret=""),
                name="mcp_oauth_app_no_secret",
            ),
            models.CheckConstraint(
                condition=Q(hash_client_secret=False),
                name="mcp_oauth_app_no_secret_hash",
            ),
            models.CheckConstraint(
                condition=Q(
                    registration_source__in=(
                        AbstractApplication.RegistrationSource.DCR,
                        AbstractApplication.RegistrationSource.CIMD,
                    )
                ),
                name="mcp_oauth_app_source_supported",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        registration_source=AbstractApplication.RegistrationSource.DCR,
                        cimd_expires_at__isnull=True,
                    )
                    | Q(
                        registration_source=AbstractApplication.RegistrationSource.CIMD,
                        cimd_expires_at__isnull=False,
                    )
                ),
                name="mcp_oauth_app_cimd_expiry_ck",
            ),
        ]

    def clean(self):
        super().clean()
        errors: dict[str, str] = {}
        if self.client_type != self.CLIENT_PUBLIC:
            errors["client_type"] = "Only public OAuth clients are supported."
        if self.authorization_grant_type != self.GRANT_AUTHORIZATION_CODE:
            errors["authorization_grant_type"] = (
                "Only the authorization-code grant is supported."
            )
        if self.client_secret:
            errors["client_secret"] = "Public clients must not have a client secret."
        if self.hash_client_secret:
            errors["hash_client_secret"] = "Public clients do not store a client secret."
        if self.skip_authorization:
            errors["skip_authorization"] = "Every authorization requires explicit consent."
        if self.registration_source not in {
            self.RegistrationSource.DCR,
            self.RegistrationSource.CIMD,
        }:
            errors["registration_source"] = "Only DCR and CIMD clients are supported."
        if self.registration_source == self.RegistrationSource.CIMD:
            try:
                validate_cimd_client_id(self.client_id)
            except ValueError as exc:
                errors["client_id"] = str(exc)
            if self.cimd_expires_at is None:
                errors["cimd_expires_at"] = "CIMD clients require a cache expiry."
        elif self.cimd_expires_at is not None:
            errors["cimd_expires_at"] = "DCR clients must not carry CIMD cache state."
        elif self.client_id.startswith("https://"):
            errors["client_id"] = "URL-shaped client IDs are reserved for CIMD."
        if not isinstance(self.allowed_scopes, list) or not self.allowed_scopes:
            errors["allowed_scopes"] = "At least one registered scope is required."
        metadata = self.metadata if isinstance(self.metadata, dict) else {}
        application_type = metadata.get("application_type")
        if application_type not in {"web", "native"}:
            errors["metadata"] = "A web or native application_type is required."
        redirect_uris = self.redirect_uris.split()
        for uri in redirect_uris:
            try:
                _validate_redirect_uri(uri)
            except ValueError as exc:
                errors["redirect_uris"] = str(exc)
                break
        if application_type == "web" and not all(
            urlsplit(uri).scheme == "https" for uri in redirect_uris
        ):
            errors["redirect_uris"] = "Web clients require HTTPS redirect URIs."
        if application_type == "native" and not all(
            urlsplit(uri).scheme == "http"
            and urlsplit(uri).hostname in LOOPBACK_HOSTS | {"localhost"}
            for uri in redirect_uris
        ):
            errors["redirect_uris"] = "Native clients require HTTP loopback redirect URIs."
        if errors:
            raise ValidationError(errors)

    def redirect_uri_allowed(self, uri):
        if self.registration_source == self.RegistrationSource.CIMD:
            return uri in self.redirect_uris.split()
        return any(
            registered == uri or _loopback_redirect_match(registered, uri)
            for registered in self.redirect_uris.split()
        )

    def is_usable(self, request):
        source_is_usable = self.registration_source in {
            self.RegistrationSource.DCR,
            self.RegistrationSource.CIMD,
        }
        cimd_is_fresh = (
            self.registration_source != self.RegistrationSource.CIMD
            or (
                self.cimd_expires_at is not None
                and self.cimd_expires_at > timezone.now()
            )
        )
        return (
            self.revoked_at is None
            and self.client_type == self.CLIENT_PUBLIC
            and self.authorization_grant_type == self.GRANT_AUTHORIZATION_CODE
            and source_is_usable
            and cimd_is_fresh
        )

    def revoke(self):
        """Revoke this client and all current OAuth credentials server-side."""

        from .services import revoke_application_oauth_credentials

        revoke_application_oauth_credentials(self)
        self.refresh_from_db(fields=["revoked_at"])


class OAuthGrant(AbstractGrant):
    """Digest-only, single-use authorization code grant."""

    # DOT's default model stores the raw authorization code. The SiteHits validator
    # stores it only as code_digest and leaves this compatibility field empty.
    code = models.CharField(max_length=1, blank=True, default="", editable=False)
    code_digest = models.CharField(max_length=64, unique=True, editable=False)
    consumed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    replayed_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        swappable = "OAUTH2_PROVIDER_GRANT_MODEL"
        indexes = [
            models.Index(fields=["expires", "consumed_at"], name="mcp_oauth_grant_cleanup_idx"),
            models.Index(fields=["replayed_at"], name="mcp_oauth_grant_replay_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(code=""),
                name="mcp_oauth_grant_digest_only",
            ),
            models.CheckConstraint(
                condition=Q(consumed_at__isnull=True) | Q(consumed_at__gte=F("created")),
                name="mcp_oauth_grant_time_ck",
            ),
            models.CheckConstraint(
                condition=Q(replayed_at__isnull=True) | Q(replayed_at__gte=F("consumed_at")),
                name="mcp_oauth_grant_replay_ck",
            ),
        ]


class OAuthIDToken(AbstractIDToken):
    """Swapped ID-token model required to keep the OAuth model graph in app 0001.

    OIDC remains disabled for the MCP profile; the model exists because DOT's
    abstract access-token model has a relation to the configured ID-token model.
    """

    class Meta:
        swappable = "OAUTH2_PROVIDER_ID_TOKEN_MODEL"


class OAuthAccessToken(AbstractAccessToken):
    """Checksum-only access token with explicit revocation state."""

    revoked_at = models.DateTimeField(null=True, blank=True, db_index=True)
    authorization_code_digest = models.CharField(
        max_length=64,
        blank=True,
        editable=False,
        db_index=True,
    )

    class Meta:
        swappable = "OAUTH2_PROVIDER_ACCESS_TOKEN_MODEL"
        indexes = [
            models.Index(fields=["expires", "revoked_at"], name="mcp_oauth_access_cleanup_idx"),
            models.Index(
                fields=["application", "user", "revoked_at"],
                name="mcp_oauth_access_principal_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(token=""),
                name="mcp_oauth_access_digest_only",
            ),
            models.CheckConstraint(
                condition=Q(revoked_at__isnull=True) | Q(revoked_at__gte=F("created")),
                name="mcp_oauth_access_time_ck",
            ),
        ]

    def is_valid(self, scopes=None):
        if not (
            self.revoked_at is None
            and self.user is not None
            and self.user.is_active
            and self.application is not None
            and self.application.is_usable(None)
            and self.resource == [settings.SITEHITS_MCP_RESOURCE_URL]
            and super().is_valid(scopes)
        ):
            return False
        granted_scope_sets = OAuthConsent.objects.filter(
            user_id=self.user_id,
            application_id=self.application_id,
            resource=settings.SITEHITS_MCP_RESOURCE_URL,
            decision=OAuthConsent.Decision.APPROVED,
            revoked_at__isnull=True,
        ).values_list("scopes", flat=True)
        token_scopes = set(self.scope.split())
        return any(
            token_scopes.issubset(set(granted_scopes))
            for granted_scopes in granted_scope_sets
        )

    def allows_audience(self, audience_uri):
        expected = settings.SITEHITS_MCP_RESOURCE_URL
        return audience_uri == expected and self.resource == [expected]

    def revoke(self):
        now = timezone.now()
        type(self).objects.filter(pk=self.pk, revoked_at__isnull=True).update(
            revoked_at=now
        )
        self.revoked_at = self.revoked_at or now


class OAuthRefreshFamily(models.Model):
    """Durable lock target and absolute state for one rotating token family."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mcp_oauth_refresh_families",
    )
    application = models.ForeignKey(
        settings.OAUTH2_PROVIDER_APPLICATION_MODEL,
        on_delete=models.CASCADE,
        related_name="sitehits_refresh_families",
    )
    resource = models.TextField()
    authorization_code_digest = models.CharField(
        max_length=64,
        blank=True,
        editable=False,
        db_index=True,
    )
    expires_at = models.DateTimeField(default=refresh_family_expiry, db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["application", "user", "revoked_at"],
                name="mcp_oauth_family_principal_idx",
            ),
            models.Index(
                fields=["expires_at", "revoked_at"],
                name="mcp_oauth_family_cleanup_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(expires_at__gt=F("created_at")),
                name="mcp_oauth_family_expiry_ck",
            ),
            models.CheckConstraint(
                condition=Q(revoked_at__isnull=True) | Q(revoked_at__gte=F("created_at")),
                name="mcp_oauth_family_revoke_ck",
            ),
        ]

    def is_active(self, *, now=None):
        now = now or timezone.now()
        expected_resource = settings.SITEHITS_MCP_RESOURCE_URL
        decision = RefreshFamilyPolicy(
            expected_resource=expected_resource,
        ).evaluate_family(
            family=RefreshFamilyState(
                family_id=self.pk,
                user_id=self.user_id,
                client_id=self.application_id,
                resource=self.resource,
                expires_at=self.expires_at,
                revoked_at=self.revoked_at,
            ),
            requested_resources=(expected_resource,),
            now=now,
        )
        return decision.rotation_allowed

    def revoke(self):
        """Revoke the family under its durable row lock and cascade to members."""

        with transaction.atomic():
            family = type(self).objects.select_for_update().get(pk=self.pk)
            # Take the transition timestamp only after the lock is acquired.
            # Otherwise a concurrent member insert could commit with created_at
            # later than this value and make revoked >= created fail.
            transition_at = timezone.now()
            if family.revoked_at is None:
                type(self).objects.filter(pk=family.pk).update(
                    revoked_at=transition_at
                )
                family.revoked_at = transition_at
            family._revoke_members(transition_at)
        self.revoked_at = family.revoked_at

    def _revoke_members(self, revoked_at):
        refresh_tokens = OAuthRefreshToken.objects.select_for_update().filter(
            family_state_id=self.pk
        )
        access_ids = list(
            refresh_tokens.exclude(access_token_id=None).values_list(
                "access_token_id", flat=True
            )
        )
        refresh_tokens.update(
            revoked=models.Case(
                models.When(revoked__isnull=True, then=models.Value(revoked_at)),
                default=F("revoked"),
            ),
            family_revoked_at=models.Case(
                models.When(
                    family_revoked_at__isnull=True,
                    then=models.Value(revoked_at),
                ),
                default=F("family_revoked_at"),
            ),
            access_token=None,
        )
        if access_ids:
            OAuthAccessToken.objects.filter(
                pk__in=access_ids,
                revoked_at__isnull=True,
            ).update(revoked_at=revoked_at)


class OAuthRefreshToken(AbstractRefreshToken):
    """Rotating checksum-only token with an absolute family lifetime."""

    family_expires_at = models.DateTimeField(default=refresh_family_expiry, db_index=True)
    family_revoked_at = models.DateTimeField(null=True, blank=True, db_index=True)
    family_state = models.ForeignKey(
        OAuthRefreshFamily,
        on_delete=models.CASCADE,
        related_name="tokens",
        editable=False,
    )
    authorization_code_digest = models.CharField(
        max_length=64,
        blank=True,
        editable=False,
        db_index=True,
    )

    class Meta:
        swappable = "OAUTH2_PROVIDER_REFRESH_TOKEN_MODEL"
        indexes = [
            models.Index(
                fields=["token_family", "family_revoked_at", "revoked"],
                name="mcp_oauth_refresh_family_idx",
            ),
            models.Index(
                fields=["family_expires_at", "revoked"],
                name="mcp_oauth_refresh_cleanup_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["token_checksum"],
                name="mcp_oauth_refresh_sum_uniq",
            ),
            models.CheckConstraint(
                condition=Q(token=""),
                name="mcp_oauth_refresh_digest_only",
            ),
            models.CheckConstraint(
                condition=Q(revoked__isnull=True) | Q(revoked__gte=F("created")),
                name="mcp_oauth_refresh_time_ck",
            ),
            models.CheckConstraint(
                condition=(
                    Q(token_family__isnull=False)
                    & Q(token_family=F("family_state_id"))
                ),
                name="mcp_oauth_refresh_family_identity_ck",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            return super().save(*args, **kwargs)
        with transaction.atomic():
            token_resources = list(self.resource or [])
            if len(token_resources) != 1:
                raise ValidationError(
                    "Refresh tokens require exactly one resource binding."
                )
            token_resource = token_resources[0]
            family_id = self.family_state_id or self.token_family or uuid4()
            family, _ = OAuthRefreshFamily.objects.get_or_create(
                pk=family_id,
                defaults={
                    "user_id": self.user_id,
                    "application_id": self.application_id,
                    "resource": token_resource,
                    "authorization_code_digest": self.authorization_code_digest,
                    "expires_at": self.family_expires_at,
                },
            )
            family = OAuthRefreshFamily.objects.select_for_update().get(pk=family.pk)
            policy = RefreshFamilyPolicy(
                expected_resource=settings.SITEHITS_MCP_RESOURCE_URL,
            )
            family_state = RefreshFamilyState(
                family_id=family.pk,
                user_id=family.user_id,
                client_id=family.application_id,
                resource=family.resource,
                expires_at=family.expires_at,
                revoked_at=family.revoked_at,
            )
            binding_decision = policy.evaluate_member_binding(
                family=family_state,
                member=RefreshMemberState(
                    user_id=self.user_id,
                    client_id=self.application_id,
                    family_id=family.pk,
                    family_mirror_id=family_id,
                    resources=(token_resource,),
                    consumed_at=self.revoked,
                ),
            )
            if not binding_decision.rotation_allowed:
                raise ValidationError("Refresh token does not match its family binding.")
            self.family_state = family
            self.token_family = family.pk
            self.family_expires_at = family.expires_at
            self.family_revoked_at = family.revoked_at
            terminal_at = self.revoked
            lifecycle_decision = policy.evaluate_family(
                family=family_state,
                requested_resources=(settings.SITEHITS_MCP_RESOURCE_URL,),
                now=timezone.now(),
            )
            if lifecycle_decision.code in {
                RefreshFamilyDecisionCode.FAMILY_REVOKED,
                RefreshFamilyDecisionCode.FAMILY_EXPIRED,
            }:
                terminal_at = terminal_at or family.revoked_at or timezone.now()
            # Insert first, then mark terminal in this same transaction.  The
            # inherited ``created`` timestamp is assigned during INSERT, so
            # pre-populating ``revoked`` could violate revoked >= created by a
            # few microseconds for members born into a revoked family.
            self.revoked = None
            result = super().save(*args, **kwargs)
            if terminal_at is not None:
                terminal_at = max(terminal_at, self.created)
                type(self).objects.filter(pk=self.pk).update(
                    revoked=terminal_at,
                    family_revoked_at=family.revoked_at,
                )
                self.revoked = terminal_at
            if terminal_at is not None and self.access_token_id is not None:
                OAuthAccessToken.objects.filter(
                    pk=self.access_token_id,
                    revoked_at__isnull=True,
                ).update(revoked_at=terminal_at)
            return result

    def is_family_expired(self):
        expires_at = (
            self.family_state.expires_at
            if self.family_state_id is not None
            else self.family_expires_at
        )
        return RefreshFamilyPolicy.family_is_expired(
            expires_at=expires_at,
            now=timezone.now(),
        )

    def revoke(self):
        """Consume one refresh token during ordinary rotation."""

        now = timezone.now()
        with transaction.atomic():
            # Every refresh transition follows the same parent-before-member
            # lock order.  That makes rotation, replay revocation, and a new
            # family member insertion serializable with respect to each other.
            OAuthRefreshFamily.objects.select_for_update().get(
                pk=self.family_state_id
            )
            family = type(self).objects.select_for_update().filter(pk=self.pk)
            access_ids = list(
                family.exclude(access_token_id=None).values_list("access_token_id", flat=True)
            )
            family.update(
                revoked=models.Case(
                    models.When(revoked__isnull=True, then=models.Value(now)),
                    default=F("revoked"),
                ),
                access_token=None,
            )
            if access_ids:
                OAuthAccessToken.objects.filter(
                    pk__in=access_ids,
                    revoked_at__isnull=True,
                ).update(revoked_at=now)
        self.revoked = self.revoked or now
        self.access_token_id = None

    def revoke_family(self):
        """Revoke every current and future member after replay or compromise."""

        if self.family_state_id is not None:
            self.family_state.revoke()
            self.family_revoked_at = self.family_state.revoked_at
            self.revoked = self.revoked or self.family_revoked_at
            self.access_token_id = None
            return
        now = timezone.now()
        with transaction.atomic():
            family = type(self).objects.select_for_update()
            if self.token_family is None:
                family = family.filter(pk=self.pk)
            else:
                family = family.filter(token_family=self.token_family)
            access_ids = list(
                family.exclude(access_token_id=None).values_list("access_token_id", flat=True)
            )
            family.update(
                revoked=models.Case(
                    models.When(revoked__isnull=True, then=models.Value(now)),
                    default=F("revoked"),
                ),
                family_revoked_at=models.Case(
                    models.When(family_revoked_at__isnull=True, then=models.Value(now)),
                    default=F("family_revoked_at"),
                ),
                access_token=None,
            )
            if access_ids:
                OAuthAccessToken.objects.filter(
                    pk__in=access_ids,
                    revoked_at__isnull=True,
                ).update(revoked_at=now)
        self.revoked = self.revoked or now
        self.family_revoked_at = self.family_revoked_at or now
        self.access_token_id = None


class OAuthConsent(models.Model):
    class Decision(models.TextChoices):
        APPROVED = "approved", "Approved"
        DENIED = "denied", "Denied"

    request_id = models.UUIDField(default=uuid4, unique=True, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mcp_oauth_consents",
    )
    application = models.ForeignKey(
        settings.OAUTH2_PROVIDER_APPLICATION_MODEL,
        on_delete=models.CASCADE,
        related_name="sitehits_consents",
    )
    resource = models.TextField()
    scopes = models.JSONField(default=list)
    redirect_uri_digest = models.CharField(max_length=64, editable=False)
    decision = models.CharField(max_length=16, choices=Decision.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["user", "application", "revoked_at"],
                name="mcp_oauth_consent_user_idx",
            ),
            models.Index(fields=["created_at"], name="mcp_oauth_consent_cleanup_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(revoked_at__isnull=True) | Q(revoked_at__gte=F("created_at")),
                name="mcp_oauth_consent_time_ck",
            ),
        ]

    def revoke(self):
        """Withdraw consent and revoke all credentials it authorized."""

        from .services import revoke_consent_oauth_credentials

        revoke_consent_oauth_credentials(self)
        self.refresh_from_db(fields=["revoked_at"])


class OAuthSecurityEvent(models.Model):
    request_id = models.UUIDField(default=uuid4, db_index=True, editable=False)
    event = models.CharField(max_length=64, db_index=True)
    outcome = models.CharField(max_length=32, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mcp_oauth_security_events",
    )
    application = models.ForeignKey(
        settings.OAUTH2_PROVIDER_APPLICATION_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sitehits_security_events",
    )
    resource = models.TextField(blank=True)
    scopes = models.JSONField(default=list, blank=True)
    subject_digest = models.CharField(max_length=64, blank=True)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["event", "created_at"], name="mcp_oauth_event_type_idx"),
            models.Index(
                fields=["application", "created_at"],
                name="mcp_oauth_event_client_idx",
            ),
        ]


class OAuthRateLimitBucket(models.Model):
    action = models.CharField(max_length=32)
    subject_digest = models.CharField(max_length=64)
    window_started_at = models.DateTimeField()
    window_ends_at = models.DateTimeField(db_index=True)
    count = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["action", "subject_digest", "window_started_at"],
                name="mcp_oauth_rate_bucket_unique",
            ),
            models.CheckConstraint(
                condition=Q(window_ends_at__gt=F("window_started_at")),
                name="mcp_oauth_rate_window_order",
            ),
        ]
        indexes = [
            models.Index(fields=["window_ends_at"], name="mcp_oauth_rate_cleanup_idx"),
        ]


class OAuthCleanupRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    job_id = models.UUIDField(default=uuid4, unique=True, editable=False)
    status = models.CharField(max_length=16, choices=Status.choices)
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    deleted = models.JSONField(default=dict, blank=True)
    errors = models.PositiveIntegerField(default=0)
    duration_ms = models.PositiveBigIntegerField(default=0)
    oldest_eligible_at = models.DateTimeField(null=True, blank=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "started_at"], name="mcp_oauth_cleanup_status_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(finished_at__isnull=True) | Q(finished_at__gte=F("started_at")),
                name="mcp_oauth_cleanup_time_order",
            ),
        ]
