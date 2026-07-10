---
title: Provider API reference
description: Python API reference for django-prometric provider capabilities, periods, errors, and result dataclasses.
---

# Provider API reference

The provider base module is the stable import surface for integrations. It
re-exports capability constants, time windows, result dataclasses, and snapshot
types so provider packages do not need to depend on internal module locations.

## Provider contract

::: django_prometric.providers.base.AnalyticsProvider
    options:
      members: true
      inherited_members: false

## User-presentable errors

::: django_prometric.providers.base.ProviderError
    options:
      members: true

## Time windows

::: django_prometric.providers.base.Period
    options:
      members: true

## Core result types

::: django_prometric.providers.base.OverviewStats

::: django_prometric.providers.base.TimeseriesPoint

::: django_prometric.providers.base.PathStat

::: django_prometric.providers.base.BreakdownItem

::: django_prometric.providers.base.PerformanceStats

::: django_prometric.providers.base.RoutePerformance

::: django_prometric.providers.base.RouteMetrics

## Application and database result types

::: django_prometric.providers.base.QueryStat

::: django_prometric.providers.base.IssueStat

::: django_prometric.providers.base.DatabaseStats

::: django_prometric.providers.base.TableStat

::: django_prometric.providers.base.IndexStat

::: django_prometric.providers.base.Cumulative

::: django_prometric.providers.base.Insight

::: django_prometric.providers.base.Notice
