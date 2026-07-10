"""Tests for the ArvanCloud provider — no live API required.

The provider's only network touch-point is ``_get``; each data test stubs it
with a path-keyed dispatch so the period handling, subdomain scoping, report
parsing, degradation and insight logic run against canned responses. The
fixtures follow the shapes in the CDN 4.0 OpenAPI schema (sanitized example
values only — no real captures).
"""

from __future__ import annotations

import datetime as dt
import io
import urllib.error
import urllib.request

import pytest
from django.test import override_settings
from django.utils import timezone

from django_prometric.providers import _load, base
from django_prometric.providers.arvancloud import ArvanCloudProvider
from django_prometric.providers.base import Period, ProviderError

# -- canned report payloads (as returned by _get, with the data wrapper) -----
TRAFFICS = {
    "data": {
        "statistics": {
            "requests": {"total": 1000, "saved": 700, "bypass": 300},
            "traffics": {"total": 5_000_000, "saved": 3_000_000, "bypass": 2_000_000},
        },
        "charts": {
            "requests": {
                "title": "reports.requests",
                "categories": ["2026-07-10T10:00:00Z", "2026-07-10T11:00:00Z"],
                # saved deliberately before total, to prove name-based matching.
                "series": [
                    {"name": "reports.requests.saved", "data": [50, 60]},
                    {"name": "reports.requests.total", "data": [100, 120]},
                ],
            }
        },
    }
}
SAVED = {
    "data": {
        "statistics": {
            "request": {"saved": 700, "total": 1000},
            "traffic": {"saved": 3_000_000, "total": 5_000_000},
        }
    }
}
# Live shape: "country" is the 2-letter code, "name" the full country name.
TRAFFICS_MAP = {
    "data": {
        "lists": [
            {
                "country": "DE",
                "name": "Germany",
                "code": "DEU",
                "requests": 100,
                "traffics": 1e6,
                "visitors": [],
            },
            {
                "country": "IR",
                "name": "Iran",
                "code": "IRN",
                "requests": 900,
                "traffics": 4e6,
                "visitors": [],
            },
        ]
    }
}
VISITORS = {"data": {"statistics": {"visitors": {"total_visitors": 321}}}}
STATUS = {
    "data": {
        "statistics": {
            "status_codes": {"2xx_sum": 800, "3xx_sum": 100, "4xx_sum": 80, "5xx_sum": 20}
        }
    }
}
ATTACKS = {"data": {"statistics": {"Attacks": {"total_attacks": 42}}}}
ATTACKS_MAP = {
    "data": {
        "lists": [
            {"country": "CN", "name": "China", "code": "CHN", "attack": 12},
            {"country": "RU", "name": "Russia", "code": "RUS", "attack": 30},
        ]
    }
}
ATTACKS_URI = {"data": [{"uri": "/admin", "count": 17}, {"uri": "/wp-login.php", "count": 25}]}
ATTACKERS = {"data": [{"ip": "203.0.113.7", "count": 20}, {"ip": "198.51.100.4", "count": 22}]}

ALL_REPORTS = {
    "traffics": TRAFFICS,
    "traffics/saved": SAVED,
    "traffics/map": TRAFFICS_MAP,
    "visitors": VISITORS,
    "status": STATUS,
    "attacks": ATTACKS,
    "attacks/map": ATTACKS_MAP,
    "attacks/uri": ATTACKS_URI,
    "attacks/attackers": ATTACKERS,
}


@pytest.fixture(autouse=True)
def _clear_cache():
    from django.core.cache import caches

    caches["default"].clear()
    yield
    caches["default"].clear()


@pytest.fixture
def period():
    return Period.from_key("24h")


