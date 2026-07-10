---
title: Django analytics dashboard and API monitoring
description: Open-source, self-hosted analytics and API monitoring for Django — per-endpoint traffic, performance, errors, and database metrics in one dashboard.
hide:
  - toc
---

<div class="pm-hero" markdown>

<div class="pm-eyebrow">Self-hosted Django analytics, resolved by route</div>

# Know what every route is doing.

django-prometric is an open-source analytics dashboard and API monitoring tool
for Django. It discovers your Django and Django REST Framework endpoints, then
connects traffic, performance, audience, error, and database metrics to the
routes that produced them—all inside your own protected, self-hosted dashboard.

<div class="pm-flow" aria-label="django-prometric data flow">
  <span>request</span><b>→</b><span>Django route</span><b>→</b><span>provider signals</span><b>→</b><span>actionable insight</span>
</div>

[Get started](getting-started.md){ .md-button .md-button--primary }
[Explore providers](providers.md){ .md-button }

</div>

!!! warning "Beta software"
    Test the dashboard in a non-critical environment before rolling it out
    broadly. The dashboard is private by default and only superusers can open it.

## One dashboard, several points of view

<div class="grid cards" markdown>

-   :material-routes:{ .lg .middle } **Routes without registration**

    ---

    Discover regular Django views and Django REST Framework endpoints directly
    from your URL configuration.

-   :material-chart-timeline-variant-shimmer:{ .lg .middle } **Metrics in context**

    ---

    See site-wide signals and drill into the path, method, status, cache, and
    latency data for a specific route.

-   :material-layers-triple:{ .lg .middle } **Composable providers**

    ---

    Combine edge traffic, application performance, errors, and database health.
    Each card names the source that answered it.

-   :material-shield-lock-outline:{ .lg .middle } **Private by default**

    ---

    Reuse Django authentication, groups, and permissions. Hide the dashboard
    behind a 404 when even its existence should remain private.

</div>

## Built for the Django stack

| | Supported versions |
| --- | --- |
| Python | 3.9–3.14 |
| Django | 4.2–6.0 |
| Django REST Framework | Detected automatically when installed |

The core package depends only on Django. Templates, CSS, JavaScript, and Chart.js
ship with the package, so the dashboard needs no Node.js build step.

## Start in five commands

```console
python -m pip install "django-prometric[full]"
# add "django_prometric" to INSTALLED_APPS
# mount path("prometric/", include("django_prometric.urls"))
python manage.py migrate
python manage.py runserver
```

Sign in as a superuser and open `/prometric/`. Continue with the
[installation guide](getting-started.md) for the exact settings and production
checklist.
