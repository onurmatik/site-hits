import json
import os
import subprocess
import sys


def _settings_process(extra_env, *, debug=False):
    env = os.environ.copy()
    for name in (
        "DJANGO_EMAIL_BACKEND",
        "EMAIL_BACKEND",
        "AWS_SES_ACCESS_KEY_ID",
        "AWS_SES_SECRET_ACCESS_KEY",
        "AWS_SES_REGION_NAME",
        "AWS_DEFAULT_REGION",
        "AWS_SES_REGION_ENDPOINT",
        "DATABASE_URL",
    ):
        env.pop(name, None)
    env.update(
        {
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "DJANGO_DEBUG": "true" if debug else "false",
            "DJANGO_SECRET_KEY": "test-only-secret-key-with-more-than-fifty-characters-123456789",
            "SITEHITS_BASE_URL": (
                "http://localhost:8000" if debug else "https://sitehits.example"
            ),
            "SITEHITS_MCP_ISSUER_URL": (
                "http://localhost:8000" if debug else "https://sitehits.example"
            ),
            "SITEHITS_MCP_RESOURCE_URL": (
                "http://localhost:8000/mcp" if debug else "https://sitehits.example/mcp"
            ),
            "SITEHITS_MCP_CORS_ORIGINS": (
                "http://localhost:3000,http://127.0.0.1:3000"
                if debug
                else "https://chatgpt.com,https://codex.openai.com"
            ),
            "DATABASE_URL": (
                "sqlite:///:memory:"
                if debug
                else "postgresql://sitehits:test@127.0.0.1:5432/sitehits"
            ),
            "SITEHITS_TRUST_PROXY_HEADERS": "false" if debug else "true",
            "SITEHITS_TRUSTED_PROXY_IPS": "127.0.0.1,::1",
            **extra_env,
        }
    )
    code = """
import json
import django
django.setup()
from django.conf import settings
print(json.dumps({
    "backend": settings.EMAIL_BACKEND,
    "access_key": settings.AWS_SES_ACCESS_KEY_ID,
    "region": settings.AWS_SES_REGION_NAME,
    "endpoint": settings.AWS_SES_REGION_ENDPOINT,
    "use_v2": settings.USE_SES_V2,
    "from_email": settings.DEFAULT_FROM_EMAIL,
}))
"""
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_local_email_backend_stays_on_console():
    result = _settings_process({}, debug=True)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["backend"] == (
        "django.core.mail.backends.console.EmailBackend"
    )


def test_production_email_backend_uses_ses_v2():
    result = _settings_process(
        {
            "AWS_SES_ACCESS_KEY_ID": "ses-access-key",
            "AWS_SES_SECRET_ACCESS_KEY": "ses-secret-key",
            "AWS_SES_REGION_NAME": "eu-west-1",
            # This test isolates SES settings from the intentionally unreleased
            # Agent Contract candidate in the source tree.
            "SITEHITS_ALLOW_UNRELEASED_AGENT_CONTRACT": "true",
        }
    )

    assert result.returncode == 0, result.stderr
    configured = json.loads(result.stdout)
    assert configured == {
        "backend": "django_ses.SESBackend",
        "access_key": "ses-access-key",
        "region": "eu-west-1",
        "endpoint": "email.eu-west-1.amazonaws.com",
        "use_v2": True,
        "from_email": "SiteHits <hello@sitehits.io>",
    }


def test_production_ses_backend_fails_fast_without_credentials():
    result = _settings_process({})

    assert result.returncode != 0
    assert "AWS_SES_ACCESS_KEY_ID" in result.stderr
    assert "AWS_SES_SECRET_ACCESS_KEY" in result.stderr
    assert "AWS_SES_REGION_NAME" in result.stderr


def test_production_settings_reject_non_postgresql_database():
    result = _settings_process(
        {
            "DATABASE_URL": "sqlite:///:memory:",
            "AWS_SES_ACCESS_KEY_ID": "ses-access-key",
            "AWS_SES_SECRET_ACCESS_KEY": "ses-secret-key",
            "AWS_SES_REGION_NAME": "eu-west-1",
        }
    )

    assert result.returncode != 0
    assert "Stage 1 production requires PostgreSQL" in result.stderr
