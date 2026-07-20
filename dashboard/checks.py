from django.conf import settings
from django.core.checks import Error, Tags, register


@register(Tags.security, deploy=True)
def check_google_oauth(app_configs, **kwargs):
    client_id = settings.SITEHITS_GOOGLE_CLIENT_ID.strip()
    client_secret = settings.SITEHITS_GOOGLE_CLIENT_SECRET.strip()
    if not client_id or not client_secret:
        return [
            Error(
                "Google OAuth is not fully configured.",
                hint=(
                    "Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET, then authorize "
                    "https://sitehits.io/accounts/google/login/callback/ in Google Cloud."
                ),
                id="dashboard.E001",
            )
        ]
    if not client_id.endswith(".apps.googleusercontent.com"):
        return [
            Error(
                "GOOGLE_OAUTH_CLIENT_ID is not a Google OAuth client ID.",
                hint="Use the client ID from a Google OAuth 2.0 Web application credential.",
                id="dashboard.E002",
            )
        ]
    return []
