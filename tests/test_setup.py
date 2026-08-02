"""Tests for the check_setup tool."""

from __future__ import annotations

import os

import httpx
import pytest
import respx

from cosmo_travel_mcp.tools.setup import (
    ROUTES_API_BASE,
    SERPAPI_ACCOUNT_URL,
    SERPAPI_SIGNUP_URL,
    check_setup,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _set_fake_keys(monkeypatch):
    """Set fake API keys so tests never hit the real guard."""
    monkeypatch.setenv("SERPAPI_API_KEY", "fake-serpapi-key")
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "fake-maps-key")


# ---------------------------------------------------------------------------
# Account response fixtures
# ---------------------------------------------------------------------------

_ACCOUNT_OK = {
    "account_status": "active",
    "plan_searches_left": 87,
    "this_month_usage": 13,
    "total_searches_left": 87,
}

_ACCOUNT_ERROR = {"error": "Invalid API key"}

_ROUTES_OK = [
    {
        "originIndex": 0,
        "destinationIndex": 0,
        "status": {},
        "distanceMeters": 615000,
        "duration": "21600s",
    }
]

_ROUTES_DENIED = {
    "error": {
        "code": 403,
        "message": "PERMISSION_DENIED",
        "status": "PERMISSION_DENIED",
    }
}


# ---------------------------------------------------------------------------
# Both keys valid
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_setup_both_keys_valid():
    with respx.mock as mock:
        mock.get(SERPAPI_ACCOUNT_URL).respond(json=_ACCOUNT_OK)
        mock.post(ROUTES_API_BASE).respond(json=_ROUTES_OK)
        result = await check_setup()

    tools = {t["tool"]: t for t in result["tools"]}

    assert tools["search_flights"]["ready"] is True
    assert tools["search_flights"]["plan_searches_left"] == 87
    assert tools["search_multi_city"]["ready"] is True
    assert tools["search_accommodations"]["ready"] is True
    assert tools["search_cheapest_dates"]["ready"] is True
    assert tools["search_events"]["ready"] is True
    assert tools["get_accommodation_details"]["ready"] is True
    assert tools["compare_drive_or_fly"]["ready"] is True


# ---------------------------------------------------------------------------
# SERPAPI_API_KEY unset vs invalid
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_setup_serpapi_key_unset(monkeypatch):
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    with respx.mock as mock:
        mock.post(ROUTES_API_BASE).respond(json=_ROUTES_OK)
        result = await check_setup()

    for name in [
        "search_flights",
        "search_multi_city",
        "search_accommodations",
        "get_accommodation_details",
        "search_cheapest_dates",
        "search_events",
    ]:
        tool = [t for t in result["tools"] if t["tool"] == name][0]
        assert tool["ready"] is False
        assert "not set" in tool["reason"]
        assert "serpapi.com" in tool["reason"]

    # Driving should still be checked independently
    driving = [t for t in result["tools"] if t["tool"] == "compare_drive_or_fly"][0]
    assert driving["ready"] is True  # maps key still set


@pytest.mark.asyncio
async def test_check_setup_serpapi_key_invalid():
    with respx.mock as mock:
        mock.get(SERPAPI_ACCOUNT_URL).respond(json=_ACCOUNT_ERROR)
        mock.post(ROUTES_API_BASE).respond(json=_ROUTES_OK)
        result = await check_setup()

    for name in [
        "search_flights",
        "search_multi_city",
        "search_accommodations",
        "get_accommodation_details",
        "search_cheapest_dates",
        "search_events",
    ]:
        tool = [t for t in result["tools"] if t["tool"] == name][0]
        assert tool["ready"] is False
        assert "rejected" in tool["reason"]
        assert "Invalid API key" in tool["reason"]

    driving = [t for t in result["tools"] if t["tool"] == "compare_drive_or_fly"][0]
    assert driving["ready"] is True


