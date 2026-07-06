"""The pluggable analytics provider contract.

The dashboard never talks to a concrete backend (Cloudflare, local
middleware, …) directly — it only consumes this interface. Third parties can
subclass :class:`AnalyticsProvider` and list their class in
``DJANGO_PROMETRIC["PROVIDERS"]`` to feed the dashboard from any data source
without touching the package core.

This module is the one import provider authors need: it defines the contract
(:class:`AnalyticsProvider`, :class:`ProviderError`) and re-exports the whole
provider vocabulary — capability constants (:mod:`.capabilities`), time
windows (:mod:`.periods`), result shapes (:mod:`.types`) and the snapshot
schema (:mod:`.report`).
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from .capabilities import (  # noqa: F401 — re-exported vocabulary
    AUDIENCE,
    BACKEND,
    BANDWIDTH,
    BOTS,
    CACHE,
    COUNTRY,
    DATABASE,
    DIM_CACHE,
    DIM_COUNTRY,
    DIM_METHOD,
    DIM_STATUS,
    INDEXES,
    INSIGHTS,
    ISSUES,
    METHOD,
    NETWORK,
    OVERVIEW,
    PATHS,
    PERFORMANCE,
    QUERIES,
    SECURITY,
    SEO,
    SLOWEST,
    STATUS,
    TABLES,
    THREATS,
    TIMESERIES,
    UNIQUES,
    VISITS,
)
from .periods import (  # noqa: F401 — re-exported vocabulary
    CUSTOM_PERIOD,
    DEFAULT_PERIOD,
    Period,
)
from .report import (  # noqa: F401 — re-exported vocabulary
    REPORT_VERSION,
    Report,
    ReportItem,
    ReportRoute,
)
from .types import (  # noqa: F401 — re-exported vocabulary
    INSIGHT_BAD,
    INSIGHT_GOOD,
    INSIGHT_WARN,
    NOTICE_INFO,
    NOTICE_WARN,
    BreakdownItem,
    Cumulative,
    DatabaseStats,
    IndexStat,
    Insight,
    IssueStat,
    Notice,
    OverviewStats,
    PathStat,
    PerformanceStats,
    QueryStat,
    RouteMetrics,
    RoutePerformance,
    TableStat,
    TimeseriesPoint,
    with_shares,
)


class ProviderError(Exception):
    """A user-presentable analytics failure.

    ``kind`` lets the UI react appropriately:
    ``config``  – provider is not (fully) configured
    ``auth``    – credentials rejected
    ``network`` – upstream unreachable
    ``quota``   – plan/time-range limit hit
    ``plan``    – feature not included in the current plan
    ``error``   – anything else
    """

    def __init__(self, message, kind: str = "error"):
        super().__init__(message)
        self.message = message
        self.kind = kind


class AnalyticsProvider:
    """Base class every analytics data source implements.

    Subclasses should raise :class:`ProviderError` for anything the user
    needs to know about; other exceptions are treated as bugs.
    """

    slug = "base"
    verbose_name = _("Analytics")
    # One line explaining what kind of data this source contributes,
    # e.g. "Edge traffic" vs "Application performance".
    kind = _("Analytics")
    # Farthest back (in days) this source can answer, or None for unlimited.
    # The UI warns and offers clamping when a request exceeds it.
    max_period_days: int | None = None
    # Template rendered as this provider's section on the route-detail page.
    route_template = ""

    def __init__(self):
        # Human-readable notes collected while answering queries, e.g.
        # "time range clamped to 24h by your plan". Rendered in the UI.
        self.notices: list[Notice] = []

    # -- configuration -----------------------------------------------------
    @property
    def is_configured(self) -> bool:
        return True

    def configuration_help(self) -> str:
        """Shown on the onboarding screen when not configured."""
        return ""

    def description(self) -> str:
        """Short data-source line for the dashboard header."""
        return ""

    def capabilities(self) -> set:
        return set()

    def add_notice(self, message, level: str = NOTICE_INFO) -> None:
        if not any(notice.message == message for notice in self.notices):
            self.notices.append(Notice(message=message, level=level))

    def exceeds_limit(self, period: Period) -> bool:
        """Whether the requested range is longer than this source can serve."""
        return self.max_period_days is not None and period.days > self.max_period_days

    def limit_period(self, period: Period) -> Period:
        """Clamp the period to this source's reach, leaving a notice."""
        if not self.exceeds_limit(period):
            return period
        self.add_notice(
            _("%(name)s can only look back %(days)s days; the window was shortened.")
            % {"name": self.verbose_name, "days": self.max_period_days},
            level=NOTICE_WARN,
        )
        return period.clamped_to(self.max_period_days)

    # -- data --------------------------------------------------------------
    def get_overview(self, period: Period) -> OverviewStats:
        raise NotImplementedError

    def get_timeseries(self, period: Period) -> list[TimeseriesPoint]:
        raise NotImplementedError

    def get_path_stats(self, period: Period, limit: int = 1500) -> list[PathStat]:
        raise NotImplementedError

    def get_breakdown(self, dimension: str, period: Period, limit: int = 12) -> list[BreakdownItem]:
        raise NotImplementedError

    def get_route_metrics(self, route, period: Period) -> RouteMetrics:
        """``route`` is a :class:`django_prometric.routes.RouteInfo`."""
        raise NotImplementedError

    def get_performance(self, period: Period) -> PerformanceStats | None:
        """Site-wide response-time percentiles, when available."""
        return None

    def get_slowest_routes(self, period: Period, limit: int = 10) -> list[RoutePerformance]:
        """Routes ordered by p95 response time, slowest first."""
        return []

    def get_top_issues(self, period: Period, limit: int = 10) -> list[IssueStat]:
        """Most frequent grouped application errors."""
        return []

    def get_security(self, period: Period) -> dict:
        """Firewall mitigations: total plus actions/sources/countries/paths
        breakdowns (lists of :class:`BreakdownItem`)."""
        return {"total": 0, "actions": [], "sources": [], "countries": [], "paths": []}

    def get_bots(self, period: Period) -> dict:
        """Human vs automated traffic: ``total`` requests, ``humans``,
        ``bots``, and a ``categories`` breakdown of the automated share."""
        return {"total": 0, "humans": 0, "bots": 0, "categories": []}

    def get_seo(self, period: Period) -> dict:
        """Search-engine crawler activity: ``total`` crawler requests,
        ``engines`` breakdown, and the most crawled ``paths``."""
        return {"total": 0, "engines": [], "paths": []}

    def get_network(self, period: Period) -> dict:
        """Protocol mix: ``http_versions`` and ``tls`` breakdowns
        (lists of :class:`BreakdownItem`)."""
        return {"http_versions": [], "tls": []}

    def get_audience(self, period: Period) -> dict:
        """Real users' clients — bots excluded where the source can tell:
        ``browsers``, ``os`` and ``devices`` breakdowns."""
        return {"browsers": [], "os": [], "devices": []}

    def get_slowest_queries(self, period: Period, limit: int = 8) -> list[QueryStat]:
        """Database queries ordered by total time spent, worst first."""
        return []

    def get_database_stats(self, period: Period) -> DatabaseStats | None:
        """Database-level health: size, connections and lifetime counters."""
        return None

    def get_table_stats(self, period: Period, limit: int = 10) -> list[TableStat]:
        """The largest user tables, biggest first."""
        return []

    def get_index_stats(self, period: Period, limit: int = 10) -> dict:
        """Unused and most-used indexes: ``{"unused": [...], "used": [...]}``."""
        return {"unused": [], "used": []}

    def get_backend(self, period: Period) -> dict:
        """Where application time goes: an ``ops`` breakdown of total time
        (values in ms) across database, templates, upstream calls, …"""
        return {"ops": []}

    def get_insights(self, period: Period) -> list[Insight]:
        """Actionable findings this source can derive from its own data."""
        return []

    def get_route_context(self, route, period: Period) -> dict:
        """Context for this provider's section on the route-detail page.

        The default ships the full :class:`RouteMetrics`; providers with a
        different shape of per-route data override this together with
        ``route_template``.
        """
        return {"metrics": self.get_route_metrics(route, period)}

    def invalidate_cache(self) -> None:
        """Drop any cached upstream responses (used by the refresh button)."""
