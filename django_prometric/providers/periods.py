"""Time windows the dashboard queries over.

A :class:`Period` is either one of the preset windows ("last 24 hours", …) or
a free ``from``/``to`` date range picked in the UI. Providers receive it with
every data call and may clamp it to their own reach
(:meth:`~django_prometric.providers.base.AnalyticsProvider.limit_period`).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

_PERIODS = {
    "24h": (_("Last 24 hours"), dt.timedelta(hours=24)),
    "7d": (_("Last 7 days"), dt.timedelta(days=7)),
    "30d": (_("Last 30 days"), dt.timedelta(days=30)),
    "90d": (_("Last 3 months"), dt.timedelta(days=90)),
}
DEFAULT_PERIOD = "24h"
CUSTOM_PERIOD = "custom"


@dataclass(frozen=True)
class Period:
    key: str
    label: str
    start: dt.datetime
    end: dt.datetime

    @property
    def days(self) -> float:
        return (self.end - self.start).total_seconds() / 86400

    @property
    def is_custom(self) -> bool:
        return self.key == CUSTOM_PERIOD

    @classmethod
    def from_key(cls, key: str | None) -> Period:
        if key not in _PERIODS:
            key = DEFAULT_PERIOD
        label, delta = _PERIODS[key]
        end = timezone.now()
        return cls(key=key, label=label, start=end - delta, end=end)

    @classmethod
    def from_request(cls, params) -> Period:
        """Build from GET/POST params: a preset ``period`` key, or a free
        ``from``/``to`` date range (``period=custom``)."""
        if params.get("period") == CUSTOM_PERIOD:
            start = _parse_date(params.get("from"))
            end = _parse_date(params.get("to"))
            if start and end and start < end:
                end = min(end + dt.timedelta(days=1), timezone.now())  # inclusive "to" day
                return cls.custom(start, end)
        return cls.from_key(params.get("period"))

    @classmethod
    def custom(cls, start: dt.datetime, end: dt.datetime) -> Period:
        last = (end - dt.timedelta(seconds=1)).date()
        label = f"{start:%Y-%m-%d} → {last}"
        return cls(key=CUSTOM_PERIOD, label=label, start=start, end=end)

    @property
    def last_day(self) -> dt.date:
        """The inclusive final day of the window, as shown in date inputs."""
        return (self.end - dt.timedelta(seconds=1)).date()

    def as_query(self) -> str:
        """URL query fragment that reproduces this period."""
        if self.is_custom:
            return f"period=custom&from={self.start:%Y-%m-%d}&to={self.last_day}"
        return f"period={self.key}"

    def clamped_to(self, max_days: int) -> Period:
        """The most recent ``max_days`` slice of this period."""
        start = max(self.start, self.end - dt.timedelta(days=max_days))
        return Period(key=self.key, label=self.label, start=start, end=self.end)

    @classmethod
    def choices(cls):
        return [(key, label) for key, (label, _delta) in _PERIODS.items()]


def _parse_date(raw) -> dt.datetime | None:
    try:
        parsed = dt.datetime.strptime((raw or "").strip(), "%Y-%m-%d")
    except ValueError:
        return None
    return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed
