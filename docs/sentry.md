---
title: Sentry performance provider
description: Connect Sentry to django-prometric for Django route performance, slow transactions, issues, queries, and backend spans.
---

# Sentry provider

An application-side source that reads [Sentry](https://sentry.io/)'s web API
with the standard library only (urllib) and caches responses through Django's
cache framework. Where the edge providers (Cloudflare, ArvanCloud) see traffic
from the outside, Sentry sees how long the application itself took **per route**
and which errors it raised. It surfaces performance and errors, **not** raw
traffic, so it fills none of the traffic capabilities.

## Enabling it

Sentry is listed by default, so it is active as soon as its credentials are
present. To set the order explicitly:

```python
DJANGO_PROMETRIC = {
    "PROVIDERS": ["cloudflare", "sentry"],
}
```

Create an auth token with the `org:read` and `event:read` scopes, then expose it
and your organization slug to the Django process:

```console
export SENTRY_API_TOKEN="..."               # raw token; do not commit it
export SENTRY_ORG="your-organization-slug"
export SENTRY_PROJECT="your-project-slug"   # optional; see below
```

Until the token and organization are both set the provider reports itself as
unconfigured and shows setup help on the providers page instead of failing.

## Configuration

All keys are optional and shown with their defaults:

```python
DJANGO_PROMETRIC = {
    "SENTRY": {
        "API_TOKEN_ENV": "SENTRY_API_TOKEN",   # env var the token is read from
        "ORG_ENV": "SENTRY_ORG",                # env var the org slug is read from
        "PROJECT_ENV": "SENTRY_PROJECT",        # env var the project slug/id is read from
        "BASE_URL": "https://sentry.io",        # set to your host for self-hosted Sentry
        "TIMEOUT": 10,                          # seconds per request
        "MAX_DAYS": 14,                         # retention window; longer ranges are clamped
    },
}
```

### Pinning a project

`SENTRY_PROJECT` is optional. When it is **unset**, the provider falls back to
the **first project** the organization returns and raises a startup warning
(`django_prometric.W001`) — with several projects that may not be the one you
mean to report on. Set `SENTRY_PROJECT` to the project's slug or numeric id to
pin it.

### Self-hosted Sentry

Point `BASE_URL` at your own Sentry install (for example
`"https://sentry.example.com"`); the API paths and token scopes are the same.

## What it shows

- **Performance** — p50/p75/p95/p99 request duration and the failure rate
  across the busiest transactions.
- **Slowest routes** — the highest-p95 transactions (with a minimum sample
  count so single-hit routes do not dominate).
- **Backend breakdown** — where request time goes, summed by span operation
  (database, cache, templates, external HTTP, middleware…).
- **Slowest queries** — the database spans that consume the most total time.
- **Issues** — the top unresolved issues by frequency, with event and
  user counts and a link back to Sentry.
- **Insights** — repeated-query (N+1) problems, slow queries, database-heavy
  requests, elevated failure rates, and tail-latency findings.

Transactions arrive named after the requested URL (the Django SDK's default
`url` transaction style), with high-cardinality path segments collapsed to `*`.
They are matched back to the dashboard's discovered routes so per-route
performance lines up with the traffic an edge provider reports.

## Time windows and retention

Performance and error data are read from the **spans** dataset over the
dashboard's selected period, converted to UTC. A plan retains this data only so
far back: `MAX_DAYS` (14 by default) caps how far a request reaches, so a longer
selected range is clamped to the retained window rather than returning empty.
Raise or lower it to match your plan's retention.

## Permissions and limits

Use an auth token with `org:read` and `event:read`. A `401`/`403` is surfaced as
an auth error naming the missing scopes, a `429` is reported as a rate-limit
notice to retry shortly, and a failure in one dataset (say, issues) never
silences the other cards or insights.
