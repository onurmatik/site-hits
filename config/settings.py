import json
import os
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_value(*names, default=""):
    for name in names:
        value = os.environ.get(name)
        if value is not None:
            return value
    return default


def env_list(name, *, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


def validate_service_url(name, value, *, path):
    """Validate a configured public identity without rewriting a single byte."""

    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name} must be a valid absolute URL.") from exc
    allowed_schemes = {"http", "https"} if DEBUG else {"https"}
    if parsed.scheme not in allowed_schemes or not parsed.hostname:
        requirement = "HTTP(S)" if DEBUG else "HTTPS"
        raise ImproperlyConfigured(f"{name} must be an absolute {requirement} URL.")
    if parsed.username is not None or parsed.password is not None:
        raise ImproperlyConfigured(f"{name} must not contain userinfo.")
    if parsed.query or parsed.fragment:
        raise ImproperlyConfigured(f"{name} must not contain a query or fragment.")
    if parsed.path != path or value.endswith("/"):
        expected = path or "an origin without a path or trailing slash"
        raise ImproperlyConfigured(f"{name} must use {expected} exactly.")
    if parsed.scheme != parsed.scheme.lower() or parsed.netloc != parsed.netloc.lower():
        raise ImproperlyConfigured(f"{name} scheme and authority must be lowercase.")
    return value


def url_origin(value):
    parsed = urlsplit(value)
    return parsed.scheme, parsed.hostname, parsed.port


def validate_cors_origins(origins):
    if not origins:
        raise ImproperlyConfigured("SITEHITS_MCP_CORS_ORIGINS must not be empty.")
    for origin in origins:
        if origin == "*":
            if DEBUG:
                continue
            raise ImproperlyConfigured(
                "SITEHITS_MCP_CORS_ORIGINS must be an explicit production allowlist."
            )
        try:
            parsed = urlsplit(origin)
            _ = parsed.port
        except ValueError as exc:
            raise ImproperlyConfigured(
                "SITEHITS_MCP_CORS_ORIGINS contains an invalid origin."
            ) from exc
        allowed_schemes = {"http", "https"} if DEBUG else {"https"}
        if (
            parsed.scheme not in allowed_schemes
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ImproperlyConfigured(
                "SITEHITS_MCP_CORS_ORIGINS entries must be exact origins."
            )

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "unsafe-local-sitehits-secret")
DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() in {"1", "true", "yes"}
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",")
    if host.strip()
]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "oauth2_provider",
    "mcp_oauth",
    "websites",
    "analytics",
    "dashboard",
    "mcp_gateway",
]

OAUTH2_PROVIDER_APPLICATION_MODEL = "mcp_oauth.OAuthApplication"
OAUTH2_PROVIDER_GRANT_MODEL = "mcp_oauth.OAuthGrant"
OAUTH2_PROVIDER_ACCESS_TOKEN_MODEL = "mcp_oauth.OAuthAccessToken"
OAUTH2_PROVIDER_REFRESH_TOKEN_MODEL = "mcp_oauth.OAuthRefreshToken"
OAUTH2_PROVIDER_ID_TOKEN_MODEL = "mcp_oauth.OAuthIDToken"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "mcp_gateway.middleware.DjangoRequestCorrelationMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "analytics.middleware.EventCorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "mcp_gateway.middleware.OAuthNoStoreMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
DEFAULT_EXCEPTION_REPORTER = "mcp_gateway.exception_reporting.SiteHitsExceptionReporter"
DEFAULT_EXCEPTION_REPORTER_FILTER = (
    "mcp_gateway.exception_reporting.SiteHitsExceptionReporterFilter"
)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"null": {"class": "logging.NullHandler"}},
    "loggers": {
        # Pinned DOT/oauthlib DEBUG records serialize callback URLs, grants,
        # OAuth requests, and token dictionaries. SiteHits owns safe lifecycle
        # audit events instead; dependency records never leave this boundary.
        "oauth2_provider": {
            "handlers": ["null"],
            "level": "WARNING",
            "propagate": False,
        },
        "oauthlib": {
            "handlers": ["null"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=60,
        conn_health_checks=True,
    )
}
if not DEBUG and DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
    raise ImproperlyConfigured(
        "Stage 1 production requires PostgreSQL; configure DATABASE_URL explicitly."
    )

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("SITEHITS_TIME_ZONE", "Europe/Istanbul")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
        if not DEBUG
        else "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "sesame.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

LOGIN_URL = "signup"
LOGIN_REDIRECT_URL = "dashboard-all"
LOGOUT_REDIRECT_URL = "home"

SESAME_MAX_AGE = 600

ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*"]
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_UNIQUE_EMAIL = True
SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True
SOCIALACCOUNT_LOGIN_ON_GET = True

SITEHITS_GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
SITEHITS_GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APPS": (
            [
                {
                    "client_id": SITEHITS_GOOGLE_CLIENT_ID,
                    "secret": SITEHITS_GOOGLE_CLIENT_SECRET,
                    "key": "",
                }
            ]
            if SITEHITS_GOOGLE_CLIENT_ID and SITEHITS_GOOGLE_CLIENT_SECRET
            else []
        ),
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online"},
        "OAUTH_PKCE_ENABLED": True,
    }
}

