from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from analytics.api import api
from analytics.views import tracker_script
from dashboard.auth import SiteHitsSesameLoginView, google_start, signup
from dashboard.views import (
    health,
    home,
    onboarding,
    onboarding_install,
    site_widget,
    start_onboarding,
)
from mcp_gateway.views import agent_manifest, mcp_documentation, oauth_consent

urlpatterns = [
    path("", home, name="home"),
    path("agent-manifest.json", agent_manifest, name="agent-manifest"),
    path("mcp-docs/", mcp_documentation, name="mcp-docs"),
    path("oauth/consent/", oauth_consent, name="mcp-oauth-consent"),
    path("start/", start_onboarding, name="start-onboarding"),
    path("onboarding/", onboarding, name="onboarding"),
    path(
        "onboarding/<slug:site_slug>/",
        onboarding_install,
        name="onboarding-install",
    ),
    path("health/", health, name="health"),
    path("widget/<str:public_key>/", site_widget, name="site-widget"),
    path("js/script.js", tracker_script, name="tracker-script"),
    path("admin/", admin.site.urls),
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path("accounts/signup/", signup, name="signup"),
    path("accounts/google/start/", google_start, name="google-start"),
    path("accounts/magic-link/", SiteHitsSesameLoginView.as_view(), name="sesame-login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("accounts/", include("allauth.urls")),
    path("dashboard/", include("dashboard.urls")),
    path("api/", api.urls),
]
