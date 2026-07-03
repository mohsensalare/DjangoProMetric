"""URL introspection: discover every route of the host project.

Walks the project's URL resolver recursively and describes each endpoint,
separating DRF API endpoints from regular Django views, and translating
Django path/regex patterns into human-readable paths and SQL-``LIKE`` style
wildcards that analytics providers can filter on.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from django.urls import Resolver404, URLPattern, URLResolver, get_resolver, resolve

from .conf import get_config

GROUP_API = "api"
GROUP_PAGE = "page"
GROUP_ADMIN = "admin"

_HIDDEN_METHODS = {"options", "head", "trace"}

# Matches path converters such as <int:pk> or <slug>.
_CONVERTER_RE = re.compile(r"<(?:[^:<>]+:)?([^:<>]+)>")


def drf_installed() -> bool:
    try:
        import rest_framework  # noqa: F401
    except ImportError:
        return False
    return True


@dataclass
class RouteInfo:
    route: str  # raw joined pattern, comparable with ResolverMatch.route
    display: str  # human readable path, e.g. /api/users/<pk>/
    wildcard: str  # provider filter pattern, e.g. /api/users/%
    name: str | None  # namespaced URL name, e.g. "shop:order-detail"
    view: str  # dotted representation of the view
    group: str  # api | page | admin
    methods: list = field(default_factory=list)
    is_dynamic: bool = False

    @property
    def key(self) -> str:
        """Stable identifier used in dashboard URLs."""
        return hashlib.md5(self.route.encode()).hexdigest()[:12]


def _describe_pattern(pattern) -> str:
    """Return a readable path fragment for a RoutePattern or RegexPattern."""
    raw = str(pattern)
    if not raw.startswith("^") and "(?P<" not in raw and "(" not in raw:
        # RoutePattern (path()) — already readable, keep converters.
        return raw
    return _simplify_regex(raw)


def _simplify_regex(raw: str) -> str:
    """Best-effort translation of a URL regex into a readable fragment."""
    text = raw.lstrip("^").rstrip("$")
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "(":
            group, i = _consume_group(text, i)
            named = re.match(r"\(\?P<([^>]+)>", group)
            if named:
                out.append(f"<{named.group(1)}>")
            elif group.startswith("(?:") or group.startswith("(?"):
                # Non-capturing/flag group: keep its simplified content.
                inner = group[group.index(":") + 1 : -1] if ":" in group[:4] else ""
                out.append(_simplify_regex(inner))
            else:
                out.append("<arg>")
        elif ch == "\\":
            if i + 1 < len(text):
                out.append(text[i + 1])
            i += 2
            continue
        elif ch in "?*+|":
            pass  # quantifiers/alternation markers are noise in a path
        elif ch == "[":
            # Character class → generic placeholder.
            end = text.find("]", i + 1)
            out.append("<arg>")
            i = (end if end != -1 else len(text) - 1) + 1
            # Skip a quantifier right after the class.
            if i < len(text) and text[i] in "?*+":
                i += 1
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _consume_group(text: str, start: int):
    """Return (group_including_parens, index_after_group_and_quantifier)."""
    depth = 0
    i = start
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                i += 1
                # Swallow a trailing quantifier such as ? * +  {1,3}
                if i < len(text) and text[i] in "?*+":
                    i += 1
                elif i < len(text) and text[i] == "{":
                    close = text.find("}", i)
                    if close != -1:
                        i = close + 1
                return text[start:i], i
        i += 1
    return text[start:], len(text)


def _to_wildcard(display: str) -> str:
    """Convert a display path into a SQL-LIKE pattern (``%`` wildcards)."""
    wildcard = _CONVERTER_RE.sub("%", display)
    return re.sub(r"%+", "%", wildcard)


def _view_group(callback, namespaces) -> str:
    module = getattr(callback, "__module__", "") or ""
    if "admin" in namespaces or module.startswith("django.contrib.admin"):
        return GROUP_ADMIN
    view_class = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
    if view_class is not None and drf_installed():
        from rest_framework.views import APIView

        if issubclass(view_class, APIView):
            return GROUP_API
    return GROUP_PAGE


def _view_methods(callback) -> list:
    actions = getattr(callback, "actions", None)  # DRF ViewSet routing
    if actions:
        return sorted(m.upper() for m in actions if m not in _HIDDEN_METHODS)
    view_class = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
    if view_class is not None:
        methods = [
            m
            for m in getattr(view_class, "http_method_names", [])
            if m not in _HIDDEN_METHODS and hasattr(view_class, m)
        ]
        return sorted(m.upper() for m in methods)
    return []


def _view_label(callback) -> str:
    view_class = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
    target = view_class or callback
    module = getattr(target, "__module__", "")
    name = getattr(target, "__qualname__", getattr(target, "__name__", repr(target)))
    return f"{module}.{name}" if module else name


def collect_routes(urlconf=None) -> list:
    """Return a RouteInfo for every URL endpoint in the project."""
    resolver = get_resolver(urlconf)
    routes = []
    _walk(resolver, "", [], routes)
    return routes


def _walk(resolver, prefix, namespaces, routes):
    for entry in resolver.url_patterns:
        if isinstance(entry, URLResolver):
            ns = namespaces + ([entry.namespace] if entry.namespace else [])
            _walk(entry, prefix + str(entry.pattern), ns, routes)
        elif isinstance(entry, URLPattern):
            route = prefix + str(entry.pattern)
            display = "/" + _describe_pattern_joined(prefix, entry.pattern)
            name = entry.name
            if name and namespaces:
                name = ":".join(namespaces + [name])
            routes.append(
                RouteInfo(
                    route=route,
                    display=display,
                    wildcard=_to_wildcard(display),
                    name=name,
                    view=_view_label(entry.callback),
                    group=_view_group(entry.callback, namespaces),
                    methods=_view_methods(entry.callback),
                    is_dynamic="<" in display,
                )
            )


def _describe_pattern_joined(prefix: str, pattern) -> str:
    described_prefix = _simplify_regex(prefix) if _looks_like_regex(prefix) else prefix
    return described_prefix + _describe_pattern(pattern)


def _looks_like_regex(fragment: str) -> bool:
    return any(token in fragment for token in ("^", "(?P<", "\\", "(?:"))


def filter_routes(routes, mode=None, include=None, exclude=None, exclude_admin=None):
    """Apply the configured include/exclude regex rules to a route list.

    Returns ``(routes, errors)`` where *errors* lists invalid regexes that
    were skipped, so the UI can surface them instead of crashing.
    """
    cfg = get_config()["ROUTES"]
    mode = mode if mode is not None else cfg["MODE"]
    include = include if include is not None else cfg["INCLUDE"]
    exclude = exclude if exclude is not None else cfg["EXCLUDE"]
    exclude_admin = exclude_admin if exclude_admin is not None else cfg["EXCLUDE_ADMIN"]

    errors = []

    def compile_all(patterns):
        compiled = []
        for raw in patterns:
            try:
                compiled.append(re.compile(raw))
            except re.error as exc:
                errors.append(f"{raw!r}: {exc}")
        return compiled

    include_re = compile_all(include)
    exclude_re = compile_all(exclude)

    result = []
    for route in routes:
        if exclude_admin and route.group == GROUP_ADMIN:
            continue
        if mode == "include":
            if not any(rx.search(route.display) for rx in include_re):
                continue
        elif mode == "exclude":
            if any(rx.search(route.display) for rx in exclude_re):
                continue
        result.append(route)
    return result, errors


def match_path(path: str) -> str | None:
    """Resolve a request path to its raw route pattern, or None.

    Used to attribute analytics paths (e.g. from Cloudflare) to Django
    routes. Falls back to the APPEND_SLASH variant.
    """
    for candidate in (path, path + "/") if not path.endswith("/") else (path,):
        try:
            return resolve(candidate).route
        except Resolver404:
            continue
        except Exception:  # noqa: BLE001 — never let a weird path break reports
            return None
    return None


@dataclass
class RouteTotals:
    """Analytics summed over every concrete path a route served."""

    requests: int = 0
    bandwidth_bytes: int = 0
    visits: int = 0
    paths: int = 0  # number of distinct concrete paths


def attribute(path_stats, routes):
    """Group per-path analytics by the Django route each path resolves to.

    ``path_stats`` is any iterable of objects with ``path``, ``requests``,
    ``bandwidth_bytes`` and ``visits`` attributes (see providers.base).
    Returns ``(totals, unmatched)``: totals maps ``RouteInfo.key`` to
    :class:`RouteTotals`, unmatched keeps the stats no route claimed.
    """
    by_route = {route.route: route for route in routes}
    totals = {}
    unmatched = []
    for stat in path_stats:
        matched = match_path(stat.path)
        route = by_route.get(matched) if matched else None
        if route is None:
            unmatched.append(stat)
            continue
        bucket = totals.setdefault(route.key, RouteTotals())
        bucket.requests += stat.requests
        bucket.bandwidth_bytes += stat.bandwidth_bytes
        bucket.visits += stat.visits
        bucket.paths += 1
    return totals, unmatched
