# Changelog

All notable changes to django-prometric will be documented in this file.

The project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-07-06

### Added

- Per-provider install extras: `pip install "django-prometric[postgres]"`
  brings the `psycopg` driver, `[cloudflare]` and `[sentry]` are stable no-op
  extras (both providers run on the standard library), and `[full]` installs
  everything.
- A provider listed in `DJANGO_PROMETRIC["PROVIDERS"]` that fails to import
  now raises `ImproperlyConfigured` with the matching `pip install` hint
  instead of a bare `ImportError`.
- Continuous-integration and release automation: a test matrix across the
  supported Python and Django versions, and a tag-gated release workflow that
  publishes to PyPI through Trusted Publishing (OIDC).

### Changed

- `django_prometric.providers.base` was split into focused modules —
  `capabilities` (the capability vocabulary), `periods` (time windows),
  `types` (result dataclasses), and `report` (the stored snapshot schema).
  Every name is still importable from `providers.base`, so existing imports
  and custom providers keep working unchanged.

## [0.1.0] - 2026-07-05

### Added

- Initial beta release.
- Route-aware analytics dashboard for Django and Django REST Framework.
- Pluggable Cloudflare, Sentry, and PostgreSQL analytics providers.
- Traffic, audience, performance, database, and snapshot views.

[Unreleased]: https://github.com/mohsensalare/DjangoProMetric/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/mohsensalare/DjangoProMetric/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/mohsensalare/DjangoProMetric/releases/tag/v0.1.0
