"""Tests for the check_setup tool."""

from __future__ import annotations

import os

import httpx
import pytest
import respx

from cosmo_travel_mcp.tools.setup import (
    ROUTES_API_BASE,
    SERPAPI_ACCOUNT_URL,
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
        "search_cheapest_dates",
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
        "search_cheapest_dates",
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

    serpapi_tools = ["search_flights", "search_multi_city", "search_accommodations", "search_cheapest_dates"]
    for tool in result["tools"]:
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
