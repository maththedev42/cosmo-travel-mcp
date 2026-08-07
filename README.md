[![CI](https://github.com/maththedev42/cosmo-travel-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/maththedev42/cosmo-travel-mcp/actions/workflows/ci.yml)

# cosmo-travel-mcp

One MCP server with twelve travel tools — flight search, multi-city itineraries,
accommodations, things to do, events, car rental offices, drive-vs-fly comparisons,
itinerary checking and calendar export — all backed
by **licensed commercial data** (SerpAPI for flights and hotels, Google Maps Routes
API for driving). Both providers offer a free tier that is sufficient for personal
use: SerpAPI gives 100 searches/month and Google Maps Routes API includes a monthly
credit.

```bash
uv tool install cosmo-travel-mcp
# (until the first PyPI release, use: uv tool install git+https://github.com/maththedev42/cosmo-travel-mcp)
cosmo-travel-mcp setup --register
```

Previously this project relied on a reverse-engineered Google Flights scraper that
started returning HTTP 200 responses that were actually internal error envelopes — a
documented, unresolved bug. Everything here now uses **licensed commercial data
providers** (SerpAPI for flights and accommodations, Google Maps Routes API for
driving).

## Getting started

The fastest path — this prompts for the keys, validates them against the real
APIs, and registers the server with Claude Code:

```bash
uv tool install cosmo-travel-mcp
# (until the first PyPI release, use: uv tool install git+https://github.com/maththedev42/cosmo-travel-mcp)
cosmo-travel-mcp setup --register
```

Drop `--register` to just print the walk-through without changing anything.

> **Don't have the keys yet?** See **[docs/GETTING_KEYS.md](./docs/GETTING_KEYS.md)**
> for the full click-path on both providers, what counts against the free quota,
> and troubleshooting.
>
> **Already registered the server without keys?** Call `check_setup` — it returns
> a `setup` field with the exact commands for your situation.

The rest of this section is the same thing, for reading ahead of time.

### 1. SerpAPI key (flights + accommodations)

1. Create a free account at [serpapi.com](https://serpapi.com/users/sign_up) —
   the free tier includes **100 searches/month**.
2. Copy the private API key from your dashboard.
3. Pass it to the server as `SERPAPI_API_KEY` (see step 3 and the registration
   command below). If the server is already registered without it, remove and
   re-add it — env vars are fixed at registration time:
   ```bash
   claude mcp remove cosmo-travel --scope user
   # then re-run the `claude mcp add` command below, with -e SERPAPI_API_KEY=…
   ```

This one key unlocks eight of the twelve tools: `search_flights`,
`search_multi_city`, `search_accommodations`, `get_accommodation_details`,
`search_cheapest_dates`, `search_events`, `search_things_to_do`, and
`search_car_rentals`.
`check_itinerary` and `build_calendar` need no key at all — they are pure
computation and cost nothing.

> **Important:** `search_cheapest_dates` costs **multiple searches per call** (up to
> `max_calls`, default 6, max 15). Budget accordingly — a single cheapest-dates query
> can burn 6-15 of your 100 free monthly searches.

### 2. Google Maps API key (driving comparison)

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project (or use an existing one).
3. Enable the **Routes API**.
4. Create an API key under "Credentials".
5. **Note:** Google requires a billing account even though there is a generous free
   monthly credit (~$200). The `check_setup` tool makes one real API call to validate
   the key, which costs a fraction of a cent.

### 3. Give the keys to the server

> **`export` is not enough for MCP use.** An MCP client starts this server with
> the environment recorded at registration time — it does not inherit your
> shell. Pass the keys as `-e` flags on `claude mcp add` (see
> [Claude Code registration](#claude-code-registration)), or let
> `cosmo-travel-mcp setup --register` do it.

Exporting only matters when you run the binary yourself, for local development:

```bash
export SERPAPI_API_KEY="your-serpapi-key"
export GOOGLE_MAPS_API_KEY="your-google-maps-key"
```

### 4. Install

```bash
uv tool install cosmo-travel-mcp           # from PyPI (post-v1.0)
# (until the first PyPI release, use: uv tool install git+https://github.com/maththedev42/cosmo-travel-mcp)
uv tool install .                 # or from a local clone (for development)
```

That puts a `cosmo-travel-mcp` binary on your PATH. Add `--force` to upgrade.

> **Do not register `uvx --from git+…` as the launch command.** uvx re-resolves
> the git dependency every time the server starts — measured at over two
> minutes on a cold cache, against the 30-second startup budget an MCP client
> allows a stdio server. It will be reported as *Failed to connect*. Install
> the tool once and register the resulting binary.

### 5. Verify setup

Call `check_setup` first — it confirms both keys work before you spend quota.
Example output when both keys are valid:

```
search_flights: ready (87 searches left this month)
search_multi_city: ready (87 searches left this month)
search_accommodations: ready (87 searches left this month)
search_cheapest_dates: ready (87 searches left; each call costs up to max_calls searches (default 6, hard cap 15))
compare_drive_or_fly: ready (Maps key valid)
```

When a key is missing, the relevant tools show `NOT ready` with a remediation hint.

## Claude Code registration

```bash
uv tool install cosmo-travel-mcp
# (until the first PyPI release, use: uv tool install git+https://github.com/maththedev42/cosmo-travel-mcp)

claude mcp add cosmo-travel --scope user \
  -e SERPAPI_API_KEY=<your-serpapi-key> \
  -e GOOGLE_MAPS_API_KEY=<your-google-maps-key> \
  -- "$(which cosmo-travel-mcp)"
```

Use the absolute path — a client spawns the server without necessarily
inheriting the PATH that makes the bare name resolvable.

Env vars are fixed at registration time, so adding a key later means replacing
the registration:

```bash
claude mcp remove cosmo-travel --scope user
# …then run the add command above
```

`cosmo-travel-mcp setup --register` does all of this for you, including the
remove-first step.

### Other MCP clients

Claude Desktop, Cursor, Windsurf, Cline, and VS Code configure MCP servers the
same conceptual way (JSON block with command + env), differing only in where the
file lives. For these clients, print a ready-to-paste config snippet:

```bash
cosmo-travel-mcp setup --client cursor
```

Pass one of `claude-desktop`, `cursor`, `windsurf`, `cline`, or `vscode`. Each
prints a JSON block with absolute binary path and placeholder env entries; fill
in your real keys and paste into your client's MCP config file. The snippet
includes the file path for your platform.

`--register` stays Claude-Code-only — for other clients we print config, we do
not attempt to edit their config files.

## The `plan-a-trip` skill

The tools tell you *what* is available; `skills/plan-a-trip/SKILL.md` tells an
agent *how to be right* with them. It is a Claude Code skill — clone the repo
and link it, or copy the directory:

```bash
ln -s "$PWD/skills/plan-a-trip" ~/.claude/skills/plan-a-trip
```

It carries ten method rules, each one a mistake made while planning a real
15-day, three-city trip:

- Never compare entry doors on a single date — three doors × three dates found
  a fare R$ 1.300 cheaper and inverted the ranking.
- `price_level` is not `price_history`. Only the 60-day series supports "wait",
  and it is usually absent: 1 of 14 queries returned one.
- Quote every candidate itinerary on the same day. A R$ 2.842 gap between two
  itineraries collapsed to R$ 1.417 once all four were re-quoted in one batch.
- Derive nights from the flights instead of typing them. An overnight arrival
  means the first night is on the plane.
- An undated event query returns what is *near*, not what *exists* — name the
  month and year, and treat "there is nothing on" as a claim needing a control.

It ships two standard-library scripts alongside it.

**`render.py`** turns the researched trip into one self-contained HTML page —
inline CSS and JS, no assets, no server — where the candidate itineraries are
buttons that re-filter the whole document:

```bash
python3 skills/plan-a-trip/render.py trip.json -o dossier.html
```

It reads a single JSON document (`example-trip.json` is a filled-in schema from
a real session) and **derives** what can be derived rather than accepting it
typed: nights from the dates, and which candidates can attend each event from
the lodging windows. It also **asserts** the totals — a candidate whose
`flights_total` disagrees with the sum of its legs fails the render rather than
publishing a page whose header contradicts its own table. Softer mismatches
render *and* print on the page, because a correction the reader cannot see is
not a correction.

**`watch.py`** is an entry point a scheduler can run to re-price everything not
yet bought and alert when a fare enters its low band — saying which signal
fired, since "below the route's normal band" and "below what this date has
cost" are different claims. Point `launchd` or `cron` at it; it is deliberately
not something the MCP server starts on its own.

## Command line

| Command | Effect |
|---|---|
| `cosmo-travel-mcp` | Run the MCP server over stdio. This is what the client invokes; you rarely run it by hand. |
| `cosmo-travel-mcp setup` | Print the key-acquisition guide and the registration command. No side effects. |
| `cosmo-travel-mcp setup --register` | Prompt for keys (hidden input), validate them live, then register the server. Accepts `--scope`, `--name`, `-y`. |
| `cosmo-travel-mcp setup --client <name>` | Print a ready-to-paste JSON config block for a non-Claude-Code MCP client (claude-desktop, cursor, windsurf, cline, vscode). |
| `cosmo-travel-mcp --version` | Print the version. |

## Tools

| Tool | Parameters | Description |
|---|---|---|
| `search_flights` | `origin`, `destination`, `outbound_date`, `return_date?`, `adults?`, `children?`, `cabin_class?`, `max_stops?`, `departure_token?`, `booking_token?`, `currency?`, `country?`, `language?`, `include_airlines?`, `exclude_airlines?`, `bags?`, `max_duration?`, `outbound_times?`, `return_times?`, `deep_search?` | One-way or round-trip flight search via SerpAPI. Phase 1: find cheapest itineraries. Phase 2 (`departure_token`): return-leg options. Phase 3 (`booking_token`): every seller + price for a specific ticket. Returns price insights (lowest price, typical range, buy advice) and per-flight carbon emissions in kg when available. Filter by airline, bags, duration, departure times, or use deep search. |
| `search_multi_city` | `legs` ([{origin, destination, date, times?}…]), `adults?`, `children?`, `cabin_class?`, `currency?`, `country?`, `language?`, `include_airlines?`, `exclude_airlines?`, `bags?`, `max_duration?`, `deep_search?` | Multi-city itinerary with 2-6 legs; airline, bag, duration, and deep-search filters supported |
| `get_accommodation_details` | `property_token`, `location`, `check_in_date`, `check_out_date`, `adults?`, `children?`, `children_ages?`, `currency?`, `country?`, `language?` | Full property details: amenities, star distribution, per-category review sentiment, images, per-source prices. Takes a `property_token` from `search_accommodations`; `location` repeats that search's text, which the engine requires even alongside a token. |
| `search_accommodations` | `location`, `check_in_date`, `check_out_date`, `adults?`, `children?`, `children_ages?`, `vacation_rentals?`, `currency?`, `country?`, `language?`, `min_price?`, `max_price?`, `sort_by?`, `min_rating?`, `hotel_class?`, `free_cancellation?` | Hotels and vacation rentals via SerpAPI Google Hotels engine. Defaults to vacation rentals (Airbnb/Vrbo/Booking.com listings). Set `vacation_rentals=false` for standard hotels. Filters: `sort_by` (lowest_price/highest_rating/most_reviewed), `min_rating` (3.5/4.0/4.5), `hotel_class` (2–5), `free_cancellation`. |
| `search_events` | `query`, `when?`, `also_search?`, `pages?`, `country?`, `language?` | Events (concerts, shows, sports, festivals) at a destination via SerpAPI. One query returns one slice of the corpus, so `pages` (1–5) and `also_search` (up to 6 extra query angles) sweep wider and deduplicate — on Porto Alegre a default call found 9 events where a sweep found 20. **Costs up to `pages × (1 + len(also_search))` searches** — fewer when a page is cached or an angle runs dry — with the actual figure reported as `searches_used`. |
| `search_things_to_do` | `location`, `category?`, `min_rating?`, `limit?`, `country?`, `language?` | What to do in a city, via SerpAPI Google Maps engine. `category` is one of attractions, museums, parks, landmarks, shopping, nightlife, restaurants, cafes, bars (default `attractions`). Each result carries `operating_hours` (per weekday) and `coordinates`, which is what a day-by-day itinerary is built from; food categories add price range, description and a reservation link. Costs 1 search per call. |
| `search_car_rentals` | `location`, `min_rating?`, `limit?`, `country?`, `language?` | Car rental **offices** near a place, via SerpAPI Google Maps engine — locations, per-weekday `operating_hours`, `website` and `phone`. **Returns no rates:** no free provider exposes car rental pricing, so hand the traveller the `website` and treat the rate as unmeasured until they report one back. Its value is choosing *where* to collect: an airport counter typically runs 24 hours while a neighbourhood branch closes on Sundays. Hours are the regular weekly schedule and do not cover holidays — confirm a 25 December pickup on the phone. Costs 1 search per call. |
| `compare_drive_or_fly` | `origin`, `destination`, `fuel_price_per_liter?`, `fuel_efficiency_km_per_liter?`, `rental_car_cost_total?`, `flight_price?`, `flight_duration_minutes?`, `currency?` | Driving distance + duration + toll estimates via Google Maps Routes API. Tolls are fetched from ``computeRoutes`` with ``extraComputations: ["TOLLS"]`` and degrade gracefully when unavailable. Optionally folds in caller-supplied flight numbers for side-by-side comparison. |
| `search_cheapest_dates` | `origin`, `destination`, `earliest_departure`, `latest_return`, `trip_duration_days`, `max_calls?` (default 6, max 15), `adults?`, `children?`, `cabin_class?`, `currency?` | Samples candidate dates across a flexible window and returns cheapest round-trip per date. **Costs up to `max_calls` SerpAPI searches per call.** |
| `check_itinerary` | `days` ([{date, stops:[{name, start, end, operating_hours?, coordinates?}]}]) | Checks a drafted itinerary for conflicts: stops on a closing day, visits outside opening hours, overlapping stops, and gaps too short to cross the distance. Returns findings (`blocker` / `warning` / `unchecked`), not prose. **Costs nothing — no API calls.** |
| `build_calendar` | `items` ([{title, start, end?, location?, description?}]), `calendar_name?`, `timezone_name?` | Generates an RFC 5545 `.ics` plus a Google Calendar link per event. Times are floating local wall-clock. Cannot write to a calendar itself — if a calendar MCP is connected, the AI uses that (with your approval); otherwise it shows the links. **Costs nothing — no API calls.** |
| `check_setup` | _(none)_ | Validates both API keys and reports which tools are ready. The SerpAPI check is free; the Maps check makes one real API call. |

## What each call costs

Every tool call that hits SerpAPI or Google Maps spends quota. The free tiers
(SerpAPI 100 searches/month, Maps ~$200/month credit) are enough for personal use,
but a cheap-seeming prompt like "find the cheapest Saturday in March" can burn a
week of quota if it runs `search_cheapest_dates` at `max_calls=15`.

When the estimated remaining searches drops to **10 or below**, every
SerpAPI-backed tool response gains a `quota_warning` field with the current
estimate.  Call `check_setup` for the exact number — the warning is a
locally-decremented best effort and does not account for concurrent clients.

**Repeated identical searches within 10 minutes are free**: the server caches
successful SerpAPI responses in memory. A cache hit is marked `cached: true`
on the tool response and costs zero searches.  Set the environment variable
`COSMO_TRAVEL_CACHE_TTL` (seconds; `0` disables the cache) at registration
time if you need a different TTL — the default is 600 (10 minutes).

| Tool | SerpAPI searches per call | Maps calls per call | Notes |
|---|---|---|---|
| `search_flights` | 1 | 0 | Phase-2 (return legs) and phase-3 (booking options) calls cost 1 additional search each. |
| `search_multi_city` | 1 | 0 | |
| `search_accommodations` | 1 | 0 | |
| `get_accommodation_details` | 1 | 0 | Drill into a single property from `search_accommodations`. |
| `search_events` | `(1 + len(also_search)) × pages` | 0 | Default call is 1. A coverage sweep (`pages=2`, two extra angles) is 6 — the response reports `searches_used`. |
| `search_things_to_do` | 1 | 0 | One per city, per category. A 3-city trip asking for attractions and food is 6 searches. |
| `search_car_rentals` | 1 | 0 | One per pickup area. Comparing an airport against a downtown branch is 2. |
| `check_itinerary` | 0 | 0 | Pure computation. |
| `build_calendar` | 0 | 0 | Pure computation. |
| `search_cheapest_dates` | up to `max_calls` (default 6, cap 15) | 0 | Each sampled date costs one search. |
| `compare_drive_or_fly` | 0 | 1 | |
| `check_setup` | 0 (free account check) | 1 | The Maps check is a minimal `computeRouteMatrix` call. |

## Reading multi-city and round-trip prices

**Prices are always full-itinerary totals, not per-leg.** This applies to both
round-trip phase 1 (`search_flights` with `return_date`) and multi-city searches
(`search_multi_city`). Each phase-1 / first-leg option's `price` is the total for
the entire journey — verified live against Google Flights (2026-07-30): a 3-leg
POA to NYC to MCO / MIA to POA search returned first-leg options priced
R$5,884 to R$36,377, matching the itinerary totals on the Google Flights website.

Use the `departure_token` from a phase-1 result to fetch the subsequent legs
(for round-trips) or examine the per-leg breakdown already included in each
multi-city result. An AI client that treats a first-leg price as a single-leg
price will misreport costs to the user.

## Examples

See [docs/EXAMPLES.md](./docs/EXAMPLES.md) for worked agent flows: multi-city
itinerary, round-trip with `departure_token` drill-down, hotels, and drive-vs-fly
comparison.

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for dev setup, test conventions, and
the walk-through for adding a new tool.

## Changelog

See [CHANGELOG.md](./CHANGELOG.md) for the release history.

## More of what I build

This is one of several. The apps and tools I work on live at
**[cosmohq.org](https://cosmohq.org)** — have a look if you want the rest.

## License

MIT — see [LICENSE](./LICENSE). Copyright (c) 2026 Matheus Weber.
