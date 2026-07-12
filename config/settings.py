from pathlib import Path
import os

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
    "websites",
    "analytics",
    "dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "analytics.middleware.EventCorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
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

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=60,
        conn_health_checks=True,
    )
}

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

SITEHITS_BASE_URL = os.environ.get("SITEHITS_BASE_URL", "http://localhost:8000").rstrip("/")
SITEHITS_HASH_SECRET = os.environ.get("SITEHITS_HASH_SECRET", SECRET_KEY)
SITEHITS_GEOIP_DB_PATH = os.environ.get("SITEHITS_GEOIP_DB_PATH", "")
SITEHITS_TRUST_PROXY_HEADERS = os.environ.get(
    "SITEHITS_TRUST_PROXY_HEADERS", "false"
).lower() in {"1", "true", "yes"}
SITEHITS_MAX_EVENT_BYTES = int(os.environ.get("SITEHITS_MAX_EVENT_BYTES", "16384"))

SECURE_PROXY_SSL_HEADER = (
    ("HTTP_X_FORWARDED_PROTO", "https") if SITEHITS_TRUST_PROXY_HEADERS else None
)
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 31_536_000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
