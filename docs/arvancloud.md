# ArvanCloud provider

An edge-traffic source for sites behind [ArvanCloud](https://www.arvancloud.ir/)
CDN. It reads ArvanCloud's **Reports API** — the authoritative source for the
dashboard's selected historical windows — with the standard library only, and
caches responses through Django's cache framework to respect the API's rate
limits. It is the ArvanCloud counterpart to the Cloudflare provider.

> **Scope of this release.** This is the Phase 1 (Reports API) integration. It
> ships overview, traffic timeseries, country, cache, status, and attack/security
> analytics. Route/path-level metrics, HTTP method breakdowns, and response-time
> percentiles are **not** included: the Reports API has no general traffic report
> grouped by URL or method, and the alternative (Metric Exporters) cannot back
> those cards — see **Why not Metric Exporters** below for the roadmap rationale.
>
> **Why not Metric Exporters (verified live).** A Metric Exporter returns a
> **Top(10) snapshot that resets on every fetch** — it is a rolling leaderboard,
> not a queryable time series, so it cannot answer "top paths over the last 7
> days" or any historical window the dashboard selects. Separately, the
> exporter-*management* endpoint returned **HTTP 403** on the probed key: creating
> or listing exporters is a higher permission than report-read, so the provider
> cannot even self-provision one. Both findings rule Metric Exporters out as a
> history source for now.

## Enabling it

The provider is **opt-in** while it is validated against different plans and
domains — add `"arvancloud"` to your provider list:

```python
DJANGO_PROMETRIC = {
    "PROVIDERS": ["arvancloud"],
}
```

Then expose an API key and your domain to the Django process:

```console
export ARVANCLOUD_API_KEY="..."        # raw key; do not commit it
export ARVANCLOUD_DOMAIN="example.com"
```

The key is sent as `Authorization: Apikey <key>`. If you would rather supply the
scheme yourself, set the variable to `Apikey ...` or `Bearer ...` and it is
preserved as given. Until both variables are set the provider reports itself as
unconfigured and shows setup help on the providers page instead of failing.

## Configuration

All keys are optional and shown with their defaults:

```python
DJANGO_PROMETRIC = {
    "ARVANCLOUD": {
        "API_KEY_ENV": "ARVANCLOUD_API_KEY",   # env var the key is read from
        "DOMAIN_ENV": "ARVANCLOUD_DOMAIN",      # env var the domain is read from
        "BASE_URL": "https://napi.arvancloud.ir/cdn/4.0",
        "TIMEOUT": 10,                          # seconds per request
        "SUBDOMAIN": "",                        # "" = whole domain, "@" = root
        "MAX_REPORT_DAYS": 90,                  # custom ranges are clamped to this
        "METRIC_EXPORTER_IDS": {},              # Phase 2 — inert until implemented
    },
}
```

### Whole domain vs one subdomain

By default the provider counts the **whole registered domain**. Because a domain
can front several applications across its subdomains, an unscoped provider raises
a startup warning (`django_prometric.W003`), mirroring the Cloudflare host
warning. Narrow it to the subdomain this project serves — or `"@"` for the root
domain:

```python
DJANGO_PROMETRIC = {
    "ARVANCLOUD": {
        "SUBDOMAIN": "blog",   # a single label, or "@" for the root domain
    },
}
```

A subdomain scope changes which cards are available. The ArvanCloud **status**
and **attack** reports cannot be filtered by subdomain, so when a scope is set
those capabilities (`STATUS`, `THREATS`, `SECURITY`) are **dropped** rather than
reporting whole-domain numbers under a per-application heading. Only a single
subdomain is supported: unique visitors cannot be safely summed across
subdomains, and several report endpoints do not accept the filter at all.

## What it shows

For a whole-domain configuration:

- **Overview** — requests, cached requests (cache ratio), traffic volume,
  unique visitors, client/server errors, and recorded attacks.
- **Traffic chart** — request timeseries for the selected period.
- **Countries** — requests by country.
- **Cache** — cached vs uncached request split.
- **Status** — 2xx/3xx/4xx/5xx class totals.
- **Security** — total attacks with attacker countries, targeted URIs, and
  attacker IPs.
- **Insights** — cache-use, elevated 4xx/5xx shares, and attack-volume findings.

## Time windows

The dashboard's `24h`, `7d`, and `30d` presets map straight to ArvanCloud report
presets. The `90d` preset and any custom range use `since`/`until` in ISO 8601
**UTC**: boundaries are converted from the project's local timezone to real UTC
before the `Z` suffix, so a window picked in, say, `Asia/Tehran` (+03:30) is not
silently shifted by its offset. Verified live: the traffic, status, country, and
cache reports serve at least a 90-day (tested to 120-day) window at daily
granularity, so no preset the UI can request is truncated. Ranges longer than
`MAX_REPORT_DAYS` (90 by default) are clamped, with a notice on the affected
cards.

> Short, recent windows are returned in **hourly** buckets and hourly data ages
> out after roughly 25 days; longer windows switch to **daily** buckets. This is
> the API's own retention, not a provider limit.

Attack reports are a special case: the ArvanCloud API exposes them **only** by
fixed preset anchored to *now* (a `since`/`until` there returns HTTP 500). A
**recent** custom window (one that reaches up to the present) is approximated to
the smallest preset that covers it, capped at `MAX_REPORT_DAYS`, with a notice.
A **historical** custom window — one that has already ended — cannot be served
at all: approximating it to a preset would return unrelated *recent* attacks, so
the Security card reports the data as unavailable and the overview leaves its
threat count unset rather than showing a misleading number.

## Permissions and limits

Use an API key with **report read** access. Some endpoints and periods are
plan-gated (for example the `5m` period is Enterprise-only); the provider maps a
`422` that looks plan-related to a "plan" error and everything else to a
configuration error, so gated features degrade with a clear message instead of
breaking the dashboard. A failure in one report leaves only its own field empty
and adds a notice, rather than failing unrelated cards.
