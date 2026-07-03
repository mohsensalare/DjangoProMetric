"""Provider registry: resolve the configured analytics provider."""

from django.utils.module_loading import import_string

from ..conf import get_config
from .base import AnalyticsProvider, Period, ProviderError  # noqa: F401 — public API

_ALIASES = {
    "auto": "django_prometric.providers.cloudflare.CloudflareProvider",
    "cloudflare": "django_prometric.providers.cloudflare.CloudflareProvider",
}


def get_provider() -> AnalyticsProvider:
    """Instantiate the provider selected by ``ANALYTICS_PROVIDER``.

    ``"auto"`` (the default) currently means Cloudflare; more built-in
    sources are planned. Any dotted path to an :class:`AnalyticsProvider`
    subclass is accepted, so projects can plug in their own data source.
    """
    spec = get_config()["ANALYTICS_PROVIDER"]
    provider_class = import_string(_ALIASES.get(spec, spec))
    return provider_class()
