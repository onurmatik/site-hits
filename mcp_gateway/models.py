"""Historical provisional metadata retained only for bounded Stage 1 cleanup.

No model in this module can issue or authenticate a credential. The active OAuth
provider lives in ``mcp_oauth`` and the runtime verifier accepts only its records.
"""

from uuid import uuid4

from django.conf import settings
from django.db import models


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

    def __str__(self):
        return f"{self.client}: {self.prefix}…"
