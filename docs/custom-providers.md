---
title: Build a custom analytics provider
description: Integrate a CDN, APM, warehouse, or internal metrics service with the django-prometric provider API.
---

# Custom providers

Implement an `AnalyticsProvider` when metrics live in another CDN, APM,
warehouse, or internal service. The dashboard talks only to this contract and
does not need to know the provider's raw response format.

## Minimal provider

Declare an identity, advertise the capabilities you can answer, and implement
their matching methods:

```python title="myproject/analytics.py"
from django_prometric.providers import base
from django_prometric.providers.base import AnalyticsProvider, OverviewStats


class InternalMetricsProvider(AnalyticsProvider):
    slug = "internal"
    verbose_name = "Internal metrics"
    kind = "Application traffic"

    def capabilities(self):
        return {base.OVERVIEW}

    def description(self):
        return "Traffic from the internal metrics service"

    def get_overview(self, period):
        payload = fetch_metrics(start=period.start, end=period.end)
        return OverviewStats(
            requests=payload["requests"],
            errors=payload.get("errors"),
            avg_response_ms=payload.get("average_ms"),
        )
```

Register the dotted class path:

```python title="settings.py"
DJANGO_PROMETRIC = {
    "PROVIDERS": [
        "myproject.analytics.InternalMetricsProvider",
        "sentry",
    ],
}
```

## Configuration state

Keep missing credentials user-presentable instead of raising during startup:

```python
class InternalMetricsProvider(AnalyticsProvider):
    # ...

    @property
    def is_configured(self):
        return bool(os.environ.get("INTERNAL_METRICS_TOKEN"))

    def configuration_help(self):
        return "Set INTERNAL_METRICS_TOKEN to a read-only service token."
```

An unconfigured provider appears in onboarding but is skipped when components
select a data source.

## Failures and notices

Raise `ProviderError` for failures the dashboard user can act on:

```python
from django_prometric.providers.base import ProviderError

raise ProviderError("The service rejected the read token.", kind="auth")
```

Supported kinds are `config`, `auth`, `network`, `quota`, `plan`, and `error`.
Use `add_notice()` when data remains useful but needs context—for example when
the requested period was shortened by an upstream retention limit.

Set `max_period_days` to let the base class detect and clamp unsupported windows:

```python
class InternalMetricsProvider(AnalyticsProvider):
    max_period_days = 30

    def get_overview(self, period):
        period = self.limit_period(period)
        # ...
```

## Capability contract

Only advertise a capability when the corresponding method returns meaningful
data. Components use the first configured provider in settings order that
advertises what they need.

Common pairs include:

| Capability | Method |
| --- | --- |
| `OVERVIEW` | `get_overview(period)` |
| `TIMESERIES` | `get_timeseries(period)` |
| `PATHS` | `get_path_stats(period, limit)` |
| Breakdown capabilities | `get_breakdown(dimension, period, limit)` |
| `PERFORMANCE` | `get_performance(period)` |
| `SLOWEST` | `get_slowest_routes(period, limit)` |
| `ISSUES` | `get_top_issues(period, limit)` |
| `DATABASE` | `get_database(period)` |

See the [provider API reference](reference/provider-api.md) for every method and
return type.
