"""The component-based dashboard.

Every card on the dashboard is a :class:`DashboardComponent`. The list of
cards — and their order — comes from ``DJANGO_PROMETRIC["COMPONENTS"]``, so
projects can drop, reorder, or add their own cards by subclassing and
listing the dotted path.

A card names a ``capability`` and draws from the first configured provider
that has it, so the dashboard composes several data sources; every card
shows which source fed it.
"""

import re

from django.template.loader import render_to_string
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _

from .conf import get_config
from .providers import ProviderError, base
from .routes import attribute, collect_routes, filter_routes, route_for_transaction


def load_components():
    """The component classes configured for the dashboard, in order."""
    return [import_string(path) for path in get_config()["COMPONENTS"]]


def build_components(providers, period):
    """Instances of every configured component a provider can feed.

    A report several sources can answer appears once per source — so their
    numbers can be compared, and so it still shows up when a project
    configures only one of them.
    """
    components = []
    for component_class in load_components():
        components.extend(component_class.build_all(providers, period))
    return components


class DashboardComponent:
    """One card on the dashboard.

    Subclass, set the class attributes, implement :meth:`get_context`, and
    add the class' dotted path to ``DJANGO_PROMETRIC["COMPONENTS"]``.
    Raise :class:`~django_prometric.providers.ProviderError` from
    ``get_context`` for failures the user should see.
    """

    template_name = ""
    title = ""
    capability = None  # a providers.base constant the data source must have
    width = "full"  # "full" or "half"
    # False on cards that merge every capable source into one instance.
    one_per_provider = True

    def __init__(self, provider, period):
        self.provider = provider
        self.period = period

    @classmethod
    def build_all(cls, providers, period):
        """One instance per capable provider."""
        if cls.capability is None:
            capable = providers[:1]
        else:
            capable = [p for p in providers if cls.capability in p.capabilities()]
        if not cls.one_per_provider:
            capable = capable[:1]
        return [cls(provider, period) for provider in capable]

    def slot(self) -> str:
        """Stable kebab-case id, used to show/hide cards per user."""
        name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", type(self).__name__).lower()
        return f"{name}-{self.provider.slug}"

    def get_context(self):
        return {}

    def render(self, request):
        context = {"component": self, **self.get_context()}
        return render_to_string(self.template_name, context, request=request)


# Overview stats where growth is a problem, not a win.
_WORSE_WHEN_UP = {"errors", "threats"}
# Overview stats where growth is neither good nor bad on its own.
_NEUTRAL = {"bandwidth_bytes"}


class OverviewCards(DashboardComponent):
    template_name = "django_prometric/components/overview.html"
    title = _("Overview")
    capability = base.OVERVIEW

    def get_context(self):
        stats = self.provider.get_overview(self.period)
        return {"stats": stats, "deltas": self._deltas(stats)}

    def _deltas(self, stats):
        """Relative change of each stat against the window right before this
        one, keyed by field name — {"delta": -0.12, "cls": "good"}."""
        length = self.period.end - self.period.start
        before = base.Period.custom(self.period.start - length, self.period.start)
        try:
            previous = self.provider.get_overview(before)
        except ProviderError:
            return {}
        deltas = {}
        for key in (
            "requests",
            "unique_visitors",
            "page_views",
            "bandwidth_bytes",
            "cached_requests",
            "errors",
            "threats",
        ):
            now, then = getattr(stats, key), getattr(previous, key)
            if now is None or not then:
                continue
            change = (now - then) / then
            if key in _NEUTRAL or not round(change * 100):
                cls = ""
            elif key in _WORSE_WHEN_UP:
                cls = "bad" if change > 0 else "good"
            else:
                cls = "good" if change > 0 else "bad"
            deltas[key] = {"delta": change, "cls": cls}
        return deltas


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
        items = self.provider.get_breakdown(base.DIM_COUNTRY, self.period, limit=8)
        return {
            "items": items,
            "chart": {"labels": [i.label for i in items], "values": [i.value for i in items]},
        }


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


