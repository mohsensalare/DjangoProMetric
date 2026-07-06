"""The data shapes providers return and templates render.

Plain dataclasses, deliberately free of provider logic: a provider fills the
fields it knows and leaves the rest at their defaults, and the templates only
ever read these shapes — never a provider's raw payloads.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

NOTICE_INFO = "info"
NOTICE_WARN = "warn"


@dataclass
class Notice:
    """A side note a provider left while answering — e.g. a clamped window.

    ``warn`` notices flag that the data shown differs from what was asked
    for; ``info`` ones are purely informative.
    """

    message: str
    level: str = NOTICE_INFO  # NOTICE_INFO | NOTICE_WARN

    def __str__(self) -> str:
        return str(self.message)


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
    p75_ms: float | None = None
    p95_ms: float | None = None
    p99_ms: float | None = None
    avg_ms: float | None = None
    failure_rate: float | None = None  # 0.0–1.0
    requests: int | None = None


@dataclass
class RoutePerformance:
    """Response-time percentiles of one route/transaction, app-side."""

    transaction: str
    requests: int = 0
    p50_ms: float | None = None
    p75_ms: float | None = None
    p95_ms: float | None = None
    p99_ms: float | None = None
    failure_rate: float | None = None


@dataclass
class QueryStat:
    """One database query aggregated across its calls, slowest first."""

    query: str
    calls: int = 0
    avg_ms: float | None = None
    total_ms: float | None = None


@dataclass
class Cumulative:
    """A counter accumulated since ``since``, not scoped to the query window.

    Providers wrap a value in this to tell the UI the number is a lifetime
    counter (last reset at ``since``), not a per-period figure — so the UI can
    mark it with a "since …" badge (see ``components/_cumulative.html``) rather
    than pretend it matches the selected period. When a provider later windows
    the counter (see :class:`CounterSampler`, Phase 2), it returns a plain
    number instead and the badge disappears with no template change.
    """

    value: int | float | None = None
    since: dt.datetime | None = None  # when this counter last reset

    def __str__(self) -> str:  # so templates can print it directly
        return "" if self.value is None else str(self.value)


@dataclass
class DatabaseStats:
    """Database-level health, mixing point-in-time state and lifetime counters.

    Point-in-time fields are correct at read time. The counter fields are
    wrapped in :class:`Cumulative` by the provider so the UI can flag them as
    accumulated since the last statistics reset.
    """

    # point-in-time (always current — no badge)
    size_bytes: int = 0
    table_count: int = 0
    index_count: int = 0
    row_estimate: int = 0
    dead_estimate: int = 0
    connections: dict = field(default_factory=dict)  # {"active": n, "idle": n, …}
    max_connections: int | None = None
    # cumulative (each wrapped in Cumulative by the provider)
    commits: Cumulative | int | None = None
    rollbacks: Cumulative | int | None = None
    cache_hit_ratio: Cumulative | float | None = None
    deadlocks: Cumulative | int | None = None
    temp_bytes: Cumulative | int | None = None

    @property
    def used_connections(self) -> int:
        return sum(self.connections.values()) if self.connections else 0

    @property
    def connection_ratio(self) -> float | None:
        if not self.max_connections:
            return None
        return self.used_connections / self.max_connections


@dataclass
class TableStat:
    """One user table's size, row estimates and scan activity."""

    name: str
    size_bytes: int = 0
    rows: int = 0
    dead_rows: int = 0
    seq_scans: int = 0  # cumulative since the last stats reset
    idx_scans: int = 0  # cumulative since the last stats reset
    last_autovacuum: dt.datetime | None = None

    @property
    def dead_ratio(self) -> float | None:
        total = self.rows + self.dead_rows
        return self.dead_rows / total if total else None


@dataclass
class IndexStat:
    """One index's size and use. ``scans == 0`` means it was never used."""

    name: str
    table: str = ""
    size_bytes: int = 0
    scans: int = 0  # cumulative; 0 = never used since the last stats reset


# Insight severities, worst first.
INSIGHT_BAD = "bad"
INSIGHT_WARN = "warn"
INSIGHT_GOOD = "good"


@dataclass
class Insight:
    """One actionable finding a provider derived from its data.

    ``title`` states what was found, ``detail`` backs it with numbers, and
    ``action`` says what to do about it. Good findings confirm health and
    carry no action.
    """

    severity: str  # INSIGHT_BAD | INSIGHT_WARN | INSIGHT_GOOD
    title: str
    detail: str = ""
    action: str = ""


@dataclass
class IssueStat:
    """One grouped application error."""

    title: str
    culprit: str = ""
    events: int = 0
    users: int = 0
    permalink: str = ""


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
