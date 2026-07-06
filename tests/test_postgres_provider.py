"""Tests for the PostgreSQL provider — no live database required.

The provider's only DB touch-point is ``_rows``; each test stubs it (and the
server version) so the SQL-shaping, counter-wrapping, degradation and insight
logic can be exercised against canned rows.
"""

import datetime as dt

import pytest
from django.db import ProgrammingError
from django.template.loader import render_to_string
from django.utils import timezone

from django_prometric.providers import base
from django_prometric.providers.base import Cumulative, Period
from django_prometric.providers.postgres import PostgresProvider


@pytest.fixture
def period():
    return Period.from_key("24h")


def _provider(rows_for):
    """A provider whose ``_rows`` dispatches on a substring of the SQL.

    ``rows_for`` maps a fragment that appears in the query to the rows it
    should return, or to an exception instance/class to raise.
    """
    provider = PostgresProvider()
    provider._pg_version = lambda: 140000

    def fake_rows(sql, params=()):
        for fragment, result in rows_for.items():
            if fragment in sql:
                if isinstance(result, type) and issubclass(result, Exception):
                    raise result("boom")
                if isinstance(result, Exception):
                    raise result
                return result
        raise AssertionError(f"unexpected query: {sql!r}")

    provider._rows = fake_rows
    return provider


# -- configuration ---------------------------------------------------------
def test_is_configured_false_on_non_postgres(monkeypatch):
    class FakeConn:
        vendor = "sqlite"

    monkeypatch.setattr("django.db.connections", {"default": FakeConn()})
    assert PostgresProvider().is_configured is False


def test_is_configured_true_on_postgres(monkeypatch):
    class FakeConn:
        vendor = "postgresql"

    monkeypatch.setattr("django.db.connections", {"default": FakeConn()})
    assert PostgresProvider().is_configured is True


# -- manual connection params ----------------------------------------------
def test_manual_params_none_without_name_or_dsn():
    assert PostgresProvider._manual_params({"DSN": "", "NAME": ""}) is None


def test_manual_params_from_fields():
    params = PostgresProvider._manual_params(
        {"NAME": "shop", "USER": "app", "PASSWORD": "s3cr3t", "HOST": "db", "PORT": 5433}
    )
    assert params == {
        "dbname": "shop",
        "user": "app",
        "password": "s3cr3t",
        "host": "db",
        "port": "5433",
    }


def test_manual_params_dsn_wins():
    params = PostgresProvider._manual_params({"DSN": "postgresql://u:p@h/db", "NAME": "ignored"})
    assert params == {"conninfo": "postgresql://u:p@h/db"}


def test_manual_mode_is_configured_probes_connection(monkeypatch):
    provider = PostgresProvider()
    provider._manual = {"dbname": "x"}  # force manual mode
    # A reachable server answers SELECT 1 → configured.
    provider._rows = lambda *a, **k: [(1,)]
    assert provider.is_configured is True
    # An unreachable one raises → not configured (shows setup help, no crash).
    bad = PostgresProvider()
    bad._manual = {"dbname": "x"}

    def boom(*_a, **_k):
        raise OSError("connection refused")

    bad._rows = boom
    assert bad.is_configured is False


# -- point-in-time ---------------------------------------------------------
def test_database_stats_wraps_counters_and_ignores_period(period):
    reset = timezone.now() - dt.timedelta(days=3)
    provider = _provider(
        {
            "SELECT stats_reset FROM pg_stat_database": [(reset,)],
            "pg_database_size": [(1_000_000,)],
            "xact_commit": [(500, 5, 95, 5, 0, 0)],  # 95/(95+5) = 0.95 cache hit
            "n_live_tup": [(12, 3400, 40)],
            "pg_stat_user_indexes": [(20,)],
            "pg_stat_activity": [("active", 2), ("idle", 3)],
            "max_connections": [(100,)],
        }
    )

    stats = provider.get_database_stats(period)

    assert stats.size_bytes == 1_000_000
    assert stats.table_count == 12
    assert stats.row_estimate == 3400
    assert stats.used_connections == 5
    assert stats.connection_ratio == 0.05
    # Counters are wrapped and carry the reset clock as their "since".
    assert isinstance(stats.commits, Cumulative)
    assert stats.commits.value == 500
    assert stats.commits.since == reset
    assert stats.cache_hit_ratio.value == pytest.approx(0.95)
    # An honesty notice is left about the lifetime counters.
    assert any("lifetime counters" in str(n) for n in provider.notices)


