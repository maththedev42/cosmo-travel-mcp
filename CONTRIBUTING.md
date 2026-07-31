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

3. **Gate it in `onboarding.py`** — add the tool name to either
   `SERPAPI_TOOLS` or `MAPS_TOOLS` (or neither, if it needs no API key).
   This keeps the `missing_key_message`, `check_setup`, and the CLI setup
   walk-through in sync.

4. **Gate it in `tools/setup.py`** — add a status dict to `check_setup()`
   and wire it into the `search_parameters` or driving-status block so the
   health check reports whether the tool is ready.

5. **Add a README row** in the tools table (name, parameters, one-line
   description).

6. **Docstring quota cost** — if the tool calls SerpAPI or Maps, document
   in the docstring exactly how many searches/API calls it spends per
   invocation, so callers can budget against the free tiers.

That's it. No DI container, no plugin registry, no adapter interfaces.
Each module is self-contained and the wiring is explicit.

## Code map

| File | What lives there |
|---|---|
| `src/cosmo_travel_mcp/server.py` | `FastMCP` instance, tool registration, server instructions |
| `src/cosmo_travel_mcp/onboarding.py` | Single source of truth for URLs, env-var names, key-acquisition steps, registration commands, and setup-guide text |
| `src/cosmo_travel_mcp/tools/flights.py` | `search_flights`, `search_multi_city`, `search_cheapest_dates`, shared `_call_serpapi` (+ retry), `_build_base_params`, and all flight response parsers |
| `src/cosmo_travel_mcp/tools/hotels.py` | `search_accommodations` (google_hotels engine) |
| `src/cosmo_travel_mcp/tools/driving.py` | `compare_drive_or_fly` (Routes API `computeRouteMatrix`) |
| `src/cosmo_travel_mcp/tools/setup.py` | `check_setup` tool, `probe_serpapi`, `probe_maps` |
| `src/cosmo_travel_mcp/tools/prompts.py` | `plan_trip` MCP prompt |
| `tests/` | One test file per tool module + `test_setup.py` + `test_prompts.py` |