EMAIL_BACKEND = env_value(
    "DJANGO_EMAIL_BACKEND",
    "EMAIL_BACKEND",
    default=(
        "django.core.mail.backends.console.EmailBackend"
        if DEBUG
        else "django_ses.SESBackend"
    ),
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "25"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "false").lower() in {"1", "true", "yes"}
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "SiteHits <hello@sitehits.io>")

AWS_SES_ACCESS_KEY_ID = os.environ.get("AWS_SES_ACCESS_KEY_ID", "")
AWS_SES_SECRET_ACCESS_KEY = os.environ.get("AWS_SES_SECRET_ACCESS_KEY", "")
AWS_SES_REGION_NAME = env_value("AWS_SES_REGION_NAME", "AWS_DEFAULT_REGION")
AWS_SES_REGION_ENDPOINT = os.environ.get(
    "AWS_SES_REGION_ENDPOINT",
    f"email.{AWS_SES_REGION_NAME}.amazonaws.com" if AWS_SES_REGION_NAME else "",
)
AWS_SES_CONFIGURATION_SET = os.environ.get("AWS_SES_CONFIGURATION_SET", "") or None
USE_SES_V2 = os.environ.get("USE_SES_V2", "true").lower() in {"1", "true", "yes"}

if not DEBUG and EMAIL_BACKEND == "django_ses.SESBackend":
    missing_ses_settings = [
        name
        for name, value in {
            "AWS_SES_ACCESS_KEY_ID": AWS_SES_ACCESS_KEY_ID,
            "AWS_SES_SECRET_ACCESS_KEY": AWS_SES_SECRET_ACCESS_KEY,
            "AWS_SES_REGION_NAME": AWS_SES_REGION_NAME,
        }.items()
        if not value
    ]
    if missing_ses_settings:
        raise ImproperlyConfigured(
            "django_ses.SESBackend requires production SES settings: "
            + ", ".join(missing_ses_settings)
        )

SITEHITS_BASE_URL = os.environ.get("SITEHITS_BASE_URL", "http://localhost:8000")
SITEHITS_HASH_SECRET = os.environ.get("SITEHITS_HASH_SECRET", SECRET_KEY)
SITEHITS_GEOIP_DB_PATH = os.environ.get("SITEHITS_GEOIP_DB_PATH", "")
SITEHITS_TRUST_PROXY_HEADERS = os.environ.get(
    "SITEHITS_TRUST_PROXY_HEADERS", "false"
).lower() in {"1", "true", "yes"}
SITEHITS_TRUSTED_PROXY_IPS = env_list(
    "SITEHITS_TRUSTED_PROXY_IPS",
    default="127.0.0.1,::1",
)
for trusted_proxy_ip in SITEHITS_TRUSTED_PROXY_IPS:
    try:
        ip_address(trusted_proxy_ip)
    except ValueError as exc:
        raise ImproperlyConfigured(
            "SITEHITS_TRUSTED_PROXY_IPS must contain exact IP addresses."
        ) from exc
if SITEHITS_TRUST_PROXY_HEADERS and not SITEHITS_TRUSTED_PROXY_IPS:
    raise ImproperlyConfigured(
        "SITEHITS_TRUSTED_PROXY_IPS is required when proxy headers are trusted."
    )
if not DEBUG and not SITEHITS_TRUST_PROXY_HEADERS:
    raise ImproperlyConfigured(
        "Stage 1 production requires trusted reverse-proxy headers."
    )
