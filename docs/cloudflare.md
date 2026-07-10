---
title: Cloudflare analytics provider
description: Connect Cloudflare GraphQL Analytics to django-prometric for route traffic, audience, cache, security, and edge performance metrics.
---

# Cloudflare provider

An edge-traffic source for sites behind [Cloudflare](https://www.cloudflare.com/).
It reads Cloudflare's **GraphQL Analytics API** with the standard library only
(urllib) and caches responses through Django's cache framework to respect the
API's rate limits. It is the broadest built-in provider: overview, traffic,
geography, status, cache, security, bots, SEO, network, audience, and per-route
metrics all come from the one zone.

## Enabling it

Cloudflare is listed by default, so it is active as soon as its credentials are
present. To set the order explicitly:

```python
DJANGO_PROMETRIC = {
    "PROVIDERS": ["cloudflare", "sentry"],
}
```

Create an API token with the **Analytics Read** permission for the zone, then
expose the token and zone ID to the Django process:

```console
export CLOUDFLARE_API_TOKEN="..."      # raw token; do not commit it
export CLOUDFLARE_ZONE_ID="..."        # the zone's ID, from its Overview page
```

Until both variables are set the provider reports itself as unconfigured and
shows setup help on the providers page instead of failing.

## Configuration

All keys are optional and shown with their defaults:

```python
DJANGO_PROMETRIC = {
    "CLOUDFLARE": {
        "API_TOKEN_ENV": "CLOUDFLARE_API_TOKEN",   # env var the token is read from
        "ZONE_ID_ENV": "CLOUDFLARE_ZONE_ID",        # env var the zone ID is read from
        "ACCOUNT_ID_ENV": "CLOUDFLARE_ACCOUNT_ID",  # optional; reserved for account-level reports
        "API_URL": "https://api.cloudflare.com/client/v4/graphql",
        "TIMEOUT": 10,                              # seconds per request
        "HOSTS": [],                                # limit to these hostnames
        "EXCLUDE_HOSTS": [],                        # drop these hostnames
    },
}
```

### One zone, several applications

By default the provider counts the **whole zone**. Because a zone commonly
fronts several hostnames (a marketing site, an API, a dashboard…), an unscoped
provider raises a startup warning (`django_prometric.W002`). Narrow it to the
hostnames this project actually serves — or drop the ones it does not:

```python
DJANGO_PROMETRIC = {
    "CLOUDFLARE": {
        "HOSTS": ["example.com", "www.example.com"],
        # or, to keep everything except a few:
        # "EXCLUDE_HOSTS": ["api.example.com"],
    },
}
```

Either setting accepts a single hostname or a list. Host filtering routes the
overview and country/status cards through the adaptive dataset (see below) so
the numbers reflect only the named hosts rather than the whole zone.

## What it shows

- **Overview** — requests, cached requests (cache ratio), bandwidth, unique
  visitors, page views, threats, and client/server errors.
- **Traffic chart** — request timeseries (daily buckets for wide zone-wide
  windows, hourly for short or host-filtered ones).
- **Countries / Status / Cache / Methods** — request breakdowns by client
  country, edge response status, cache status, and HTTP method.
- **Per-route metrics** — for each discovered URL: requests, bandwidth,
  statuses, methods, cache split, a request timeseries, last-seen time, and
  TTFB percentiles.
- **Performance** — edge TTFB p50/p95/p99 (plan-gated; see below).
- **Security** — firewall mitigations: how much was blocked or challenged, by
  which rule source, from which countries, and against which paths.
- **Bots** — verified-bot share of traffic split by crawler category, with the
  remainder counted as humans.
- **SEO** — which search-engine crawlers visit and which pages they fetch.
- **Network** — HTTP and TLS version distribution.
- **Audience** — real users' browsers, operating systems, and device types
  (verified bots filtered out).
- **Insights** — cache use, elevated 4xx/5xx shares, firewall activity, AI
  crawler volume, and legacy-HTTP findings.

## Two datasets, and why the plan matters

Cloudflare exposes analytics through two datasets, and the provider chooses
between them per query:

- **Daily (`httpRequests1dGroups`)** — zone-wide daily rollups. Available on
  **every plan**, so unscoped overview, country, and status cards work even on a
  free zone.
- **Adaptive (`httpRequestsAdaptiveGroups` / `firewallEventsAdaptive`)** —
  request-level data that can be filtered by path and host. This backs
  per-route metrics, host-scoped numbers, bots, SEO, network, audience, and
  security.

On **free zones** the adaptive dataset is limited to a **24-hour window** and
does not expose TTFB quantiles. Both limits are detected at runtime the first
time Cloudflare rejects the query, remembered for a day, and degraded
gracefully: a wider path-level request is clamped to the last 24 hours with a
notice, and the performance card is dropped rather than erroring. The grouped
firewall dataset is closed below the Business plan, so security metrics are
aggregated from the newest raw firewall events instead.

## Permissions and limits

Use a token scoped to the zone with **Analytics Read**; the zone name shown in
the header additionally needs zone read. A `401`/`403` is surfaced as an auth
error with a clear message, quota/plan limits degrade the affected card as
described above, and a failure in one dataset never silences the other cards or
insights.
