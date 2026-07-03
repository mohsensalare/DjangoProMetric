"""The component-based dashboard.

Every card on the dashboard is a :class:`DashboardComponent`. The list of
cards — and their order — comes from ``DJANGO_PROMETRIC["COMPONENTS"]``, so
projects can drop, reorder, or add their own cards by subclassing and
listing the dotted path.
"""

from django.template.loader import render_to_string
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _

from .conf import get_config
from .providers import base
from .routes import attribute, collect_routes, filter_routes


def load_components():
    """The component classes configured for the dashboard, in order."""
    return [import_string(path) for path in get_config()["COMPONENTS"]]


class DashboardComponent:
    """One card on the dashboard.

    Subclass, set the class attributes, implement :meth:`get_context`, and
    add the class' dotted path to ``DJANGO_PROMETRIC["COMPONENTS"]``.
    A component is skipped when the active provider lacks its
    ``capability``; raise :class:`~django_prometric.providers.ProviderError`
    from ``get_context`` for failures the user should see.
    """

    template_name = ""
    title = ""
    capability = None  # a providers.base constant the data source must have
    width = "full"  # "full" or "half"

    def __init__(self, provider, period):
        self.provider = provider
        self.period = period

    def is_available(self):
        return self.capability is None or self.capability in self.provider.capabilities()

    def get_context(self):
        return {}

    def render(self, request):
        context = {"component": self, **self.get_context()}
        return render_to_string(self.template_name, context, request=request)


class OverviewCards(DashboardComponent):
    template_name = "django_prometric/components/overview.html"
    title = _("Overview")
    capability = base.OVERVIEW

    def get_context(self):
        return {"stats": self.provider.get_overview(self.period)}


class TrafficChart(DashboardComponent):
    template_name = "django_prometric/components/traffic.html"
    title = _("Traffic")
    capability = base.TIMESERIES

    def get_context(self):
        points = self.provider.get_timeseries(self.period)
        return {
            "chart": {"labels": [p.label for p in points], "values": [p.requests for p in points]}
        }


class CountryChart(DashboardComponent):
    template_name = "django_prometric/components/country.html"
    title = _("Countries")
    capability = base.COUNTRY
    width = "half"

    def get_context(self):
        return {"items": self.provider.get_breakdown(base.DIM_COUNTRY, self.period)}


class StatusChart(DashboardComponent):
    template_name = "django_prometric/components/status.html"
    title = _("Status codes")
    capability = base.STATUS
    width = "half"

    def get_context(self):
        return {"items": self.provider.get_breakdown(base.DIM_STATUS, self.period)}


class CacheChart(DashboardComponent):
    template_name = "django_prometric/components/cache.html"
    title = _("Cache")
    capability = base.CACHE
    width = "half"

    def get_context(self):
        return {"items": self.provider.get_breakdown(base.DIM_CACHE, self.period)}


class PerformanceCard(DashboardComponent):
    """Response-time percentiles; shown locked when the source has none."""

    template_name = "django_prometric/components/performance.html"
    title = _("Performance")
    width = "half"

    def is_available(self):
        return True

    def get_context(self):
        if base.PERFORMANCE not in self.provider.capabilities():
            return {"locked": True}
        stats = self.provider.get_performance(self.period)
        if base.PERFORMANCE not in self.provider.capabilities():
            # The provider discovered mid-query that its plan gates timings.
            return {"locked": True}
        return {"locked": False, "stats": stats}


class TopRoutesTable(DashboardComponent):
    template_name = "django_prometric/components/top_routes.html"
    title = _("Top routes")
    capability = base.PATHS

    def get_context(self):
        routes, _errors = filter_routes(collect_routes())
        totals, unmatched = attribute(self.provider.get_path_stats(self.period), routes)
        rows = sorted(
            ((route, totals[route.key]) for route in routes if route.key in totals),
            key=lambda pair: pair[1].requests,
            reverse=True,
        )[:10]
        return {
            "rows": rows,
            "unmatched_paths": len(unmatched),
            "unmatched_requests": sum(stat.requests for stat in unmatched),
        }
