"""PostgreSQL provider: database health and query performance.

Reads from Django's own database connection — no external API, no token. It is
the database counterpart to the Sentry provider: both are application-side,
capability-partial sources. Postgres has no HTTP request data, so it fills
none of the traffic capabilities; its home is queries, tables, indexes,
connections and the insights derived from them.

Two kinds of number come out of Postgres and are treated differently:

* **Point-in-time state** — size, row/table/index counts, connections — is
  correct at read time and shown plainly.
* **Cumulative counters** — commits, cache-hit ratio, deadlocks, query call
  counts — accumulate since the server's last statistics reset. They ignore
  the selected period and are wrapped in :class:`~.base.Cumulative` so the UI
  marks them with a "since …" badge. There are two independent reset clocks:
  ``pg_stat_database.stats_reset`` for general stats and
  ``pg_stat_statements_info.stats_reset`` for query stats (PG14+).

Everything except query-level stats works on a stock Postgres. Query stats
need the ``pg_stat_statements`` extension; those queries degrade gracefully
with an install notice when it is absent.
"""

from __future__ import annotations

import datetime as dt
import importlib

from django.db import ProgrammingError
from django.db.utils import OperationalError
from django.utils import timezone
from django.utils.translation import gettext as _

from ..conf import get_config
from . import base
from .base import (
    AnalyticsProvider,
    Cumulative,
    DatabaseStats,
    IndexStat,
    Insight,
    Period,
    QueryStat,
    TableStat,
)

# A sentinel so memoised "fetch once" lookups can cache a ``None`` result.
_UNSET = object()


def _db_error_types() -> tuple:
    """The "query/extension unavailable" errors to catch for graceful
    degradation. In alias mode the driver's exceptions are wrapped in Django's;
    in manual mode they arrive raw from psycopg — so we catch both."""
    errors = [ProgrammingError, OperationalError]
    for name in ("psycopg", "psycopg2"):
        try:
            driver = importlib.import_module(name)
        except ImportError:
            continue
        errors.extend((driver.ProgrammingError, driver.OperationalError))
    return tuple(errors)


_DB_ERRORS = _db_error_types()


