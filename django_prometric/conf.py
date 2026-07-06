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
    # Analytics providers, in priority order. Built-in aliases ("cloudflare",
    # "sentry") or dotted paths to AnalyticsProvider subclasses. Every listed
    # provider appears on the providers page; unconfigured ones show their
    # setup instructions there.
    "PROVIDERS": ["cloudflare", "sentry"],
    "CLOUDFLARE": {
        # Names of the environment variables the credentials are read from.
        "API_TOKEN_ENV": "CLOUDFLARE_API_TOKEN",
        "ZONE_ID_ENV": "CLOUDFLARE_ZONE_ID",
        "ACCOUNT_ID_ENV": "CLOUDFLARE_ACCOUNT_ID",
        "API_URL": "https://api.cloudflare.com/client/v4/graphql",
        "TIMEOUT": 10,
        # A zone often serves several hostnames (frontend, backend, …).
        # List the hostnames this Django project answers to count only its
        # traffic, or list hostnames to drop. Empty = the whole zone.
        "HOSTS": [],
        "EXCLUDE_HOSTS": [],
    },
    "SENTRY": {
        "API_TOKEN_ENV": "SENTRY_API_TOKEN",
        "ORG_ENV": "SENTRY_ORG",
        "PROJECT_ENV": "SENTRY_PROJECT",  # optional; first project when unset
        "BASE_URL": "https://sentry.io",
        "TIMEOUT": 10,
        # How many days back the plan retains performance data. Longer
        # requests are flagged in the UI and clamped on demand.
        "MAX_DAYS": 14,
    },
    "POSTGRES": {
        "DB_ALIAS": "default",  # which Django DB connection to inspect
        # Manual connection — an escape hatch for when the DB_ALIAS above is not
        # PostgreSQL (or you would rather not inspect a Django-registered
        # connection). Set NAME (or a full DSN) and the provider opens its own
        # read-only connection with psycopg instead of using DB_ALIAS. Leave
        # NAME/DSN empty to keep using DB_ALIAS.
        "DSN": "",  # e.g. "postgresql://user:pass@host:5432/dbname" — wins over the fields below
        "NAME": "",
        "USER": "",
        "PASSWORD": "",
        "HOST": "",
        "PORT": "",
        "OPTIONS": {},  # extra keyword args passed to psycopg.connect()
        "MAX_TABLES": 10,  # rows in the tables card
        "MAX_INDEXES": 10,  # rows in each indexes section
        "MAX_QUERIES": 8,  # rows in the slowest-queries card
        # Phase 2 (counter windowing) — inert until implemented.
        "SAMPLE_ENABLED": False,
        "SAMPLE_RETENTION_DAYS": 90,
    },
    "ROUTES": {
        "MODE": "all",  # all | include | exclude
        "INCLUDE": [],  # list of regexes matched against the display path
        "EXCLUDE": [],
        "EXCLUDE_ADMIN": True,
        # Extra regexes that mark a route as admin — for admin skins or a
        # relocated admin URL the automatic detection cannot know about.
        "ADMIN_PATTERNS": [],
        # Dotted path to a callable(display_path) -> bool. Return False to
        # drop a route from every listing. Runs after MODE/INCLUDE/EXCLUDE.
        "FILTER": None,
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
        "django_prometric.components.InsightsPanel",
        "django_prometric.components.TrafficChart",
        "django_prometric.components.PerformanceCard",
        "django_prometric.components.BackendCard",
        "django_prometric.components.SlowestQueriesTable",
        "django_prometric.components.SlowestRoutesTable",
        "django_prometric.components.CountryChart",
        "django_prometric.components.StatusChart",
        "django_prometric.components.BotsCard",
        "django_prometric.components.SeoCard",
        "django_prometric.components.AudienceCard",
        "django_prometric.components.NetworkCard",
        "django_prometric.components.CacheChart",
        "django_prometric.components.SecurityCard",
        "django_prometric.components.IssuesTable",
        "django_prometric.components.TopRoutesTable",
        "django_prometric.components.DatabaseCard",
        "django_prometric.components.TablesTable",
        "django_prometric.components.IndexesTable",
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
        # Pre-list era spelling: a single ANALYTICS_PROVIDER selection.
        legacy = user.get("ANALYTICS_PROVIDER")
        if legacy and legacy != "auto" and "PROVIDERS" not in user:
            merged["PROVIDERS"] = [legacy]
        _config_cache = merged
    return _config_cache


@receiver(setting_changed)
def _reset_config(*, setting, **kwargs):
    if setting == "DJANGO_PROMETRIC":
        global _config_cache
        _config_cache = None
