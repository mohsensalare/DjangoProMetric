"""Startup configuration checks (django.core.checks).

Registered from :meth:`PrometricConfig.ready`. Each configured provider is
asked for scope warnings (see :meth:`AnalyticsProvider.configuration_warnings`)
so a mis-scoped Sentry project or an unfiltered Cloudflare zone surfaces at
startup — as ``manage.py check`` / ``runserver`` output — instead of silently
skewing the dashboard's numbers.
"""

from __future__ import annotations

from django.core.checks import register

from .providers import get_providers


@register()
def check_provider_scope(app_configs, **kwargs):
    warnings = []
    for provider in get_providers():
        if provider.is_configured:
            warnings.extend(provider.configuration_warnings())
    return warnings
