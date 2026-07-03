"""Configuration access for django-prometric.

All settings live under a single ``DJANGO_PROMETRIC`` dict in the host
project's settings module. Every key is optional; sane defaults are applied
and nested dicts are merged one level deep, so users only override what they
need.
"""

from django.conf import settings
from django.dispatch import receiver
from django.test.signals import setting_changed

DEFAULTS = {
    # "auto" currently selects the Cloudflare provider. Set a dotted path
    # to plug in a custom AnalyticsProvider.
    "ANALYTICS_PROVIDER": "auto",
    "CLOUDFLARE": {
        # Names of the environment variables the credentials are read from.
        "API_TOKEN_ENV": "CLOUDFLARE_API_TOKEN",
        "ZONE_ID_ENV": "CLOUDFLARE_ZONE_ID",
        "ACCOUNT_ID_ENV": "CLOUDFLARE_ACCOUNT_ID",
        "API_URL": "https://api.cloudflare.com/client/v4/graphql",
        "TIMEOUT": 10,
    },
    "ROUTES": {
        "MODE": "all",  # all | include | exclude
        "INCLUDE": [],  # list of regexes matched against the display path
        "EXCLUDE": [],
        "EXCLUDE_ADMIN": True,
    },
    # superuser | staff | permission | "dotted.path.to.callable"
    "ACCESS": "superuser",
    # When True, unauthorized visitors get a 404 instead of a login redirect,
    # so the dashboard URL cannot be discovered by probing.
    "STEALTH_404": False,
    "CACHE_ALIAS": "default",
    "CACHE_TTL": 300,  # seconds analytics responses are cached
    "COMPONENTS": [
        "django_prometric.components.OverviewCards",
        "django_prometric.components.TrafficChart",
        "django_prometric.components.CountryChart",
        "django_prometric.components.StatusChart",
        "django_prometric.components.CacheChart",
        "django_prometric.components.PerformanceCard",
        "django_prometric.components.TopRoutesTable",
    ],
    "SITE_NAME": None,  # shown in the dashboard header; defaults to provider info
}

_config_cache = None


def get_config():
    """Return DEFAULTS overridden by ``settings.DJANGO_PROMETRIC``."""
    global _config_cache
    if _config_cache is None:
        user = getattr(settings, "DJANGO_PROMETRIC", {}) or {}
        merged = {}
        for key, default in DEFAULTS.items():
            value = user.get(key, default)
            if isinstance(default, dict) and isinstance(value, dict) and value is not default:
                merged[key] = {**default, **value}
            else:
                merged[key] = value
        _config_cache = merged
    return _config_cache


@receiver(setting_changed)
def _reset_config(*, setting, **kwargs):
    if setting == "DJANGO_PROMETRIC":
        global _config_cache
        _config_cache = None
