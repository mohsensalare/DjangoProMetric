"""URLconf for the dashboard. Mount it under any prefix you like::

path("prometric/", include("django_prometric.urls"))
"""

from django.urls import path

from . import views

app_name = "django_prometric"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("cards/<slug:slot>/", views.card, name="card"),
    path("providers/", views.providers_list, name="providers"),
    path("providers/<slug:slug>/", views.provider_detail, name="provider-detail"),
    path("routes/", views.routes, name="routes"),
    path("routes/traffic/", views.route_traffic, name="route-traffic"),
    path("routes/<slug:key>/", views.route_detail, name="route-detail"),
    path("routes/<slug:key>/sections/<slug:slug>/", views.route_section, name="route-section"),
    path("snapshots/", views.snapshots, name="snapshots"),
    path("snapshots/take/", views.snapshot_take, name="snapshot-take"),
    path("snapshots/compare/", views.snapshot_compare, name="snapshot-compare"),
    path("snapshots/<int:pk>/", views.snapshot_detail, name="snapshot-detail"),
    path("snapshots/<int:pk>/delete/", views.snapshot_delete, name="snapshot-delete"),
    path("preferences/cards/", views.preferences_cards, name="preferences-cards"),
    path("refresh/", views.refresh, name="refresh"),
]
