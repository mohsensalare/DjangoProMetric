"""Dashboard views. Everything here is gated by ``dashboard_access_required``."""

import re
from collections import Counter

from django.contrib import messages
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import format_html_join
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views.decorators.http import require_POST

from . import __version__
from .components import build_components
from .conf import get_config
from .models import Preferences, Snapshot
from .permissions import dashboard_access_required
from .providers import (
    Period,
    ProviderError,
    base,
    configured_providers,
    get_provider,
    get_providers,
    provider_for,
)
from .routes import attribute, collect_routes, drf_installed, filter_routes
from .snapshots import capture, comparison, headline, route_comparison
from .templatetags.prometric_extras import num, size


def _base_context(period, providers=None):
    if providers is None:
        providers = configured_providers()
    return {
        "providers": providers,
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


def _card_placeholders(providers, period, source=""):
    """Skeleton slots for every card the page will load asynchronously.

    Nothing here talks to a provider; the page renders instantly and each
    card fills itself in from the ``card`` endpoint.
    """
    cards = []
    for component in build_components(providers, period):
        url = reverse("django_prometric:card", args=[component.slot()])
        query = period.as_query()
        if source:
            query += "&" + urlencode({"source": source})
        cards.append({"component": component, "url": f"{url}?{query}"})
    return cards


@dashboard_access_required
def dashboard(request):
    providers = configured_providers()
    if not providers:
        return redirect("django_prometric:providers")
    period = Period.from_request(request.GET)
    preferences = _preferences(request)
    context = _base_context(period, providers)
    context["cards"] = _apply_card_order(
        _card_placeholders(providers, period), preferences.card_order
    )
    context["customizable"] = True
    context["hidden_slots"] = preferences.hidden_cards
    return render(request, "django_prometric/dashboard.html", context)


def _apply_card_order(cards, order):
    """Sort placeholders by the user's stored order; cards they never
    arranged keep their configured position, after the ordered ones."""
    if not order:
        return cards
    position = {slot: rank for rank, slot in enumerate(order)}
    ranked = sorted(
        (position.get(card["component"].slot(), len(position) + index), index)
        for index, card in enumerate(cards)
    )
    return [cards[index] for _rank, index in ranked]


def _preferences(request) -> Preferences:
    """The user's stored preferences; an unsaved blank one when there are none."""
    if not request.user.is_authenticated:
        return Preferences()
    found = Preferences.objects.filter(user=request.user).first()
    return found if found is not None else Preferences(user=request.user)


@dashboard_access_required
@require_POST
def preferences_cards(request):
    """Persist the customize panel's state for this user.

    Repeated ``hidden=<slot>`` fields name the cards switched off; repeated
    ``order=<slot>`` fields carry the arranged display order. Both land in
    the ``cards`` group of the preferences document.
    """
    if not request.user.is_authenticated:
        return JsonResponse({"saved": False}, status=400)

    def slots(name):
        found = request.POST.getlist(name)
        return [slot for slot in found if re.fullmatch(r"[a-z0-9-]{1,80}", slot)][:100]

    preferences = _preferences(request)
    preferences.set_cards(hidden=slots("hidden"), order=slots("order"))
    preferences.save()
    return JsonResponse({"saved": True, "cards": preferences.data.get("cards", {})})


@dashboard_access_required
def card(request, slot):
    """One rendered card, fetched by the dashboard after the page loads.

    Card failures stay inside their card; one broken source never blanks
    the page.
    """
    source = request.GET.get("source", "")
    if source:
        provider = get_provider(source)
        if provider is None or not provider.is_configured:
            raise Http404
        providers = [provider]
    else:
        providers = configured_providers()
    period = Period.from_request(request.GET)
    component = next((c for c in build_components(providers, period) if c.slot() == slot), None)
    if component is None:
        raise Http404
    try:
        html = component.render(request)
    except ProviderError as error:
        html = render_to_string(
            "django_prometric/components/error.html",
            {"component": component, "error": error},
            request=request,
        )
    html += _notices_html(providers)
    return HttpResponse(html)


def _notices_html(providers):
    """Notices a provider raised while answering — e.g. a clamped window."""
    return format_html_join(
        "",
        '<div class="pm-notice pm-notice--{}">{}</div>',
        ((notice.level, notice.message) for provider in providers for notice in provider.notices),
    )


@dashboard_access_required
def providers_list(request):
    period = Period.from_request(request.GET)
    context = _base_context(period)
    context["all_providers"] = [
        {
            "provider": provider,
            "capabilities": sorted(provider.capabilities()),
        }
        for provider in get_providers()
    ]
    return render(request, "django_prometric/providers.html", context)


@dashboard_access_required
def provider_detail(request, slug):
    provider = get_provider(slug)
    if provider is None:
        raise Http404
    period = Period.from_request(request.GET)
    context = _base_context(period)
    context["provider"] = provider
    context["capabilities"] = sorted(provider.capabilities())
    if provider.is_configured:
        # Only this provider feeds the cards here — its numbers, unmixed.
        context["cards"] = _card_placeholders([provider], period, source=provider.slug)
    return render(request, "django_prometric/provider_detail.html", context)


@dashboard_access_required
def routes(request):
    period = Period.from_request(request.GET)
    providers = configured_providers()
    visible, config_errors = _routes_for(request)

    query = request.GET.get("q", "").strip()
    if query:
        visible = _search(visible, query)
    counts = Counter(route.group for route in visible)
    group = request.GET.get("group", "")
    if group:
        visible = [route for route in visible if route.group == group]

    provider = provider_for(base.PATHS, providers)
    traffic_url = ""
    if provider is not None:
        traffic_url = "{}?{}".format(reverse("django_prometric:route-traffic"), period.as_query())
        if request.GET.get("admin"):
            traffic_url += "&" + urlencode({"admin": request.GET["admin"]})

    override = {"1": False, "0": True}.get(request.GET.get("admin"))
    admin_hidden = get_config()["ROUTES"]["EXCLUDE_ADMIN"] if override is None else override

    context = _base_context(period, providers)
    context.update(
        {
            "routes_list": visible,
            "traffic_provider": provider,
            "traffic_url": traffic_url,
            "counts": counts,
            "total": sum(counts.values()),
            "group": group,
            "query": query,
            "admin_hidden": admin_hidden,
            "config_errors": config_errors,
            "drf_installed": drf_installed(),
        }
    )
    return render(request, "django_prometric/routes.html", context)


@dashboard_access_required
def route_traffic(request):
    """Per-route request totals as JSON — the slow column, loaded after the table."""
    providers = configured_providers()
    provider = provider_for(base.PATHS, providers)
    if provider is None:
        return JsonResponse({"totals": {}})
    period = Period.from_request(request.GET)
    visible, _errors = _routes_for(request)
    try:
        totals, unmatched = attribute(provider.get_path_stats(period), visible)
    except ProviderError as error:
        return JsonResponse({"error": str(error.message)})
    note = ""
    if unmatched:
        note = ngettext(
            "%(count)s path (%(requests)s requests) was served but doesn't match any route above.",
            "%(count)s paths (%(requests)s requests) were served but don't match any route above.",
            len(unmatched),
        ) % {
            "count": len(unmatched),
            "requests": num(sum(stat.requests for stat in unmatched)),
        }
    return JsonResponse(
        {
            "totals": {
                key: {"requests": num(stat.requests), "bandwidth": size(stat.bandwidth_bytes)}
                for key, stat in totals.items()
            },
            "note": note,
            "notices": [
                {"message": str(notice), "level": notice.level} for notice in provider.notices
            ],
        }
    )


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
    period = Period.from_request(request.GET)
    visible, _errors = _routes_for(request)
    route = next((r for r in visible if r.key == key), None)
    if route is None:
        raise Http404
    providers = configured_providers()
    sections = []
    for provider in providers:
        if not provider.route_template:
            continue
        url = reverse("django_prometric:route-section", args=[route.key, provider.slug])
        query = period.as_query()
        if request.GET.get("admin"):
            query += "&" + urlencode({"admin": request.GET["admin"]})
        sections.append({"provider": provider, "url": f"{url}?{query}"})
    context = _base_context(period, providers)
    context.update({"route": route, "sections": sections})
    return render(request, "django_prometric/route_detail.html", context)


@dashboard_access_required
def route_section(request, key, slug):
    """One provider's slice of a route, fetched after the page loads."""
    provider = get_provider(slug)
    if provider is None or not provider.is_configured or not provider.route_template:
        raise Http404
    period = Period.from_request(request.GET)
    visible, _errors = _routes_for(request)
    route = next((r for r in visible if r.key == key), None)
    if route is None:
        raise Http404
    try:
        context = provider.get_route_context(route, period)
    except ProviderError as error:
        html = render_to_string(
            "django_prometric/_load_error.html", {"error": error}, request=request
        )
    else:
        html = render_to_string(
            provider.route_template,
            {"provider": provider, "route": route, "period": period, **context},
            request=request,
        )
    return HttpResponse(html + _notices_html([provider]))


@dashboard_access_required
def snapshots(request):
    period = Period.from_request(request.GET)
    providers = configured_providers()
    context = _base_context(period, providers)
    context["snapshots"] = Snapshot.objects.select_related("parent")
    context["sources"] = [
        {"provider": provider, "limited": provider.exceeds_limit(period)} for provider in providers
    ]
    return render(request, "django_prometric/snapshots.html", context)


@dashboard_access_required
@require_POST
def snapshot_take(request):
    configured = configured_providers()
    if request.POST.get("retake"):
        parent = get_object_or_404(Snapshot, pk=request.POST["retake"])
        period = _retake_period(parent)
        wanted = parent.provider_slugs
    else:
        parent = None
        period = Period.from_request(request.POST)
        wanted = request.POST.getlist("providers")
    selected = [provider for provider in configured if provider.slug in wanted]
    if not selected:
        messages.error(request, _("Pick at least one data source for the snapshot."))
        return _redirect_back(request)
    snapshot, errors = capture(selected, period, parent=parent)
    for message in errors:
        messages.error(request, _("Could not include %(report)s") % {"report": message})
    if snapshot is None:
        return _redirect_back(request)
    return redirect("django_prometric:snapshot-detail", pk=snapshot.pk)


def _retake_period(parent):
    """The parent's window length, ending now — same report, new period."""
    if parent.period != base.CUSTOM_PERIOD or not (parent.window_start and parent.window_end):
        return Period.from_key(parent.period)
    from django.utils import timezone

    end = timezone.now()
    return Period.custom(end - (parent.window_end - parent.window_start), end)


@dashboard_access_required
def snapshot_detail(request, pk):
    snapshot = get_object_or_404(Snapshot, pk=pk)
    series = snapshot.series
    context = _base_context(Period.from_key(snapshot.period))
    context.update(
        {
            "snapshot": snapshot,
            "series": series,
            "sections": comparison(series),
            "filters_changed": any(s.filters != series[0].filters for s in series),
        }
    )
    return render(request, "django_prometric/snapshot_detail.html", context)


@dashboard_access_required
def snapshot_compare(request):
    """2–4 snapshots side by side — ?s=<pk>&s=<pk>[&base=<pk>]."""
    ids = [value for value in request.GET.getlist("s") if value.isdigit()]
    if not ids and request.GET.get("a", "").isdigit() and request.GET.get("b", "").isdigit():
        ids = [request.GET["a"], request.GET["b"]]  # the pre-multi spelling
    ids = list(dict.fromkeys(ids))[:4]
    picked = list(Snapshot.objects.filter(pk__in=ids))
    if len(picked) < 2:
        raise Http404
    picked.sort(key=lambda snapshot: snapshot.taken_at)
    base_pk = request.GET.get("base", "")
    if base_pk.isdigit():
        for snapshot in picked:
            if snapshot.pk == int(base_pk):
                picked.remove(snapshot)
                picked.insert(0, snapshot)
                break
    sections = comparison(picked)
    context = _base_context(Period.from_key(picked[0].period))
    context.update(
        {
            "snaps": picked,
            "sections": sections,
            "headline": headline(sections),
            "route_sections": route_comparison(picked),
            "filters_changed": any(s.filters != picked[0].filters for s in picked),
        }
    )
    return render(request, "django_prometric/snapshot_compare.html", context)


@dashboard_access_required
@require_POST
def snapshot_delete(request, pk):
    get_object_or_404(Snapshot, pk=pk).delete()
    return redirect("django_prometric:snapshots")


@dashboard_access_required
@require_POST
def refresh(request):
    for provider in configured_providers():
        provider.invalidate_cache()
    return _redirect_back(request)
