# PostgreSQL provider

A database-health source that reads PostgreSQL's own statistics views through
Django's database connection — no external API and no token. It is the database
counterpart to the Sentry provider: application-side and capability-partial. It
surfaces query performance and database health, **not** HTTP traffic, so it
fills none of the traffic capabilities.

## Enabling it

Add `"postgres"` to your provider list — it is opt-in, since it is only
meaningful when your Django database is PostgreSQL:

```python
DJANGO_PROMETRIC = {
    "PROVIDERS": ["cloudflare", "sentry", "postgres"],
}
```

When the inspected connection is not PostgreSQL — and no manual connection is
configured (see [Configuration](#configuration)) — the provider reports itself
as unconfigured and shows setup help on the providers page instead of failing.

## Configuration

All keys are optional and shown with their defaults:

```python
DJANGO_PROMETRIC = {
    "POSTGRES": {
        "DB_ALIAS": "default",   # which DATABASES connection to inspect
        # Manual connection (see below) — leave empty to use DB_ALIAS:
        "DSN": "",               # or a full DSN, which wins over the fields
        "NAME": "",
        "USER": "",
        "PASSWORD": "",
        "HOST": "",
        "PORT": "",
        "OPTIONS": {},           # extra kwargs for psycopg.connect()
        "MAX_TABLES": 10,        # rows in the Tables card
        "MAX_INDEXES": 10,       # rows in each Indexes section
        "MAX_QUERIES": 8,        # rows in the Slowest queries card
        # Phase 2 (counter windowing) — inert until implemented:
        "SAMPLE_ENABLED": False,
        "SAMPLE_RETENTION_DAYS": 90,
    },
}
```

### Choosing what it connects to

There are two ways to point the provider at a PostgreSQL database:

1. **A Django connection (default).** `DB_ALIAS` names an entry in `DATABASES`.
   This is the simplest path when your app already runs on PostgreSQL — it
   reuses Django's connection and driver, and auto-detection just works.

2. **A direct connection.** When the `DB_ALIAS` database is *not* PostgreSQL
   (e.g. your app runs on sqlite, or you want to inspect a different server
   without adding it to `DATABASES`), set `NAME`/`USER`/`PASSWORD`/`HOST`/`PORT`
   — or a single `DSN` — and the provider opens its own **read-only** connection
   with [psycopg](https://www.psycopg.org/) instead. A driver must be
   installed: `pip install "django-prometric[postgres]"` brings `psycopg`
   (recommended); an existing `psycopg2` also works. `OPTIONS` is passed
   straight through to `psycopg.connect()`.

Keep secrets out of settings by reading them from the environment:

```python
import os

DJANGO_PROMETRIC = {
    "PROVIDERS": ["postgres"],
    "POSTGRES": {
        "NAME": os.environ.get("PROMETRIC_PG_NAME", ""),
        "USER": os.environ.get("PROMETRIC_PG_USER", ""),
        "PASSWORD": os.environ.get("PROMETRIC_PG_PASSWORD", ""),
        "HOST": os.environ.get("PROMETRIC_PG_HOST", ""),
        "PORT": os.environ.get("PROMETRIC_PG_PORT", ""),
    },
}
```

In manual mode the provider verifies it can actually reach the server; if the
credentials are wrong or the host is unreachable it reports itself as
unconfigured and shows setup help rather than a broken card.

## What it shows

Three cards (`DatabaseCard`, `TablesTable`, `IndexesTable`) plus insights and
the slowest-queries card:

- **Database health** — size, table/index counts, live-row estimate,
  connections vs `max_connections`, and the lifetime counters (commits,
  cache-hit ratio, deadlocks, temp files).
- **Tables** — the largest tables with their size, rows, dead-tuple ratio and
  last autovacuum; bloated tables are highlighted.
- **Indexes** — unused indexes (with total reclaimable space) and the most-used
  ones.
- **Insights** — low cache-hit ratio, unused indexes, bloated tables, slow
  queries and connection pressure.

## Point-in-time vs cumulative numbers

Postgres exposes two kinds of number, and the UI treats them differently:

- **Point-in-time state** (size, counts, connections) is correct at read time
  and shown plainly.
- **Cumulative counters** (commits, cache-hit ratio, deadlocks, query call
  counts) accumulate since the server's last statistics reset. They ignore the
  selected dashboard period and are marked with a **“since …” badge** so the
  number is not mistaken for a per-period figure. There are two independent
  reset clocks: `pg_stat_database.stats_reset` for general stats and
  `pg_stat_statements_info.stats_reset` (PG14+) for query stats.

## Query statistics need an extension

The slowest-queries card and the slow-query insight read
[`pg_stat_statements`](https://www.postgresql.org/docs/current/pgstatstatements.html).
Everything else works on a stock server. If the extension is absent, those
queries degrade gracefully and leave a notice with the install steps:

```
shared_preload_libraries = 'pg_stat_statements'   # in postgresql.conf, then restart
CREATE EXTENSION pg_stat_statements;              # once, in the database
```

## Permissions

A locked-down role sees only its own rows in the statistics views. For
project-wide visibility, connect as an owner or grant the role
[`pg_read_all_stats`](https://www.postgresql.org/docs/current/predefined-roles.html)
(PG10+).

## Roadmap: period-scoped counters (Phase 2)

The `Cumulative` marking is deliberately reusable. A planned base-layer
`CounterSampler` (periodic sampling + diff, like Prometheus `rate()`) will let
any provider turn lifetime counters into true per-period values. When it lands
for Postgres, the badged counters become plain period-scoped numbers with no
template change. Enable it with `SAMPLE_ENABLED` and a scheduled
`prometric_sample` command.
