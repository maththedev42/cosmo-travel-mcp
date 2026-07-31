[![CI](https://github.com/maththedev42/cosmo-travel-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/maththedev42/cosmo-travel-mcp/actions/workflows/ci.yml)

# cosmo-travel-mcp

One MCP server with six travel tools — flight search, multi-city itineraries,
accommodations, cheapest-dates sampling, and drive-vs-fly comparisons — all backed
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

This one key unlocks four of the six tools: `search_flights`,
`search_multi_city`, `search_accommodations`, and `search_cheapest_dates`.

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

## Command line

| Command | Effect |
|---|---|
| `cosmo-travel-mcp` | Run the MCP server over stdio. This is what the client invokes; you rarely run it by hand. |
| `cosmo-travel-mcp setup` | Print the key-acquisition guide and the registration command. No side effects. |
| `cosmo-travel-mcp setup --register` | Prompt for keys (hidden input), validate them live, then register the server. Accepts `--scope`, `--name`, `-y`. |
| `cosmo-travel-mcp --version` | Print the version. |

## Tools

| Tool | Parameters | Description |
|---|---|---|
| `search_flights` | `origin`, `destination`, `outbound_date`, `return_date?`, `adults?`, `children?`, `cabin_class?`, `max_stops?`, `departure_token?`, `currency?`, `country?`, `language?` | One-way or round-trip flight search via SerpAPI |
| `search_multi_city` | `legs` ([{origin, destination, date}…]), `adults?`, `children?`, `cabin_class?`, `currency?`, `country?`, `language?` | Multi-city itinerary with 2-6 legs |
| `search_accommodations` | `location`, `check_in_date`, `check_out_date`, `adults?`, `children?`, `children_ages?`, `vacation_rentals?`, `currency?`, `country?`, `language?`, `min_price?`, `max_price?` | Hotels and vacation rentals via SerpAPI Google Hotels engine. Defaults to vacation rentals (Airbnb/Vrbo/Booking.com listings). Set `vacation_rentals=false` for standard hotels. |
| `compare_drive_or_fly` | `origin`, `destination`, `fuel_price_per_liter?`, `fuel_efficiency_km_per_liter?`, `rental_car_cost_total?`, `flight_price?`, `flight_duration_minutes?`, `currency?` | Driving distance + duration via Google Maps Routes API. Optionally folds in caller-supplied flight numbers for side-by-side comparison. |
| `search_cheapest_dates` | `origin`, `destination`, `earliest_departure`, `latest_return`, `trip_duration_days`, `max_calls?` (default 6, max 15), `adults?`, `children?`, `cabin_class?`, `currency?` | Samples candidate dates across a flexible window and returns cheapest round-trip per date. **Costs up to `max_calls` SerpAPI searches per call.** |
| `check_setup` | _(none)_ | Validates both API keys and reports which tools are ready. The SerpAPI check is free; the Maps check makes one real API call. |

## What each call costs

Every tool call that hits SerpAPI or Google Maps spends quota. The free tiers
(SerpAPI 100 searches/month, Maps ~$200/month credit) are enough for personal use,
but a cheap-seeming prompt like "find the cheapest Saturday in March" can burn a
week of quota if it runs `search_cheapest_dates` at `max_calls=15`.

| Tool | SerpAPI searches per call | Maps calls per call | Notes |
|---|---|---|---|
| `search_flights` | 1 | 0 | Phase-2 (return legs) calls cost 1 additional search. |
| `search_multi_city` | 1 | 0 | |
| `search_accommodations` | 1 | 0 | |
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

## License

MIT — see [LICENSE](./LICENSE). Copyright (c) 2026 Matheus Weber.
