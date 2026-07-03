"""Small display filters used by the dashboard templates."""

from django import template

register = template.Library()


@register.filter
def flag(country_code):
    """'AE' → 🇦🇪. Non-ISO values (e.g. Cloudflare's 'T1') render nothing."""
    code = (country_code or "").upper()
    if len(code) != 2 or not code.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in code)


@register.filter
def pct(value):
    """0.423 → '42%'."""
    if value is None:
        return "—"
    return f"{round(value * 100)}%"


@register.filter
def num(value):
    """1234567 → '1,234,567'."""
    if value is None:
        return "—"
    return f"{value:,}"
