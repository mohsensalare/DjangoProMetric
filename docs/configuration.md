---
title: Configure django-prometric
description: Reference for django-prometric settings, provider priority, caching, access control, and Django route filtering.
---

# Configuration

Every option lives under one `DJANGO_PROMETRIC` dictionary. All keys are
optional, and nested dictionaries are merged with their defaults.

## Core settings

```python title="settings.py"
DJANGO_PROMETRIC = {
    "PROVIDERS": ["cloudflare", "sentry"],
    "ACCESS": "superuser",
    "STEALTH_404": False,
    "CACHE_ALIAS": "default",
    "CACHE_TTL": 300,
    "SITE_NAME": None,
}
```

| Setting | Default | Meaning |
| --- | --- | --- |
| `PROVIDERS` | `["cloudflare", "sentry"]` | Built-in aliases or dotted provider class paths, in priority order |
| `ACCESS` | `"superuser"` | Baseline access policy: `superuser`, `staff`, `permission`, or a callable path |
| `STEALTH_404` | `False` | Return 404 instead of revealing the dashboard to unauthorized requests |
| `CACHE_ALIAS` | `"default"` | Django cache connection used for provider responses |
| `CACHE_TTL` | `300` | Provider response cache lifetime in seconds |
| `SITE_NAME` | `None` | Optional name displayed in the dashboard header |

Provider-specific keys are documented in each [provider guide](providers.md).

## Route filtering

By default every non-admin route is shown. Filter display paths with regular
expressions:

```python title="settings.py"
DJANGO_PROMETRIC = {
    "ROUTES": {
        "MODE": "exclude",  # all | include | exclude
        "INCLUDE": [],
        "EXCLUDE": [r"^/health/$", r"^/internal/"],
        "EXCLUDE_ADMIN": True,
        "ADMIN_PATTERNS": [],
        "FILTER": None,
    },
}
```

| Option | Behavior |
| --- | --- |
| `MODE="all"` | Keep all discovered routes before the custom filter runs |
| `MODE="include"` | Keep only paths matching at least one `INCLUDE` expression |
| `MODE="exclude"` | Drop paths matching any `EXCLUDE` expression |
| `EXCLUDE_ADMIN` | Hide routes recognized as Django admin routes |
| `ADMIN_PATTERNS` | Extra expressions for relocated admin sites or admin skins |
| `FILTER` | Dotted callable receiving a display path and returning a boolean |

### Custom filter

```python title="myproject/metrics.py"
def visible_in_prometric(display_path: str) -> bool:
    return not display_path.startswith(("/__debug__/", "/health/"))
```

```python title="settings.py"
DJANGO_PROMETRIC = {
    "ROUTES": {
        "FILTER": "myproject.metrics.visible_in_prometric",
    },
}
```

The callable runs after the selected include/exclude mode.

## Dashboard components

`COMPONENTS` is the ordered list of dashboard component class paths. Override
it only when you need to remove, reorder, or add a custom component; keeping the
default is the stable path for most projects.

!!! note
    Settings are read lazily and reset when Django's `setting_changed` signal
    fires, so `override_settings()` works as expected in tests.
