"""Sentry provider: application-side performance and errors.

Complements an edge source such as Cloudflare — Sentry sees how long the
application itself took, per transaction (= per route), and which errors it
raised. Uses Sentry's web API with the standard library only; responses are
cached through Django's cache framework.

Transactions arrive named after the requested URL (for the Django SDK's
default ``url`` transaction style), with high-cardinality segments collapsed
to ``*`` — e.g. ``https://*/en/project/Projects/*/comments/``. They are
matched to routes in :mod:`django_prometric.routes`.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from django.core.cache import caches
from django.utils.translation import gettext as _

from ..conf import get_config
from . import base
from .base import (
    AnalyticsProvider,
    BreakdownItem,
    Insight,
    IssueStat,
    PerformanceStats,
    Period,
    ProviderError,
    QueryStat,
    RoutePerformance,
    with_shares,
)

_DATETIME = "%Y-%m-%dT%H:%M:%S"

# Bumped by invalidate_cache() so every cached query key changes at once.
_VERSION_KEY = "prometric:sentry:key-version"

_PERCENTILES = (
    "p50(span.duration)",
    "p75(span.duration)",
    "p95(span.duration)",
    "p99(span.duration)",
)


class SentryProvider(AnalyticsProvider):
    slug = "sentry"
    verbose_name = "Sentry"
    kind = _("Application performance")
    route_template = "django_prometric/route/performance.html"

    def __init__(self):
        super().__init__()
        config = get_config()
        sentry = config["SENTRY"]
        self.base_url = sentry["BASE_URL"].rstrip("/")
        self.timeout = sentry["TIMEOUT"]
        self.max_period_days = sentry["MAX_DAYS"]
        self.token = os.environ.get(sentry["API_TOKEN_ENV"], "").strip()
        self.org = os.environ.get(sentry["ORG_ENV"], "").strip()
        self.project = os.environ.get(sentry["PROJECT_ENV"], "").strip()
        self.token_env = sentry["API_TOKEN_ENV"]
        self.org_env = sentry["ORG_ENV"]
        self.project_env = sentry["PROJECT_ENV"]
        self.cache = caches[config["CACHE_ALIAS"]]
        self.cache_ttl = config["CACHE_TTL"]

    # -- configuration -----------------------------------------------------
    @property
    def is_configured(self) -> bool:
        return bool(self.token and self.org)

    def configuration_help(self) -> str:
        return _(
            "Set the %(token)s and %(org)s environment variables to a Sentry "
            "auth token with org:read and event:read scopes and your "
            "organization slug, then restart the server."
        ) % {"token": self.token_env, "org": self.org_env}

    def configuration_warnings(self) -> list:
        # Console-facing developer message; deliberately not translated.
        from django.core.checks import Warning as CheckWarning

        if self.project:
            return []
        return [
            CheckWarning(
                f"Sentry analytics are not pinned to a project ({self.project_env} is unset).",
                hint=(
                    f"ProMetric falls back to the first project in the "
                    f"organization, so the dashboard may report a different "
                    f"project's numbers. Set the {self.project_env} environment "
                    f"variable to the project slug or numeric id you want to "
                    f"report on."
                ),
                obj="django_prometric.providers.sentry",
                id="django_prometric.W001",
            )
        ]

    def description(self) -> str:
        project = self.project or self._default_project()
        return f"{self.org}/{project}" if project else self.org

    def capabilities(self) -> set:
        return {
            base.PERFORMANCE,
            base.SLOWEST,
            base.ISSUES,
            base.QUERIES,
            base.BACKEND,
            base.INSIGHTS,
        }

    # -- HTTP plumbing -------------------------------------------------------
    def _api(self, path: str, params: list) -> object:
        url = f"{self.base_url}/api/0/{path}?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token}"})
        # Flaky links drop TLS connections mid-handshake now and then; one
        # immediate retry absorbs that without hiding real outages.
        for attempt in (1, 2):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode())
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    raise ProviderError(
                        _("Sentry rejected the auth token. Check its scopes."), kind="auth"
                    ) from exc
                if exc.code == 429:
                    raise ProviderError(
                        _("Sentry rate limit reached; try again shortly."), kind="quota"
                    ) from exc
                detail = ""
                try:
                    detail = (json.loads(exc.read().decode()).get("detail") or "")[:200]
                except Exception:  # noqa: BLE001 — the body is best-effort context
                    pass
                raise ProviderError(
                    _("Sentry API returned HTTP %(code)s. %(detail)s")
                    % {"code": exc.code, "detail": detail},
                    kind="network",
                ) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt == 1:
                    continue
                raise ProviderError(
                    _("Could not reach the Sentry API: %(reason)s")
                    % {"reason": getattr(exc, "reason", exc)},
                    kind="network",
                ) from exc

    def _cached_api(self, path: str, params: list) -> object:
        version = self.cache.get(_VERSION_KEY, 0)
        raw = path + urllib.parse.urlencode(params)
        key = f"prometric:sentry:q:{version}:" + hashlib.md5(raw.encode()).hexdigest()
        result = self.cache.get(key)
        if result is None:
            result = self._api(path, params)
            self.cache.set(key, result, self.cache_ttl)
        return result

    def _project_info(self) -> dict:
        """The configured (or first) project as ``{"slug": …, "id": …}``."""
        key = "prometric:sentry:project"
        info = self.cache.get(key)
        if info is None:
            info = {"slug": self.project, "id": ""}
            try:
                projects = self._api(f"organizations/{self.org}/projects/", []) or []
                match = next(
                    (p for p in projects if self.project in (p.get("slug"), str(p.get("id")))),
                    projects[0] if projects else None,
                )
                if match:
                    info = {"slug": match.get("slug", ""), "id": str(match.get("id", ""))}
            except (ProviderError, LookupError, TypeError):
                pass
            self.cache.set(key, info, 3600)
        return info

    def _default_project(self) -> str:
        return self._project_info()["slug"]

    # -- events (spans dataset) queries ---------------------------------------
    def _events(self, fields: list, period: Period, query: str, sort: str, limit: int) -> list:
        effective = self.limit_period(period)
        params = [("field", name) for name in fields]
        params += [
            ("query", query),
            ("dataset", "spans"),
            ("start", effective.start.strftime(_DATETIME)),
            ("end", effective.end.strftime(_DATETIME)),
            ("utc", "true"),
            ("sort", sort),
            ("per_page", str(limit)),
        ]
        project_id = self._project_info()["id"]
        if project_id:
            params.append(("project", project_id))
        body = self._cached_api(f"organizations/{self.org}/events/", params)
        return (body or {}).get("data") or []

    @staticmethod
    def _row_performance(row: dict) -> RoutePerformance:
        return RoutePerformance(
            transaction=row.get("transaction", ""),
            requests=int(row.get("count()") or 0),
            p50_ms=row.get("p50(span.duration)"),
            p75_ms=row.get("p75(span.duration)"),
            p95_ms=row.get("p95(span.duration)"),
            p99_ms=row.get("p99(span.duration)"),
            failure_rate=row.get("failure_rate()"),
        )

    # -- provider API ----------------------------------------------------------
    def get_performance(self, period: Period) -> PerformanceStats | None:
        rows = self._events(
            ["count()", "failure_rate()", *_PERCENTILES],
            period,
            query="is_transaction:true",
            sort="-count()",
            limit=1,
        )
        if not rows:
            return None
        row = rows[0]
        return PerformanceStats(
            p50_ms=row.get("p50(span.duration)"),
            p75_ms=row.get("p75(span.duration)"),
            p95_ms=row.get("p95(span.duration)"),
            p99_ms=row.get("p99(span.duration)"),
            failure_rate=row.get("failure_rate()"),
            requests=int(row.get("count()") or 0),
        )

    def get_slowest_routes(self, period: Period, limit: int = 10) -> list[RoutePerformance]:
        rows = self._events(
            ["transaction", "count()", "failure_rate()", *_PERCENTILES],
            period,
            # Single-digit sample counts make meaningless "slowest" entries.
            query="is_transaction:true count():>=10",
            sort="-p95(span.duration)",
            limit=limit,
        )
        return [self._row_performance(row) for row in rows]

    def get_transactions(self, period: Period, limit: int = 100) -> list[RoutePerformance]:
        """The busiest transactions with their percentiles, for route matching."""
        rows = self._events(
            ["transaction", "count()", "failure_rate()", *_PERCENTILES],
            period,
            query="is_transaction:true",
            sort="-count()",
            limit=limit,
        )
        return [self._row_performance(row) for row in rows]

    def get_top_issues(self, period: Period, limit: int = 10) -> list[IssueStat]:
        effective = self.limit_period(period)
        params = [
            ("query", "is:unresolved"),
            ("sort", "freq"),
            ("start", effective.start.strftime(_DATETIME)),
            ("end", effective.end.strftime(_DATETIME)),
            ("utc", "true"),
            ("limit", str(limit)),
        ]
        project_id = self._project_info()["id"]
        if project_id:
            params.append(("project", project_id))
        issues = self._cached_api(f"organizations/{self.org}/issues/", params)
        return [
            IssueStat(
                title=issue.get("title", ""),
                culprit=issue.get("culprit") or "",
                events=int(issue.get("count") or 0),
                users=int(issue.get("userCount") or 0),
                permalink=issue.get("permalink") or "",
            )
            for issue in issues or []
        ]

    def get_slowest_queries(self, period: Period, limit: int = 8) -> list[QueryStat]:
        rows = self._events(
            ["span.description", "count()", "avg(span.duration)", "sum(span.duration)"],
            period,
            query="span.op:db",
            sort="-sum(span.duration)",
            limit=limit,
        )
        return [
            QueryStat(
                query=row.get("span.description") or "",
                calls=int(row.get("count()") or 0),
                avg_ms=row.get("avg(span.duration)"),
                total_ms=row.get("sum(span.duration)"),
            )
            for row in rows
            if row.get("span.description")
        ]

    # Span-op groups shown in the backend time breakdown; the request span
    # itself (http.server) is the whole, not a part.
    _OP_LABELS = {
        "db": _("Database"),
        "db.query": _("Database"),
        "db.redis": _("Redis"),
        "cache": _("Cache"),
        "cache.get": _("Cache"),
        "cache.set": _("Cache"),
        "template.render": _("Templates"),
        "view.response.render": _("View rendering"),
        "http.client": _("External HTTP"),
        "event.django": _("Signals"),
        "middleware.django": _("Middleware"),
    }

    def get_backend(self, period: Period) -> dict:
        """Where request time goes, summed across all spans in the period."""
        rows = self._events(
            ["span.op", "sum(span.duration)"],
            period,
            query="",
            sort="-sum(span.duration)",
            limit=20,
        )
        totals, request_ms = {}, 0
        for row in rows:
            op = row.get("span.op") or ""
            spent = row.get("sum(span.duration)") or 0
            if op == "http.server":
                request_ms += spent
                continue
            if not op:
                continue
            label = str(self._OP_LABELS.get(op, op))
            totals[label] = totals.get(label, 0) + spent
        ops = [BreakdownItem(label=label, value=int(value)) for label, value in totals.items()]
        ops.sort(key=lambda item: item.value, reverse=True)
        return {"ops": with_shares(ops), "request_ms": int(request_ms)}

    # -- insights ------------------------------------------------------------
    _SLOW_QUERY_MS = 500
    _DB_SHARE_WARN = 0.4  # of total request time
    _FAILURE_WARN = 0.05
    _P95_WARN_MS = 1000

    def get_insights(self, period: Period) -> list[Insight]:
        insights = []
        rules = (
            self._n_plus_one_insights,
            self._query_insights,
            self._db_share_insights,
            self._performance_insights,
        )
        for rule in rules:
            try:
                insights.extend(rule(period))
            except ProviderError:
                continue  # one failing dataset never silences the other findings
        return insights

    def _n_plus_one_insights(self, period: Period) -> list[Insight]:
        effective = self.limit_period(period)
        params = [
            ("query", "is:unresolved issue.category:db_query"),
            ("sort", "freq"),
            ("start", effective.start.strftime(_DATETIME)),
            ("end", effective.end.strftime(_DATETIME)),
            ("utc", "true"),
            ("limit", "5"),
        ]
        project_id = self._project_info()["id"]
        if project_id:
            params.append(("project", project_id))
        issues = self._cached_api(f"organizations/{self.org}/issues/", params) or []
        if not issues:
            return []
        worst = issues[0]
        return [
            Insight(
                severity=base.INSIGHT_BAD,
                title=_("Sentry detected %(count)s repeated-query (N+1) problems")
                % {"count": len(issues)},
                detail=_("Worst offender: %(culprit)s.")
                % {"culprit": worst.get("culprit") or worst.get("title", "")},
                action=_(
                    "Load the related objects up front with select_related() or "
                    "prefetch_related() instead of querying inside a loop."
                ),
            )
        ]

    def _query_insights(self, period: Period) -> list[Insight]:
        slow = [
            stat
            for stat in self.get_slowest_queries(period, limit=3)
            if (stat.avg_ms or 0) >= self._SLOW_QUERY_MS
        ]
        if not slow:
            return []
        worst = slow[0]
        return [
            Insight(
                severity=base.INSIGHT_WARN,
                title=_("Some database queries are slow"),
                detail=_("The worst averages %(avg)dms over %(calls)s calls: %(query)s")
                % {
                    "avg": worst.avg_ms,
                    "calls": f"{worst.calls:,}",
                    "query": worst.query[:120],
                },
                action=_(
                    "Add an index for its filter columns, or rework the query — "
                    "the full list is in the slowest-queries card."
                ),
            )
        ]

    def _db_share_insights(self, period: Period) -> list[Insight]:
        backend = self.get_backend(period)
        request_ms = backend.get("request_ms") or 0
        if not request_ms:
            return []
        db_ms = sum(item.value for item in backend["ops"] if str(item.label) == _("Database"))
        share = db_ms / request_ms
        if share >= self._DB_SHARE_WARN:
            return [
                Insight(
                    severity=base.INSIGHT_WARN,
                    title=_("Requests spend most of their time in the database"),
                    detail=_("%(share).0f%% of total request time went to database queries.")
                    % {"share": share * 100},
                    action=_(
                        "Cache repeated lookups and check the slowest-queries "
                        "card for what to optimise first."
                    ),
                )
            ]
        return []

    def _performance_insights(self, period: Period) -> list[Insight]:
        stats = self.get_performance(period)
        if stats is None or not stats.requests:
            return []
        insights = []
        if (stats.failure_rate or 0) >= self._FAILURE_WARN:
            insights.append(
                Insight(
                    severity=base.INSIGHT_BAD,
                    title=_("A worrying share of requests fail"),
                    detail=_("%(share).1f%% of %(requests)s transactions ended in an error.")
                    % {"share": stats.failure_rate * 100, "requests": f"{stats.requests:,}"},
                    action=_("Start with the top entries of the application errors list."),
                )
            )
        if (stats.p95_ms or 0) >= self._P95_WARN_MS:
            slowest = self.get_slowest_routes(period, limit=1)
            detail = _("The slowest 5%% of requests take over %(p95).0fms.") % {"p95": stats.p95_ms}
            if slowest:
                detail += " " + _("Slowest route: %(route)s.") % {"route": slowest[0].transaction}
            insights.append(
                Insight(
                    severity=base.INSIGHT_WARN,
                    title=_("Tail latency is high"),
                    detail=detail,
                    action=_("Profile that route; the slowest-queries card often names the cause."),
                )
            )
        elif (stats.failure_rate or 1) < self._FAILURE_WARN:
            insights.append(
                Insight(
                    severity=base.INSIGHT_GOOD,
                    title=_("Application latency looks healthy"),
                    detail=_("p95 is %(p95).0fms across %(requests)s transactions.")
                    % {"p95": stats.p95_ms or 0, "requests": f"{stats.requests:,}"},
                )
            )
        return insights

    def get_route_context(self, route, period: Period) -> dict:
        from ..routes import matching_transactions

        transactions = matching_transactions(route, self.get_transactions(period))
        return {"transactions": transactions}

    def invalidate_cache(self) -> None:
        try:
            self.cache.incr(_VERSION_KEY)
        except ValueError:
            self.cache.set(_VERSION_KEY, 1, None)