SITEHITS_MAX_EVENT_BYTES = int(os.environ.get("SITEHITS_MAX_EVENT_BYTES", "16384"))
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
SITEHITS_GOAL_PLANNING_MODEL = os.environ.get(
    "SITEHITS_GOAL_PLANNING_MODEL",
    "gpt-5.6-sol",
).strip()
SITEHITS_GOAL_PLANNING_TIMEOUT_SECONDS = float(
    os.environ.get("SITEHITS_GOAL_PLANNING_TIMEOUT_SECONDS", "30")
)
SITEHITS_GOAL_PLANNING_RATE_LIMIT = int(
    os.environ.get("SITEHITS_GOAL_PLANNING_RATE_LIMIT", "10")
)
SITEHITS_MCP_ISSUER_URL_EXPLICIT = "SITEHITS_MCP_ISSUER_URL" in os.environ
SITEHITS_MCP_ISSUER_URL = os.environ.get(
    "SITEHITS_MCP_ISSUER_URL",
    SITEHITS_BASE_URL,
)
SITEHITS_MCP_RESOURCE_URL_EXPLICIT = "SITEHITS_MCP_RESOURCE_URL" in os.environ
SITEHITS_MCP_RESOURCE_URL = os.environ.get(
    "SITEHITS_MCP_RESOURCE_URL",
    f"{SITEHITS_BASE_URL}/mcp",
)
SITEHITS_MCP_HOST = os.environ.get("SITEHITS_MCP_HOST", "127.0.0.1")
SITEHITS_MCP_PORT = int(os.environ.get("SITEHITS_MCP_PORT", "8001"))
SITEHITS_MCP_TOKEN_SECRET_EXPLICIT = "SITEHITS_MCP_TOKEN_SECRET" in os.environ
SITEHITS_MCP_TOKEN_SECRET = os.environ.get(
    "SITEHITS_MCP_TOKEN_SECRET",
    SECRET_KEY,
)
SITEHITS_MCP_DOCUMENTATION_URL_EXPLICIT = "SITEHITS_MCP_DOCUMENTATION_URL" in os.environ
SITEHITS_MCP_DOCUMENTATION_URL = os.environ.get(
    "SITEHITS_MCP_DOCUMENTATION_URL",
    f"{SITEHITS_BASE_URL}/mcp-docs/",
)
SITEHITS_MCP_SKILL_UPDATE_URL = os.environ.get(
    "SITEHITS_MCP_SKILL_UPDATE_URL",
    "https://sitehits.io/INSTALL.md",
)
SITEHITS_MCP_ACCESS_TOKEN_TTL_SECONDS = 15 * 60
SITEHITS_MCP_REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60
SITEHITS_MCP_AUTHORIZATION_CODE_TTL_SECONDS = 60
SITEHITS_MCP_AUTHORIZATION_REQUEST_TTL_SECONDS = 10 * 60
SITEHITS_MCP_CIMD_ENABLED = True
SITEHITS_MCP_CIMD_FETCH_TIMEOUT_SECONDS = 3.0
SITEHITS_MCP_CIMD_MAX_DOCUMENT_BYTES = 8 * 1024
SITEHITS_MCP_CIMD_MIN_CACHE_SECONDS = 5 * 60
SITEHITS_MCP_CIMD_MAX_CACHE_SECONDS = 60 * 60
SITEHITS_MCP_CIMD_MAX_CONCURRENT_FETCHES = 10
SITEHITS_MCP_CORS_ORIGINS = env_list(
    "SITEHITS_MCP_CORS_ORIGINS",
    default=(
        "http://localhost:3000,http://127.0.0.1:3000"
        if DEBUG
        else "https://chatgpt.com,https://codex.openai.com"
    ),
)

validate_service_url("SITEHITS_BASE_URL", SITEHITS_BASE_URL, path="")
validate_service_url("SITEHITS_MCP_ISSUER_URL", SITEHITS_MCP_ISSUER_URL, path="")
validate_service_url("SITEHITS_MCP_RESOURCE_URL", SITEHITS_MCP_RESOURCE_URL, path="/mcp")
if SITEHITS_BASE_URL != SITEHITS_MCP_ISSUER_URL:
    raise ImproperlyConfigured(
        "SITEHITS_BASE_URL and SITEHITS_MCP_ISSUER_URL must be byte-identical."
    )
if url_origin(SITEHITS_BASE_URL) != url_origin(SITEHITS_MCP_RESOURCE_URL):
    raise ImproperlyConfigured(
        "SITEHITS_MCP_RESOURCE_URL must share the public SiteHits origin."
    )
validate_cors_origins(SITEHITS_MCP_CORS_ORIGINS)

PUBLIC_BASE_URL = SITEHITS_BASE_URL
OAUTH_ISSUER = SITEHITS_MCP_ISSUER_URL
MCP_RESOURCE_URL = SITEHITS_MCP_RESOURCE_URL
MCP_RESOURCE_METADATA_URL = (
    f"{SITEHITS_BASE_URL}/.well-known/oauth-protected-resource/mcp"
)
DJANGO_EMBEDDED_MCP_RESOURCE_URL = SITEHITS_MCP_RESOURCE_URL
DJANGO_EMBEDDED_MCP_REFRESH_FAMILY_TTL_SECONDS = (
    SITEHITS_MCP_REFRESH_TOKEN_TTL_SECONDS
)

