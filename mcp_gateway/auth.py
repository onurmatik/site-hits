"""SiteHits configuration adapter for package-owned bearer verification."""

from django.conf import settings
from django_embedded_mcp.tokens import DigestDjangoOAuthToolkitTokenVerifier


class DjangoOAuthToolkitTokenVerifier(DigestDjangoOAuthToolkitTokenVerifier):
    """Bind reusable digest verification to SiteHits identity configuration."""

    def __init__(self):
        super().__init__(
            resource=settings.SITEHITS_MCP_RESOURCE_URL,
            issuer=settings.SITEHITS_MCP_ISSUER_URL,
            allowed_scopes=settings.SITEHITS_MCP_OAUTH_SCOPES,
        )


token_verifier = DjangoOAuthToolkitTokenVerifier()
