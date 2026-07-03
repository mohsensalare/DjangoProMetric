from django.http import HttpResponse, JsonResponse


def home(request):
    return HttpResponse("<h1>ProMetric example</h1><p>Visit /prometric/ for the dashboard.</p>")


def about(request):
    return HttpResponse("<h1>About</h1>")


def article_detail(request, slug):
    return HttpResponse(f"<h1>Article: {slug}</h1>")


def legacy_report(request, year):
    return JsonResponse({"year": year, "status": "archived"})
