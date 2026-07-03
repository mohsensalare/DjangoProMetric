"""The pluggable analytics provider contract.

The dashboard never talks to a concrete backend (Cloudflare, local
middleware, …) directly — it only consumes this interface. Third parties can
subclass :class:`AnalyticsProvider` and point
``DJANGO_PROMETRIC["ANALYTICS_PROVIDER"]`` at their class to feed the
dashboard from any data source without touching the package core.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

# ---------------------------------------------------------------------------
# Capabilities — a provider advertises what it can answer; dashboard
# components declare what they need and are hidden or locked otherwise.
# ---------------------------------------------------------------------------
OVERVIEW = "overview"
TIMESERIES = "timeseries"
PATHS = "paths"
COUNTRY = "country"
STATUS = "status"
CACHE = "cache"
METHOD = "method"
PERFORMANCE = "performance"
BANDWIDTH = "bandwidth"
UNIQUES = "uniques"
THREATS = "threats"
VISITS = "visits"

# Breakdown dimensions accepted by AnalyticsProvider.get_breakdown().
DIM_COUNTRY = "country"
DIM_STATUS = "status"
DIM_CACHE = "cache"
DIM_METHOD = "method"

_PERIODS = {
    "24h": (_("Last 24 hours"), dt.timedelta(hours=24)),
    "7d": (_("Last 7 days"), dt.timedelta(days=7)),
    "30d": (_("Last 30 days"), dt.timedelta(days=30)),
}
DEFAULT_PERIOD = "24h"


@dataclass(frozen=True)
class Period:
    key: str
    label: str
    start: dt.datetime
    end: dt.datetime

    @property
    def days(self) -> float:
        return (self.end - self.start).total_seconds() / 86400

    @classmethod
    def from_key(cls, key: str | None) -> Period:
        if key not in _PERIODS:
            key = DEFAULT_PERIOD
        label, delta = _PERIODS[key]
        end = timezone.now()
        return cls(key=key, label=label, start=end - delta, end=end)

    @classmethod
    def choices(cls):
        return [(key, label) for key, (label, _delta) in _PERIODS.items()]


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


@dataclass
class OverviewStats:
    requests: int = 0
    unique_visitors: int | None = None
    page_views: int | None = None
    bandwidth_bytes: int | None = None
    cached_requests: int | None = None
    threats: int | None = None
    errors: int | None = None
    avg_response_ms: float | None = None

    @property
    def cache_ratio(self) -> float | None:
        if self.cached_requests is None or not self.requests:
            return None
        return self.cached_requests / self.requests

    @property
    def error_ratio(self) -> float | None:
        if self.errors is None or not self.requests:
            return None
        return self.errors / self.requests


@dataclass
class TimeseriesPoint:
    label: str
    requests: int = 0


@dataclass
class PathStat:
    path: str
    requests: int = 0
    bandwidth_bytes: int = 0
    visits: int = 0


@dataclass
class BreakdownItem:
    label: str
    value: int = 0
    share: float = 0.0  # 0.0–1.0 of the listed total


def with_shares(items: list[BreakdownItem]) -> list[BreakdownItem]:
    total = sum(item.value for item in items)
    if total:
        for item in items:
            item.share = item.value / total
    return items


@dataclass
class PerformanceStats:
    p50_ms: float | None = None
    p95_ms: float | None = None
    p99_ms: float | None = None
    avg_ms: float | None = None


@dataclass
class RouteMetrics:
    requests: int = 0
    visits: int | None = None
    bandwidth_bytes: int | None = None
    errors: int | None = None
    last_seen: dt.datetime | None = None
    countries: list[BreakdownItem] = field(default_factory=list)
    statuses: list[BreakdownItem] = field(default_factory=list)
    methods: list[BreakdownItem] = field(default_factory=list)
    cache: list[BreakdownItem] = field(default_factory=list)
    timeseries: list[TimeseriesPoint] = field(default_factory=list)
    performance: PerformanceStats | None = None

    @property
    def error_ratio(self) -> float | None:
        if self.errors is None or not self.requests:
            return None
        return self.errors / self.requests


# ---------------------------------------------------------------------------
# Report schema — the exact shape of the JSON stored in ``Snapshot.data``.
# Snapshots are kept for months, so this schema is deliberately independent
# of the live dataclasses above and carries a version number. Always build a
# Report and call ``as_dict()``; never hand-write the dict.
# ---------------------------------------------------------------------------
REPORT_VERSION = 1


@dataclass
class ReportItem:
    """One breakdown line of a report, e.g. a country or a status code."""

    label: str
    value: int = 0


@dataclass
class ReportRoute:
    """One row of a report's top-routes table."""

    route: str
    requests: int = 0
    bandwidth_bytes: int = 0


@dataclass
class Report:
    """A report as stored in ``Snapshot.data`` — this class is the schema.

    Write-only: snapshots are built through this class, while stored ones
    are read back as plain dicts with exactly these keys.
    """

    window_start: str  # ISO datetime the data covers from
    window_end: str  # ISO datetime the data covers to
    overview: OverviewStats
    countries: list[ReportItem] = field(default_factory=list)  # largest first
    statuses: list[ReportItem] = field(default_factory=list)  # largest first
    top_routes: list[ReportRoute] = field(default_factory=list)  # busiest first
    unmatched_paths: int = 0  # concrete paths no route claimed
    unmatched_requests: int = 0
    version: int = REPORT_VERSION

    def as_dict(self) -> dict:
        return asdict(self)


class AnalyticsProvider:
    """Base class every analytics data source implements.

    Subclasses should raise :class:`ProviderError` for anything the user
    needs to know about; other exceptions are treated as bugs.
    """

    slug = "base"
    verbose_name = _("Analytics")

    def __init__(self):
        # Human-readable notes collected while answering queries, e.g.
        # "time range clamped to 24h by your plan". Rendered in the UI.
        self.notices: list[str] = []

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

    def add_notice(self, message) -> None:
        if message not in self.notices:
            self.notices.append(message)

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

    def invalidate_cache(self) -> None:
        """Drop any cached upstream responses (used by the refresh button)."""
