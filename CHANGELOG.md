# Changelog

All notable changes to django-prometric will be documented in this file.

The project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.0] - 2026-07-10

### Added

- **ArvanCloud provider** (opt-in, `django-prometric[arvancloud]`): an
  edge-traffic source that reads ArvanCloud's Reports API with the standard
  library only. Supplies overview, traffic timeseries, country, cache, status,
  threats, security, and derived insights over the dashboard's selected period,
  scoped to a domain or a single subdomain. Status and attack reports cannot be
  filtered by subdomain, so those capabilities are dropped under a subdomain
  scope rather than reporting whole-domain numbers. Custom and `90d` windows go
  out as `since`/`until` ranges, with boundaries converted from the project's
  local timezone to real UTC so a window is not shifted by its offset (a live
  probe measured a +6.4% error on an unconverted Asia/Tehran day). The report
  window is served up to 90 days (verified live to 120 at daily granularity), so
  `MAX_REPORT_DAYS` defaults to 90 and no UI preset is truncated. Attack reports
  take only now-anchored presets, so a historical custom range that has already
  ended is reported as unavailable instead of being approximated to unrelated
  recent attacks. Route/path, HTTP method, and performance cards are
  intentionally not advertised — a live probe confirmed ArvanCloud Metric
  Exporters return only a reset-on-fetch Top(10) snapshot (not a queryable
  history) and that the exporter-management endpoint requires a higher
  permission (HTTP 403 on a report-read key). See
  [`docs/arvancloud.md`](docs/arvancloud.md).
- Provider guides for **Cloudflare** ([`docs/cloudflare.md`](docs/cloudflare.md))
  and **Sentry** ([`docs/sentry.md`](docs/sentry.md)), and links to every
  provider guide from the README, so all four built-in providers are documented
  alongside PostgreSQL and ArvanCloud.

## [0.2.1] - 2026-07-06

### Changed

- Maintenance release. No functional changes from 0.2.0; published as a new
  version because 0.2.0 is no longer available on PyPI.

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

[Unreleased]: https://github.com/mohsensalare/DjangoProMetric/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/mohsensalare/DjangoProMetric/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/mohsensalare/DjangoProMetric/releases/tag/v0.2.1
[0.2.0]: https://github.com/mohsensalare/DjangoProMetric/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/mohsensalare/DjangoProMetric/releases/tag/v0.1.0
