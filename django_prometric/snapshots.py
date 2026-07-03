"""Saving reports for later comparison.

:func:`capture` freezes what the provider reports right now — together with
the active route filters — into a :class:`~django_prometric.models.Snapshot`
row. The stored JSON follows the :class:`~django_prometric.providers.base.Report`
schema. Taking the same report again later (a "retake") links back to the
first snapshot, and :func:`comparison` lays the runs out side by side.
"""

from django.utils.translation import gettext_lazy as _

from .conf import get_config
from .models import Snapshot
from .providers import base
from .routes import attribute, collect_routes, filter_routes

TOP_ROUTES = 20


def capture(provider, period, parent=None):
    """Fetch the current report and store it as a Snapshot."""
    if parent is not None:
        parent = parent.parent or parent  # a series is anchored to its first take
    capabilities = provider.capabilities()
    report = base.Report(
        window_start=period.start.isoformat(),
        window_end=period.end.isoformat(),
        overview=provider.get_overview(period),
    )
    if base.COUNTRY in capabilities:
        report.countries = _items(provider.get_breakdown(base.DIM_COUNTRY, period))
    if base.STATUS in capabilities:
        report.statuses = _items(provider.get_breakdown(base.DIM_STATUS, period))
    if base.PATHS in capabilities:
        report.top_routes, report.unmatched_paths, report.unmatched_requests = _top_routes(
            provider, period
        )
    return Snapshot.objects.create(
        provider=provider.slug,
        period=period.key,
        filters=dict(get_config()["ROUTES"]),
        data=report.as_dict(),
        parent=parent,
    )


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
_METRICS = [
    ("requests", _("Requests")),
    ("unique_visitors", _("Unique visitors")),
    ("page_views", _("Page views")),
    ("bandwidth_bytes", _("Bandwidth")),
    ("cached_requests", _("Cached requests")),
    ("errors", _("Errors")),
    ("threats", _("Threats")),
    ("avg_response_ms", _("Avg response time")),
]


def comparison(series):
    """Rows for the side-by-side table: a metric per row, a snapshot per column."""
    rows = []
    for key, label in _METRICS:
        values = [snapshot.data.get("overview", {}).get(key) for snapshot in series]
        if all(value is None for value in values):
            continue
        rows.append({"key": key, "label": label, "values": values, "change": _change(values)})
    return rows


def _change(values):
    """Relative change from the first known value to the last, or None."""
    numbers = [value for value in values if value is not None]
    if len(numbers) < 2 or not numbers[0]:
        return None
    return (numbers[-1] - numbers[0]) / numbers[0]
