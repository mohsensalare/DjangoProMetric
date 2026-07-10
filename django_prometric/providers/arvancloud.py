"""ArvanCloud CDN provider — edge traffic, cache, geography, status and attacks.

Phase 1 (Reports API MVP). The Reports API is the authoritative source for the
dashboard's selected historical windows; it is read with the standard library
only (urllib) and responses are cached through Django's cache framework to
respect the API's rate limits — mirroring the Cloudflare and Sentry providers.

Scope is a single registered ArvanCloud domain, optionally narrowed to one
subdomain. Several reports (status, attacks) cannot be filtered by subdomain in
the current API, so those capabilities are dropped when a subdomain scope is
configured rather than mixing another application's traffic into the numbers —
the same host-scoping principle the Cloudflare provider follows for zones.

Deliberately *not* implemented yet (gated on an authenticated Metric Exporter
discovery spike): route/path level metrics, HTTP method breakdowns and
response-time percentiles. Those
depend on live exporter labels, units and time semantics that cannot be proven
from the OpenAPI schema alone, so the provider must not advertise them until
fixtures back them. The class is structured so that plane slots in later.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from django.core.cache import caches
from django.utils import timezone
from django.utils.translation import gettext as _

from ..conf import get_config
from . import base
from .base import (
    AnalyticsProvider,
    BreakdownItem,
    Insight,
    OverviewStats,
    Period,
    ProviderError,
    TimeseriesPoint,
    with_shares,
)

_DATETIME = "%Y-%m-%dT%H:%M:%SZ"

# Bumped by invalidate_cache() so every cached response key changes at once.
_VERSION_KEY = "prometric:arvan:key-version"

# Dashboard preset periods that map straight to an ArvanCloud report preset.
_DIRECT_PRESETS = {"24h", "7d", "30d"}

# ArvanCloud report period presets, shortest first, as (name, seconds). Attack
# reports accept only a preset (no since/until), so a custom window is
# approximated to the smallest preset that covers it. 5m is enterprise-only and
# 45m is not offered on every plan, so neither is used for approximation.
_ATTACK_PRESETS = (
    ("1h", 3600),
    ("3h", 3 * 3600),
    ("6h", 6 * 3600),
    ("12h", 12 * 3600),
    ("24h", 24 * 3600),
    ("7d", 7 * 86400),
    ("30d", 30 * 86400),
)

# OpenAPI pattern for filter[subdomain]; "@" is the root domain.
_SUBDOMAIN_RE = re.compile(r"^[\w@-]+$")


def _int_or_none(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_utc(stamp: dt.datetime) -> dt.datetime:
    """A period boundary as real UTC, ready to stamp with a ``Z`` suffix.

    Preset periods come from ``timezone.now()`` (UTC when ``USE_TZ`` is on),
    but a custom range is built with ``timezone.make_aware`` in the project's
    local zone. Formatting either with ``strftime("...Z")`` and no conversion
    sends local wall-clock as if it were UTC. On a recent, hourly-bucketed
    window that shifts the result — measured at +6.4% for a single Asia/Tehran
    day (a +03:30 offset). Long windows use daily buckets, which usually mask
    the shift, so the bug is easy to miss without converting here.
    """
    if timezone.is_naive(stamp):
        stamp = timezone.make_aware(stamp)
    return stamp.astimezone(dt.timezone.utc)


def _parse_dt(raw) -> dt.datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return dt.datetime.strptime(text, _DATETIME)
    except ValueError:
        pass
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _stamp_label(raw, daily: bool) -> str:
    stamp = _parse_dt(raw)
    if stamp is None:
        return str(raw or "")
    return stamp.strftime("%m-%d" if daily else "%H:%M")


def _series_named(series: list, name: str) -> list:
    """The ``data`` array of the series with this ``name`` — matched by name,
    never by position, so extra or reordered series can't shift the mapping."""
    for entry in series or []:
        if (entry or {}).get("name") == name:
            return entry.get("data") or []
    return []


def _looks_plan_gated(detail: str) -> bool:
    low = (detail or "").lower()
    return any(word in low for word in ("enterprise", "plan", "upgrade", "not allowed"))


