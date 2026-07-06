"""Report schema — the exact shape of the JSON stored in ``Snapshot.data``.

Snapshots are kept for months, so this schema is deliberately independent of
the live dataclasses in :mod:`.types` and carries a version number. Always
build a :class:`Report` and call ``as_dict()``; never hand-write the dict.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .types import OverviewStats, PerformanceStats, RoutePerformance

REPORT_VERSION = 2


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
    """One provider's report as stored in ``Snapshot.data`` — this class is
    the schema. Every section is optional; a provider fills what it can.

    Write-only: snapshots are built through this class, while stored ones
    are read back as plain dicts with exactly these keys.
    """

    window_start: str  # ISO datetime the data covers from
    window_end: str  # ISO datetime the data covers to
    overview: OverviewStats | None = None
    performance: PerformanceStats | None = None
    countries: list[ReportItem] = field(default_factory=list)  # largest first
    statuses: list[ReportItem] = field(default_factory=list)  # largest first
    top_routes: list[ReportRoute] = field(default_factory=list)  # busiest first
    slowest_routes: list[RoutePerformance] = field(default_factory=list)  # slowest first
    unmatched_paths: int = 0  # concrete paths no route claimed
    unmatched_requests: int = 0
    clamped: bool = False  # True when a provider limit shortened the window
    version: int = REPORT_VERSION

    def as_dict(self) -> dict:
        return asdict(self)
