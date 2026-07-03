from django.urls import include, path, re_path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("about/", views.about, name="about"),
    path("articles/<slug:slug>/", views.article_detail, name="article-detail"),
    re_path(r"^reports/(?P<year>[0-9]{4})/$", views.legacy_report, name="legacy-report"),
]

try:
    from rest_framework.routers import DefaultRouter

    from . import api
except ImportError:
    pass
else:
    router = DefaultRouter()
    router.register("articles", api.ArticleViewSet, basename="api-article")
    urlpatterns += [
        path("api/health/", api.HealthView.as_view(), name="api-health"),
        path("api/", include(router.urls)),
    ]
