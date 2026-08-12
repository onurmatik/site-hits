# django-embedded-mcp

`django-embedded-mcp` is the reusable protocol layer for Django products that expose
an OAuth-protected MCP server. It owns byte-exact resource handling, public-client
redirect policy, fail-closed Client ID Metadata Document fetching and validation,
the Dynamic Client Registration fallback policy, OAuth metadata, Bearer challenges,
header-only bearer enforcement, digest-backed token verification, and the stable MCP
SDK configuration seam.

The host product intentionally retains:

- product branding, public URLs, and environment configuration;
- the canonical Agent Contract and deterministic tool registry;
- Django user, consent, grant, token, and audit models;
- capability, ownership, approval, and service-dispatch behavior.

The public modules accept those product values through explicit arguments. They do
not read product-specific settings and do not call private Django OAuth Toolkit APIs.
