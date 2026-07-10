# django-prometric

[![Status: Beta](https://img.shields.io/badge/status-beta-f59e0b)](https://github.com/mohsensalare/DjangoProMetric/releases)
[![Python 3.9–3.14](https://img.shields.io/badge/python-3.9%E2%80%933.14-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Django 4.2–6.0](https://img.shields.io/badge/django-4.2%E2%80%936.0-092E20?logo=django&logoColor=white)](https://www.djangoproject.com/)
[![Documentation](https://img.shields.io/badge/docs-live-00a884)](https://mohsensalare.github.io/DjangoProMetric/)
[![License: MIT](https://img.shields.io/badge/license-MIT-2563eb)](https://github.com/mohsensalare/DjangoProMetric/blob/main/LICENSE)

**Route-aware analytics and operational insight for Django.**

django-prometric discovers the URLs and Django REST Framework endpoints in
your project, then connects traffic, performance, audience, error, and database
metrics to the routes that produced them—all inside a protected Django
dashboard.

[Documentation](https://mohsensalare.github.io/DjangoProMetric/) · [Quick start](#quick-start) · [Providers](#providers) ·
[Configuration](#configuration) · [Security](#security) ·
[Development](#development)

> **Beta:** Test the dashboard in a non-critical environment before rolling it
> out broadly.

## Why django-prometric?

| Capability | What it gives you |
| --- | --- |
| Route discovery | Django and DRF endpoints without manual registration |
| Unified analytics | Edge, application, and database metrics in one dashboard |
| Per-route insight | Traffic and performance connected to the relevant URL pattern |
| Provider composition | Cloudflare, Sentry, PostgreSQL, and custom providers in priority order |
| Historical comparison | Snapshots and repeated runs compared over time |
| Private by default | Django authentication, permissions, and superuser-only default access |
| No frontend toolchain | Packaged templates, CSS, and JavaScript ready for `collectstatic` |

## Providers

| Provider | Contributes | Install | Setup |
| --- | --- | --- | --- |
| **Cloudflare** | Traffic, audience, cache, bandwidth, bots, security, SEO, and network data | `django-prometric[cloudflare]` | API token + zone ID |
| **ArvanCloud** | Edge traffic, cache, countries, status, and attack/security data | `django-prometric[arvancloud]` | API key + domain |
| **Sentry** | Application performance, slow routes, issues, queries, and backend operations | `django-prometric[sentry]` | Auth token + organization |
| **PostgreSQL** | Database health, tables, indexes, slow queries, and derived insights | `django-prometric[postgres]` | Existing Django PostgreSQL connection |

Providers are evaluated in list order. The first configured provider capable
of supplying a dashboard component is used for that component. Unconfigured
providers stay visible with setup instructions instead of breaking the
dashboard.

## Compatibility

| | Supported versions |
| --- | --- |
| Python | 3.9–3.14 |
| Django | 4.2, 5.0, 5.1, 5.2, 6.0 |

The only required runtime dependency is Django 4.2 or newer. DRF integration
activates automatically when Django REST Framework is installed.

## Quick start

### 1. Install

Install the core package with the extras for the providers you plan to use:

```console
python -m pip install "django-prometric[full]"
```

| Command | What it installs |
| --- | --- |
| `pip install django-prometric` | Core dashboard; Cloudflare and Sentry already work (standard library only) |
| `pip install "django-prometric[postgres]"` | Core + the `psycopg` driver for the PostgreSQL provider |
| `pip install "django-prometric[full]"` | Core + every provider dependency |

`[cloudflare]` and `[sentry]` are currently empty extras — they exist so the
per-provider install commands stay stable if those providers ever need a
dependency of their own.

### 2. Register the application

```python
INSTALLED_APPS = [
    # ...
    "django_prometric",
]
```

### 3. Mount the dashboard

```python
from django.urls import include, path

urlpatterns = [
    # ...
    path("prometric/", include("django_prometric.urls")),
]
```

### 4. Apply migrations

```console
python manage.py migrate
```

### 5. Open the dashboard

Sign in as a superuser and visit `/prometric/`. Providers without credentials
will show their setup requirements on the Providers page.

For production deployments, include django-prometric in your normal static
files workflow:

```console
python manage.py collectstatic
```

## Provider setup

Enable providers in the order you want them considered:

```python
DJANGO_PROMETRIC = {
    "PROVIDERS": ["cloudflare", "sentry", "postgres"],
}
```

Cloudflare and Sentry are listed by default. PostgreSQL is opt-in.

### Cloudflare

Create an API token with **Analytics Read** permission, then expose the token
and zone ID to the Django process:

```console
export CLOUDFLARE_API_TOKEN="..."
export CLOUDFLARE_ZONE_ID="..."
```

If one zone serves several applications, limit analytics to this project's
hostnames:

```python
DJANGO_PROMETRIC = {
    "PROVIDERS": ["cloudflare"],
    "CLOUDFLARE": {
        "HOSTS": ["example.com", "www.example.com"],
    },
}
```

Some route-level and performance metrics depend on the Cloudflare plan. The
provider detects unavailable features and reports plan limits in the UI.

Read the [Cloudflare provider guide](https://mohsensalare.github.io/DjangoProMetric/cloudflare/)
for host scoping, the two analytics datasets, and plan limits.

### ArvanCloud

For sites behind [ArvanCloud](https://www.arvancloud.ir/) CDN. Create an API key
with report read access, then expose it and your domain:

```console
export ARVANCLOUD_API_KEY="..."
export ARVANCLOUD_DOMAIN="example.com"
```

The provider is opt-in while it is validated across plans; add it to your list:

```python
DJANGO_PROMETRIC = {
    "PROVIDERS": ["arvancloud"],
    "ARVANCLOUD": {
        "SUBDOMAIN": "blog",   # optional; "@" for the root domain
    },
}
```

This release reads the ArvanCloud Reports API (overview, traffic, countries,
cache, status, and security) over the dashboard's presets up to 90 days and
custom ranges, with time-window boundaries converted to real UTC. Route-level,
HTTP-method, and performance cards are not included — a live probe confirmed
ArvanCloud Metric Exporters return only a reset-on-fetch Top(10) snapshot (no
history), so they cannot back those cards.

Read the [ArvanCloud provider guide](https://mohsensalare.github.io/DjangoProMetric/arvancloud/)
for scope limits, time-window handling, and permissions.

### Sentry

Create an auth token with `org:read` and `event:read` scopes:

```console
export SENTRY_API_TOKEN="..."
export SENTRY_ORG="your-organization-slug"
export SENTRY_PROJECT="your-project-slug"  # optional
```

When `SENTRY_PROJECT` is omitted, the first project returned by Sentry is used.
The default performance lookback is 14 days; change it with
`DJANGO_PROMETRIC["SENTRY"]["MAX_DAYS"]`.

Read the [Sentry provider guide](https://mohsensalare.github.io/DjangoProMetric/sentry/)
for project pinning, self-hosted Sentry, and metric semantics.

### PostgreSQL

Install the provider's driver, then point it at a database:

```console
python -m pip install "django-prometric[postgres]"
```

If your Django project already talks to PostgreSQL, the driver is already
installed and the extra adds nothing new. The provider reads the selected
Django database connection and requires no external API or token:

```python
DJANGO_PROMETRIC = {
    "PROVIDERS": ["postgres"],
    "POSTGRES": {
        "DB_ALIAS": "default",
    },
}
```

Database, table, and index metrics use standard PostgreSQL statistics views.
Query-level metrics additionally require `pg_stat_statements`.

Read the [PostgreSQL provider guide](https://mohsensalare.github.io/DjangoProMetric/postgres/)
for extension setup, permissions, and metric semantics.

### Custom providers

Any data source can feed the dashboard. Subclass `AnalyticsProvider`, declare
the capabilities you can answer, and implement the matching `get_*` methods:

```python
from django_prometric.providers import base
from django_prometric.providers.base import AnalyticsProvider, OverviewStats

class MyProvider(AnalyticsProvider):
    slug = "mysource"
    verbose_name = "My source"

    def capabilities(self):
        return {base.OVERVIEW}

    def get_overview(self, period):
        return OverviewStats(requests=...)
```

List it by dotted path, mixed freely with the built-in aliases:

```python
DJANGO_PROMETRIC = {
    "PROVIDERS": ["cloudflare", "myproject.analytics.MyProvider"],
}
```

The full contract — capabilities, time windows, and the result dataclasses —
lives in `django_prometric.providers.base`.

## Configuration

All settings are optional and live under one `DJANGO_PROMETRIC` dictionary:

```python
DJANGO_PROMETRIC = {
    "PROVIDERS": ["cloudflare", "sentry"],
    "ACCESS": "superuser",
    "STEALTH_404": False,
    "CACHE_ALIAS": "default",
    "CACHE_TTL": 300,
    "SITE_NAME": None,
}
```

Built-in provider aliases may be mixed with dotted paths to custom
`AnalyticsProvider` subclasses.

### Route filtering

Administrative routes are excluded by default. Additional routes can be
selected with regular expressions matched against their display paths:

```python
DJANGO_PROMETRIC = {
    "ROUTES": {
        "MODE": "exclude",  # all | include | exclude
        "EXCLUDE": [r"^/health/$", r"^/internal/"],
        "EXCLUDE_ADMIN": True,
    },
}
```

For application-specific rules, set `ROUTES.FILTER` to a dotted callable. It
receives a display path and returns `True` when the route should be kept.

## Security

> The dashboard exposes operational information. Do not make it anonymously
> accessible.

Access is restricted to superusers by default:

```python
DJANGO_PROMETRIC = {
    "ACCESS": "superuser",  # superuser | staff | permission | dotted callable
    "STEALTH_404": True,
}
```

Users granted `django_prometric.view_dashboard` can access the dashboard
regardless of the baseline policy. Set `ACCESS` to `permission` to allow only
explicitly granted users and groups.

A custom policy must accept the current user and return a boolean:

```python
DJANGO_PROMETRIC = {
    "ACCESS": "myproject.permissions.can_view_metrics",
}
```

With `STEALTH_404` enabled, unauthorized requests receive a 404 response
instead of a login redirect or permission error.

## Development

```console
git clone https://github.com/mohsensalare/DjangoProMetric.git
cd DjangoProMetric
python -m pip install -e ".[dev]"

python -m pytest
ruff check .
ruff format --check .
```

These are the same checks CI runs on every push and pull request. The build
and release process — versioning, tags, and Trusted Publishing to PyPI — is
documented in [RELEASING.md](https://github.com/mohsensalare/DjangoProMetric/blob/main/RELEASING.md).

## Links

- [Documentation](https://mohsensalare.github.io/DjangoProMetric/)
- [Development and release process](https://github.com/mohsensalare/DjangoProMetric/blob/main/RELEASING.md)
- [Changelog](https://github.com/mohsensalare/DjangoProMetric/blob/main/CHANGELOG.md)
- [Issue tracker](https://github.com/mohsensalare/DjangoProMetric/issues)
- [Source code](https://github.com/mohsensalare/DjangoProMetric)

## License

django-prometric is released under the [MIT License](https://github.com/mohsensalare/DjangoProMetric/blob/main/LICENSE).