class PostgresProvider(AnalyticsProvider):
    slug = "postgres"
    verbose_name = "PostgreSQL"
    kind = _("Database health")
    # No per-route database data in Phase 1, so no route_template.

    def __init__(self):
        super().__init__()
        cfg = get_config()["POSTGRES"]
        self.db_alias = cfg["DB_ALIAS"]
        self.max_tables = cfg["MAX_TABLES"]
        self.max_indexes = cfg["MAX_INDEXES"]
        self.max_queries = cfg["MAX_QUERIES"]
        # Manual connection params (psycopg kwargs) when DSN/NAME is configured,
        # else None → inspect the Django connection named by DB_ALIAS.
        self._manual = self._manual_params(cfg)
        self._conn = None  # lazily-opened direct connection (manual mode only)
        self._configured_cache = _UNSET
        self._pg_version_cache = _UNSET
        self._db_reset_cache = _UNSET
        self._stmt_reset_cache = _UNSET
        self._db_stats_cache = _UNSET

    @staticmethod
    def _manual_params(cfg) -> dict | None:
        """Translate the POSTGRES config into psycopg.connect() kwargs, or
        None when no manual connection is requested (DB_ALIAS is used then)."""
        dsn = (cfg.get("DSN") or "").strip()
        if dsn:
            return {"conninfo": dsn, **(cfg.get("OPTIONS") or {})}
        name = (cfg.get("NAME") or "").strip()
        if not name:
            return None
        params = {"dbname": name}
        for key, pg_key in (
            ("USER", "user"),
            ("PASSWORD", "password"),
            ("HOST", "host"),
            ("PORT", "port"),
        ):
            value = cfg.get(key)
            if value not in (None, ""):
                params[pg_key] = str(value)
        params.update(cfg.get("OPTIONS") or {})
        return params

    def _source(self) -> str:
        """Human label for where the data comes from — alias or manual host."""
        if not self._manual:
            return self.db_alias
        if "conninfo" in self._manual:
            return _("manual DSN")
        host = self._manual.get("host", "localhost")
        return f"{self._manual['dbname']} @ {host}"

    # -- configuration -----------------------------------------------------
    @property
    def is_configured(self) -> bool:
        # Manual mode: trust the config, but only report ready if we can
        # actually reach the server (a bad password should show setup help,
        # not a broken card). Cached so we probe at most once per instance.
        if self._manual:
            if self._configured_cache is _UNSET:
                try:
                    self._rows("SELECT 1")
                    self._configured_cache = True
                except Exception:  # noqa: BLE001 — unreachable/misconfigured
                    self._configured_cache = False
            return self._configured_cache

        from django.db import connections

        try:
            return connections[self.db_alias].vendor == "postgresql"
        except Exception:  # noqa: BLE001 — an unknown alias just means "not us"
            return False

    def configuration_help(self) -> str:
        return _(
            "This source reads PostgreSQL's own statistics views. Point it at a "
            "PostgreSQL database either by setting POSTGRES.DB_ALIAS to a "
            "PostgreSQL connection in DATABASES, or — when that database is not "
            "PostgreSQL — by giving POSTGRES.NAME/USER/PASSWORD/HOST/PORT (or "
            "POSTGRES.DSN) so the provider connects directly. Query-level stats "
            "additionally need the pg_stat_statements extension."
        )

    def description(self) -> str:
        try:
            (name,) = self._rows("SELECT current_database()")[0]
        except Exception:  # noqa: BLE001 — description is best-effort
            return self._source()
        return f"{name} @ {self._source()}"

    def capabilities(self) -> set:
        return {
            base.DATABASE,
            base.TABLES,
            base.INDEXES,
            base.QUERIES,
            base.INSIGHTS,
        }

    # -- query plumbing ----------------------------------------------------
    def _connection(self):
        """Open (once) the direct connection used in manual mode.

        psycopg (v3) is preferred; psycopg2 is accepted as a fallback. Both
        speak the ``%s`` paramstyle the queries below use.
        """
        if self._conn is not None:
            return self._conn

        conninfo = self._manual.get("conninfo")
        kwargs = {k: v for k, v in self._manual.items() if k != "conninfo"}
        try:
            import psycopg  # psycopg 3

            self._conn = (
                psycopg.connect(conninfo, autocommit=True, **kwargs)
                if conninfo
                else psycopg.connect(autocommit=True, **kwargs)
            )
            return self._conn
        except ImportError:
            pass

        try:
            import psycopg2  # older driver
        except ImportError as exc:  # neither driver present
            raise base.ProviderError(
                _(
                    "A manual POSTGRES connection needs a database driver. "
                    'Install one with: pip install "django-prometric[postgres]" '
                    "(psycopg2 is also accepted if you already use it)."
                )
            ) from exc

        conn = psycopg2.connect(conninfo) if conninfo else psycopg2.connect(**kwargs)
        conn.autocommit = True
        self._conn = conn
        return self._conn

    def _rows(self, sql: str, params=()) -> list:
        """Run a parameterised read on the configured connection."""
        if self._manual:
            with self._connection().cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()

        from django.db import connections

        with connections[self.db_alias].cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def _pg_version(self) -> int:
        if self._manual:
            if self._pg_version_cache is _UNSET:
                try:
                    (value,) = self._rows("SHOW server_version_num")[0]
                    self._pg_version_cache = int(value)
                except Exception:  # noqa: BLE001 — fall back to "modern enough"
                    self._pg_version_cache = 130000
            return self._pg_version_cache

        from django.db import connections

        return connections[self.db_alias].pg_version  # e.g. 140004 for 14.4

    def _db_reset(self) -> dt.datetime | None:
        """When general statistics (pg_stat_database) last reset."""
        if self._db_reset_cache is _UNSET:
            try:
                (value,) = self._rows(
                    "SELECT stats_reset FROM pg_stat_database WHERE datname = current_database()"
                )[0]
            except Exception:  # noqa: BLE001
                value = None
            self._db_reset_cache = value
        return self._db_reset_cache

    def _stmt_reset(self) -> dt.datetime | None:
        """When query statistics last reset — pg_stat_statements_info is
        PG14+ and only present when the extension is installed; None if not."""
        if self._stmt_reset_cache is _UNSET:
            try:
                (value,) = self._rows("SELECT stats_reset FROM pg_stat_statements_info")[0]
            except (*_DB_ERRORS, IndexError):
                value = None
            self._stmt_reset_cache = value
        return self._stmt_reset_cache

    # -- point-in-time state (period is ignored, state is always current) --
    def get_database_stats(self, period: Period) -> DatabaseStats | None:
        # Point-in-time, so it does not depend on ``period``. Memoise it for the
        # life of this (per-request) instance: the card and two insight rules
        # would otherwise each re-run the same batch of small queries.
        if self._db_stats_cache is not _UNSET:
            return self._db_stats_cache

        stats = DatabaseStats()
        since = self._db_reset()

        (stats.size_bytes,) = self._rows("SELECT pg_database_size(current_database())")[0]

        commits, rollbacks, blks_hit, blks_read, deadlocks, temp_bytes = self._rows(
            "SELECT xact_commit, xact_rollback, blks_hit, blks_read, deadlocks, "
            "temp_bytes FROM pg_stat_database WHERE datname = current_database()"
        )[0]
        reads = (blks_hit or 0) + (blks_read or 0)
        hit_ratio = (blks_hit / reads) if reads else None
        stats.commits = Cumulative(commits, since)
        stats.rollbacks = Cumulative(rollbacks, since)
        stats.cache_hit_ratio = Cumulative(hit_ratio, since)
        stats.deadlocks = Cumulative(deadlocks, since)
        stats.temp_bytes = Cumulative(temp_bytes, since)

        stats.table_count, stats.row_estimate, stats.dead_estimate = self._rows(
            "SELECT count(*), coalesce(sum(n_live_tup), 0), "
            "coalesce(sum(n_dead_tup), 0) FROM pg_stat_user_tables"
        )[0]
        (stats.index_count,) = self._rows("SELECT count(*) FROM pg_stat_user_indexes")[0]

        stats.connections = {
            (state or "unknown"): count
            for state, count in self._rows(
                "SELECT state, count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() GROUP BY state"
            )
        }
        try:
            (stats.max_connections,) = self._rows(
                "SELECT setting::int FROM pg_settings WHERE name = 'max_connections'"
            )[0]
        except (IndexError, *_DB_ERRORS):
            stats.max_connections = None

        self.add_notice(
            _(
                "Commit, cache-hit, deadlock and temp-file figures are lifetime "
                "counters accumulated since the last statistics reset, not the "
                "selected period. They are marked with a “since” badge."
            ),
            level=base.NOTICE_INFO,
        )
        self._db_stats_cache = stats
        return stats

    def get_table_stats(self, period: Period, limit: int = 10) -> list[TableStat]:
        rows = self._rows(
            "SELECT relname, pg_total_relation_size(relid), n_live_tup, n_dead_tup, "
            "seq_scan, idx_scan, last_autovacuum "
            "FROM pg_stat_user_tables "
            "ORDER BY pg_total_relation_size(relid) DESC LIMIT %s",
            (limit or self.max_tables,),
        )
        return [
            TableStat(
                name=name,
                size_bytes=size or 0,
                rows=live or 0,
                dead_rows=dead or 0,
                seq_scans=seq or 0,
                idx_scans=idx or 0,
                last_autovacuum=autovacuum,
            )
            for (name, size, live, dead, seq, idx, autovacuum) in rows
        ]

    def get_index_stats(self, period: Period, limit: int = 10) -> dict:
        limit = limit or self.max_indexes
        # Unused = never scanned, excluding indexes backing a primary key or a
        # unique constraint (dropping those changes semantics, not just size).
        unused = [
            IndexStat(name=index, table=table, size_bytes=size or 0, scans=scans or 0)
            for (table, index, size, scans) in self._rows(
                "SELECT s.relname, s.indexrelname, pg_relation_size(s.indexrelid), "
                "s.idx_scan FROM pg_stat_user_indexes s "
                "JOIN pg_index i ON i.indexrelid = s.indexrelid "
                "WHERE s.idx_scan = 0 AND NOT i.indisunique AND NOT i.indisprimary "
                "ORDER BY pg_relation_size(s.indexrelid) DESC LIMIT %s",
                (limit,),
            )
        ]
        used = [
            IndexStat(name=index, table=table, size_bytes=size or 0, scans=scans or 0)
            for (table, index, size, scans) in self._rows(
                "SELECT s.relname, s.indexrelname, pg_relation_size(s.indexrelid), "
                "s.idx_scan FROM pg_stat_user_indexes s WHERE s.idx_scan > 0 "
                "ORDER BY s.idx_scan DESC LIMIT %s",
                (limit,),
            )
        ]
        return {"unused": unused, "used": used}

    # -- query-level stats (needs the pg_stat_statements extension) --------
    def get_slowest_queries(self, period: Period, limit: int = 8) -> list[QueryStat]:
        limit = limit or self.max_queries
        # total_exec_time / mean_exec_time are PG13+; older servers spell them
        # total_time / mean_time. Pick by server version.
        if self._pg_version() >= 130000:
            cols = "total_exec_time, mean_exec_time"
        else:
            cols = "total_time, mean_time"
        sql = (
            f"SELECT query, calls, {cols} FROM pg_stat_statements "
            "WHERE dbid = (SELECT oid FROM pg_database "
            "WHERE datname = current_database()) "
            "ORDER BY 3 DESC LIMIT %s"
        )
        try:
            rows = self._rows(sql, (limit,))
        except _DB_ERRORS:
            self.add_notice(
                _(
                    "Query statistics need the pg_stat_statements extension. Add "
                    "it to shared_preload_libraries, restart PostgreSQL, then run "
                    "CREATE EXTENSION pg_stat_statements."
                ),
                level=base.NOTICE_WARN,
            )
            return []
        return [
            QueryStat(query=query, calls=calls or 0, avg_ms=mean, total_ms=total)
            for (query, calls, total, mean) in rows
        ]

    # -- insights ----------------------------------------------------------
    _CACHE_HIT_WARN = 0.95
    _DEAD_RATIO_WARN = 0.2
    _SLOW_QUERY_MS = 500
    _UNUSED_INDEX_BYTES = 1024 * 1024  # 1 MiB — ignore trivially small indexes
    _CONNECTION_WARN = 0.85  # of max_connections
    _STALE_VACUUM = dt.timedelta(days=7)

    def get_insights(self, period: Period) -> list[Insight]:
        insights: list[Insight] = []
        rules = (
            self._cache_insights,
            self._unused_index_insights,
            self._bloat_insights,
            self._slow_query_insights,
            self._connection_insights,
        )
        for rule in rules:
            try:
                insights.extend(rule(period))
            except Exception:  # noqa: BLE001 — one broken rule never hides the rest
                continue
        if not any(i.severity != base.INSIGHT_GOOD for i in insights):
            insights.append(
                Insight(
                    severity=base.INSIGHT_GOOD,
                    title=_("Database health looks good"),
                    detail=_(
                        "Cache hit ratio, table bloat, index use and connection "
                        "headroom are all within healthy ranges."
                    ),
                )
            )
        return insights

    def _cache_insights(self, period: Period) -> list[Insight]:
        stats = self.get_database_stats(period)
        ratio = stats.cache_hit_ratio.value if stats and stats.cache_hit_ratio else None
        if ratio is None or ratio >= self._CACHE_HIT_WARN:
            return []
        return [
            Insight(
                severity=base.INSIGHT_WARN,
                title=_("Cache hit ratio is low"),
                detail=_(
                    "Only %(pct).1f%% of block reads were served from the cache "
                    "(since the last statistics reset)."
                )
                % {"pct": ratio * 100},
                action=_(
                    "The working set may not fit in shared_buffers. Consider "
                    "raising shared_buffers or reducing the data scanned per query."
                ),
            )
        ]

    def _unused_index_insights(self, period: Period) -> list[Insight]:
        unused = [
            index
            for index in self.get_index_stats(period, limit=self.max_indexes)["unused"]
            if index.size_bytes >= self._UNUSED_INDEX_BYTES
        ]
        if not unused:
            return []
        reclaimable = sum(index.size_bytes for index in unused)
        worst = unused[0]
        from django.template.defaultfilters import filesizeformat

        return [
            Insight(
                severity=base.INSIGHT_WARN,
                title=_("Unused indexes are wasting space"),
                detail=_(
                    "%(count)s index(es) were never scanned since the last stats "
                    "reset, holding %(size)s. Largest: %(name)s on %(table)s."
                )
                % {
                    "count": len(unused),
                    "size": filesizeformat(reclaimable),
                    "name": worst.name,
                    "table": worst.table,
                },
                action=_(
                    "Confirm they are unused over a full workload cycle, then "
                    "consider dropping them to reclaim space and speed up writes."
                ),
            )
        ]

    def _bloat_insights(self, period: Period) -> list[Insight]:
        now = timezone.now()
        bloated = []
        for table in self.get_table_stats(period, limit=self.max_tables):
            ratio = table.dead_ratio
            if ratio is None or ratio < self._DEAD_RATIO_WARN:
                continue
            stale = (
                table.last_autovacuum is None or now - table.last_autovacuum > self._STALE_VACUUM
            )
            if stale:
                bloated.append((table, ratio))
        if not bloated:
            return []
        table, ratio = max(bloated, key=lambda pair: pair[1])
        return [
            Insight(
                severity=base.INSIGHT_WARN,
                title=_("Some tables are bloated with dead rows"),
                detail=_(
                    "%(name)s is %(pct).0f%% dead tuples and has not been autovacuumed recently."
                )
                % {"name": table.name, "pct": ratio * 100},
                action=_(
                    "Run VACUUM (ANALYZE) on it, and tune autovacuum thresholds if it bloats again."
                ),
            )
        ]

    def _slow_query_insights(self, period: Period) -> list[Insight]:
        slow = [
            query
            for query in self.get_slowest_queries(period, limit=3)
            if (query.avg_ms or 0) >= self._SLOW_QUERY_MS
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
                    "Add an index for its filter columns or rework the query — "
                    "the full list is in the slowest-queries card."
                ),
            )
        ]

    def _connection_insights(self, period: Period) -> list[Insight]:
        stats = self.get_database_stats(period)
        ratio = stats.connection_ratio if stats else None
        if ratio is None or ratio < self._CONNECTION_WARN:
            return []
        used = sum(stats.connections.values())
        return [
            Insight(
                severity=base.INSIGHT_BAD,
                title=_("Connection slots are nearly exhausted"),
                detail=_("%(used)s of %(max)s connections are in use (%(pct).0f%%).")
                % {"used": used, "max": stats.max_connections, "pct": ratio * 100},
                action=_(
                    "Add a connection pooler such as PgBouncer, or lower the "
                    "application's pool size, before new connections are refused."
                ),
            )
        ]

    def invalidate_cache(self) -> None:
        # No external cache in Phase 1; drop the memoised reads so a refresh
        # re-runs them, and close any direct connection we opened.
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001 — closing is best-effort
                pass
            self._conn = None
        self._configured_cache = _UNSET
        self._pg_version_cache = _UNSET
        self._db_reset_cache = _UNSET
        self._stmt_reset_cache = _UNSET
        self._db_stats_cache = _UNSET