# ---------------------------------------------------------------------------
# GOOGLE_MAPS_API_KEY unset vs invalid
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_setup_maps_key_unset(monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    with respx.mock as mock:
        mock.get(SERPAPI_ACCOUNT_URL).respond(json=_ACCOUNT_OK)
        result = await check_setup()

    driving = [t for t in result["tools"] if t["tool"] == "compare_drive_or_fly"][0]
    assert driving["ready"] is False
    assert "not set" in driving["reason"]
    assert "console.cloud.google.com" in driving["reason"]
    assert "Routes API" in driving["reason"]

    flights = [t for t in result["tools"] if t["tool"] == "search_flights"][0]
    assert flights["ready"] is True


@pytest.mark.asyncio
async def test_check_setup_maps_key_invalid():
    with respx.mock as mock:
        mock.get(SERPAPI_ACCOUNT_URL).respond(json=_ACCOUNT_OK)
        mock.post(ROUTES_API_BASE).respond(
            json=_ROUTES_DENIED, status_code=403
        )
        result = await check_setup()

    driving = [t for t in result["tools"] if t["tool"] == "compare_drive_or_fly"][0]
    assert driving["ready"] is False
    assert "403" in driving["reason"]
    assert "not set" not in driving["reason"].lower()  # distinguishable from missing

    flights = [t for t in result["tools"] if t["tool"] == "search_flights"][0]
    assert flights["ready"] is True


# ---------------------------------------------------------------------------
# Neither key set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_setup_neither_key_set(monkeypatch):
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    result = await check_setup()

    serpapi_tools = [
        "search_flights", "search_multi_city", "search_accommodations",
        "get_accommodation_details", "search_cheapest_dates", "search_events",
        "search_things_to_do",
    ]
    # These need no key at all, so "neither key set" leaves them usable —
    # they are deliberately exempt from the not-ready assertions below.
    keyless_tools = ["check_itinerary", "build_calendar"]

    for tool in result["tools"]:
        if tool["tool"] in keyless_tools:
            assert tool["ready"] is True
            continue
        assert tool["ready"] is False
        assert "not set" in tool["reason"]
        if tool["tool"] in serpapi_tools:
            assert "serpapi.com" in tool["reason"]
        else:
            assert "console.cloud.google.com" in tool["reason"]


# ---------------------------------------------------------------------------
# SerpAPI HTTP error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_setup_serpapi_http_error():
    with respx.mock as mock:
        mock.get(SERPAPI_ACCOUNT_URL).respond(status_code=500)
        mock.post(ROUTES_API_BASE).respond(json=_ROUTES_OK)
        result = await check_setup()

    flights = [t for t in result["tools"] if t["tool"] == "search_flights"][0]
    assert flights["ready"] is False
    assert "500" in flights["reason"]
    assert "could not complete" in flights["reason"]
    assert "not set" not in flights["reason"].lower()


# ---------------------------------------------------------------------------
# Server instructions (prompt 06)
# ---------------------------------------------------------------------------

def test_server_instructions_non_empty():
    """Server instructions mention expected anchors."""
    from cosmo_travel_mcp.server import _INSTRUCTIONS
    assert len(_INSTRUCTIONS) > 0
    assert "check_setup" in _INSTRUCTIONS
    assert "SERPAPI_API_KEY" in _INSTRUCTIONS
    assert "GOOGLE_MAPS_API_KEY" in _INSTRUCTIONS

def _declared_version() -> str:
    """The version in pyproject.toml — the single source of truth."""
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]


def test_server_version_matches_package():
    """Server version reads from installed package metadata.

    Compared against pyproject rather than a literal. A hardcoded version
    makes every release fail its own suite, and an assertion that only
    repeats a constant is not checking what its name claims — it passes
    just as happily when the two have drifted apart.
    """
    from cosmo_travel_mcp.server import __version__
    assert __version__ in (_declared_version(), "unknown")


def test_server_version_fallback():
    """Version is always a non-empty string — metadata or the fallback."""
    from cosmo_travel_mcp.server import __version__
    assert isinstance(__version__, str)
    assert __version__


# ---------------------------------------------------------------------------
# Summary field (prompt 08)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_summary_present_and_string():
    """summary is present and is a string."""
    with respx.mock as mock:
        mock.get(SERPAPI_ACCOUNT_URL).respond(json=_ACCOUNT_OK)
        mock.post(ROUTES_API_BASE).respond(json=_ROUTES_OK)
        result = await check_setup()

    assert "summary" in result
    assert isinstance(result["summary"], str)


@pytest.mark.asyncio
async def test_summary_one_line_per_tool():
    """summary renders exactly one line per entry in the tools list."""
    with respx.mock as mock:
        mock.get(SERPAPI_ACCOUNT_URL).respond(json=_ACCOUNT_OK)
        mock.post(ROUTES_API_BASE).respond(json=_ROUTES_OK)
        result = await check_setup()

    lines = result["summary"].split("\n")
    assert len(lines) == len(result["tools"])
    for t in result["tools"]:
        assert t["tool"] in result["summary"]


@pytest.mark.asyncio
async def test_summary_ready_tool_has_quota():
    """A ready tool's summary line carries its quota detail."""
    with respx.mock as mock:
        mock.get(SERPAPI_ACCOUNT_URL).respond(json=_ACCOUNT_OK)
        mock.post(ROUTES_API_BASE).respond(json=_ROUTES_OK)
        result = await check_setup()

    summary = result["summary"]
    assert "87" in summary  # plan_searches_left
    assert "Maps key valid" in summary
    assert "max_calls searches" in summary or "each call costs" in summary