def test_table_stats_maps_rows_and_dead_ratio(period):
    provider = _provider(
        {
            "pg_stat_user_tables": [
                ("orders", 2048, 80, 20, 4, 100, None),
            ],
        }
    )
    tables = provider.get_table_stats(period, limit=5)
    assert tables[0].name == "orders"
    assert tables[0].size_bytes == 2048
    assert tables[0].dead_ratio == pytest.approx(0.2)


def test_index_stats_splits_unused_and_used(period):
    provider = _provider(
        {
            "s.idx_scan = 0": [("orders", "orders_stale_idx", 4096, 0)],
            "s.idx_scan > 0": [("orders", "orders_pkey", 8192, 999)],
        }
    )
    result = provider.get_index_stats(period, limit=5)
    assert result["unused"][0].name == "orders_stale_idx"
    assert result["unused"][0].scans == 0
    assert result["used"][0].scans == 999


# -- graceful degradation --------------------------------------------------
def test_slowest_queries_degrades_without_extension(period):
    provider = _provider({"pg_stat_statements": ProgrammingError})
    assert provider.get_slowest_queries(period) == []
    assert any("pg_stat_statements extension" in str(n) for n in provider.notices)


def test_slowest_queries_degrades_on_raw_driver_error(period):
    # In manual (direct-psycopg) mode the missing-extension error arrives as a
    # raw psycopg exception, not Django's — degradation must still catch it.
    psycopg = pytest.importorskip("psycopg", reason="needs the [postgres] extra")

    provider = _provider({"pg_stat_statements": psycopg.errors.UndefinedTable})
    assert provider.get_slowest_queries(period) == []
    assert any("pg_stat_statements extension" in str(n) for n in provider.notices)


def test_slowest_queries_maps_rows(period):
    provider = _provider(
        {
            "pg_stat_statements": [("SELECT 1", 10, 500.0, 50.0)],
        }
    )
    queries = provider.get_slowest_queries(period)
    assert queries[0].calls == 10
    assert queries[0].total_ms == 500.0
    assert queries[0].avg_ms == 50.0


# -- insights --------------------------------------------------------------
def test_insight_rules_survive_a_failing_source(period, monkeypatch):
    provider = PostgresProvider()
    provider._pg_version = lambda: 140000

    # Cache rule raises; the others still run and a GOOD summary is appended.
    def boom(*_a, **_k):
        raise ProgrammingError("stats view blew up")

    monkeypatch.setattr(provider, "get_database_stats", boom)
    monkeypatch.setattr(provider, "get_index_stats", lambda *a, **k: {"unused": [], "used": []})
    monkeypatch.setattr(provider, "get_table_stats", lambda *a, **k: [])
    monkeypatch.setattr(provider, "get_slowest_queries", lambda *a, **k: [])

    insights = provider.get_insights(period)
    assert insights  # did not blow up
    assert insights[-1].severity == base.INSIGHT_GOOD


def test_low_cache_hit_produces_warning(period, monkeypatch):
    provider = PostgresProvider()
    stats = base.DatabaseStats(cache_hit_ratio=Cumulative(0.80, None))
    monkeypatch.setattr(provider, "get_database_stats", lambda *_a, **_k: stats)
    warnings = provider._cache_insights(period)
    assert warnings and warnings[0].severity == base.INSIGHT_WARN


# -- the reusable Cumulative badge partial ---------------------------------
def test_cumulative_badge_renders_only_with_since():
    when = dt.datetime(2026, 6, 12, tzinfo=dt.timezone.utc)
    with_badge = render_to_string("django_prometric/components/_cumulative.html", {"since": when})
    assert "pm-cumulative-badge" in with_badge
    assert "since" in with_badge

    without = render_to_string("django_prometric/components/_cumulative.html", {"since": None})
    assert "pm-cumulative-badge" not in without