def _provider(bodies=None, *, key="secret-key", domain="example.com", subdomain="", max_days=90):
    """A provider whose ``_get`` dispatches on the report name in the path.

    A body value that is an exception instance is raised, to exercise the
    degradation paths.
    """
    provider = ArvanCloudProvider()
    provider.api_key = key
    provider.domain = domain
    provider.subdomain = subdomain
    provider.max_period_days = max_days
    provider.calls = []

    bodies = ALL_REPORTS if bodies is None else bodies

    def fake_get(path, params=None):
        provider.calls.append((path, list(params or [])))
        name = path.split("/reports/", 1)[1] if "/reports/" in path else path
        result = bodies.get(name)
        if result is None:
            raise AssertionError(f"unexpected report {name!r} (path {path})")
        if isinstance(result, Exception):
            raise result
        return result

    provider._get = fake_get
    return provider


def _live(*, key="secret-key", domain="example.com"):
    """A provider with its real ``_get`` intact, for exercising the HTTP layer."""
    provider = ArvanCloudProvider()
    provider.api_key = key
    provider.domain = domain
    return provider


def _params_for(provider, name):
    for path, params in provider.calls:
        if path.endswith("/reports/" + name):
            return params
    raise AssertionError(f"{name} was not requested; calls: {[p for p, _ in provider.calls]}")


def _custom(days):
    end = timezone.now()
    return Period.custom(end - timezone.timedelta(days=days), end)


def _historical(days=3):
    """A custom range that has already closed (ends 30 days ago)."""
    end = timezone.now() - timezone.timedelta(days=30)
    return Period.custom(end - timezone.timedelta(days=days), end)


# -- configuration ----------------------------------------------------------
def test_not_configured_without_credentials(monkeypatch):
    monkeypatch.delenv("ARVANCLOUD_API_KEY", raising=False)
    monkeypatch.delenv("ARVANCLOUD_DOMAIN", raising=False)
    assert ArvanCloudProvider().is_configured is False


def test_configured_reads_env_and_lowercases_domain(monkeypatch):
    monkeypatch.setenv("ARVANCLOUD_API_KEY", "  key  ")
    monkeypatch.setenv("ARVANCLOUD_DOMAIN", "Example.COM")
    provider = ArvanCloudProvider()
    assert provider.is_configured is True
    assert provider.api_key == "key"
    assert provider.domain == "example.com"


def test_invalid_subdomain_is_not_configured():
    provider = _provider()
    provider.subdomain = "bad host!"
    assert provider._subdomain_valid is False
    assert provider.is_configured is False
    assert "invalid" in provider.configuration_help().lower()


def test_at_sign_subdomain_is_valid():
    provider = _provider(subdomain="@")
    assert provider._subdomain_valid is True
    assert provider.is_configured is True


def test_description_plain_and_scoped():
    assert _provider().description() == "example.com"
    assert _provider(subdomain="blog").description() == "example.com (blog)"


@override_settings(DJANGO_PROMETRIC={"ARVANCLOUD": {"BASE_URL": "https://api.example/cdn/4.0/"}})
def test_base_url_is_normalized():
    assert ArvanCloudProvider().base_url == "https://api.example/cdn/4.0"


def test_auth_header_wraps_raw_key_and_preserves_scheme():
    assert _provider(key="raw")._auth_header() == "Apikey raw"
    assert _provider(key="Apikey raw")._auth_header() == "Apikey raw"
    assert _provider(key="Bearer jwt")._auth_header() == "Bearer jwt"


def test_capabilities_drop_whole_domain_reports_when_scoped():
    full = _provider().capabilities()
    assert {base.STATUS, base.THREATS, base.SECURITY} <= full
    scoped = _provider(subdomain="blog").capabilities()
    assert scoped.isdisjoint({base.STATUS, base.THREATS, base.SECURITY})
    assert base.OVERVIEW in scoped and base.COUNTRY in scoped


def test_configuration_warning_only_without_subdomain():
    assert _provider().configuration_warnings()  # unscoped → one warning
    assert _provider(subdomain="blog").configuration_warnings() == []


def test_registry_alias_resolves():
    assert _load("arvancloud") is ArvanCloudProvider


