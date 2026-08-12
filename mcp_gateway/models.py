import hashlib
import hmac
import secrets
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.db import models
from django.utils import timezone


class MCPAccessToken(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mcp_access_tokens",
    )
    name = models.CharField(max_length=120)
    prefix = models.CharField(max_length=16, db_index=True)
    token_digest = models.CharField(max_length=64, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]  # noqa: RUF012 - Django migration state uses a list.

    def __str__(self):
        return f"{self.user}: {self.name} ({self.prefix}…)"

    @staticmethod
    def digest(raw_token):
        return hmac.new(
            settings.SITEHITS_MCP_TOKEN_SECRET.encode("utf-8"),
            raw_token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @classmethod
    def issue(cls, *, user, name="MCP client", expires_at=None):
        raw_token = f"shm_{secrets.token_urlsafe(32)}"
        token = cls.objects.create(
            user=user,
            name=name,
            prefix=raw_token[:12],
            token_digest=cls.digest(raw_token),
            expires_at=expires_at,
        )
        return token, raw_token

    @classmethod
    def authenticate(cls, raw_token):
        if not raw_token.startswith("shm_") or len(raw_token) < 24:
            return None
        token = (
            cls.objects.select_related("user")
            .filter(token_digest=cls.digest(raw_token), revoked_at__isnull=True)
            .first()
        )
        now = timezone.now()
        if token is None or not token.user.is_active:
            return None
        if token.expires_at and token.expires_at <= now:
            return None
        if token.last_used_at is None or token.last_used_at < now - timedelta(minutes=5):
            cls.objects.filter(pk=token.pk).update(last_used_at=now)
            token.last_used_at = now
        return token


class MCPOAuthClient(models.Model):
    client_id = models.CharField(max_length=512, unique=True)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]  # noqa: RUF012 - Django migration state uses a list.

    def __str__(self):
        return self.metadata.get("client_name") or self.client_id


class MCPOAuthAuthorizationRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    client = models.ForeignKey(MCPOAuthClient, on_delete=models.CASCADE)
    redirect_uri = models.URLField(max_length=2048)
    redirect_uri_provided_explicitly = models.BooleanField(default=True)
    scopes = models.JSONField(default=list)
    resource = models.URLField(max_length=2048)
    state = models.TextField(blank=True)
    code_challenge = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Authorization request for {self.client}"


class MCPOAuthAuthorizationCode(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mcp_oauth_authorization_codes",
    )
    client = models.ForeignKey(MCPOAuthClient, on_delete=models.CASCADE)
    prefix = models.CharField(max_length=16, db_index=True)
    code_digest = models.CharField(max_length=64, unique=True, editable=False)
    redirect_uri = models.URLField(max_length=2048)
    redirect_uri_provided_explicitly = models.BooleanField(default=True)
    scopes = models.JSONField(default=list)
    resource = models.URLField(max_length=2048)
    code_challenge = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]  # noqa: RUF012 - Django migration state uses a list.

    @classmethod
    def issue(cls, *, authorization_request, user):
        raw_code = f"shc_{secrets.token_urlsafe(32)}"
        code = cls.objects.create(
            user=user,
            client=authorization_request.client,
            prefix=raw_code[:12],
            code_digest=MCPAccessToken.digest(raw_code),
            redirect_uri=authorization_request.redirect_uri,
            redirect_uri_provided_explicitly=(
                authorization_request.redirect_uri_provided_explicitly
            ),
            scopes=authorization_request.scopes,
            resource=authorization_request.resource,
            code_challenge=authorization_request.code_challenge,
            expires_at=timezone.now()
            + timedelta(
                seconds=getattr(
                    settings,
                    "SITEHITS_MCP_AUTHORIZATION_CODE_TTL_SECONDS",
                    300,
                )
            ),
        )
        return code, raw_code

    def __str__(self):
        return f"{self.client}: {self.prefix}…"


class MCPOAuthRefreshToken(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mcp_oauth_refresh_tokens",
    )
    client = models.ForeignKey(MCPOAuthClient, on_delete=models.CASCADE)
    prefix = models.CharField(max_length=16, db_index=True)
    token_digest = models.CharField(max_length=64, unique=True, editable=False)
    scopes = models.JSONField(default=list)
    resource = models.URLField(max_length=2048)
    family_id = models.UUIDField(default=uuid4, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]  # noqa: RUF012 - Django migration state uses a list.

    @classmethod
    def issue(cls, *, user, client, scopes, resource, family_id=None):
        raw_token = f"shr_{secrets.token_urlsafe(32)}"
        token = cls.objects.create(
            user=user,
            client=client,
            prefix=raw_token[:12],
            token_digest=MCPAccessToken.digest(raw_token),
            scopes=scopes,
            resource=resource,
            family_id=family_id or uuid4(),
            expires_at=timezone.now()
            + timedelta(
                seconds=getattr(
                    settings,
                    "SITEHITS_MCP_REFRESH_TOKEN_TTL_SECONDS",
                    2_592_000,
                )
            ),
        )
        return token, raw_token

    def __str__(self):
        return f"{self.client}: {self.prefix}…"


class MCPOAuthAccessToken(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mcp_oauth_access_tokens",
    )
    client = models.ForeignKey(MCPOAuthClient, on_delete=models.CASCADE)
    prefix = models.CharField(max_length=16, db_index=True)
    token_digest = models.CharField(max_length=64, unique=True, editable=False)
    scopes = models.JSONField(default=list)
    resource = models.URLField(max_length=2048)
    family_id = models.UUIDField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]  # noqa: RUF012 - Django migration state uses a list.

    @classmethod
    def issue(cls, *, user, client, scopes, resource, family_id):
        raw_token = f"sho_{secrets.token_urlsafe(32)}"
        token = cls.objects.create(
            user=user,
            client=client,
            prefix=raw_token[:12],
            token_digest=MCPAccessToken.digest(raw_token),
            scopes=scopes,
            resource=resource,
            family_id=family_id,
            expires_at=timezone.now()
            + timedelta(
                seconds=getattr(
                    settings,
                    "SITEHITS_MCP_ACCESS_TOKEN_TTL_SECONDS",
                    3600,
                )
            ),
        )
        return token, raw_token

    @classmethod
    def authenticate(cls, raw_token):
        if not raw_token.startswith("sho_") or len(raw_token) < 24:
            return None
        token = (
            cls.objects.select_related("user", "client")
            .filter(
                token_digest=MCPAccessToken.digest(raw_token),
                revoked_at__isnull=True,
                client__revoked_at__isnull=True,
            )
            .first()
        )
        now = timezone.now()
        if token is None or not token.user.is_active:
            return None
        if token.expires_at <= now or token.resource != settings.SITEHITS_MCP_RESOURCE_URL:
            return None
        if token.last_used_at is None or token.last_used_at < now - timedelta(minutes=5):
            cls.objects.filter(pk=token.pk).update(last_used_at=now)
            token.last_used_at = now
        return token

    def __str__(self):
        return f"{self.client}: {self.prefix}…"
