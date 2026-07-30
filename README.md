# cosmo-travel-mcp

Single MCP (Model Context Protocol) server bundling travel-planning tools behind one
process, so you register one server with one set of API keys instead of juggling
several disconnected community MCP servers.

Previously this project relied on a reverse-engineered Google Flights scraper that
started returning HTTP 200 responses that were actually internal error envelopes — a
documented, unresolved bug. Everything here now uses **licensed commercial data
providers** (SerpAPI for flights and accommodations, Google Maps Routes API for
driving).

## Getting started

The fastest path — this prompts for the keys, validates them against the real
APIs, and registers the server with Claude Code:

```bash
uvx --from git+https://github.com/maththedev42/cosmo-travel-mcp cosmo-travel-mcp setup --register
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

### 4. Install and run

**From this repo (local):**

```bash
uv tool install .
cosmo-travel-mcp
```

**From GitHub (no clone needed):**

```bash
uvx --from git+https://github.com/maththedev42/cosmo-travel-mcp cosmo-travel-mcp
```

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
claude mcp add cosmo-travel --scope user \
  -e SERPAPI_API_KEY=<your-serpapi-key> \
  -e GOOGLE_MAPS_API_KEY=<your-google-maps-key> \
  -- uvx --from git+https://github.com/maththedev42/cosmo-travel-mcp cosmo-travel-mcp
```

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

## License

MIT — see [LICENSE](./LICENSE). Copyright (c) 2026 Matheus Weber.