def test_secret_never_appears_in_configuration_strings(monkeypatch):
    monkeypatch.setenv("ARVANCLOUD_API_KEY", "super-secret-token")
    monkeypatch.setenv("ARVANCLOUD_DOMAIN", "example.com")
    provider = ArvanCloudProvider()
    assert "super-secret-token" not in provider.description()
    assert "super-secret-token" not in provider.configuration_help()


# -- HTTP errors & retry ----------------------------------------------------
def _http_error(code, body=b""):
    return urllib.error.HTTPError("http://x", code, "", None, io.BytesIO(body))


@pytest.mark.parametrize(
    "code,kind",
    [(401, "auth"), (403, "auth"), (404, "config"), (429, "quota"), (500, "network")],
)
def test_http_error_mapping(code, kind):
    err = _provider()._http_error(_http_error(code))
    assert err.kind == kind


def test_422_is_config_by_default_and_plan_when_gated():
    provider = _provider()
    assert provider._http_error(_http_error(422, b'{"message":"bad filter"}')).kind == "config"
    gated = provider._http_error(_http_error(422, b'{"message":"Enterprise plan required"}'))
    assert gated.kind == "plan"


def test_get_retries_one_transient_failure(monkeypatch):
    provider = _live()
    calls = {"n": 0}

    class _Resp:
        headers = type("H", (), {"get_content_type": lambda self: "application/json"})()

        def read(self):
            return b'{"data": {"ok": true}}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(request, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError("connection reset")
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert provider._get("/domains/example.com/reports/traffics") == {"data": {"ok": True}}
    assert calls["n"] == 2


def test_get_gives_up_after_second_failure(monkeypatch):
    provider = _live()
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("down")),
    )
    with pytest.raises(ProviderError) as exc:
        provider._get("/domains/example.com/reports/traffics")
    assert exc.value.kind == "network"


def test_get_rejects_non_json_response(monkeypatch):
    provider = _live()

    class _Resp:
        headers = type("H", (), {"get_content_type": lambda self: "text/html"})()

        def read(self):
            return b"<html>login</html>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    with pytest.raises(ProviderError) as exc:
        provider._get("/domains/example.com/reports/traffics")
    assert exc.value.kind == "error"


# -- caching ----------------------------------------------------------------
def test_responses_are_cached_and_shared(period):
    provider = _provider()
    provider.get_overview(period)
    after_first = len(provider.calls)
    provider.get_overview(period)  # everything served from cache now
    assert len(provider.calls) == after_first


def test_invalidate_cache_forces_refetch(period):
    provider = _provider()
    provider.get_overview(period)
    before = len(provider.calls)
    provider.invalidate_cache()
    provider.get_overview(period)
    assert len(provider.calls) > before


# -- period & scope ---------------------------------------------------------
@pytest.mark.parametrize("key", ["24h", "7d", "30d"])
def test_recent_presets_map_directly(key):
    provider = _provider()
    provider.get_timeseries(Period.from_key(key))
    assert _params_for(provider, "traffics") == [("period", key)]


def test_custom_range_uses_since_until():
    provider = _provider()
    provider.get_timeseries(_custom(3))
    params = dict(_params_for(provider, "traffics"))
    assert "since" in params and "until" in params
    assert "period" not in params


def test_ninety_days_uses_since_until_without_clamp():
    # 90d is the longest preset the UI offers and is served whole, so it goes
    # out as a since/until range with no "shortened" notice.
    provider = _provider()
    provider.get_timeseries(Period.from_key("90d"))
    params = dict(_params_for(provider, "traffics"))
    assert "since" in params and "period" not in params
    assert not any(n.level == base.NOTICE_WARN for n in provider.notices)


def test_over_limit_custom_range_is_clamped_with_a_notice():
    provider = _provider()
    provider.get_timeseries(_custom(120))  # beyond the 90-day reach
    params = dict(_params_for(provider, "traffics"))
    assert "since" in params and "period" not in params
    assert any(n.level == base.NOTICE_WARN for n in provider.notices)


