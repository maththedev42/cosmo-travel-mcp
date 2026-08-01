# Changelog

All notable changes to cosmo-travel-mcp.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`search_events`** — concerts, shows, sports and festivals at a destination,
  with venue, dates and ticket links. Optional `when` window (today, weekend,
  next_week…).
- **`get_accommodation_details`** — drill into one property from a
  `property_token`: amenities, star distribution, per-category review
  sentiment, images and per-source prices.
- **Price insights on flight results** — buy-timing advice, whether current
  prices are low/typical/high for the route, the recent low, the typical
  range, and a ~60-day price history. Carbon-emission figures per option.
- **Flight filters** — include/exclude airlines (or alliances), carry-on bag
  count, maximum itinerary duration, departure/arrival hour windows, and
  deep search.
- **Hotel filters** — sort order, minimum rating, hotel class, free
  cancellation.
- **Booking phase** — a `booking_token` from a phase-2 result returns the
  sellers offering that itinerary with their prices, so "which site is
  cheapest" is answerable.
- **Low-quota warning** — once fewer than ten SerpAPI searches remain on the
  plan, every search response carries a `quota_warning`. The account is
  checked once per session (free, no quota spent) and counted down locally.
- **Setup for other MCP clients** — `cosmo-travel-mcp setup --client
  <cursor|claude-desktop|windsurf|vscode|cline>` prints a ready-to-paste
  config block and the file it belongs in, so registration is no longer
  Claude-Code-only.
- **`check_itinerary`** — checks a drafted plan before it reaches the traveller:
  stops scheduled on a closing day, visits outside opening hours, overlapping
  stops, and gaps too short to cross the distance between them. Returns
  findings (`blocker` / `warning` / `unchecked`), never prose — how they are
  shown is the client's business. Hours it cannot read are reported as
  `unchecked` rather than assumed open. Costs nothing.
- **`build_calendar`** — emits an RFC 5545 `.ics` and a Google Calendar link
  per event, with escaping, line folding and deterministic UIDs so a re-import
  does not duplicate. Times are floating local wall-clock. It cannot write to
  a calendar itself: when a calendar MCP is connected the AI is told to use it
  and to confirm first, and otherwise to present the links. Costs nothing.
- **Event coverage sweep** — `search_events` gains `pages` (1–5) and
  `also_search` (up to 6 extra query angles), deduplicated, with
  `searches_used` reported. One query returns one slice of Google's corpus:
  measured live on Porto Alegre, a default call found 9 events where a sweep
  found 20, including a local race and tribute shows that no single phrasing
  surfaced. Barren angles no longer sink the sweep.
- **`search_things_to_do`** — what to do in a city: attractions, museums,
  parks, landmarks, shopping, nightlife, and food (restaurants, cafés, bars).
  Each result carries per-weekday `operating_hours` and `coordinates`, so an
  itinerary can avoid scheduling a stop on its closing day and can group
  nearby stops into the same day. Food categories additionally return price
  range, a short description, service options and a reservation link.
- **`plan_trip` rebuilt as a full itinerary guide** — the prompt now covers
  every tool the server exposes (it had silently omitted `search_events` and
  `get_accommodation_details` since they were added) and gains an assembly
  section: fix the skeleton from flight times, anchor date-bound events,
  cluster by coordinates, respect opening hours, and never invent a detail no
  tool returned.
- **Toll estimates in `compare_drive_or_fly`** — driving cost now includes
  tolls, fetched from the Routes API `computeRoutes` endpoint. When the toll
  currency matches the fuel currency they are folded into the total;
  otherwise they are reported separately. Toll-free routes and unavailable
  toll data leave the result shape unchanged.
- **Session response cache** — identical SerpAPI searches repeated within ten
  minutes are served from memory, marked `cached: true`, and cost no quota.
  Tune or disable with `COSMO_TRAVEL_CACHE_TTL` (seconds; `0` disables).
- Captured SerpAPI response fixtures under `tests/fixtures/`, with the rule
  that shape assertions use real recorded bodies rather than invented ones.

### Fixed

- `search_events` raised `ValueError` instead of returning an empty result
  when a query matched nothing. SerpAPI reports "nothing found" as an `error`
  body rather than an empty array, and the tests mocked an empty array — a
  shape the engine never sends for that case — so the real path crashed.
- Buy advice was ungrammatical whenever SerpAPI omitted a field — the common
  case — producing output like `Prices are currently high for this route: .`
- A price-insights payload carrying only a price history was discarded whole.
- Absent carbon-emission figures were reported as `0 kg`, asserting a flight
  emitted nothing; a null figure crashed the search outright and silently
  dropped sampled dates from `search_cheapest_dates`.
- `get_accommodation_details` omitted the `q` parameter the engine requires
  alongside a `property_token` (HTTP 400), and read its response from a
  `property` wrapper the engine does not return — the tool could not have
  worked. It also read a `rating_breakdown` field that does not exist.
- Event addresses were read from the venue object instead of the event, so
  they were always missing.
- Locations with a space in the name — "New York", "Porto Alegre" — never
  received the events-search prefix.
- `free_cancellation` is now refused for vacation rentals, where the engine
  ignores it, instead of being silently dropped.
- Departure/arrival hour windows were validated and then transmitted
  unparsed, so `"18, 23"` reached the engine with a leading space.

## [1.0.0] - 2026-07-31

### Added

- **Six MCP tools** powered by licensed commercial data:
  - `search_flights` — one-way and round-trip flight search via SerpAPI.
  - `search_multi_city` — multi-city itineraries with 2–6 legs.
  - `search_accommodations` — hotels and vacation rentals via SerpAPI Google Hotels engine.
  - `search_cheapest_dates` — cheapest round-trip sampling across a flexible date window.
  - `compare_drive_or_fly` — driving distance/duration via Google Maps Routes API with optional flight comparison.
  - `check_setup` — validates both API keys live and reports quota status and remediation hints.
- **`plan_trip` MCP prompt** — starter entry point for AI clients.
- **Setup CLI** (`cosmo-travel-mcp setup` and `setup --register`) with key validation, hidden input, and Claude Code registration.
- **Onboarding** — `check_setup` returns a `setup` field with exact remediation commands; server instructions and version exposed at startup.
- **Quota-cost transparency** — per-tool cost table in README, multi-city/round-trip pricing notes, and cheapest-dates quota guard (`max_calls` cap).
- **Transient-failure retry** — `_call_serpapi` retries once on transient SerpAPI errors.
- **CI workflow** (`.github/workflows/ci.yml`) — dual-env test matrix (no keys + fake keys), `uv` caching.
- **Trusted-publishing workflow** (`.github/workflows/publish.yml`) — PyPI publish via OIDC on `v*` tags, no stored API tokens.
- **Contributor onboarding** — `CONTRIBUTING.md`, `docs/EXAMPLES.md`, issue templates, PR template.
- **Release runbook** — `docs/RELEASING.md` with one-time PyPI trusted-publisher setup and per-release tag flow.

### Fixed

- Three latent correctness bugs found in review (early 2026-07 cycle).
- README no longer recommends `export` for MCP use — keys must be passed via `-e` at registration time.
- Server registration now uses the installed binary path, not `uvx` (which exceeded the 30-second MCP startup budget).
- Missing-key tool errors are now self-sufficient with inline remediation instructions.
- Production defects from the initial scaffold (01b cycle) repaired.

[1.0.0]: https://github.com/maththedev42/cosmo-travel-mcp/releases/tag/v1.0.0
