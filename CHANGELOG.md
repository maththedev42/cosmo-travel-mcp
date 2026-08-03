# Changelog

All notable changes to cosmo-travel-mcp.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.0] - 2026-08-03

### Added

- **A `plan-a-trip` skill** (`skills/plan-a-trip/`). The `plan_trip` prompt
  says which tool to call; nothing said how to be right. The skill carries ten
  method rules, each one a mistake made while planning a real 15-day, 3-city
  trip: never compare entry doors on a single date, quote every candidate on
  the same day, derive nights from flights rather than typing them, and so on.

- **A price watch** (`skills/plan-a-trip/watch.py`). Standard library only, so
  a scheduler runs it with the system Python. It re-prices every unpurchased
  leg against a stored baseline and says which signal fired: `price_history`
  is the floor that exact date has had, `typical_price_range` is the route
  across the year, and the two are worded differently because they are not the
  same claim. A leg with neither is reported *unmeasured*, never *fine*. It
  skips its own run when the remaining quota would fall under a reserve.

  It also watches shows: a fare oscillates and you ask *is it low*, but a show
  is published and then sells out, so event watches report arrivals and never
  prices.

- **Per-item timezones in `build_calendar`.** Set `timezone_name` on an item
  and a Porto Alegre departure, an Orlando show and a New York flight each
  render in their own zone inside one file. The call-level argument is now
  only the default for items that omit one.

### Fixed

- **`check_itinerary` passed stops nobody had checked.** A scheduled stop with
  no `operating_hours` skipped every check and was counted as clean, while the
  docstring promised the opposite: "an empty findings list means every stop was
  verifiable". It bit hardest on the stops an itinerary is built around, since
  `search_events` returns a date and a venue but no hours — a New Year's Eve
  street party, a concert and a theatre show all came back verified when
  nothing had looked at them. Such a stop is now an `unchecked` finding with
  reason `hours_missing`. An entry with no start time stays quiet: it is a
  note, not a claim about opening.

  Seven tests were encoding the old behaviour by passing coordinates without
  hours. They now supply an always-open map, so each still tests the one thing
  it names.

- **Anchoring a whole calendar to one timezone.** `build_calendar` took a
  single `timezone_name` for the file, so a multi-city trip showed every event
  in one city's clock — a 01:35 departure from Porto Alegre rendered as 01:35
  in New York. See the per-item zone above.

### Fixed

- **The booking phase returned nothing usable.** `_parse_booking_options` read
  `book_with` and `price` off the outer dict, but the engine nests every option
  one level down under a slot key naming how the ticket is sold — `together`
  for a single booking, `departing`/`returning` when the directions are sold
  apart. Every entry came back with a blank seller and a null price, which is
  exactly the shape of "this ticket has no sellers". The answer to *where do I
  buy this* was unavailable for the whole 1.0 line.

  The tests could not catch it: their fixtures were hand-written in the same
  flat shape the parser assumed, so they asserted `seller == "Delta"` against
  bytes the engine never sends and stayed green. They are now driven by a
  recorded live response (`tests/fixtures/google_flights_booking.json`), and an
  unrecognized container yields no row rather than a blank one — a caller can
  act on an empty list, but will present a blank seller as fact.

- The `bags` parameter is carry-on only, which the docstring did not say.
  Callers read it as "bags" and priced trips without hold luggage.

### Added

- Booking options now carry `fare` (Basic Economy, Main Cabin, ...),
  `fare_conditions` and `sold_as`. Three rows for one flight at three prices
  look like a caller bug until the fare tier explains them.

- **Checked-bag prices.** The booking response's `baggage_prices` is now
  surfaced, both per option and for the itinerary. This is the only place the
  engine prices hold luggage — `bags` filters carry-on and nothing else.

- `compare_drive_or_fly` converts tolls into the caller's currency. Tolls come
  back in the road's local currency, so the total silently dropped them
  whenever it differed from the one fuel was priced in. Pass `fx_rate` to make
  it deterministic, or let the tool fetch a daily ECB reference rate.

- `compare_drive_or_fly` reports `rental_breakeven` when no rental cost is
  given: the most the car can cost and still beat flying. The rental is
  normally the unknown — the caller is deciding whether to rent at all — so
  requiring it to run the comparison forced a guess into the answer.

- Every flight and accommodation response echoes `adults`. `search_flights`
  defaults to 1 and `search_accommodations` to 2; comparing the two without
  noticing doubles one side of the trip budget and reads as a data error.

## [1.0.1] - 2026-08-02

### Changed

- The PyPI summary and keywords still described the six-tool server: they
  named flights, accommodations and driving, and omitted events, things to do,
  itinerary checking and calendar export. That summary is the one line shown
  on the project page before anyone decides to install, and the keywords are
  what PyPI search matches on.

### Fixed

- The version tests asserted against the literal `"1.0.0"`, so every release
  broke its own suite, and `test_server_version_matches_package` was not
  comparing the server to the package at all — it compared a constant to
  itself and passed just as happily once the two had drifted. It now reads the
  declared version from `pyproject.toml`.

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

[1.0.1]: https://github.com/maththedev42/cosmo-travel-mcp/releases/tag/v1.0.1
[1.0.0]: https://github.com/maththedev42/cosmo-travel-mcp/releases/tag/v1.0.0
