"""URL introspection: discover every route of the host project.

Walks the project's URL resolver recursively and describes each endpoint,
separating DRF API endpoints from regular Django views, and translating
Django path/regex patterns into human-readable paths and SQL-``LIKE`` style
wildcards that analytics providers can filter on.

Projects using ``i18n_patterns`` get one route per endpoint — not one per
language. The language prefix is shown as ``<lang>`` in the display path,
keys stay stable whatever language is active, and analytics paths of every
language are attributed to the same route.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from django.conf import settings
from django.urls import URLPattern, URLResolver, get_resolver
from django.urls.resolvers import LocalePrefixPattern
from django.utils.module_loading import import_string

from .conf import get_config

GROUP_API = "api"
GROUP_PAGE = "page"
GROUP_ADMIN = "admin"

_HIDDEN_METHODS = {"options", "head", "trace"}

# Matches path converters such as <int:pk> or <slug>.
_CONVERTER_RE = re.compile(r"<(?:[^:<>]+:)?([^:<>]+)>")

LANG_PLACEHOLDER = "<lang>"


def drf_installed() -> bool:
    try:
        import rest_framework  # noqa: F401
    except ImportError:
        return False
    return True


@dataclass
class RouteInfo:
    route: str  # raw joined pattern; language-neutral, the key is derived from it
    display: str  # human readable path, e.g. /<lang>/api/users/<pk>/
    wildcard: str  # provider filter pattern, e.g. /api/users/%
    name: str | None  # namespaced URL name, e.g. "shop:order-detail"
    view: str  # dotted representation of the view
    group: str  # api | page | admin
    methods: list = field(default_factory=list)
    is_dynamic: bool = False
    # Concrete path patterns providers can filter on — one per language for
    # i18n routes; ``%`` marks a wildcard segment.
    path_patterns: list = field(default_factory=list)
    # Full-path regex, independent of the active language.
    matcher: re.Pattern | None = field(default=None, repr=False, compare=False)

    @property
    def key(self) -> str:
        """Stable identifier used in dashboard URLs."""
        return hashlib.md5(self.route.encode()).hexdigest()[:12]

    def matches(self, path: str) -> bool:
        """Whether a concrete request path belongs to this route."""
        if self.matcher is None:
            return False
        candidate = path.lstrip("/")
        if self.matcher.match(candidate):
            return True
        # The APPEND_SLASH redirect target counts as the same route.
        return not path.endswith("/") and bool(self.matcher.match(candidate + "/"))


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


def _admin_patterns() -> list:
    """Compiled ADMIN_PATTERNS from settings; invalid ones are ignored here
    and reported by :func:`filter_routes`."""
    compiled = []
    for raw in get_config()["ROUTES"]["ADMIN_PATTERNS"]:
        try:
            compiled.append(re.compile(raw))
        except re.error:
            continue
    return compiled


def _view_group(callback, namespaces, display, admin_res) -> str:
    module = getattr(callback, "__module__", "") or ""
    top_package = module.split(".", 1)[0]
    if (
        "admin" in namespaces
        or module.startswith("django.contrib.admin")
        # Admin skins (grappelli, admin_volt, jazzmin, …) live in packages
        # named after the admin they restyle.
        or "admin" in top_package.lower()
        or any(rx.search(display) for rx in admin_res)
    ):
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


def _language_codes() -> list:
    return [code for code, _name in settings.LANGUAGES]


def _pattern_regex(pattern) -> str:
    """This level's regex source, with the anchors of inner levels removed."""
    return pattern.regex.pattern.lstrip("^")


@dataclass
class _Walk:
    """Per-level state carried down the resolver tree."""

    prefix: str = ""  # joined str(pattern) of the levels above
    regex: str = ""  # joined regex sources of the levels above
    namespaces: list = field(default_factory=list)
    langs: list = field(default_factory=list)  # non-empty under i18n_patterns
    lang_optional: bool = False  # True when the default language has no prefix


def collect_routes(urlconf=None) -> list:
    """Return a RouteInfo for every URL endpoint in the project."""
    resolver = get_resolver(urlconf)
    routes = []
    _walk(resolver, _Walk(), routes, _admin_patterns())
    return routes


def _walk(resolver, state: _Walk, routes, admin_res):
    for entry in resolver.url_patterns:
        if isinstance(entry, URLResolver):
            if isinstance(entry.pattern, LocalePrefixPattern):
                # One subtree for all languages: mark it instead of joining
                # the active language's prefix into the route string.
                inner = _Walk(
                    prefix=state.prefix,
                    regex=state.regex,
                    namespaces=state.namespaces,
                    langs=_language_codes(),
                    lang_optional=not entry.pattern.prefix_default_language,
                )
                _walk(entry, inner, routes, admin_res)
                continue
            inner = _Walk(
                prefix=state.prefix + str(entry.pattern),
                regex=state.regex + _pattern_regex(entry.pattern),
                namespaces=state.namespaces + ([entry.namespace] if entry.namespace else []),
                langs=state.langs,
                lang_optional=state.lang_optional,
            )
            _walk(entry, inner, routes, admin_res)
        elif isinstance(entry, URLPattern):
            view = _view_label(entry.callback)
            if view.startswith("django_prometric."):
                continue  # the dashboard doesn't report on itself
            routes.append(_route_info(entry, state, view, admin_res))


