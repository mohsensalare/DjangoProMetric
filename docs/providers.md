---
title: Analytics providers
description: Combine Cloudflare, ArvanCloud, Sentry, and PostgreSQL metrics in one self-hosted Django analytics and monitoring dashboard.
---

# Providers

Providers translate external analytics and database statistics into a shared
set of dashboard capabilities. List several providers to assemble a complete
view of the system.

| Provider | Best for | Main capabilities | Extra dependency |
| --- | --- | --- | --- |
| [Cloudflare](cloudflare.md) | Edge and route traffic | Traffic, audience, cache, security, routes, TTFB | None |
| [ArvanCloud](arvancloud.md) | Edge traffic for ArvanCloud CDN | Traffic, countries, cache, status, security | None |
| [Sentry](sentry.md) | Application telemetry | Performance, slow routes, issues, queries, backend spans | None |
| [PostgreSQL](postgres.md) | Database health | Connections, tables, indexes, slow queries, insights | `psycopg` |

## How composition works

```python title="settings.py"
DJANGO_PROMETRIC = {
    "PROVIDERS": ["cloudflare", "sentry", "postgres"],
}
```

Providers are evaluated in list order. For each component, django-prometric uses
the first configured provider that declares the required capability. This means
Cloudflare can answer traffic while Sentry answers performance and PostgreSQL
answers database cards in the same dashboard.

Unconfigured providers remain visible on the Providers page with setup help.
They do not prevent other providers from rendering.

## Choose a combination

=== "Cloudflare stack"

    ```python
    DJANGO_PROMETRIC = {
        "PROVIDERS": ["cloudflare", "sentry", "postgres"],
    }
    ```

    Full edge, application, and database coverage for a site behind Cloudflare.

=== "ArvanCloud stack"

    ```python
    DJANGO_PROMETRIC = {
        "PROVIDERS": ["arvancloud", "sentry", "postgres"],
    }
    ```

    Domain traffic from ArvanCloud plus application and database telemetry.

=== "Application only"

    ```python
    DJANGO_PROMETRIC = {
        "PROVIDERS": ["sentry", "postgres"],
    }
    ```

    Useful when edge analytics are unavailable. Traffic cards remain empty, but
    performance, errors, queries, and database health still work.

Need another source? Implement a [custom provider](custom-providers.md) and list
its dotted Python path alongside the built-in aliases.
