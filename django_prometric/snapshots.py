"""Saving reports for later comparison.

:func:`capture` freezes what the chosen providers report right now —
together with the active route filters — into a
:class:`~django_prometric.models.Snapshot` row. The stored JSON holds one
:class:`~django_prometric.providers.base.Report` per provider. Taking the
same report again later (a "retake") links back to the first snapshot, and
:func:`comparison` lays the runs out side by side, per provider.
"""

from django.utils.translation import gettext as _

from .conf import get_config
from .models import Snapshot
from .providers import base, get_provider
from .routes import attribute, collect_routes, filter_routes

TOP_ROUTES = 20
SLOWEST_ROUTES = 20


def capture(providers, period, parent=None):
    """Fetch the current reports and store them as one Snapshot.

    Providers that fail don't sink the whole snapshot: their error messages
    come back in ``errors`` and the snapshot keeps the reports that worked.
    Returns ``(snapshot, errors)``; snapshot is None when nothing succeeded.
    """
    if parent is not None:
        parent = parent.parent or parent  # a series is anchored to its first take
    reports, errors = {}, []
    for provider in providers:
        try:
            reports[provider.slug] = _provider_report(provider, period).as_dict()
        except base.ProviderError as error:
            errors.append(f"{provider.verbose_name}: {error.message}")
    if not reports:
        return None, errors
    snapshot = Snapshot.objects.create(
        provider=",".join(reports),
        period=period.key,
        window_start=period.start,
        window_end=period.end,
        filters=dict(get_config()["ROUTES"]),
        data={"version": base.REPORT_VERSION, "reports": reports},
        parent=parent,
    )
    return snapshot, errors


def _provider_report(provider, period) -> base.Report:
    effective = provider.limit_period(period)
    capabilities = provider.capabilities()
    report = base.Report(
        window_start=effective.start.isoformat(),
        window_end=effective.end.isoformat(),
        clamped=effective is not period,
    )
    if base.OVERVIEW in capabilities:
        report.overview = provider.get_overview(effective)
    if base.PERFORMANCE in capabilities:
        report.performance = provider.get_performance(effective)
    if base.COUNTRY in capabilities:
        report.countries = _items(provider.get_breakdown(base.DIM_COUNTRY, effective))
    if base.STATUS in capabilities:
        report.statuses = _items(provider.get_breakdown(base.DIM_STATUS, effective))
    if base.PATHS in capabilities:
        report.top_routes, report.unmatched_paths, report.unmatched_requests = _top_routes(
            provider, effective
        )
    if base.SLOWEST in capabilities:
        report.slowest_routes = provider.get_slowest_routes(effective, limit=SLOWEST_ROUTES)
    return report


def _items(breakdown):
    return [base.ReportItem(label=item.label, value=item.value) for item in breakdown]


def _top_routes(provider, period):
    routes, _errors = filter_routes(collect_routes())
    totals, unmatched = attribute(provider.get_path_stats(period), routes)
    busiest = sorted(
        (route for route in routes if route.key in totals),
        key=lambda route: totals[route.key].requests,
        reverse=True,
    )[:TOP_ROUTES]
    top = [
        base.ReportRoute(
            route=route.display,
            requests=totals[route.key].requests,
            bandwidth_bytes=totals[route.key].bandwidth_bytes,
        )
        for route in busiest
    ]
    return top, len(unmatched), sum(stat.requests for stat in unmatched)


# Overview metrics shown in the comparison table, in display order.
# Keys are OverviewStats field names (see providers.base.Report).
_OVERVIEW_METRICS = [
    ("requests", _("Requests")),
    ("unique_visitors", _("Unique visitors")),
    ("page_views", _("Page views")),
    ("bandwidth_bytes", _("Bandwidth")),
    ("cached_requests", _("Cached requests")),
    ("errors", _("Errors")),
    ("threats", _("Threats")),
]

# PerformanceStats field names, same idea.
_PERFORMANCE_METRICS = [
    ("requests", _("Transactions")),
    ("p50_ms", _("p50")),
    ("p75_ms", _("p75")),
    ("p95_ms", _("p95")),
    ("p99_ms", _("p99")),
    ("failure_rate", _("Failure rate")),
]


