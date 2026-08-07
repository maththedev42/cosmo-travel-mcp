# Contributing to cosmo-travel-mcp

## Dev setup

```bash
uv sync
uv run pytest
```

One dependency (`uv`) gets you a locked Python environment and the full test
suite. The project requires Python >= 3.11.

## The both-env-states rule

The test suite must pass in **two environments**:

```bash
uv run pytest                           # no keys set
SERPAPI_API_KEY=fake uv run pytest      # keys set (fake values)
```

Everything is HTTP-mocked — no real network calls — but tools deliberately
check `os.environ` and surface different messages when keys are absent. This
has regressed before (tests that accidentally passed only when
`SERPAPI_API_KEY` was already set in the shell), so CI runs both states and
every PR must preserve the dual-state guarantee.

## Test conventions

- **Zero real network calls.** Every outbound HTTP request is mocked via
  [respx](https://github.com/lundberg/respx). The `mock.get(...)` / `mock.post(...)`
  routes assert on the *full* URL (including query parameters), not just the base.
- **Assert on stable anchors.** Use exact strings for error messages that come
  from `onboarding.py` (the single source of truth), not loose substring matches
  that pass when wording drifts.
- **Environment isolation.** Tests that need a missing key use `monkeypatch.delenv`
  or an explicit `SERPAPI_API_KEY=""` block. The autouse fixture already sets
  `SERPAPI_API_KEY=fake` so individual tests override it deliberately.
- **Async.** All tool tests are `@pytest.mark.asyncio`. The MCP server runs async
  over stdio, so tools are `async def` and tests `await` them.
- **Backoff patching.** The retry backoff in `flights.py` is exposed as
  `_RETRY_BACKOFF_SECONDS`. Tests monkeypatch it to `0` so the suite stays fast.

## Commit expectations

- One commit per feature or prompt, named after the capability it delivers
  (see `git log --oneline` for the house style).
- The full suite must be green before committing. Run both env states.

## Getting a change onto `main`

`main` is covered by a ruleset. It cannot be pushed to directly, force-pushed,
or deleted — by anyone, maintainer included. Every change arrives through a
pull request that has both `test (3.11)` and `test (3.12)` green.

Pull requests require **one approving review**. GitHub refuses to let an author
approve their own pull request, so for a solo maintainer that rule would be a
deadlock; the repository-admin role is therefore a bypass actor in
`bypass_mode: pull_request`. Read plainly, that means:

- an outside contributor's PR needs a real approval, and
- the maintainer can merge their own PR without one, but still cannot push to
  `main` directly.

So the approval requirement is genuine for contributions and is, honestly, a
formality on the maintainer's own work. The gate that actually holds on every
change regardless of author is CI plus the no-direct-push rule.

## Adding a new tool

The internal architecture is deliberately flat — every tool is a module in
`src/cosmo_travel_mcp/tools/` with a `register(mcp)` function. Adding one
takes about six touch-points, all predictable:

1. **Write the tool module** in `tools/`. Follow the existing shape:
   - A free-standing `async def` function with typed parameters and a
     google-style docstring.
   - Call the shared `_call_serpapi` (in `flights.py`) for SerpAPI-backed
     tools, or `httpx` directly for Maps-backed tools.
   - Read API keys from the environment with `os.environ.get(…)` or via the
     existing helpers (`_get_api_key()`, `_get_maps_api_key()`).
   - Return a plain `dict[str, Any]` (FastMCP serialises it).
   - Provide a `def register(mcp): mcp.tool()(your_function)` at the bottom.
     This is the module's public contract — `server.py` calls it during
     startup.

2. **Register it in `server.py`** — import the module and call its
   `register(mcp)` inside `main()`, keeping the existing alphabetical-ish
   ordering.

3. **Add it to the registry in `onboarding.py`** — put the tool name in
   `SERPAPI_TOOLS`, `MAPS_TOOLS` or `KEYLESS_TOOLS`, whichever gates it.

   `check_setup` builds its per-tool readiness report straight from these
   tuples, so this one edit is what makes the tool appear in the "what can I
   use right now" surface. There is no second list to keep in step.

   It did not always work that way. The tuples were documentation shaped like
   code until 1.2.1 — nothing in `src/` read them, while `check_setup` wrote
   the same mapping out by hand beside them. They drifted:
   `search_things_to_do` was absent for three releases and `search_car_rentals`
   went the same way, with no surface noticing.
   `test_every_registered_tool_belongs_to_exactly_one_key_group` now compares
   the registry against what actually gets registered on the server.

4. **Add a README row** in the tools table (name, parameters, one-line
   description).

5. **Docstring quota cost** — if the tool calls SerpAPI or Maps, document
   in the docstring exactly how many searches/API calls it spends per
   invocation, so callers can budget against the free tiers.

That's it. No DI container, no plugin registry, no adapter interfaces.
Each module is self-contained and the wiring is explicit.

(There used to be a sixth touch-point: hand-writing a status dict inside
`check_setup`. Wiring the registry up removed it.)

## Code map

| File | What lives there |
|---|---|
| `src/cosmo_travel_mcp/server.py` | `FastMCP` instance, tool registration, server instructions |
| `src/cosmo_travel_mcp/onboarding.py` | Single source of truth for URLs, env-var names, key-acquisition steps, registration commands, setup-guide text, and the tool registry (`SERPAPI_TOOLS` / `MAPS_TOOLS` / `KEYLESS_TOOLS`) that `check_setup` reports from |
| `src/cosmo_travel_mcp/tools/flights.py` | `search_flights`, `search_multi_city`, `search_cheapest_dates`, shared `_call_serpapi` (+ retry), `_build_base_params`, and all flight response parsers |
| `src/cosmo_travel_mcp/tools/hotels.py` | `search_accommodations`, `get_accommodation_details` (google_hotels engine) |
| `src/cosmo_travel_mcp/tools/places.py` | `search_things_to_do` (google_maps engine) + `_parse_place`, shared with car rentals |
| `src/cosmo_travel_mcp/tools/car_rentals.py` | `search_car_rentals` — offices, hours and contacts; never rates |
| `src/cosmo_travel_mcp/tools/events.py` | `search_events` (google_events engine) |
| `src/cosmo_travel_mcp/tools/itinerary.py` | `check_itinerary`, `build_calendar` — no API calls |
| `src/cosmo_travel_mcp/tools/driving.py` | `compare_drive_or_fly` (Routes API `computeRouteMatrix`) |
| `src/cosmo_travel_mcp/tools/setup.py` | `check_setup` tool, `probe_serpapi`, `probe_maps` |
| `src/cosmo_travel_mcp/tools/prompts.py` | `plan_trip` MCP prompt |
| `tests/` | One test file per tool module + `test_setup.py` + `test_prompts.py` |
