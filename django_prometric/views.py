"""Dashboard views. Everything here is gated by ``dashboard_access_required``."""

import re
from collections import Counter

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from . import __version__
from .components import load_components
from .conf import get_config
from .models import Snapshot
from .permissions import dashboard_access_required
from .providers import Period, ProviderError, base, get_provider
from .routes import attribute, collect_routes, drf_installed, filter_routes
from .snapshots import capture, comparison


def _base_context(provider, period):
    return {
        "provider": provider,
        "period": period,
        "periods": Period.choices(),
        "site_name": get_config()["SITE_NAME"],
        "version": __version__,
    }


def _routes_for(request):
    """Visible routes, honouring the page's ``?admin=1/0`` override."""
    override = {"1": False, "0": True}.get(request.GET.get("admin"))
    return filter_routes(collect_routes(), exclude_admin=override)


def _redirect_back(request):
    target = request.POST.get("next", "")
    if url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return redirect(target)
    return redirect("django_prometric:dashboard")


@dashboard_access_required
def dashboard(request):
    provider = get_provider()
    period = Period.from_key(request.GET.get("period"))
    context = _base_context(provider, period)
    if not provider.is_configured:
        return render(request, "django_prometric/onboarding.html", context)
    if base.OVERVIEW in provider.capabilities():
        try:
            provider.get_overview(period)  # fail once here instead of once per card
        except ProviderError as error:
            context["error"] = error
            return render(request, "django_prometric/dashboard.html", context)
    cards = []
    for component_class in load_components():
        component = component_class(provider, period)
        if not component.is_available():
            continue
        try:
            cards.append(component.render(request))
        except ProviderError as error:
            cards.append(
                render_to_string(
                    "django_prometric/components/error.html",
                    {"component": component, "error": error},
                    request=request,
                )
            )
    context["cards"] = cards
    return render(request, "django_prometric/dashboard.html", context)


@dashboard_access_required
def routes(request):
    provider = get_provider()
    period = Period.from_key(request.GET.get("period"))
    visible, config_errors = _routes_for(request)

    query = request.GET.get("q", "").strip()
    if query:
        visible = _search(visible, query)
    counts = Counter(route.group for route in visible)
    group = request.GET.get("group", "")
    if group:
        visible = [route for route in visible if route.group == group]

    totals, unmatched, error = {}, [], None
    if provider.is_configured and base.PATHS in provider.capabilities():
        try:
            totals, unmatched = attribute(provider.get_path_stats(period), visible)
        except ProviderError as exc:
            error = exc

    override = {"1": False, "0": True}.get(request.GET.get("admin"))
    admin_hidden = get_config()["ROUTES"]["EXCLUDE_ADMIN"] if override is None else override

    context = _base_context(provider, period)
    context.update(
        {
            "rows": [(route, totals.get(route.key)) for route in visible],
            "counts": counts,
            "total": sum(counts.values()),
            "group": group,
            "query": query,
            "admin_hidden": admin_hidden,
            "config_errors": config_errors,
            "unmatched_paths": len(unmatched),
            "unmatched_requests": sum(stat.requests for stat in unmatched),
            "drf_installed": drf_installed(),
            "error": error,
        }
    )
    return render(request, "django_prometric/routes.html", context)


def _search(routes_list, query):
    """Filter by regex; silently fall back to substring on an invalid pattern."""
    try:
        pattern = re.compile(query, re.IGNORECASE)
    except re.error:
        needle = query.lower()
        return [
            r
            for r in routes_list
            if needle in r.display.lower() or needle in (r.name or "").lower()
        ]
    return [r for r in routes_list if pattern.search(r.display) or pattern.search(r.name or "")]


@dashboard_access_required
def route_detail(request, key):
    provider = get_provider()
    period = Period.from_key(request.GET.get("period"))
    visible, _errors = _routes_for(request)
    route = next((r for r in visible if r.key == key), None)
    if route is None:
        raise Http404
    context = _base_context(provider, period)
    context["route"] = route
    if provider.is_configured:
        try:
            metrics = provider.get_route_metrics(route, period)
        except ProviderError as error:
            context["error"] = error
        else:
            context["metrics"] = metrics
            context["chart"] = {
                "labels": [p.label for p in metrics.timeseries],
                "values": [p.requests for p in metrics.timeseries],
            }
    return render(request, "django_prometric/route_detail.html", context)


@dashboard_access_required
def snapshots(request):
    provider = get_provider()
    period = Period.from_key(request.GET.get("period"))
    context = _base_context(provider, period)
    context["snapshots"] = Snapshot.objects.select_related("parent")
    return render(request, "django_prometric/snapshots.html", context)


@dashboard_access_required
@require_POST
def snapshot_take(request):
    provider = get_provider()
    if not provider.is_configured:
        return redirect("django_prometric:dashboard")
    if request.POST.get("retake"):
        parent = get_object_or_404(Snapshot, pk=request.POST["retake"])
        period = Period.from_key(parent.period)
    else:
        parent = None
        period = Period.from_key(request.POST.get("period"))
    try:
        snapshot = capture(provider, period, parent=parent)
    except ProviderError as error:
        messages.error(
            request,
            _("Could not take the snapshot: %(reason)s") % {"reason": error.message},
        )
        return _redirect_back(request)
    return redirect("django_prometric:snapshot-detail", pk=snapshot.pk)


@dashboard_access_required
def snapshot_detail(request, pk):
    snapshot = get_object_or_404(Snapshot, pk=pk)
    series = snapshot.series
    provider = get_provider()
    context = _base_context(provider, Period.from_key(snapshot.period))
    context.update(
        {
            "snapshot": snapshot,
            "series": series,
            "rows": comparison(series),
            "filters_changed": any(s.filters != series[0].filters for s in series),
        }
    )
    return render(request, "django_prometric/snapshot_detail.html", context)


@dashboard_access_required
@require_POST
def snapshot_delete(request, pk):
    get_object_or_404(Snapshot, pk=pk).delete()
    return redirect("django_prometric:snapshots")


@dashboard_access_required
@require_POST
def refresh(request):
    get_provider().invalidate_cache()
    return _redirect_back(request)
