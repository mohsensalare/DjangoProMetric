"""The capability vocabulary shared by providers and dashboard components.

A provider advertises what it can answer
(:meth:`~django_prometric.providers.base.AnalyticsProvider.capabilities`);
dashboard components declare what they need and are hidden or locked when no
configured provider offers it.
"""

OVERVIEW = "overview"
TIMESERIES = "timeseries"
PATHS = "paths"
COUNTRY = "country"
STATUS = "status"
CACHE = "cache"
METHOD = "method"
PERFORMANCE = "performance"
SLOWEST = "slowest"  # per-route response-time percentiles, slowest first
ISSUES = "issues"  # grouped application errors
SECURITY = "security"  # firewall mitigations: blocks, challenges, attack sources
BOTS = "bots"  # human vs automated traffic, crawler categories
SEO = "seo"  # search-engine crawler activity: engines, crawled pages
NETWORK = "network"  # protocol mix: HTTP versions, TLS versions
AUDIENCE = "audience"  # real users' browsers, operating systems, devices
QUERIES = "queries"  # slowest database queries, app-side
BACKEND = "backend"  # where request time goes: database, templates, upstream calls
INSIGHTS = "insights"  # actionable findings derived from the data
DATABASE = "database"  # database-level health overview: size, connections, counters
TABLES = "tables"  # biggest / hottest tables, with bloat
INDEXES = "indexes"  # unused & most-used indexes
BANDWIDTH = "bandwidth"
UNIQUES = "uniques"
THREATS = "threats"
VISITS = "visits"

# Breakdown dimensions accepted by AnalyticsProvider.get_breakdown().
DIM_COUNTRY = "country"
DIM_STATUS = "status"
DIM_CACHE = "cache"
DIM_METHOD = "method"