@override_settings(TIME_ZONE="Asia/Tehran")
def test_since_until_are_converted_to_utc():
    # Asia/Tehran is +03:30; a local midnight must go out as the real UTC
    # instant, not the wall-clock stamped with a bare Z.
    provider = _provider()
    start = timezone.make_aware(dt.datetime(2026, 6, 1, 0, 0, 0))
    end = timezone.make_aware(dt.datetime(2026, 6, 2, 0, 0, 0))
    provider.get_timeseries(Period.custom(start, end))
    params = dict(_params_for(provider, "traffics"))
    assert params["since"] == "2026-05-31T20:30:00Z"
    assert params["until"] == "2026-06-01T20:30:00Z"


def test_attack_reports_use_a_matching_preset():
    provider = _provider()
    provider.get_security(Period.from_key("7d"))
    assert _params_for(provider, "attacks") == [("period", "7d")]
    assert not provider.notices  # a matching preset needs no approximation notice


def test_attack_reports_approximate_a_custom_window():
    provider = _provider()
    provider.get_security(_custom(2))  # 2 days → next preset up is 7d
    assert _params_for(provider, "attacks") == [("period", "7d")]
    assert any("closest available" in str(n.message) for n in provider.notices)


def test_attack_reports_unavailable_for_a_historical_custom_range():
    # Attack presets are anchored to now, so a window that already closed can
    # only be faked with unrelated recent attacks — get_security refuses it.
    provider = _provider()
    with pytest.raises(ProviderError) as exc:
        provider.get_security(_historical())
    assert exc.value.kind == "config"
    assert all("/reports/attacks" not in path for path, _ in provider.calls)


def test_overview_threats_unset_for_historical_custom():
    # The overview degrades silently: threats stay None, no transient notice,
    # and the attack reports are never requested (the delta compares against a
    # past window on every render, so a notice here would always fire).
    provider = _provider()
    stats = provider.get_overview(_historical())
    assert stats.requests == 1000
    assert stats.errors == 100  # status still works over since/until
    assert stats.threats is None
    assert not any(n.level == base.NOTICE_INFO for n in provider.notices)
    assert all("/reports/attacks" not in path for path, _ in provider.calls)


def test_subdomain_filter_only_on_reports_that_accept_it():
    provider = _provider(subdomain="blog")
    provider.get_overview(Period.from_key("24h"))
    assert ("filter[subdomain]", "blog") in _params_for(provider, "traffics")
    # status & attacks are whole-domain only, so scoped overview skips them.
    assert all("/reports/status" not in path for path, _ in provider.calls)
    assert all("/reports/attacks" not in path for path, _ in provider.calls)


def test_unscoped_status_never_carries_a_subdomain_filter(period):
    provider = _provider()
    provider.get_overview(period)
    assert not any(k == "filter[subdomain]" for k, _ in _params_for(provider, "status"))


# -- overview ---------------------------------------------------------------
def test_overview_maps_all_fields(period):
    stats = _provider().get_overview(period)
    assert stats.requests == 1000
    assert stats.bandwidth_bytes == 5_000_000
    assert stats.cached_requests == 700
    assert stats.unique_visitors == 321
    assert stats.errors == 100  # 4xx (80) + 5xx (20)
    assert stats.threats == 42
    assert stats.page_views is None and stats.avg_response_ms is None


def test_overview_scoped_omits_whole_domain_fields(period):
    stats = _provider(subdomain="blog").get_overview(period)
    assert stats.requests == 1000
    assert stats.errors is None
    assert stats.threats is None


def test_overview_degrades_when_one_report_fails(period):
    bodies = dict(ALL_REPORTS, visitors=ProviderError("boom", kind="network"))
    provider = _provider(bodies)
    stats = provider.get_overview(period)
    assert stats.requests == 1000  # spine still works
    assert stats.unique_visitors is None
    assert provider.notices


