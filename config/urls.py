from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from analytics.api import api
from dashboard.auth import SiteHitsSesameLoginView, google_start, signup
from analytics.views import tracker_script
from dashboard.views import (
    health,
    home,
    onboarding,
    onboarding_install,
    start_onboarding,
)


urlpatterns = [
    path("", home, name="home"),
    path("start/", start_onboarding, name="start-onboarding"),
    path("onboarding/", onboarding, name="onboarding"),
    path(
        "onboarding/<slug:site_slug>/",
        onboarding_install,
        name="onboarding-install",
    ),
    path("health/", health, name="health"),
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
