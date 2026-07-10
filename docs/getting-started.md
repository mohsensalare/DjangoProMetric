---
title: Install django-prometric
description: Install django-prometric, mount its Django dashboard, run migrations, and configure the first analytics provider.
---

# Get started

You can add django-prometric to an existing Django project without changing its
views, middleware, or URL patterns. A basic installation takes a few minutes.

## Install the package

=== "Every provider"

    ```console
    python -m pip install "django-prometric[full]"
    ```

=== "Core only"

    ```console
    python -m pip install django-prometric
    ```

=== "PostgreSQL"

    ```console
    python -m pip install "django-prometric[postgres]"
    ```

The core install already supports Cloudflare, ArvanCloud, and Sentry because
those integrations use Python's standard library. The `postgres` extra adds the
`psycopg` driver.

## Register the application

Add `django_prometric` to `INSTALLED_APPS`:

```python title="settings.py"
INSTALLED_APPS = [
    # ...
    "django_prometric",
]
```

## Mount the dashboard

Mount its URLs under any prefix you prefer:

```python title="urls.py" hl_lines="5"
from django.urls import include, path

urlpatterns = [
    # ...
    path("prometric/", include("django_prometric.urls")),
]
```

## Create the database tables

```console
python manage.py migrate
```

The migrations store dashboard preferences and historical snapshots. Analytics
data fetched from providers is cached through Django's configured cache backend.

## Open the dashboard

Start Django, sign in as a superuser, and visit `/prometric/` (or the prefix you
mounted). Providers without credentials appear on the **Providers** page with
their setup requirements; an unconfigured provider does not break the dashboard.

!!! tip
    Start with one provider. Cloudflare or ArvanCloud supplies edge traffic,
    Sentry adds application performance and errors, and PostgreSQL contributes
    database health.

## Configure a provider

For example, enable Cloudflare and Sentry in priority order:

```python title="settings.py"
DJANGO_PROMETRIC = {
    "PROVIDERS": ["cloudflare", "sentry"],
}
```

Then pass credentials through the environment:

```console
export CLOUDFLARE_API_TOKEN="..."
export CLOUDFLARE_ZONE_ID="..."
export SENTRY_API_TOKEN="..."
export SENTRY_ORG="your-organization"
```

See [Providers](providers.md) to choose a source and understand which cards it
can answer.

## Production checklist

- Run `python manage.py collectstatic` as part of the normal deployment.
- Keep provider tokens in environment variables or a secrets manager.
- Leave access restricted; superusers are the default.
- Set `STEALTH_404` when the dashboard URL should not be discoverable.
- Use a shared production cache when several application processes serve the
  dashboard.

Continue with [Configuration](configuration.md) or review the
[security model](security.md).
