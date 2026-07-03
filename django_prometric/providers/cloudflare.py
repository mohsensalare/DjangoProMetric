"""Cloudflare GraphQL Analytics provider.

Implemented with the standard library only (urllib) so the package stays
dependency-free. Responses are cached through Django's cache framework to
respect Cloudflare's API rate limits.

Plan-awareness: on free zones Cloudflare limits ``httpRequestsAdaptiveGroups``
queries to a 24-hour window (GraphQL error code ``quota``) and does not expose
TTFB quantiles (code ``authz``). Both cases are detected at runtime and
degrade gracefully instead of erroring the dashboard.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import urllib.error
import urllib.request

from django.core.cache import caches
from django.utils.translation import gettext as _

from ..conf import get_config
from . import base
from .base import (
    AnalyticsProvider,
    BreakdownItem,
    OverviewStats,
    PathStat,
    PerformanceStats,
    Period,
    ProviderError,
    RouteMetrics,
    TimeseriesPoint,
    with_shares,
)

_DATE = "%Y-%m-%d"
_DATETIME = "%Y-%m-%dT%H:%M:%SZ"

# Cache keys for runtime feature detection (per process + shared cache).
_PERF_FLAG_KEY = "prometric:cf:performance-available"
_CLAMP_FLAG_KEY = "prometric:cf:adaptive-clamped"
# Bumped by invalidate_cache() so every cached query key changes at once.
_VERSION_KEY = "prometric:cf:key-version"

_BREAKDOWN_DIMENSIONS = {
    base.DIM_CACHE: "cacheStatus",
    base.DIM_METHOD: "clientRequestHTTPMethodName",
    base.DIM_COUNTRY: "clientCountryName",
    base.DIM_STATUS: "edgeResponseStatus",
}


class CloudflareProvider(AnalyticsProvider):
    slug = "cloudflare"
    verbose_name = "Cloudflare"

    def __init__(self):
        super().__init__()
        config = get_config()
        cf = config["CLOUDFLARE"]
        self.api_url = cf["API_URL"]
        self.timeout = cf["TIMEOUT"]
        self.token = os.environ.get(cf["API_TOKEN_ENV"], "").strip()
        self.zone_id = os.environ.get(cf["ZONE_ID_ENV"], "").strip()
        self.account_id = os.environ.get(cf["ACCOUNT_ID_ENV"], "").strip()
        self.token_env = cf["API_TOKEN_ENV"]
        self.zone_env = cf["ZONE_ID_ENV"]
        self.cache = caches[config["CACHE_ALIAS"]]
        self.cache_ttl = config["CACHE_TTL"]

    # -- configuration -----------------------------------------------------
    @property
    def is_configured(self) -> bool:
        return bool(self.token and self.zone_id)

    def configuration_help(self) -> str:
        return _(
            "Set the %(token)s and %(zone)s environment variables to a "
            "Cloudflare API token with Analytics Read permission and your "
            "zone ID, then restart the server."
        ) % {"token": self.token_env, "zone": self.zone_env}

    def description(self) -> str:
        name = self._zone_name()
        return name or f"zone {self.zone_id[:10]}…"

    def capabilities(self) -> set:
        caps = {
            base.OVERVIEW,
            base.TIMESERIES,
            base.PATHS,
            base.COUNTRY,
            base.STATUS,
            base.CACHE,
            base.METHOD,
            base.BANDWIDTH,
            base.UNIQUES,
            base.THREATS,
            base.VISITS,
        }
        if self.cache.get(_PERF_FLAG_KEY) != "no":
            caps.add(base.PERFORMANCE)
        return caps

    # -- HTTP / GraphQL plumbing --------------------------------------------
    def _http(self, url: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise ProviderError(
                    _("Cloudflare rejected the API token. Check its permissions."),
                    kind="auth",
                ) from exc
            raise ProviderError(
                _("Cloudflare API returned HTTP %(code)s.") % {"code": exc.code},
                kind="network",
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(
                _("Could not reach the Cloudflare API: %(reason)s")
                % {"reason": getattr(exc, "reason", exc)},
                kind="network",
            ) from exc

    def _graphql(self, query: str) -> dict:
        """Run a GraphQL query and return the first zone object."""
        body = self._http(self.api_url, {"query": query})
        errors = body.get("errors") or []
        if errors:
            first = errors[0]
            code = (first.get("extensions") or {}).get("code", "")
            message = first.get("message", "")
            if code == "quota":
                raise ProviderError(message, kind="quota")
            if code == "authz":
                raise ProviderError(message, kind="plan")
            raise ProviderError(_("Cloudflare analytics error: %(message)s") % {"message": message})
        try:
            return body["data"]["viewer"]["zones"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                _("Cloudflare returned no data for this zone. Check the zone ID."),
                kind="config",
            ) from exc

    def _cached_graphql(self, query: str) -> dict:
        version = self.cache.get(_VERSION_KEY, 0)
        key = f"prometric:cf:q:{version}:" + hashlib.md5(query.encode()).hexdigest()
        result = self.cache.get(key)
        if result is None:
            result = self._graphql(query)
            self.cache.set(key, result, self.cache_ttl)
        return result

    def _zone_name(self) -> str:
        key = "prometric:cf:zone-name"
        name = self.cache.get(key)
        if name is None:
            try:
                body = self._http(f"https://api.cloudflare.com/client/v4/zones/{self.zone_id}")
                name = (body.get("result") or {}).get("name") or ""
            except ProviderError:
                name = ""
            self.cache.set(key, name, 3600)
        return name

    # -- adaptive (per-path) queries with plan clamping ----------------------
    # Free-plan adaptive queries allow at most a 1-day range, and Cloudflare
    # measures it against "now" at execution time — so even an exact 24h
    # window overflows by the request latency. Clamp a minute short of a day.
    _CLAMP_WINDOW = dt.timedelta(hours=23, minutes=59)

    def _adaptive_period(self, period: Period) -> Period:
        """Clamp the period once the plan limit has been observed."""
        if self._too_wide(period) and self.cache.get(_CLAMP_FLAG_KEY) == "yes":
            return self._clamped(period)
        return period

    def _too_wide(self, period: Period) -> bool:
        return period.end - period.start > self._CLAMP_WINDOW

    def _clamped(self, period: Period) -> Period:
        if period.days > 1:  # only worth a notice when data is actually cut
            self.add_notice(
                _(
                    "Your Cloudflare plan limits path-level analytics to a "
                    "24-hour window, so per-route numbers cover the last 24 "
                    "hours only."
                )
            )
        return Period(
            key=period.key,
            label=period.label,
            start=period.end - self._CLAMP_WINDOW,
            end=period.end,
        )

    def _adaptive_query(self, build_query, period: Period) -> dict:
        """Run an adaptive-dataset query, retrying clamped on plan limits."""
        effective = self._adaptive_period(period)
        try:
            return self._cached_graphql(build_query(effective))
        except ProviderError as exc:
            if exc.kind == "quota" and self._too_wide(effective):
                self.cache.set(_CLAMP_FLAG_KEY, "yes", 86400)
                return self._cached_graphql(build_query(self._clamped(period)))
            raise

    # -- daily dataset (zone-wide, works on every plan) ----------------------
    def _daily_groups(self, period: Period) -> list:
        since = period.start.strftime(_DATE)
        until = period.end.strftime(_DATE)
        query = f"""{{ viewer {{ zones(filter: {{zoneTag: "{self.zone_id}"}}) {{
            groups: httpRequests1dGroups(
                limit: 100,
                filter: {{date_geq: "{since}", date_leq: "{until}"}},
                orderBy: [date_ASC]
            ) {{
                dimensions {{ date }}
                sum {{
                    requests bytes cachedRequests pageViews threats
                    responseStatusMap {{ edgeResponseStatus requests }}
                    countryMap {{ clientCountryName requests }}
                }}
                uniq {{ uniques }}
            }}
        }} }} }}"""
        return self._cached_graphql(query).get("groups") or []

    # -- provider API --------------------------------------------------------
    def get_overview(self, period: Period) -> OverviewStats:
        stats = OverviewStats(
            bandwidth_bytes=0,
            cached_requests=0,
            threats=0,
            errors=0,
            unique_visitors=0,
            page_views=0,
        )
        for group in self._daily_groups(period):
            total = group.get("sum") or {}
            stats.requests += total.get("requests", 0)
            stats.bandwidth_bytes += total.get("bytes", 0)
            stats.cached_requests += total.get("cachedRequests", 0)
            stats.page_views += total.get("pageViews", 0)
            stats.threats += total.get("threats", 0)
            stats.unique_visitors += (group.get("uniq") or {}).get("uniques", 0)
            for item in total.get("responseStatusMap") or []:
                if item.get("edgeResponseStatus", 0) >= 400:
                    stats.errors += item.get("requests", 0)
        return stats

    def get_timeseries(self, period: Period) -> list[TimeseriesPoint]:
        if period.days > 2:
            return [
                TimeseriesPoint(
                    label=(group.get("dimensions") or {}).get("date", ""),
                    requests=(group.get("sum") or {}).get("requests", 0),
                )
                for group in self._daily_groups(period)
            ]

        def build(effective: Period) -> str:
            since = effective.start.strftime(_DATETIME)
            return f"""{{ viewer {{ zones(filter: {{zoneTag: "{self.zone_id}"}}) {{
                series: httpRequestsAdaptiveGroups(
                    limit: 60,
                    filter: {{datetime_gt: "{since}"}},
                    orderBy: [datetimeHour_ASC]
                ) {{ count dimensions {{ datetimeHour }} }}
            }} }} }}"""

        zone = self._adaptive_query(build, period)
        points = []
        for group in zone.get("series") or []:
            stamp = (group.get("dimensions") or {}).get("datetimeHour", "")
            points.append(
                TimeseriesPoint(label=stamp[11:16] or stamp, requests=group.get("count", 0))
            )
        return points

    def get_path_stats(self, period: Period, limit: int = 1500) -> list[PathStat]:
        def build(effective: Period) -> str:
            since = effective.start.strftime(_DATETIME)
            return f"""{{ viewer {{ zones(filter: {{zoneTag: "{self.zone_id}"}}) {{
                paths: httpRequestsAdaptiveGroups(
                    limit: {int(limit)},
                    filter: {{datetime_gt: "{since}"}},
                    orderBy: [count_DESC]
                ) {{
                    count
                    sum {{ edgeResponseBytes visits }}
                    dimensions {{ clientRequestPath }}
                }}
            }} }} }}"""

        zone = self._adaptive_query(build, period)
        stats = []
        for group in zone.get("paths") or []:
            total = group.get("sum") or {}
            stats.append(
                PathStat(
                    path=(group.get("dimensions") or {}).get("clientRequestPath", ""),
                    requests=group.get("count", 0),
                    bandwidth_bytes=total.get("edgeResponseBytes", 0),
                    visits=total.get("visits", 0),
                )
            )
        return stats

    def get_breakdown(self, dimension: str, period: Period, limit: int = 12) -> list[BreakdownItem]:
        if dimension in (base.DIM_COUNTRY, base.DIM_STATUS):
            return self._daily_breakdown(dimension, period, limit)
        field = _BREAKDOWN_DIMENSIONS[dimension]

        def build(effective: Period) -> str:
            since = effective.start.strftime(_DATETIME)
            return f"""{{ viewer {{ zones(filter: {{zoneTag: "{self.zone_id}"}}) {{
                breakdown: httpRequestsAdaptiveGroups(
                    limit: {int(limit)},
                    filter: {{datetime_gt: "{since}"}},
                    orderBy: [count_DESC]
                ) {{ count dimensions {{ {field} }} }}
            }} }} }}"""

        zone = self._adaptive_query(build, period)
        items = [
            BreakdownItem(
                label=str((group.get("dimensions") or {}).get(field, "")),
                value=group.get("count", 0),
            )
            for group in zone.get("breakdown") or []
        ]
        return with_shares(items)

    def _daily_breakdown(self, dimension: str, period: Period, limit: int) -> list[BreakdownItem]:
        """Country/status share computed from the plan-unrestricted daily maps."""
        map_key, label_key = {
            base.DIM_COUNTRY: ("countryMap", "clientCountryName"),
            base.DIM_STATUS: ("responseStatusMap", "edgeResponseStatus"),
        }[dimension]
        totals = {}
        for group in self._daily_groups(period):
            for item in (group.get("sum") or {}).get(map_key) or []:
                label = str(item.get(label_key, ""))
                totals[label] = totals.get(label, 0) + item.get("requests", 0)
        items = [BreakdownItem(label=label, value=value) for label, value in totals.items()]
        items.sort(key=lambda item: item.value, reverse=True)
        return with_shares(items[:limit])

    def get_route_metrics(self, route, period: Period) -> RouteMetrics:
        if route.is_dynamic:
            path_filter = f'clientRequestPath_like: "{route.wildcard}"'
        else:
            path_filter = f'clientRequestPath: "{route.display}"'

        def build(effective: Period) -> str:
            since = effective.start.strftime(_DATETIME)
            base_filter = f'{{datetime_gt: "{since}", {path_filter}}}'
            return f"""{{ viewer {{ zones(filter: {{zoneTag: "{self.zone_id}"}}) {{
                total: httpRequestsAdaptiveGroups(limit: 1, filter: {base_filter}) {{
                    count sum {{ edgeResponseBytes visits }}
                }}
                countries: httpRequestsAdaptiveGroups(limit: 12, filter: {base_filter}, orderBy: [count_DESC]) {{
                    count dimensions {{ clientCountryName }}
                }}
                statuses: httpRequestsAdaptiveGroups(limit: 12, filter: {base_filter}, orderBy: [count_DESC]) {{
                    count dimensions {{ edgeResponseStatus }}
                }}
                methods: httpRequestsAdaptiveGroups(limit: 8, filter: {base_filter}, orderBy: [count_DESC]) {{
                    count dimensions {{ clientRequestHTTPMethodName }}
                }}
                cache: httpRequestsAdaptiveGroups(limit: 8, filter: {base_filter}, orderBy: [count_DESC]) {{
                    count dimensions {{ cacheStatus }}
                }}
                series: httpRequestsAdaptiveGroups(limit: 60, filter: {base_filter}, orderBy: [datetimeHour_ASC]) {{
                    count dimensions {{ datetimeHour }}
                }}
                last: httpRequestsAdaptiveGroups(limit: 1, filter: {base_filter}, orderBy: [datetime_DESC]) {{
                    count dimensions {{ datetime }}
                }}
            }} }} }}"""

        zone = self._adaptive_query(build, period)

        def breakdown(alias: str, field: str) -> list[BreakdownItem]:
            return with_shares(
                [
                    BreakdownItem(
                        label=str((group.get("dimensions") or {}).get(field, "")),
                        value=group.get("count", 0),
                    )
                    for group in zone.get(alias) or []
                ]
            )

        total_groups = zone.get("total") or []
        total = total_groups[0] if total_groups else {}
        total_sum = total.get("sum") or {}

        statuses = breakdown("statuses", "edgeResponseStatus")
        metrics = RouteMetrics(
            requests=total.get("count", 0),
            visits=total_sum.get("visits"),
            bandwidth_bytes=total_sum.get("edgeResponseBytes"),
            errors=sum(
                item.value for item in statuses if item.label.isdigit() and int(item.label) >= 400
            ),
            countries=breakdown("countries", "clientCountryName"),
            statuses=statuses,
            methods=breakdown("methods", "clientRequestHTTPMethodName"),
            cache=breakdown("cache", "cacheStatus"),
            timeseries=[
                TimeseriesPoint(
                    label=((group.get("dimensions") or {}).get("datetimeHour", ""))[11:16],
                    requests=group.get("count", 0),
                )
                for group in zone.get("series") or []
            ],
        )

        last_groups = zone.get("last") or []
        if last_groups:
            stamp = (last_groups[0].get("dimensions") or {}).get("datetime")
            if stamp:
                metrics.last_seen = dt.datetime.strptime(stamp, _DATETIME).replace(
                    tzinfo=dt.timezone.utc
                )

        metrics.performance = self._performance(path_filter, period)
        return metrics

    def _performance(self, path_filter: str, period: Period) -> PerformanceStats | None:
        """TTFB quantiles; detects plan gating once and remembers it."""
        if self.cache.get(_PERF_FLAG_KEY) == "no":
            return None

        def build(effective: Period) -> str:
            since = effective.start.strftime(_DATETIME)
            return f"""{{ viewer {{ zones(filter: {{zoneTag: "{self.zone_id}"}}) {{
                perf: httpRequestsAdaptiveGroups(
                    limit: 1,
                    filter: {{datetime_gt: "{since}", {path_filter}}}
                ) {{
                    quantiles {{
                        edgeTimeToFirstByteMsP50
                        edgeTimeToFirstByteMsP95
                        edgeTimeToFirstByteMsP99
                    }}
                }}
            }} }} }}"""

        try:
            zone = self._adaptive_query(build, period)
        except ProviderError as exc:
            if exc.kind == "plan":
                self.cache.set(_PERF_FLAG_KEY, "no", 86400)
                return None
            raise
        self.cache.set(_PERF_FLAG_KEY, "yes", 86400)
        groups = zone.get("perf") or []
        if not groups:
            return None
        quantiles = groups[0].get("quantiles") or {}
        return PerformanceStats(
            p50_ms=quantiles.get("edgeTimeToFirstByteMsP50"),
            p95_ms=quantiles.get("edgeTimeToFirstByteMsP95"),
            p99_ms=quantiles.get("edgeTimeToFirstByteMsP99"),
        )

    def get_performance(self, period: Period) -> PerformanceStats | None:
        """Zone-wide TTFB quantiles for the dashboard performance card."""
        return self._performance('clientRequestPath_like: "%"', period)

    def invalidate_cache(self) -> None:
        try:
            self.cache.incr(_VERSION_KEY)
        except ValueError:
            self.cache.set(_VERSION_KEY, 1, None)
