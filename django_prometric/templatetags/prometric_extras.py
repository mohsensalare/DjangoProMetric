"""Small display filters used by the dashboard templates."""

import re

from django import template
from django.template.defaultfilters import filesizeformat
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()

_CONVERTER_RE = re.compile(r"(<[^>]+>)")


@register.filter
def route_html(display):
    """Highlight ``<param>`` converters inside a route path."""
    parts = _CONVERTER_RE.split(str(display))
    html = "".join(
        f'<span class="pm-param">{escape(part)}</span>' if part.startswith("<") else escape(part)
        for part in parts
    )
    return mark_safe(html)  # noqa: S308 — every part is escaped above


@register.filter
def flag(country_code):
    """'AE' → 🇦🇪. Non-ISO values (e.g. Cloudflare's 'T1') render nothing."""
    code = (country_code or "").upper()
    if len(code) != 2 or not code.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in code)


@register.filter
def status_class(label):
    """'404' → 'warn': the colour bucket for an HTTP status code."""
    try:
        status = int(label)
    except (TypeError, ValueError):
        return ""
    if status >= 500:
        return "err"
    if status >= 400:
        return "warn"
    if status >= 300:
        return "redirect"
    return "ok"


@register.filter
def num(value):
    """1234567 → '1,234,567'."""
    if value is None:
        return "—"
    return f"{value:,}"


@register.filter
def pct(value):
    """0.423 → '42%'."""
    if value is None:
        return "—"
    return f"{round(value * 100)}%"


@register.filter
def delta(value):
    """0.5 → '+50%', -0.04 → '−4%': relative change between snapshot runs."""
    if value is None:
        return ""
    sign = "+" if value >= 0 else "−"
    return f"{sign}{abs(round(value * 100))}%"


@register.filter
def size(value):
    """Bytes → human size, with an em dash for unknown."""
    if value is None:
        return "—"
    return filesizeformat(value)


@register.filter
def ms(value):
    """123.4 → '123 ms'."""
    if value is None:
        return "—"
    return f"{round(value)} ms"