# Latency ratings, after common APM guidance: users read <100 ms as instant
# and >1 s as sluggish; tail percentiles get proportionally more slack.
# (good, fair) in ms — below good is green, below fair amber, above red.
_LATENCY_BANDS = {
    "p50_ms": (100, 300),
    "p75_ms": (200, 500),
    "p95_ms": (500, 1000),
    "p99_ms": (1000, 3000),
}
_FAILURE_BANDS = (0.01, 0.05)  # 1% / 5%

_PERCENTILE_MEANING = {
    "p50_ms": _("Half of all requests finish faster than this — the typical experience."),
    "p75_ms": _("Three out of four requests finish faster than this."),
    "p95_ms": _("All but the slowest 5% stay under this — how the slow moments feel."),
    "p99_ms": _("The worst 1%: outliers, cold caches and heavy queries."),
}


def _rating(value, good, fair):
    if value < good:
        return "good"
    return "fair" if value < fair else "poor"


def _meter_position(value, good, fair):
    """Where a value sits on a three-zone meter, as '0'–'100' (unlocalised)."""
    third = 100 / 3
    if value <= good:
        spot = third * value / good if good else 0
    elif value <= fair:
        spot = third + third * (value - good) / (fair - good)
    else:
        spot = min(100, 2 * third + third * (value - fair) / fair)
    return f"{spot:.1f}"


class PerformanceCard(DashboardComponent):
    """Application response-time percentiles and failure rate, rated
    good/fair/poor against the bands above."""

    template_name = "django_prometric/components/performance.html"
    title = _("Performance")
    capability = base.PERFORMANCE
    width = "half"

    _RATING_WORDS = {"good": _("Good"), "fair": _("Okay"), "poor": _("Slow")}

    def get_context(self):
        stats = self.provider.get_performance(self.period)
        return {"stats": stats, "rows": self.rows(stats)}

    def rows(self, stats):
        if stats is None:
            return []
        rows = []
        for key, (good, fair) in _LATENCY_BANDS.items():
            value = getattr(stats, key, None)
            if value is None:
                continue
            rating = _rating(value, good, fair)
            rows.append(
                {
                    "label": key[:3],
                    "value": value,
                    "is_rate": False,
                    "rating": rating,
                    "word": self._RATING_WORDS[rating],
                    "position": _meter_position(value, good, fair),
                    "tip": "{} {}".format(
                        _PERCENTILE_MEANING[key],
                        _("Good under %(good)s ms, okay under %(fair)s ms, slow above that.")
                        % {"good": good, "fair": fair},
                    ),
                }
            )
        if stats.failure_rate is not None:
            good, fair = _FAILURE_BANDS
            rating = _rating(stats.failure_rate, good, fair)
            rows.append(
                {
                    "label": _("Failure rate"),
                    "value": stats.failure_rate,
                    "is_rate": True,
                    "rating": rating,
                    "word": {"good": _("Good"), "fair": _("Okay"), "poor": _("High")}[rating],
                    "position": _meter_position(stats.failure_rate, good, fair),
                    "tip": _(
                        "The share of requests that ended in an error. "
                        "Good under 1%, okay under 5%, worrying above that."
                    ),
                }
            )
        return rows


class SlowestRoutesTable(DashboardComponent):
    """Routes ordered by p95 response time — where optimisation pays off."""

    template_name = "django_prometric/components/slowest.html"
    title = _("Slowest routes")
    capability = base.SLOWEST

    def get_context(self):
        routes, _errors = filter_routes(collect_routes())
        rows = [
            (perf, route_for_transaction(perf.transaction, routes))
            for perf in self.provider.get_slowest_routes(self.period)
        ]
        return {"rows": rows}


class SecurityCard(DashboardComponent):
    """Firewall mitigations: what was blocked or challenged, by what, from where."""

    template_name = "django_prometric/components/security.html"
    title = _("Security")
    capability = base.SECURITY
    width = "half"

    def get_context(self):
        return {"security": self.provider.get_security(self.period)}


