"""URLconf for the dashboard. Mount it under any prefix you like::

path("prometric/", include("django_prometric.urls"))
"""

from django.urls import path

from . import views

app_name = "django_prometric"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("routes/", views.routes, name="routes"),
    path("routes/<slug:key>/", views.route_detail, name="route-detail"),
    path("snapshots/", views.snapshots, name="snapshots"),
    path("snapshots/take/", views.snapshot_take, name="snapshot-take"),
    path("snapshots/<int:pk>/", views.snapshot_detail, name="snapshot-detail"),
    path("snapshots/<int:pk>/delete/", views.snapshot_delete, name="snapshot-delete"),
    path("refresh/", views.refresh, name="refresh"),
]
