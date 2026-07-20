from django.urls import path

from . import views


urlpatterns = [
    path("all", views.dashboard, {"site_slug": "all"}, name="dashboard-all"),
    path(
        "<slug:site_slug>/product-metrics",
        views.product_metrics_settings,
        name="product-metrics-settings",
    ),
    path("<slug:site_slug>", views.dashboard, name="dashboard-site"),
]