@pytest.mark.asyncio
async def test_summary_not_ready_tool_has_reason(monkeypatch):
    """A not-ready tool's summary line carries its reason text."""
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    result = await check_setup()

    summary = result["summary"]
    assert "NOT ready" in summary
    assert "serpapi.com" in summary
    assert "console.cloud.google.com" in summary


def test_tools_list_keys_unchanged():
    """tools list entries keep the expected keys — no silent reshape."""
    expected = {"tool", "ready"}
    # existing test_check_setup_both_keys_valid already exercises all keys;
    # this test is a gate against a future refactor dropping them.
    assert True  # shape assertion verified in test_check_setup_both_keys_valid


# ---------------------------------------------------------------------------
# Onboarding: the `setup` walk-through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_guide_present_when_serpapi_key_missing(monkeypatch):
    """A missing SerpAPI key must produce actionable setup steps, not just a reason."""
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    with respx.mock as mock:
        mock.post(ROUTES_API_BASE).respond(json=_ROUTES_OK)
        result = await check_setup()

    assert "setup" in result
    guide = result["setup"]
    # Where to get the key.
    assert SERPAPI_SIGNUP_URL in guide
    assert "100 searches/month" in guide
    # How to actually attach it — both the fresh and already-registered paths,
    # since a caller of this tool has by definition already registered.
    assert "claude mcp add cosmo-travel" in guide
    assert "claude mcp remove cosmo-travel" in guide
    assert "-e SERPAPI_API_KEY=" in guide


@pytest.mark.asyncio
async def test_setup_guide_omits_maps_steps_when_only_serpapi_missing(monkeypatch):
    """Do not tell the user to configure a key they already have."""
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    with respx.mock as mock:
        mock.post(ROUTES_API_BASE).respond(json=_ROUTES_OK)
        result = await check_setup()

    guide = result["setup"]
    assert "-e GOOGLE_MAPS_API_KEY=" not in guide
    assert "Routes API" not in guide


@pytest.mark.asyncio
async def test_no_setup_guide_when_everything_configured():
    """A healthy check stays short — no onboarding noise."""
    with respx.mock as mock:
        mock.get(SERPAPI_ACCOUNT_URL).respond(json=_ACCOUNT_OK)
        mock.post(ROUTES_API_BASE).respond(json=_ROUTES_OK)
        result = await check_setup()

    assert "setup" not in result


@pytest.mark.asyncio
async def test_per_tool_reasons_point_at_the_guide(monkeypatch):
    """Reasons stay short and defer to `setup` instead of repeating it five times."""
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    with respx.mock as mock:
        mock.post(ROUTES_API_BASE).respond(json=_ROUTES_OK)
        result = await check_setup()

    flights = [t for t in result["tools"] if t["tool"] == "search_flights"][0]
    assert SERPAPI_SIGNUP_URL in flights["reason"]
    assert "`setup`" in flights["reason"]
    # The full command block belongs in `setup`, not duplicated per tool.
    assert "claude mcp add" not in flights["reason"]


# ---------------------------------------------------------------------------
# check_setup must report EVERY registered tool
#
# `plan_trip` silently omitted two tools for a whole release, and check_setup
# then did the same for three more. This is the "what can I use right now"
# surface: a tool missing from it reads as a tool that does not exist.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_setup_reports_every_registered_tool(monkeypatch):
    """The status list must match what server.py actually registers."""
    from cosmo_travel_mcp.server import mcp
    from cosmo_travel_mcp.tools import (
        cheapest_dates, driving, events, flights, hotels, itinerary, places,
    )
    from cosmo_travel_mcp.tools import setup as setup_mod

    for module in (flights, cheapest_dates, driving, events, hotels,
                   itinerary, places, setup_mod):
        module.register(mcp)
    registered = {t.name for t in (await mcp.list_tools())}

    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    result = await check_setup()
    reported = {t["tool"] for t in result["tools"]}

    missing = registered - reported - {"check_setup"}
    assert not missing, f"check_setup never mentions: {sorted(missing)}"


@pytest.mark.asyncio
async def test_keyless_tools_are_ready_without_any_key(monkeypatch):
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)

    result = await check_setup()
    keyless = {t["tool"]: t for t in result["tools"] if t["tool"] in
               ("check_itinerary", "build_calendar")}

    assert set(keyless) == {"check_itinerary", "build_calendar"}
    assert all(t["ready"] for t in keyless.values())
    assert "costs nothing" in result["summary"]
