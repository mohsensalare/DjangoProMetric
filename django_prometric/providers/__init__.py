"""Provider registry: instantiate the analytics providers the project lists.

The dashboard composes data from every configured provider — each component
draws from the first provider (in ``PROVIDERS`` order) that has the needed
capability, and every card names its source.
"""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

from ..conf import get_config
from .base import AnalyticsProvider, Period, ProviderError  # noqa: F401 — public API

_ALIASES = {
    "cloudflare": "django_prometric.providers.cloudflare.CloudflareProvider",
    "sentry": "django_prometric.providers.sentry.SentryProvider",
    "postgres": "django_prometric.providers.postgres.PostgresProvider",
    "arvancloud": "django_prometric.providers.arvancloud.ArvanCloudProvider",
    # Pre-list era spelling of the default selection.
    "auto": "django_prometric.providers.cloudflare.CloudflareProvider",
}

# Built-in providers whose dependencies ship as a pip extra of the same name.
_EXTRAS = frozenset({"cloudflare", "sentry", "postgres", "arvancloud"})


def _load(spec: str) -> type[AnalyticsProvider]:
    try:
        return import_string(_ALIASES.get(spec, spec))
    except ImportError as exc:
        hint = (
            f'install its dependencies with: pip install "django-prometric[{spec}]"'
            if spec in _EXTRAS
            else "check the dotted path and that its package is installed"
        )
        raise ImproperlyConfigured(
            f"DJANGO_PROMETRIC lists the {spec!r} analytics provider, "
            f"but it could not be imported ({exc}) — {hint}."
        ) from exc


def get_providers() -> list[AnalyticsProvider]:
    """Every listed provider, configured or not, in settings order."""
    return [_load(spec)() for spec in get_config()["PROVIDERS"]]


def configured_providers() -> list[AnalyticsProvider]:
    return [provider for provider in get_providers() if provider.is_configured]


def get_provider(slug: str) -> AnalyticsProvider | None:
    """The listed provider with this slug, or None."""
    for provider in get_providers():
        if provider.slug == slug:
            return provider
    return None


def provider_for(capability: str, providers=None) -> AnalyticsProvider | None:
    """The first configured provider that can answer this capability."""
    for provider in providers if providers is not None else configured_providers():
        if capability in provider.capabilities():
            return provider
    return None