def comparison(snapshots):
    """Per-provider sections for a side-by-side table: a metric per row, a
    snapshot per column. The first snapshot is the baseline every other
    column's delta is measured against."""
    sections = []
    for slug in _slugs(snapshots):
        rows = _rows(
            [snapshot.reports.get(slug) or {} for snapshot in snapshots],
            [("overview", _OVERVIEW_METRICS), ("performance", _PERFORMANCE_METRICS)],
        )
        if rows:
            sections.append(_section(slug, _("Metric"), rows))
    return sections


# The rows worth pulling out as the comparison's headline, in order.
_HEADLINE_KEYS = (
    "overview.requests",
    "overview.errors",
    "overview.bandwidth_bytes",
    "performance.p95_ms",
    "performance.failure_rate",
)


def headline(sections):
    """A handful of key rows summarised above the comparison tables."""
    found = {}
    for section in sections:
        for row in section["rows"]:
            found.setdefault(row["key"], row)
    return [found[key] for key in _HEADLINE_KEYS if key in found]


def route_comparison(snapshots):
    """Route-by-route sections: requests per route (from the stored top
    routes) and p95 per transaction (from the stored slowest routes)."""
    sections = []
    for slug in _slugs(snapshots):
        reports = [snapshot.reports.get(slug) or {} for snapshot in snapshots]
        for title, list_key, label_key, value_key, worse_up, unit in (
            (_("Requests by route"), "top_routes", "route", "requests", False, ""),
            (_("p95 by route"), "slowest_routes", "transaction", "p95_ms", True, "ms"),
        ):
            rows = _route_rows(reports, list_key, label_key, value_key, worse_up, unit)
            if rows:
                section = _section(slug, _("Route"), rows)
                section["title"] = title
                sections.append(section)
    return sections


def _slugs(snapshots):
    slugs = []
    for snapshot in snapshots:
        for slug in snapshot.reports:
            if slug not in slugs:
                slugs.append(slug)
    return slugs


def _section(slug, first_column, rows):
    provider = get_provider(slug)
    return {
        "slug": slug,
        "name": provider.verbose_name if provider else slug,
        "first_column": first_column,
        "rows": rows,
    }


def _rows(reports, groups):
    rows = []
    for section, metrics in groups:
        for key, label in metrics:
            values = [(report.get(section) or {}).get(key) for report in reports]
            if all(value is None for value in values):
                continue
            # Growth in latency, failures or threats is a regression.
            worse_when_up = key.endswith("_ms") or key in ("failure_rate", "errors", "threats")
            rows.append(
                {
                    "key": f"{section}.{key}",
                    "unit": "ms" if key.endswith("_ms") else "",
                    "is_bytes": key == "bandwidth_bytes",
                    "is_rate": key == "failure_rate",
                    "worse_when_up": worse_when_up,
                    "mono": False,
                    "label": label,
                    "cells": _cells(values, worse_when_up),
                }
            )
    return rows


def _route_rows(reports, list_key, label_key, value_key, worse_up, unit):
    tables = [
        {entry.get(label_key): entry.get(value_key) for entry in report.get(list_key) or []}
        for report in reports
    ]
    labels = []
    for table in tables:
        for label in table:
            if label not in labels:
                labels.append(label)
    rows = []
    for label in labels:
        values = [table.get(label) for table in tables]
        rows.append(
            {
                "key": f"{list_key}.{label}",
                "unit": unit,
                "is_bytes": False,
                "is_rate": False,
                "worse_when_up": worse_up,
                "mono": True,
                "label": label,
                "cells": _cells(values, worse_up),
            }
        )
    rows.sort(
        key=lambda row: next(
            (cell["value"] for cell in reversed(row["cells"]) if cell["value"] is not None), 0
        ),
        reverse=True,
    )
    return rows


def _cells(values, worse_when_up):
    """One cell per column: the value plus its delta against the baseline
    (column 0), pre-classified as good or bad for colouring."""
    baseline = values[0]
    cells = [{"value": baseline, "delta": None, "cls": ""}]
    for value in values[1:]:
        delta = (value - baseline) / baseline if value is not None and baseline else None
        cls = ""
        if delta:
            regressed = delta > 0 if worse_when_up else delta < 0
            cls = "bad" if regressed else "good"
        cells.append({"value": value, "delta": delta, "cls": cls})
    return cells
