from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.generic import RedirectView

from analytics.api import api
from analytics.views import tracker_script
from dashboard.views import health


urlpatterns = [
    path("", RedirectView.as_view(pattern_name="dashboard-all", permanent=False)),
    path("health/", health, name="health"),
    path("js/script.js", tracker_script, name="tracker-script"),
    path("admin/", admin.site.urls),
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("dashboard/", include("dashboard.urls")),
    path("api/", api.urls),
]
