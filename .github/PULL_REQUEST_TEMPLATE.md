## Checklist

- [ ] `uv run pytest` passes with no keys set
- [ ] `SERPAPI_API_KEY=fake uv run pytest` passes with keys set
- [ ] If a new tool was added, the README tools table is updated
- [ ] If the tool surface changed, the onboarding module (`onboarding.py`
      and/or `tools/setup.py`) is in sync
- [ ] Docstrings document quota costs for any new SerpAPI or Maps calls