agent_contract = json.loads((BASE_DIR / "agent" / "contract.yaml").read_text(encoding="utf-8"))
SITEHITS_MCP_OAUTH_SCOPES = tuple(agent_contract["scopes"])
SITEHITS_MCP_BOOTSTRAP_SCOPES = tuple(
    agent_contract["tools"][agent_contract["bootstrap"]["tool"]]["required_scopes"]
)
OAUTH2_PROVIDER = {
    "SCOPES": {
        name: definition["description"]
        for name, definition in agent_contract["scopes"].items()
    },
    "DEFAULT_SCOPES": [],
    "OAUTH2_VALIDATOR_CLASS": "mcp_gateway.oauth.SiteHitsOAuth2Validator",
    "RESOURCE_SERVER_TOKEN_RESOURCE_VALIDATOR": (
        "django_embedded_mcp.oauth.exact_resource_audience"
    ),
    "AUTHORIZATION_CODE_EXPIRE_SECONDS": SITEHITS_MCP_AUTHORIZATION_CODE_TTL_SECONDS,
    "ACCESS_TOKEN_EXPIRE_SECONDS": SITEHITS_MCP_ACCESS_TOKEN_TTL_SECONDS,
    "REFRESH_TOKEN_EXPIRE_SECONDS": SITEHITS_MCP_REFRESH_TOKEN_TTL_SECONDS,
    "REFRESH_TOKEN_GRACE_PERIOD_SECONDS": 0,
    "REFRESH_TOKEN_REUSE_PROTECTION": True,
    "ROTATE_REFRESH_TOKEN": True,
    "REQUEST_APPROVAL_PROMPT": "force",
    "PKCE_REQUIRED": True,
    "ALLOW_URI_WILDCARDS": False,
    "ALLOW_LOCALHOST_LOOPBACK": False,
    # HTTP is admitted only so the product validator can permit the two exact
    # native-app loopback hosts; every non-loopback callback remains HTTPS-only.
    "ALLOWED_REDIRECT_URI_SCHEMES": ["https", "http"],
    "ALLOWED_SCHEMES": ["http", "https"] if DEBUG else ["https"],
    "COMPLIANT_BCP_RFC9700_IMPLICIT_GRANT": True,
    "COMPLIANT_BCP_RFC9700_PASSWORD_GRANT": True,
    "COMPLIANT_BCP_RFC9700_PKCE_METHOD": True,
    "COMPLIANT_BCP_RFC9700_ACCESS_TOKEN_TRANSPORT": True,
    "COMPLIANT_BCP_RFC9700_AUTHZ_RESPONSE_ISS": True,
    "COMPLIANT_BCP_RFC9700_TOKEN_STORAGE": True,
    "COMPLIANT_BCP_RFC9700_REFRESH_TOKEN": True,
    "COMPLIANT_BCP_RFC9700_REDIRECT_URI_SCHEME": False,
    "COMPLIANT_BCP_RFC9700_REDIRECT_URI_MATCHING": True,
    "COMPLIANT_BCP_RFC9700_PKCE_REQUIRED": True,
    "DCR_ENABLED": True,
    "DCR_REGISTRATION_PERMISSION_CLASSES": (
        "oauth2_provider.dcr.AllowAllDCRPermission",
    ),
    # SiteHits resolves CIMD in django-embedded-mcp before DOT handles the
    # grant. DOT's independent resolver is kept off so it cannot authorize
    # stale metadata or create a second client-registration policy path.
    "CIMD_ENABLED": False,
    "OIDC_ENABLED": False,
    # DOT also uses this setting for RFC 9207 authorization-response `iss`.
    # Bind it to the same canonical identity as discovery, never request Host.
    "OIDC_ISS_ENDPOINT": SITEHITS_MCP_ISSUER_URL,
    "OAUTH2_RESPONSE_TYPES_SUPPORTED": ["code"],
    "OAUTH2_TOKEN_ENDPOINT_AUTH_METHODS_SUPPORTED": ["none"],
    "OAUTH2_GRANT_TYPES_SUPPORTED": ["authorization_code", "refresh_token"],
    "OAUTH2_PROTECTED_RESOURCE_IDENTIFIER": SITEHITS_MCP_RESOURCE_URL,
    "OAUTH2_PROTECTED_RESOURCE_AUTHORIZATION_SERVERS": [SITEHITS_MCP_ISSUER_URL],
    "OAUTH2_PROTECTED_RESOURCE_BEARER_METHODS_SUPPORTED": ["header"],
    "OAUTH2_PROTECTED_RESOURCE_NAME": "SiteHits analytics MCP",
    "OAUTH2_PROTECTED_RESOURCE_DOCUMENTATION": SITEHITS_MCP_DOCUMENTATION_URL,
}

# Uvicorn validates the direct peer before applying forwarded scheme/client data.
# Django must not independently trust a raw X-Forwarded-Proto header.
SECURE_PROXY_SSL_HEADER = None
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 31_536_000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
