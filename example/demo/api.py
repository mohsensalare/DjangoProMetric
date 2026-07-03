"""DRF demo endpoints. This module is only imported when DRF is installed."""

from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

ARTICLES = [
    {"id": 1, "slug": "hello-world", "title": "Hello world"},
    {"id": 2, "slug": "second-post", "title": "Second post"},
]


class HealthView(APIView):
    def get(self, request):
        return Response({"status": "ok"})


class ArticleViewSet(viewsets.ViewSet):
    def list(self, request):
        return Response(ARTICLES)

    def retrieve(self, request, pk=None):
        for article in ARTICLES:
            if str(article["id"]) == str(pk):
                return Response(article)
        return Response({"detail": "Not found"}, status=404)