# -- timeseries -------------------------------------------------------------
def test_timeseries_matches_total_series_by_name(period):
    points = _provider().get_timeseries(period)
    assert [p.requests for p in points] == [100, 120]
    assert [p.label for p in points] == ["10:00", "11:00"]


def test_timeseries_returns_empty_on_length_mismatch(period):
    broken = {
        "data": {
            "charts": {
                "requests": {
                    "categories": ["2026-07-10T10:00:00Z"],
                    "series": [{"name": "reports.requests.total", "data": [1, 2, 3]}],
                }
            }
        }
    }
    assert _provider({"traffics": broken}).get_timeseries(period) == []


# -- breakdowns -------------------------------------------------------------
def test_country_breakdown_is_sorted_with_shares(period):
    items = _provider().get_breakdown(base.DIM_COUNTRY, period)
    assert [i.label for i in items] == ["Iran", "Germany"]
    assert items[0].value == 900
    assert round(items[0].share, 2) == 0.90


def test_cache_breakdown_splits_hits_and_misses(period):
    items = _provider().get_breakdown(base.DIM_CACHE, period)
    assert {str(i.label): i.value for i in items} == {"Cached": 700, "Uncached": 300}


def test_status_breakdown_returns_classes(period):
    items = _provider().get_breakdown(base.DIM_STATUS, period)
    assert {i.label: i.value for i in items} == {"2xx": 800, "3xx": 100, "4xx": 80, "5xx": 20}


def test_status_breakdown_unavailable_when_scoped(period):
    with pytest.raises(NotImplementedError):
        _provider(subdomain="blog").get_breakdown(base.DIM_STATUS, period)


def test_unknown_breakdown_dimension_raises(period):
    with pytest.raises(NotImplementedError):
        _provider().get_breakdown(base.DIM_METHOD, period)


# -- security ---------------------------------------------------------------
def test_security_aggregates_total_countries_paths_and_sources(period):
    security = _provider().get_security(period)
    assert security["total"] == 42
    assert security["actions"] == []
    assert security["countries"][0].label == "Russia"  # 30 > 12
    assert security["paths"][0].label == "/wp-login.php"  # 25 > 17
    assert security["sources"][0].label == "198.51.100.4"  # 22 > 20


def test_security_total_survives_a_failing_detail_report(period):
    bodies = dict(ALL_REPORTS, **{"attacks/uri": ProviderError("boom", kind="network")})
    security = _provider(bodies).get_security(period)
    assert security["total"] == 42
    assert security["paths"] == []
    assert security["countries"]  # unrelated detail still present


# -- insights ---------------------------------------------------------------
def test_low_cache_use_warns(period):
    low = {
        "data": {
            "statistics": {
                "requests": {"total": 1000, "saved": 100},
                "traffics": {"total": 5_000_000},
            }
        }
    }
    insights = _provider(dict(ALL_REPORTS, traffics=low)).get_insights(period)
    assert any(i.severity == base.INSIGHT_WARN and "cache" in i.title.lower() for i in insights)


def test_high_server_error_share_is_flagged(period):
    insights = _provider().get_insights(period)
    assert any(i.severity == base.INSIGHT_BAD and "5xx" in i.detail for i in insights)


def test_elevated_attack_volume_warns(period):
    busy = {"data": {"statistics": {"Attacks": {"total_attacks": 600}}}}
    bodies = dict(ALL_REPORTS, attacks=busy)
    insights = _provider(bodies).get_insights(period)
    assert any(i.severity == base.INSIGHT_WARN and "attack" in i.title.lower() for i in insights)


def test_insights_skip_whole_domain_rules_when_scoped(period):
    # A scoped provider must not touch status/attacks reports for insights.
    provider = _provider(subdomain="blog")
    provider.get_insights(period)
    assert all("/reports/attacks" not in path for path, _ in provider.calls)
    assert all("/reports/status" not in path for path, _ in provider.calls)
