# Changelog

All notable changes to cosmo-travel-mcp.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-08-02

First public release. Eleven tools and one prompt.

### Added

- **`search_flights`** — one-way and round-trip flight search via SerpAPI.
  Round trips are two-phase: a `departure_token` from a phase-1 result returns
  the return legs priced against that outbound.
- **`search_multi_city`** — multi-city itineraries with 2–6 legs.
- **`search_accommodations`** — hotels and vacation rentals via the Google
  Hotels engine.
- **`search_cheapest_dates`** — cheapest round-trip sampling across a flexible
  date window, with a `max_calls` cap because each sampled date costs a search.
- **`compare_drive_or_fly`** — driving distance and duration via the Google
  Maps Routes API, with optional flight comparison.
- **`check_setup`** — validates both API keys live and reports quota status
  and remediation hints. The SerpAPI half is free.
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
- **`plan_trip` prompt** — the entry point for a whole trip. Sequences all
  eleven tools, states the search budget, and carries an assembly section: fix
  the skeleton from flight times, anchor date-bound events, cluster stops by
  coordinates, respect opening hours, leave slack, and never invent a detail
  no tool returned.
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
- **Setup CLI** — `cosmo-travel-mcp setup` and `setup --register`: key
  validation, hidden input, and Claude Code registration in one command.
  `setup --client <cursor|claude-desktop|windsurf|vscode|cline>` prints a
  ready-to-paste config block and the file it belongs in, so registration is
  not Claude-Code-only.
- **Onboarding** — `check_setup` returns a `setup` field with the exact
  remediation commands, and the server exposes its instructions and version at
  startup. Registration uses the installed binary path, not `uvx`, which
  exceeded the 30-second MCP startup budget.
- **Quota-cost transparency** — a per-tool cost table in the README,
  round-trip and multi-city pricing notes, and the `max_calls` guard on
  `search_cheapest_dates`.
- **Transient-failure retry** — `_call_serpapi` retries once on transient
  SerpAPI errors.
- **CI** (`.github/workflows/ci.yml`) — dual-env test matrix (no keys, fake
  keys) with `uv` caching. 365 tests.
- **Trusted publishing** (`.github/workflows/publish.yml`) — PyPI release via
  OIDC on `v*` tags, with no stored API tokens.
- **Contributor onboarding** — `CONTRIBUTING.md`, `docs/EXAMPLES.md`,
  `docs/RELEASING.md`, issue templates and a PR template.

[1.0.0]: https://github.com/maththedev42/cosmo-travel-mcp/releases/tag/v1.0.0
