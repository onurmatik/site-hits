from django.urls import path

from . import views


urlpatterns = [
    path("all", views.dashboard, {"site_slug": "all"}, name="dashboard-all"),
    path("<slug:site_slug>", views.dashboard, name="dashboard-site"),
]