def _route_info(entry, state: _Walk, view: str, admin_res) -> RouteInfo:
    raw = state.prefix + str(entry.pattern)
    core = _describe_pattern_joined(state.prefix, entry.pattern)
    if state.langs:
        route = f"{LANG_PLACEHOLDER}/{raw}"
        display = f"/{LANG_PLACEHOLDER}/{core}"
        patterns = [_to_wildcard(f"/{lang}/{core}") for lang in state.langs]
        if state.lang_optional:
            patterns.append(_to_wildcard(f"/{core}"))
    else:
        route = raw
        display = f"/{core}"
        patterns = [_to_wildcard(display)]

    name = entry.name
    if name and state.namespaces:
        name = ":".join(state.namespaces + [name])
    return RouteInfo(
        route=route,
        display=display,
        wildcard=patterns[0],
        name=name,
        view=view,
        group=_view_group(entry.callback, state.namespaces, display, admin_res),
        methods=_view_methods(entry.callback),
        is_dynamic="<" in display,
        path_patterns=patterns,
        matcher=_compile_matcher(state, entry),
    )


def _compile_matcher(state: _Walk, entry) -> re.Pattern | None:
    """A full-path regex for this endpoint, valid for every language."""
    lang_fragment = ""
    if state.langs:
        alternatives = "|".join(re.escape(code) for code in state.langs)
        lang_fragment = f"(?:{alternatives})/"
        if state.lang_optional:
            lang_fragment = f"(?:{lang_fragment})?"
    try:
        return re.compile(lang_fragment + state.regex + _pattern_regex(entry.pattern))
    except re.error:
        return None


def _describe_pattern_joined(prefix: str, pattern) -> str:
    described_prefix = _simplify_regex(prefix) if _looks_like_regex(prefix) else prefix
    return described_prefix + _describe_pattern(pattern)


def _looks_like_regex(fragment: str) -> bool:
    return any(token in fragment for token in ("^", "(?P<", "\\", "(?:"))


def filter_routes(routes, mode=None, include=None, exclude=None, exclude_admin=None):
    """Apply the configured route-selection rules to a route list.

    Returns ``(routes, errors)`` where *errors* lists invalid regexes and
    filter failures that were skipped, so the UI can surface them instead of
    crashing.
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
    compile_all(cfg["ADMIN_PATTERNS"])  # report broken admin patterns too

    accept = None
    if cfg["FILTER"]:
        try:
            accept = import_string(cfg["FILTER"])
        except ImportError as exc:
            errors.append(f"ROUTES['FILTER']: {exc}")

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
        if accept is not None and not accept(route.display):
            continue
        result.append(route)
    return result, errors


@dataclass
class RouteTotals:
    """Analytics summed over every concrete path a route served."""

    requests: int = 0
    bandwidth_bytes: int = 0
    visits: int = 0
    paths: int = 0  # number of distinct concrete paths


def attribute(path_stats, routes):
    """Group per-path analytics by the Django route each path belongs to.

    ``path_stats`` is any iterable of objects with ``path``, ``requests``,
    ``bandwidth_bytes`` and ``visits`` attributes (see providers.base).
    Returns ``(totals, unmatched)``: totals maps ``RouteInfo.key`` to
    :class:`RouteTotals`, unmatched keeps the stats no route claimed.

    Matching uses each route's own compiled pattern, so it works for every
    language of an ``i18n_patterns`` project no matter which one is active.
    """
    exact = {}  # concrete pattern -> route, so static paths skip the regex scan
    dynamic = []
    for route in routes:
        if route.is_dynamic:
            dynamic.append(route)
        else:
            for pattern in route.path_patterns:
                exact.setdefault(pattern, route)

    totals = {}
    unmatched = []
    for stat in path_stats:
        route = (
            exact.get(stat.path)
            or exact.get(stat.path + "/")
            or next((r for r in dynamic if r.matches(stat.path)), None)
        )
        if route is None:
            unmatched.append(stat)
            continue
        bucket = totals.setdefault(route.key, RouteTotals())
        bucket.requests += stat.requests
        bucket.bandwidth_bytes += stat.bandwidth_bytes
        bucket.visits += stat.visits
        bucket.paths += 1
    return totals, unmatched


# -- matching Sentry-style transactions to routes ---------------------------


def _normalize_transaction(transaction: str) -> str:
    """Strip the scheme://host prefix Sentry keeps in URL-named transactions."""
    if "://" in transaction:
        rest = transaction.split("://", 1)[1]
        return "/" + (rest.split("/", 1)[1] if "/" in rest else "")
    return transaction


def _segments_match(pattern_segments, path_segments) -> bool:
    if len(pattern_segments) != len(path_segments):
        return False
    return all(
        expected == "*" or actual == expected
        for expected, actual in zip(pattern_segments, path_segments)
    )


def matching_transactions(route, transactions):
    """The transactions that belong to this route, busiest first.

    A transaction such as ``https://*/en/project/Projects/*/comments/``
    matches the route ``/<lang>/project/Projects/<pk>/comments/``: dynamic
    segments (converters and the language prefix) accept anything.
    """
    pattern = _CONVERTER_RE.sub("*", route.display).split("/")
    matched = [
        tx
        for tx in transactions
        if tx.transaction == route.view
        or tx.transaction == route.name
        or _segments_match(pattern, _normalize_transaction(tx.transaction).split("/"))
    ]
    return sorted(matched, key=lambda tx: tx.requests, reverse=True)


def route_for_transaction(transaction: str, routes):
    """The route a Sentry-style transaction name belongs to, or None."""
    segments = _normalize_transaction(transaction).split("/")
    for route in routes:
        if transaction in (route.view, route.name):
            return route
        if _segments_match(_CONVERTER_RE.sub("*", route.display).split("/"), segments):
            return route
    return None
