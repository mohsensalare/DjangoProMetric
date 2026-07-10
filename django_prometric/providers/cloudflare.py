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
from collections import Counter

from django.core.cache import caches
from django.utils.translation import gettext as _

from ..conf import get_config
from . import base
from .base import (
    AnalyticsProvider,
    BreakdownItem,
    Insight,
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


def _host_list(value) -> list[str]:
    """Normalise a HOSTS setting to a clean list, accepting a bare string."""
    if isinstance(value, str):
        value = [value]
    return [host.strip() for host in (value or []) if host and host.strip()]


class CloudflareProvider(AnalyticsProvider):
    slug = "cloudflare"
    verbose_name = "Cloudflare"
    kind = _("Edge traffic")
    route_template = "django_prometric/route/traffic.html"

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
        # A zone can front many hostnames; these narrow the numbers to the
        # ones this Django project actually serves. Either setting accepts a
        # single hostname or a list of them.
        self.hosts = _host_list(cf["HOSTS"])
        self.exclude_hosts = _host_list(cf["EXCLUDE_HOSTS"])
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

    def configuration_warnings(self) -> list:
        # Console-facing developer message; deliberately not translated.
        from django.core.checks import Warning as CheckWarning

        if self.host_filtered:
            return []
        return [
            CheckWarning(
                "Cloudflare analytics are not scoped to a hostname.",
                hint=(
                    "A zone often fronts several hostnames, so the dashboard "
                    "counts the whole zone — including hosts this project does "
                    "not serve. Set DJANGO_PROMETRIC['CLOUDFLARE']['HOSTS'] to "
                    "the hostname (or list of hostnames) this project answers "
                    "to, or EXCLUDE_HOSTS to drop the ones it does not."
                ),
                obj="django_prometric.providers.cloudflare",
                id="django_prometric.W002",
            )
        ]

    def description(self) -> str:
        name = self._zone_name() or f"zone {self.zone_id[:10]}…"
        if self.hosts:
            name += f" ({', '.join(self.hosts)})"
        return name

    @property
    def host_filtered(self) -> bool:
        return bool(self.hosts or self.exclude_hosts)

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
            base.SECURITY,
            base.BOTS,
            base.SEO,
            base.NETWORK,
            base.AUDIENCE,
            base.INSIGHTS,
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
        # Flaky links drop TLS connections mid-handshake now and then; one
        # immediate retry absorbs that without hiding real outages.
        for attempt in (1, 2):
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
                if attempt == 1:
                    continue
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
                    "Showing the last 24 hours instead of the selected range "
                    "— your Cloudflare plan limits path-level analytics to a "
                    "24-hour window."
                ),
                level=base.NOTICE_WARN,
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

    def _where(self, effective: Period, any_of: tuple = ()) -> str:
        """The adaptive-dataset filter object: time window, host rules, and
        optionally a group of alternatives (``any_of``) that is OR-combined.

        OR groups (paths, hosts) each become one member of an AND list, so
        several of them coexist in a single filter object.
        """
        parts = [
            f'datetime_gt: "{effective.start.strftime(_DATETIME)}"',
            f'datetime_leq: "{effective.end.strftime(_DATETIME)}"',
        ]
        groups = []
        if any_of:
            groups.append("{OR: [" + ", ".join(any_of) + "]}")
        if self.hosts:
            hosts = ", ".join(f'{{clientRequestHTTPHost: "{host}"}}' for host in self.hosts)
            groups.append(f"{{OR: [{hosts}]}}")
        groups.extend(f'{{clientRequestHTTPHost_neq: "{host}"}}' for host in self.exclude_hosts)
        if groups:
            parts.append("AND: [" + ", ".join(groups) + "]")
        return "{" + ", ".join(parts) + "}"

    def _path_conditions(self, route) -> tuple:
        """One filter object per concrete path pattern the route serves."""
        return tuple(
            f'{{clientRequestPath_like: "{pattern}"}}'
            if "%" in pattern
            else f'{{clientRequestPath: "{pattern}"}}'
            for pattern in route.path_patterns
        )

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
        if self.host_filtered:
            # The daily dataset is zone-wide; honour the host rules through
            # the adaptive dataset instead of reporting other hosts' numbers.
            return self._adaptive_overview(period)
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

    _CACHED_STATUSES = {"hit", "stale", "updating", "revalidated"}

    def _adaptive_overview(self, period: Period) -> OverviewStats:
        def build(effective: Period) -> str:
            where = self._where(effective)
            return f"""{{ viewer {{ zones(filter: {{zoneTag: "{self.zone_id}"}}) {{
                total: httpRequestsAdaptiveGroups(limit: 1, filter: {where}) {{
                    count sum {{ edgeResponseBytes visits }}
                }}
                statuses: httpRequestsAdaptiveGroups(limit: 25, filter: {where}, orderBy: [count_DESC]) {{
                    count dimensions {{ edgeResponseStatus }}
                }}
                cache: httpRequestsAdaptiveGroups(limit: 10, filter: {where}, orderBy: [count_DESC]) {{
                    count dimensions {{ cacheStatus }}
                }}
            }} }} }}"""

        zone = self._adaptive_query(build, period)
        total_groups = zone.get("total") or []
        total = total_groups[0] if total_groups else {}
        stats = OverviewStats(
            requests=total.get("count", 0),
            bandwidth_bytes=(total.get("sum") or {}).get("edgeResponseBytes", 0),
            page_views=(total.get("sum") or {}).get("visits", 0),
            cached_requests=0,
            errors=0,
        )
        for group in zone.get("statuses") or []:
            if (group.get("dimensions") or {}).get("edgeResponseStatus", 0) >= 400:
                stats.errors += group.get("count", 0)
        for group in zone.get("cache") or []:
            if (group.get("dimensions") or {}).get("cacheStatus", "") in self._CACHED_STATUSES:
                stats.cached_requests += group.get("count", 0)
        return stats

    def get_timeseries(self, period: Period) -> list[TimeseriesPoint]:
        if period.days > 2 and not self.host_filtered:
            return [
                TimeseriesPoint(
                    label=(group.get("dimensions") or {}).get("date", ""),
                    requests=(group.get("sum") or {}).get("requests", 0),
                )
                for group in self._daily_groups(period)
            ]

        def build(effective: Period) -> str:
            return f"""{{ viewer {{ zones(filter: {{zoneTag: "{self.zone_id}"}}) {{
                series: httpRequestsAdaptiveGroups(
                    limit: 60,
                    filter: {self._where(effective)},
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
            return f"""{{ viewer {{ zones(filter: {{zoneTag: "{self.zone_id}"}}) {{
                paths: httpRequestsAdaptiveGroups(
                    limit: {int(limit)},
                    filter: {self._where(effective)},
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
        if dimension in (base.DIM_COUNTRY, base.DIM_STATUS) and not self.host_filtered:
            return self._daily_breakdown(dimension, period, limit)
        field = _BREAKDOWN_DIMENSIONS[dimension]

        def build(effective: Period) -> str:
            return f"""{{ viewer {{ zones(filter: {{zoneTag: "{self.zone_id}"}}) {{
                breakdown: httpRequestsAdaptiveGroups(
                    limit: {int(limit)},
                    filter: {self._where(effective)},
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
        conditions = self._path_conditions(route)

        def build(effective: Period) -> str:
            base_filter = self._where(effective, any_of=conditions)
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

        metrics.performance = self._performance(period, conditions)
        return metrics

    def get_route_context(self, route, period: Period) -> dict:
        metrics = self.get_route_metrics(route, period)
        return {
            "metrics": metrics,
            # JSON-serialisable copy of the timeseries for the chart canvas.
            "chart": {
                "labels": [point.label for point in metrics.timeseries],
                "values": [point.requests for point in metrics.timeseries],
            },
        }

    def _performance(self, period: Period, conditions: tuple = ()) -> PerformanceStats | None:
        """TTFB quantiles; detects plan gating once and remembers it."""
        if self.cache.get(_PERF_FLAG_KEY) == "no":
            return None

        def build(effective: Period) -> str:
            return f"""{{ viewer {{ zones(filter: {{zoneTag: "{self.zone_id}"}}) {{
                perf: httpRequestsAdaptiveGroups(
                    limit: 1,
                    filter: {self._where(effective, any_of=conditions)}
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
        return self._performance(period)

    # What the firewall dataset's raw labels mean, for humans.
    _ACTION_LABELS = {
        "block": _("Blocked"),
        "challenge": _("Challenged"),
        "managed_challenge": _("Managed challenge"),
        "jschallenge": _("JS challenge"),
        "log": _("Logged"),
        "skip": _("Skipped"),
    }
    _SOURCE_LABELS = {
        "waf": "WAF",
        "firewallmanaged": _("Managed rules"),
        "firewallcustom": _("Custom rules"),
        "firewallrules": _("Firewall rules"),
        "ratelimit": _("Rate limiting"),
        "securitylevel": _("Security level"),
        "botfight": _("Bot Fight Mode"),
        "l7ddos": _("DDoS protection"),
        "ip": _("IP rules"),
        "country": _("Country rules"),
    }

    # Raw firewall events fetched per security query. The grouped dataset
    # (firewallEventsAdaptiveGroups) is closed below the Business plan, so
    # the newest raw events are aggregated here instead.
    _SECURITY_SAMPLE = 3000

    def get_security(self, period: Period) -> dict:
        """Firewall mitigations: how much was blocked/challenged, by what,
        from where, and which paths were attacked."""

        def build(effective: Period) -> str:
            return f"""{{ viewer {{ zones(filter: {{zoneTag: "{self.zone_id}"}}) {{
                events: firewallEventsAdaptive(limit: {self._SECURITY_SAMPLE},
                        filter: {self._where(effective)}, orderBy: [datetime_DESC]) {{
                    action source clientCountryName clientRequestPath
                }}
            }} }} }}"""

        events = self._adaptive_query(build, period).get("events") or []

        def tally(field: str, top: int, labels: dict | None = None) -> list[BreakdownItem]:
            counts = Counter(str(event.get(field) or "") for event in events)
            found = [
                BreakdownItem(label=str((labels or {}).get(raw.lower(), raw)), value=count)
                for raw, count in counts.most_common(top)
            ]
            return with_shares(found)

        return {
            "total": len(events),
            "actions": tally("action", 8, self._ACTION_LABELS),
            "sources": tally("source", 8, self._SOURCE_LABELS),
            "countries": tally("clientCountryName", 6),
            "paths": tally("clientRequestPath", 6),
        }

    _SEARCH_CRAWLER = "Search Engine Crawler"

    def get_bots(self, period: Period) -> dict:
        """Verified-bot share of traffic, split by crawler category.

        Cloudflare only labels bots it has verified (search engines, AI
        crawlers, monitoring services, …); everything else — people and
        unverified automation — lands in ``humans``.
        """

        def build(effective: Period) -> str:
            return f"""{{ viewer {{ zones(filter: {{zoneTag: "{self.zone_id}"}}) {{
                categories: httpRequestsAdaptiveGroups(limit: 15,
                        filter: {self._where(effective)}, orderBy: [count_DESC]) {{
                    count dimensions {{ verifiedBotCategory }}
                }}
            }} }} }}"""

        zone = self._adaptive_query(build, period)
        categories, humans = [], 0
        for group in zone.get("categories") or []:
            label = (group.get("dimensions") or {}).get("verifiedBotCategory") or ""
            count = group.get("count", 0)
            if label:
                categories.append(BreakdownItem(label=label, value=count))
            else:
                humans += count
        bots = sum(item.value for item in categories)
        return {
            "total": humans + bots,
            "humans": humans,
            "bots": bots,
            "categories": with_shares(categories),
        }

    def get_seo(self, period: Period) -> dict:
        """What search engines crawl: which engines come by, which pages
        they fetch, and how often."""
        crawler = f'{{verifiedBotCategory: "{self._SEARCH_CRAWLER}"}}'

        def build(effective: Period) -> str:
            where = self._where(effective, any_of=(crawler,))
            return f"""{{ viewer {{ zones(filter: {{zoneTag: "{self.zone_id}"}}) {{
                total: httpRequestsAdaptiveGroups(limit: 1, filter: {where}) {{ count }}
                engines: httpRequestsAdaptiveGroups(limit: 8, filter: {where},
                        orderBy: [count_DESC]) {{
                    count dimensions {{ userAgentBrowser }}
                }}
                paths: httpRequestsAdaptiveGroups(limit: 8, filter: {where},
                        orderBy: [count_DESC]) {{
                    count dimensions {{ clientRequestPath }}
                }}
            }} }} }}"""

        zone = self._adaptive_query(build, period)
        total_groups = zone.get("total") or []
        return {
            "total": total_groups[0].get("count", 0) if total_groups else 0,
            "engines": self._grouped(zone, "engines", "userAgentBrowser"),
            "paths": self._grouped(zone, "paths", "clientRequestPath"),
        }

    def get_network(self, period: Period) -> dict:
        """How clients connect: HTTP versions and TLS versions."""

        def build(effective: Period) -> str:
            where = self._where(effective)
            return f"""{{ viewer {{ zones(filter: {{zoneTag: "{self.zone_id}"}}) {{
                http: httpRequestsAdaptiveGroups(limit: 8, filter: {where},
                        orderBy: [count_DESC]) {{
                    count dimensions {{ clientRequestHTTPProtocol }}
                }}
                tls: httpRequestsAdaptiveGroups(limit: 8, filter: {where},
                        orderBy: [count_DESC]) {{
                    count dimensions {{ clientSSLProtocol }}
                }}
            }} }} }}"""

        zone = self._adaptive_query(build, period)
        return {
            "http_versions": self._grouped(zone, "http", "clientRequestHTTPProtocol"),
            "tls": self._grouped(zone, "tls", "clientSSLProtocol"),
        }

    def get_audience(self, period: Period) -> dict:
        """Real users' browsers, operating systems and devices — verified
        bots are filtered out so this reflects people, not crawlers."""
        people = '{verifiedBotCategory: ""}'

        def build(effective: Period) -> str:
            where = self._where(effective, any_of=(people,))
            return f"""{{ viewer {{ zones(filter: {{zoneTag: "{self.zone_id}"}}) {{
                browsers: httpRequestsAdaptiveGroups(limit: 8, filter: {where},
                        orderBy: [count_DESC]) {{
                    count dimensions {{ userAgentBrowser }}
                }}
                os: httpRequestsAdaptiveGroups(limit: 8, filter: {where},
                        orderBy: [count_DESC]) {{
                    count dimensions {{ userAgentOS }}
                }}
                devices: httpRequestsAdaptiveGroups(limit: 5, filter: {where},
                        orderBy: [count_DESC]) {{
                    count dimensions {{ clientDeviceType }}
                }}
            }} }} }}"""

        zone = self._adaptive_query(build, period)
        return {
            "browsers": self._grouped(zone, "browsers", "userAgentBrowser"),
            "os": self._grouped(zone, "os", "userAgentOS"),
            "devices": self._grouped(zone, "devices", "clientDeviceType"),
        }

    def _grouped(self, zone: dict, alias: str, field: str) -> list[BreakdownItem]:
        return with_shares(
            [
                BreakdownItem(
                    label=str((group.get("dimensions") or {}).get(field, "")),
                    value=group.get("count", 0),
                )
                for group in zone.get(alias) or []
            ]
        )

    # -- insights ------------------------------------------------------------
    # Thresholds are shares of total requests over the selected period.
    _CACHE_LOW = 0.20
    _CACHE_HEALTHY = 0.50
    _5XX_WARN = 0.005
    _5XX_BAD = 0.02
    _404_WARN = 0.05
    _AI_CRAWLER_WARN = 0.10
    _HTTP1_WARN = 0.50
    _MITIGATION_WARN = 500  # absolute mitigated requests

    def get_insights(self, period: Period) -> list[Insight]:
        insights = []
        rules = (
            self._status_insights,
            self._cache_insights,
            self._security_insights,
            self._bot_insights,
            self._network_insights,
        )
        for rule in rules:
            try:
                insights.extend(rule(period))
            except ProviderError:
                continue  # one gated dataset never silences the other findings
        return insights

    def _cache_insights(self, period: Period) -> list[Insight]:
        stats = self.get_overview(period)
        ratio = stats.cache_ratio
        if ratio is None or not stats.requests:
            return []
        if ratio < self._CACHE_LOW:
            return [
                Insight(
                    severity=base.INSIGHT_WARN,
                    title=_("The edge cache is barely used"),
                    detail=_("Only %(share)d%% of %(requests)s requests were served from cache.")
                    % {"share": ratio * 100, "requests": f"{stats.requests:,}"},
                    action=_(
                        "Send Cache-Control headers on static files and cacheable "
                        "pages so the CDN can answer them without hitting Django."
                    ),
                )
            ]
        if ratio >= self._CACHE_HEALTHY:
            return [
                Insight(
                    severity=base.INSIGHT_GOOD,
                    title=_("The edge cache is pulling its weight"),
                    detail=_("%(share)d%% of requests never reached your server.")
                    % {"share": ratio * 100},
                )
            ]
        return []

    def _status_insights(self, period: Period) -> list[Insight]:
        statuses = self.get_breakdown(base.DIM_STATUS, period, limit=25)
        total = sum(item.value for item in statuses)
        if not total:
            return []
        errors_5xx = sum(int(i.value) for i in statuses if i.label.startswith("5"))
        not_found = sum(int(i.value) for i in statuses if i.label == "404")
        insights = []
        if errors_5xx / total >= self._5XX_WARN:
            insights.append(
                Insight(
                    severity=base.INSIGHT_BAD
                    if errors_5xx / total >= self._5XX_BAD
                    else base.INSIGHT_WARN,
                    title=_("Requests are failing with server errors"),
                    detail=_("%(count)s requests (%(share).1f%%) ended in a 5xx status.")
                    % {"count": f"{errors_5xx:,}", "share": errors_5xx / total * 100},
                    action=_("Open the application errors list and fix the top offenders."),
                )
            )
        elif errors_5xx == 0:
            insights.append(
                Insight(
                    severity=base.INSIGHT_GOOD,
                    title=_("No server errors at the edge"),
                    detail=_("None of the %(requests)s requests returned a 5xx status.")
                    % {"requests": f"{total:,}"},
                )
            )
        if not_found / total >= self._404_WARN:
            insights.append(
                Insight(
                    severity=base.INSIGHT_WARN,
                    title=_("A lot of traffic hits dead ends"),
                    detail=_("%(count)s requests (%(share).1f%%) got a 404.")
                    % {"count": f"{not_found:,}", "share": not_found / total * 100},
                    action=_(
                        "Check the top routes for broken links, and add redirects "
                        "for pages that moved."
                    ),
                )
            )
        return insights

    def _security_insights(self, period: Period) -> list[Insight]:
        security = self.get_security(period)
        total = security["total"]
        if not total:
            return []
        top_path = security["paths"][0].label if security["paths"] else ""
        detail = _("%(count)s requests were blocked or challenged in this period.") % {
            "count": f"{total:,}"
        }
        if top_path:
            detail += " " + _("Most-targeted path: %(path)s.") % {"path": top_path}
        if total < self._MITIGATION_WARN:
            return [
                Insight(
                    severity=base.INSIGHT_GOOD, title=_("The firewall has your back"), detail=detail
                )
            ]
        return [
            Insight(
                severity=base.INSIGHT_WARN,
                title=_("Heavy firewall activity"),
                detail=detail,
                action=_(
                    "Review the security card; a rate limit or a stricter rule on "
                    "the targeted paths can cut this noise at the edge."
                ),
            )
        ]

    def _bot_insights(self, period: Period) -> list[Insight]:
        bots = self.get_bots(period)
        if not bots["total"]:
            return []
        ai_requests = sum(item.value for item in bots["categories"] if item.label == "AI Crawler")
        share = ai_requests / bots["total"]
        if share >= self._AI_CRAWLER_WARN:
            return [
                Insight(
                    severity=base.INSIGHT_WARN,
                    title=_("AI crawlers take a serious slice of your traffic"),
                    detail=_("AI crawlers made %(count)s requests (%(share).1f%% of all traffic).")
                    % {"count": f"{ai_requests:,}", "share": share * 100},
                    action=_(
                        "Decide whether that's welcome: Cloudflare can block or "
                        "rate-limit AI crawlers with a single setting."
                    ),
                )
            ]
        return []

    def _network_insights(self, period: Period) -> list[Insight]:
        network = self.get_network(period)
        versions = network["http_versions"]
        total = sum(item.value for item in versions)
        if not total:
            return []
        http1 = sum(item.value for item in versions if item.label == "HTTP/1.1")
        if http1 / total >= self._HTTP1_WARN:
            return [
                Insight(
                    severity=base.INSIGHT_WARN,
                    title=_("Most connections still speak HTTP/1.1"),
                    detail=_("%(share).0f%% of requests came over HTTP/1.1.")
                    % {"share": http1 / total * 100},
                    action=_(
                        "That usually means bots or very old clients; real "
                        "browsers negotiate HTTP/2 or 3 on their own. Cross-check "
                        "with the bots card."
                    ),
                )
            ]
        return []

    def invalidate_cache(self) -> None:
        try:
            self.cache.incr(_VERSION_KEY)
        except ValueError:
            self.cache.set(_VERSION_KEY, 1, None)