class InsightsPanel(DashboardComponent):
    """Actionable findings, merged from every source that can derive any —
    problems first, each with what to do about it."""

    template_name = "django_prometric/components/insights.html"
    title = _("Insights")
    capability = base.INSIGHTS

    _ORDER = {base.INSIGHT_BAD: 0, base.INSIGHT_WARN: 1, base.INSIGHT_GOOD: 2}
    one_per_provider = False  # findings from every source merge into one panel

    @classmethod
    def build_all(cls, providers, period):
        components = super().build_all(providers, period)
        for component in components:
            component.sources = [
                provider for provider in providers if base.INSIGHTS in provider.capabilities()
            ]
        return components

    def get_context(self):
        insights = []
        for provider in self.sources:
            try:
                found = provider.get_insights(self.period)
            except ProviderError:
                continue  # a broken source must not hide the others' findings
            for insight in found:
                insight.source = provider.verbose_name
            insights.extend(found)
        insights.sort(key=lambda insight: self._ORDER.get(insight.severity, 3))
        return {"insights": insights}


class BotsCard(DashboardComponent):
    """Verified bots vs everyone else, and which crawlers they are."""

    template_name = "django_prometric/components/bots.html"
    title = _("Bots")
    capability = base.BOTS
    width = "half"

    def get_context(self):
        bots = self.provider.get_bots(self.period)
        share = bots["bots"] / bots["total"] if bots["total"] else 0
        return {
            "bots": bots,
            "bot_share": share,
            "chart": {
                "labels": [str(_("People & unverified")), str(_("Verified bots"))],
                "values": [bots["humans"], bots["bots"]],
            },
        }


class SeoCard(DashboardComponent):
    """Search-engine crawler activity: who crawls, what they fetch."""

    template_name = "django_prometric/components/seo.html"
    title = _("SEO crawlers")
    capability = base.SEO
    width = "half"

    def get_context(self):
        return {"seo": self.provider.get_seo(self.period)}


class AudienceCard(DashboardComponent):
    """Real users' browsers, systems and devices — crawlers excluded."""

    template_name = "django_prometric/components/audience.html"
    title = _("Users")
    capability = base.AUDIENCE
    width = "half"

    def get_context(self):
        return {"audience": self.provider.get_audience(self.period)}


class NetworkCard(DashboardComponent):
    """Protocol mix: HTTP versions and TLS versions."""

    template_name = "django_prometric/components/network.html"
    title = _("Network")
    capability = base.NETWORK
    width = "half"

    def get_context(self):
        return {"network": self.provider.get_network(self.period)}


class BackendCard(DashboardComponent):
    """Where request time is spent inside the application."""

    template_name = "django_prometric/components/backend.html"
    title = _("Where time goes")
    capability = base.BACKEND
    width = "half"

    def get_context(self):
        return {"backend": self.provider.get_backend(self.period)}


class SlowestQueriesTable(DashboardComponent):
    """Database queries ranked by the total time they cost."""

    template_name = "django_prometric/components/queries.html"
    title = _("Slowest queries")
    capability = base.QUERIES

    def get_context(self):
        return {"queries": self.provider.get_slowest_queries(self.period)}


class IssuesTable(DashboardComponent):
    """Most frequent application errors."""

    template_name = "django_prometric/components/issues.html"
    title = _("Errors")
    capability = base.ISSUES

    def get_context(self):
        return {"issues": self.provider.get_top_issues(self.period)}


class DatabaseCard(DashboardComponent):
    """Database-level health: size, row/table counts, connections and the
    lifetime counters (commits, cache hit, deadlocks) since the last reset."""

    template_name = "django_prometric/components/database.html"
    title = _("Database health")
    capability = base.DATABASE

    def get_context(self):
        return {"stats": self.provider.get_database_stats(self.period)}


class TablesTable(DashboardComponent):
    """The largest user tables with their bloat and vacuum status."""

    template_name = "django_prometric/components/tables.html"
    title = _("Tables")
    capability = base.TABLES

    # Rows this fraction dead are flagged as bloated in the template.
    _DEAD_WARN = 0.2

    def get_context(self):
        tables = self.provider.get_table_stats(self.period)
        return {"tables": tables, "dead_warn": self._DEAD_WARN}


class IndexesTable(DashboardComponent):
    """Unused indexes (reclaimable space) and, below, the most-used ones."""

    template_name = "django_prometric/components/indexes.html"
    title = _("Indexes")
    capability = base.INDEXES

    def get_context(self):
        indexes = self.provider.get_index_stats(self.period)
        unused = indexes["unused"]
        return {
            "unused": unused,
            "used": indexes["used"],
            "reclaimable": sum(index.size_bytes for index in unused),
        }


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
