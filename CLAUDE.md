# cosmo-travel-mcp

Python MCP server — twelve travel tools over SerpAPI (flights, lodging, events, places)
and the Google Maps Routes API (driving). Published to PyPI as `cosmo-travel-mcp`.

## Commands

`uv run pytest`. CI runs the suite twice — once with the API keys unset, once with them
set — on **Python 3.11 and 3.12**. All four jobs must be green before a merge is possible.

## Landmines

- **Every SerpAPI call spends real quota.** 100 searches/month on the free tier, shared
  with the live trip watch that runs weekly. Tests mock HTTP with `respx`; a test that
  reaches the network is a bug even when it passes. `search_cheapest_dates` burns up to
  `max_calls` searches per invocation — never loop it.
- **`skills/` is not packaged.** `[tool.setuptools.packages.find]` is `where = ["src"]`,
  so `skills/plan-a-trip/watch.py` ships only through git. It is standard-library-only on
  purpose, because a launchd job runs it with the system Python. `tests/test_watch.py`
  loads it by path with `importlib.util.spec_from_file_location` — a plain import fails.
- **Never `git add -A` here.** Stage explicit paths, and scrub `api_key` out of any
  captured provider response before it lands in `tests/`.
- Bump the version only when something under `src/` changes. A `skills/`-only change
  stays under `[Unreleased]` in `CHANGELOG.md` — nothing was distributed.
- PyPI's JSON API lags a publish by minutes and will happily report the previous version.
  Verify a release against `https://pypi.org/simple/cosmo-travel-mcp/`.

## Merging

A ruleset requires a PR, `test (3.11)` and `test (3.12)` green, and one approval. A plain
`gh pr merge` fails with *"the base branch policy prohibits the merge"* — use
`gh pr merge <N> --squash --admin`.
