"""URL configuration for the django-prometric example project."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    # Mount the dashboard under any prefix you like; keep it private by
    # picking a non-guessable one if you prefer.
    path("prometric/", include("django_prometric.urls")),
    path("", include("demo.urls")),
]
