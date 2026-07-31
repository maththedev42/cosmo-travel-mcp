# Changelog

All notable changes to cosmo-travel-mcp.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