class ArvanCloudProvider(AnalyticsProvider):
    slug = "arvancloud"
    verbose_name = "ArvanCloud"
    kind = _("Edge traffic")
    route_template = ""

    def __init__(self):
        super().__init__()
        config = get_config()
        arvan = config["ARVANCLOUD"]
        self.base_url = arvan["BASE_URL"].rstrip("/")
        self.timeout = arvan["TIMEOUT"]
        self.api_key_env = arvan["API_KEY_ENV"]
        self.domain_env = arvan["DOMAIN_ENV"]
        self.api_key = os.environ.get(self.api_key_env, "").strip()
        self.domain = os.environ.get(self.domain_env, "").strip().lower()
        self.subdomain = str(arvan["SUBDOMAIN"] or "").strip().lower()
        self.max_period_days = arvan["MAX_REPORT_DAYS"]
        self.cache = caches[config["CACHE_ALIAS"]]
        self.cache_ttl = config["CACHE_TTL"]

    # -- configuration -----------------------------------------------------
    @property
    def _subdomain_valid(self) -> bool:
        return not self.subdomain or bool(_SUBDOMAIN_RE.match(self.subdomain))

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.domain and self._subdomain_valid)

    def configuration_help(self) -> str:
        if self.api_key and self.domain and not self._subdomain_valid:
            return _(
                'The configured ArvanCloud subdomain scope ("%(subdomain)s") is '
                'invalid. Use a single label such as "blog", or "@" for the root '
                "domain."
            ) % {"subdomain": self.subdomain}
        return _(
            "Set the %(key)s and %(domain)s environment variables to an "
            "ArvanCloud API key with report read access and your domain "
            "(for example example.com), then restart the server."
        ) % {"key": self.api_key_env, "domain": self.domain_env}

    def configuration_warnings(self) -> list:
        # Console-facing developer message; deliberately not translated.
        from django.core.checks import Warning as CheckWarning

        if self.subdomain:
            return []
        return [
            CheckWarning(
                "ArvanCloud analytics are not scoped to a subdomain.",
                hint=(
                    "A domain can front several applications across its "
                    "subdomains, so the dashboard counts the whole domain — "
                    "including hosts this project does not serve. Set "
                    "DJANGO_PROMETRIC['ARVANCLOUD']['SUBDOMAIN'] to the "
                    "subdomain this project answers to, or '@' for the root "
                    "domain."
                ),
                obj="django_prometric.providers.arvancloud",
                id="django_prometric.W003",
            )
        ]

    def description(self) -> str:
        if self.subdomain:
            return f"{self.domain} ({self.subdomain})"
        return self.domain

    @property
    def scoped(self) -> bool:
        """True when narrowed to one subdomain — some reports can't be filtered
        to it, so they must not feed scoped numbers."""
        return bool(self.subdomain)

    def capabilities(self) -> set:
        caps = {
            base.OVERVIEW,
            base.TIMESERIES,
            base.COUNTRY,
            base.CACHE,
            base.BANDWIDTH,
            base.UNIQUES,
            base.INSIGHTS,
        }
        # Status and attack reports are whole-domain only in the current API,
        # so a subdomain scope would silently mix in other apps' traffic.
        if not self.scoped:
            caps |= {base.STATUS, base.THREATS, base.SECURITY}
        return caps

    # -- HTTP plumbing -------------------------------------------------------
    def _auth_header(self) -> str:
        """The Authorization value.

        A raw key becomes ``Apikey <key>``; a value that already carries an
        ``Apikey`` or ``Bearer`` scheme is preserved as supplied.
        """
        key = self.api_key
        if key[:7].lower() in ("apikey ", "bearer "):
            return key
        return f"Apikey {key}"

    def _get(self, path: str, params: list | None = None) -> dict:
        query = urllib.parse.urlencode(params or [], doseq=True)
        url = f"{self.base_url}{path}"
        if query:
            url += "?" + query
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "Authorization": self._auth_header()},
        )
        # Flaky links drop TLS connections mid-handshake now and then; one
        # immediate retry absorbs that without hiding real outages.
        for attempt in (1, 2):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                    ctype = response.headers.get_content_type()
                    if ctype and "json" not in ctype:
                        raise ProviderError(
                            _("ArvanCloud returned an unexpected response."), kind="error"
                        )
                    try:
                        return json.loads(raw.decode())
                    except (ValueError, UnicodeDecodeError) as exc:
                        raise ProviderError(
                            _("ArvanCloud returned an unreadable response."), kind="error"
                        ) from exc
            except urllib.error.HTTPError as exc:
                # Never echo the request URL: report endpoints carry no secret,
                # but keeping messages URL-free keeps the exporter data plane
                # (Phase 2) safe by construction.
                raise self._http_error(exc) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt == 1:
                    continue
                raise ProviderError(
                    _("Could not reach the ArvanCloud API: %(reason)s")
                    % {"reason": getattr(exc, "reason", exc)},
                    kind="network",
                ) from exc

    def _http_error(self, exc: urllib.error.HTTPError) -> ProviderError:
        code = exc.code
        if code in (401, 403):
            return ProviderError(
                _("ArvanCloud rejected the API key. Check its permissions."), kind="auth"
            )
        if code == 404:
            return ProviderError(
                _("ArvanCloud has no such domain, or the key cannot access it."), kind="config"
            )
        if code == 429:
            return ProviderError(
                _("ArvanCloud rate limit reached; try again shortly."), kind="quota"
            )
        if code == 422:
            detail = self._error_detail(exc)
            return ProviderError(
                _("ArvanCloud rejected the request: %(detail)s")
                % {"detail": detail or _("invalid filter or time window")},
                kind="plan" if _looks_plan_gated(detail) else "config",
            )
        return ProviderError(
            _("ArvanCloud API returned HTTP %(code)s.") % {"code": code}, kind="network"
        )

    @staticmethod
    def _error_detail(exc) -> str:
        try:
            body = json.loads(exc.read().decode())
        except Exception:  # noqa: BLE001 — the body is best-effort context
            return ""
        message = body.get("message") if isinstance(body, dict) else ""
        return (message or "")[:200]

    def _cached_get(self, path: str, params: list | None = None) -> dict:
        params = params or []
        version = self.cache.get(_VERSION_KEY, 0)
        # Only the method, path and canonical query go into the key — never the
        # API key — so cache keys are safe to log.
        canonical = "GET " + path + "?" + urllib.parse.urlencode(sorted(params))
        key = f"prometric:arvan:v1:{version}:" + hashlib.sha256(canonical.encode()).hexdigest()
        result = self.cache.get(key)
        if result is None:
            result = self._get(path, params)
            self.cache.set(key, result, self.cache_ttl)
        return result

    def _report(self, name: str, params: list):
        """A report endpoint's ``data`` payload (dict or list, ``None`` when
        absent). Callers coerce with ``or {}`` / ``or []``."""
        domain = urllib.parse.quote(self.domain, safe="")
        body = self._cached_get(f"/domains/{domain}/reports/{name}", params)
        return (body or {}).get("data")

    # -- period & scope helpers ---------------------------------------------
    def _time_params(self, period: Period) -> list:
        """Query params for a report that accepts a preset or a since/until
        range. Presets are used for the matching recent windows; everything
        else (custom ranges, the clamped 90-day window) uses since/until."""
        effective = self.limit_period(period)  # clamps to MAX_REPORT_DAYS + notes it
        if not period.is_custom and period.key in _DIRECT_PRESETS:
            return [("period", period.key)]
        # Convert to real UTC before the Z suffix — see _to_utc.
        return [
            ("since", _to_utc(effective.start).strftime(_DATETIME)),
            ("until", _to_utc(effective.end).strftime(_DATETIME)),
        ]

    def _window_has_ended(self, period: Period) -> bool:
        """True when the window's end is meaningfully in the past.

        Attack presets are always anchored to *now*, so they can approximate a
        recent window but can never reach one that has already closed.
        """
        return _to_utc(timezone.now()) - _to_utc(period.end) > dt.timedelta(hours=1)

    def _attacks_unavailable(self, period: Period) -> bool:
        """Whether the attack reports cannot answer this window at all.

        They take only a now-anchored preset (a since/until here 500s), so a
        custom range that has already ended cannot be fetched — approximating
        it to a preset would return unrelated *recent* attacks. Callers skip or
        degrade the threat/security cards when this is true.
        """
        return period.is_custom and self._window_has_ended(period)

    def _attack_params(self, period: Period) -> list:
        """A single preset for the attack reports, which take no custom range.

        Picks the smallest preset that fully covers the requested window,
        capped at MAX_REPORT_DAYS, and notes when the effective window had to
        differ from the dashboard period. A window that has already ended is
        refused (see _attacks_unavailable); the raised error becomes the
        Security card's message rather than a whole-dashboard notice, because
        the period-over-period delta always compares against a past window and
        would otherwise flag every render.
        """
        if not period.is_custom and period.key in _DIRECT_PRESETS:
            return [("period", period.key)]
        if self._attacks_unavailable(period):
            raise ProviderError(
                _(
                    "ArvanCloud attack reports cover only recent fixed windows, "
                    "so security data is not available for a historical range."
                ),
                kind="config",
            )
        effective = self.limit_period(period)
        window = (effective.end - effective.start).total_seconds()
        preset = next(
            (name for name, seconds in _ATTACK_PRESETS if seconds >= window),
            _ATTACK_PRESETS[-1][0],
        )
        self.add_notice(
            _(
                "ArvanCloud attack reports use fixed windows; showing the "
                "closest available (%(preset)s)."
            )
            % {"preset": preset},
            level=base.NOTICE_WARN,
        )
        return [("period", preset)]

    def _scope(self) -> list:
        """The subdomain filter, for the reports that accept one."""
        return [("filter[subdomain]", self.subdomain)] if self.subdomain else []

    def _safe(self, fn, period: Period):
        """Run an optional overview part; a failure leaves its field ``None``
        and a single notice instead of failing the whole overview."""
        try:
            return fn(period)
        except ProviderError:
            self.add_notice(
                _("Some ArvanCloud figures are unavailable right now."), level=base.NOTICE_INFO
            )
            return None

    @staticmethod
    def _safe_list(fn, period: Period) -> list:
        try:
            return fn(period)
        except ProviderError:
            return []

    # -- reports -------------------------------------------------------------
    def _traffics(self, period: Period) -> dict:
        return self._report("traffics", self._time_params(period) + self._scope()) or {}

    def _visitors_total(self, period: Period) -> int | None:
        data = self._report("visitors", self._time_params(period) + self._scope()) or {}
        visitors = (data.get("statistics") or {}).get("visitors") or {}
        return _int_or_none(visitors.get("total_visitors"))

    def _status_classes(self, period: Period) -> dict:
        # Status reports carry no subdomain filter, so one is never sent.
        data = self._report("status", self._time_params(period)) or {}
        codes = (data.get("statistics") or {}).get("status_codes") or {}
        return {
            "2xx": int(codes.get("2xx_sum") or 0),
            "3xx": int(codes.get("3xx_sum") or 0),
            "4xx": int(codes.get("4xx_sum") or 0),
            "5xx": int(codes.get("5xx_sum") or 0),
        }

    def _status_errors(self, period: Period) -> int:
        classes = self._status_classes(period)
        return classes["4xx"] + classes["5xx"]

    def _attacks_total(self, period: Period) -> int:
        data = self._report("attacks", self._attack_params(period)) or {}
        attacks = (data.get("statistics") or {}).get("Attacks") or {}
        return int(attacks.get("total_attacks") or 0)

    # -- provider API --------------------------------------------------------
    def get_overview(self, period: Period) -> OverviewStats:
        traffic = self._traffics(period)
        statistics = traffic.get("statistics") or {}
        requests = statistics.get("requests") or {}
        traffics = statistics.get("traffics") or {}
        stats = OverviewStats(
            requests=int(requests.get("total") or 0),
            # Edge traffic volume, in the "traffics" field. Live values are
            # consistent with bytes (≈5–6 KB/request on a probed domain), which
            # is how the dashboard renders it.
            bandwidth_bytes=_int_or_none(traffics.get("total")),
            # "saved" == requests answered from the ArvanCloud cache.
            cached_requests=_int_or_none(requests.get("saved")),
            # total_visitors is ArvanCloud's unique-visitor count.
            unique_visitors=self._safe(self._visitors_total, period),
        )
        # Status and attack totals are whole-domain only; never report them
        # under a subdomain scope (they would count other applications).
        if not self.scoped:
            stats.errors = self._safe(self._status_errors, period)
            # Attack reports can't answer a window that has already ended, so
            # leave threats unset for a historical range instead of raising a
            # transient-failure notice; the Security card carries the reason.
            if not self._attacks_unavailable(period):
                stats.threats = self._safe(self._attacks_total, period)
        return stats

    def get_timeseries(self, period: Period) -> list[TimeseriesPoint]:
        charts = (self._traffics(period).get("charts") or {}).get("requests") or {}
        categories = charts.get("categories") or []
        series = _series_named(charts.get("series") or [], "reports.requests.total")
        if len(series) != len(categories):
            return []  # never pair mismatched arrays by position
        daily = period.days > 2
        return [
            TimeseriesPoint(label=_stamp_label(label, daily), requests=int(value or 0))
            for label, value in zip(categories, series)
        ]

    def get_breakdown(self, dimension: str, period: Period, limit: int = 12) -> list[BreakdownItem]:
        if dimension == base.DIM_COUNTRY:
            return self._country_breakdown(period, limit)
        if dimension == base.DIM_CACHE:
            return self._cache_breakdown(period)
        if dimension == base.DIM_STATUS and not self.scoped:
            return self._status_breakdown(period)
        # An empty list would read as valid zero traffic; refuse instead.
        raise NotImplementedError(dimension)

    def _country_breakdown(self, period: Period, limit: int) -> list[BreakdownItem]:
        data = self._report("traffics/map", self._time_params(period) + self._scope()) or {}
        rows = sorted(
            data.get("lists") or [], key=lambda r: int(r.get("requests") or 0), reverse=True
        )
        # Live data: "name" is the full country name, "country" is the 2-letter
        # code and "code" the 3-letter one (the OpenAPI description is wrong).
        items = [
            BreakdownItem(
                label=str(r.get("name") or r.get("country") or r.get("code") or ""),
                value=int(r.get("requests") or 0),
            )
            for r in rows[:limit]
        ]
        return with_shares(items)

    def _cache_breakdown(self, period: Period) -> list[BreakdownItem]:
        data = self._report("traffics/saved", self._time_params(period) + self._scope()) or {}
        request = (data.get("statistics") or {}).get("request") or {}
        total = int(request.get("total") or 0)
        hits = int(request.get("saved") or 0)
        misses = max(total - hits, 0)
        return with_shares(
            [
                BreakdownItem(label=_("Cached"), value=hits),
                BreakdownItem(label=_("Uncached"), value=misses),
            ]
        )

    def _status_breakdown(self, period: Period) -> list[BreakdownItem]:
        classes = self._status_classes(period)
        items = [BreakdownItem(label=label, value=value) for label, value in classes.items()]
        return with_shares(items)

    def get_security(self, period: Period) -> dict:
        return {
            "total": self._attacks_total(period),
            # No verified mitigation-action field exists in the Reports API.
            "actions": [],
            "sources": self._safe_list(self._attackers, period),
            "countries": self._safe_list(self._attack_countries, period),
            "paths": self._safe_list(self._attack_uris, period),
        }

    def _attack_countries(self, period: Period) -> list[BreakdownItem]:
        data = self._report("attacks/map", self._attack_params(period)) or {}
        rows = sorted(
            data.get("lists") or [], key=lambda r: int(r.get("attack") or 0), reverse=True
        )
        items = [
            BreakdownItem(
                label=str(r.get("name") or r.get("country") or ""), value=int(r.get("attack") or 0)
            )
            for r in rows[:6]
        ]
        return with_shares(items)

    def _attack_uris(self, period: Period) -> list[BreakdownItem]:
        rows = sorted(
            self._report("attacks/uri", self._attack_params(period)) or [],
            key=lambda r: int(r.get("count") or 0),
            reverse=True,
        )
        items = [
            BreakdownItem(label=str(r.get("uri") or ""), value=int(r.get("count") or 0))
            for r in rows[:8]
        ]
        return with_shares(items)

    def _attackers(self, period: Period) -> list[BreakdownItem]:
        rows = sorted(
            self._report("attacks/attackers", self._attack_params(period)) or [],
            key=lambda r: int(r.get("count") or 0),
            reverse=True,
        )
        items = [
            BreakdownItem(label=str(r.get("ip") or ""), value=int(r.get("count") or 0))
            for r in rows[:8]
        ]
        return with_shares(items)

    # -- insights ------------------------------------------------------------
    # Thresholds are shares of total requests over the selected period.
    _CACHE_LOW = 0.20
    _CACHE_HEALTHY = 0.50
    _5XX_WARN = 0.005
    _5XX_BAD = 0.02
    _4XX_WARN = 0.10
    _ATTACK_WARN = 500  # absolute attacks in the window

    def get_insights(self, period: Period) -> list[Insight]:
        insights: list[Insight] = []
        rules = [self._cache_insights]
        if not self.scoped:
            rules += [self._status_insights, self._attack_insights]
        for rule in rules:
            try:
                insights.extend(rule(period))
            except ProviderError:
                continue  # one unavailable report never silences the others
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
                        "pages so ArvanCloud can answer them without hitting Django."
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
        classes = self._status_classes(period)
        total = sum(classes.values())
        if not total:
            return []
        insights = []
        share_5xx = classes["5xx"] / total
        if share_5xx >= self._5XX_WARN:
            insights.append(
                Insight(
                    severity=base.INSIGHT_BAD if share_5xx >= self._5XX_BAD else base.INSIGHT_WARN,
                    title=_("Requests are failing with server errors"),
                    detail=_("%(count)s requests (%(share).1f%%) ended in a 5xx status.")
                    % {"count": f"{classes['5xx']:,}", "share": share_5xx * 100},
                    action=_("Check your origin and the application error logs for the causes."),
                )
            )
        elif classes["5xx"] == 0:
            insights.append(
                Insight(
                    severity=base.INSIGHT_GOOD,
                    title=_("No server errors at the edge"),
                    detail=_("None of the %(requests)s requests returned a 5xx status.")
                    % {"requests": f"{total:,}"},
                )
            )
        share_4xx = classes["4xx"] / total
        if share_4xx >= self._4XX_WARN:
            insights.append(
                Insight(
                    severity=base.INSIGHT_WARN,
                    title=_("A lot of traffic hits client errors"),
                    detail=_("%(count)s requests (%(share).1f%%) returned a 4xx status.")
                    % {"count": f"{classes['4xx']:,}", "share": share_4xx * 100},
                    action=_(
                        "Look for broken links and bad API calls, and add "
                        "redirects for pages that moved."
                    ),
                )
            )
        return insights

    def _attack_insights(self, period: Period) -> list[Insight]:
        security = self.get_security(period)
        total = security["total"]
        if not total:
            return []
        top_path = security["paths"][0].label if security["paths"] else ""
        detail = _("%(count)s attacks were recorded in this period.") % {"count": f"{total:,}"}
        if top_path:
            detail += " " + _("Most-targeted path: %(path)s.") % {"path": top_path}
        if total < self._ATTACK_WARN:
            return [
                Insight(severity=base.INSIGHT_GOOD, title=_("Attack traffic is low"), detail=detail)
            ]
        return [
            Insight(
                severity=base.INSIGHT_WARN,
                title=_("Elevated attack volume"),
                detail=detail,
                action=_(
                    "Review the security card; a stricter WAF rule or a rate "
                    "limit on the targeted paths can cut this at the edge."
                ),
            )
        ]

    def invalidate_cache(self) -> None:
        try:
            self.cache.incr(_VERSION_KEY)
        except ValueError:
            self.cache.set(_VERSION_KEY, 1, None)
