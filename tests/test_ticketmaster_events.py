"""Tests for search_ticketmaster_events tool (Ticketmaster Discovery API v2)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from cosmo_travel_mcp.tools.ticketmaster_events import (
    TICKETMASTER_BASE,
    search_ticketmaster_events,
)


@pytest.fixture(autouse=True)
def _set_fake_api_key(monkeypatch):
    """Set a fake TICKETMASTER_API_KEY so tests pass key checks."""
    monkeypatch.setenv("TICKETMASTER_API_KEY", "tm-fake-secret-key-12345")


@pytest.mark.asyncio
async def test_search_ticketmaster_events_parses_fixture():
    """Parse a REAL Discovery API response end to end.

    tests/fixtures/ticketmaster_discovery_events.json is a live capture
    (2026-09-01, GET /discovery/v2/events.json, countryCode=US, city=New
    York) trimmed only of unused image arrays and venue policy prose — every
    field asserted below is untouched real data, not invented.
    """
    fixture_path = Path(__file__).parent / "fixtures" / "ticketmaster_discovery_events.json"
    fixture_data = json.loads(fixture_path.read_text(encoding="utf-8"))

    with respx.mock as mock:
        mock.get(TICKETMASTER_BASE).respond(json=fixture_data)
        result = await search_ticketmaster_events(city="New York", country_code="US")

    assert result["provider"] == "ticketmaster"
    assert result["total_results"] == 3
    assert len(result["events"]) == 3

    # 1. "Lady A: This Winter's Night Tour 2026" — public sale + 8 presales,
    # no priceRanges (most real events don't carry one, see the module
    # docstring's live-sampled ratio).
    ev1 = result["events"][0]
    assert ev1["title"] == "Lady A: This Winter's Night Tour 2026"
    assert ev1["date"] == "2026-12-17 | 20:00:00"
    assert ev1["sales"]["public"]["start_date_time"] == "2026-06-12T14:00:00Z"
    assert ev1["sales"]["public"]["start_tbd"] is False
    assert "start_tba" in ev1["sales"]["public"]
    assert ev1["sales"]["public"]["start_tba"] is False
    assert len(ev1["sales"]["presales"]) == 8
    assert ev1["venue"] == "Beacon Theatre"
    assert ev1["address"] == "2124 Broadway @ 74th St"
    assert ev1["location"] == {"lat": 40.779925, "lng": -73.980673}
    assert ev1["category"] == "Music"
    assert "price_ranges" not in ev1

    # 2. "New York Knicks vs. Boston Celtics" — real capture where
    # `sales.public` has NO startDateTime and `startTBA: true` instead of
    # startTBD. This is the case that mattered: reading only startTBD would
    # have reported `start_tbd: false` for a sale date that is genuinely
    # unannounced, the opposite of what this tool exists to report.
    ev2 = result["events"][1]
    assert ev2["title"] == "New York Knicks vs. Boston Celtics"
    assert "start_date_time" not in ev2["sales"]["public"]
    assert ev2["sales"]["public"]["start_tbd"] is False
    assert ev2["sales"]["public"]["start_tba"] is True
    assert "presales" not in ev2["sales"]

    # 3. "The Rocky Horror Picture Show" — the one real event (of 116
    # sampled live) that carried priceRanges, and a venue whose address
    # has both line1 and line2 ("Berlin", NYC).
    ev3 = result["events"][2]
    assert ev3["title"].startswith("The Rocky Horror Picture Show")
    assert ev3["price_ranges"][0]["min"] == 31.52
    assert ev3["price_ranges"][0]["max"] == 31.52
    assert ev3["price_ranges"][0]["currency"] == "USD"
    assert ev3["venue"] == "Berlin"
    assert "25 Avenue A" in ev3["address"]


@pytest.mark.asyncio
async def test_search_ticketmaster_events_empty_results():
    """Response without _embedded events returns empty list."""
    with respx.mock as mock:
        mock.get(TICKETMASTER_BASE).respond(json={"page": {"totalElements": 0}})
        result = await search_ticketmaster_events(city="EmptyCity")

    assert result["events"] == []
    assert result["total_results"] == 0


@pytest.mark.asyncio
async def test_search_ticketmaster_events_missing_key(monkeypatch):
    """Missing TICKETMASTER_API_KEY raises ValueError with remediation."""
    monkeypatch.delenv("TICKETMASTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TICKETMASTER_API_KEY is not set"):
        await search_ticketmaster_events(city="Chicago")


@pytest.mark.asyncio
async def test_search_ticketmaster_events_invalid_key_401():
    """HTTP 401 response with Ticketmaster fault payload raises ValueError."""
    fault_payload = {
        "fault": {
            "faultstring": "Invalid ApiKey",
            "detail": {"errorcode": "oauth.v2.InvalidApiKey"},
        }
    }
    with respx.mock as mock:
        mock.get(TICKETMASTER_BASE).respond(status_code=401, json=fault_payload)
        with pytest.raises(ValueError, match="Ticketmaster API key rejected: Invalid ApiKey"):
            await search_ticketmaster_events(city="Miami")


@pytest.mark.asyncio
async def test_search_ticketmaster_events_key_redaction_on_http_error(monkeypatch):
    """Assert API key is NEVER leaked in HTTPStatusError messages or request URLs."""
    secret_key = "my-secret-ticketmaster-key-xyz"
    monkeypatch.setenv("TICKETMASTER_API_KEY", secret_key)

    with respx.mock as mock:
        mock.get(TICKETMASTER_BASE).respond(status_code=500, text="Internal Server Error")
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await search_ticketmaster_events(city="Boston")

    err_str = str(exc_info.value)
    url_str = str(exc_info.value.request.url) if exc_info.value.request else ""

    assert secret_key not in err_str, f"Secret key leaked in exception message: {err_str}"
    assert secret_key not in url_str, f"Secret key leaked in request URL: {url_str}"
    assert "apikey=***" in err_str or "apikey=***" in url_str


@pytest.mark.asyncio
async def test_search_ticketmaster_events_key_redaction_on_request_error(monkeypatch):
    """Assert API key is NEVER leaked in RequestError (network failure) messages or request URLs."""
    secret_key = "my-secret-ticketmaster-key-network-secret"
    monkeypatch.setenv("TICKETMASTER_API_KEY", secret_key)

    with respx.mock as mock:
        mock.get(TICKETMASTER_BASE).side_effect = httpx.ConnectError("Connection refused")
        with pytest.raises(httpx.RequestError) as exc_info:
            await search_ticketmaster_events(city="Dallas")

    err_str = str(exc_info.value)
    url_str = str(exc_info.value.request.url) if exc_info.value.request else ""

    assert secret_key not in err_str, f"Secret key leaked in RequestError message: {err_str}"
    assert secret_key not in url_str, f"Secret key leaked in RequestError URL: {url_str}"


@pytest.mark.asyncio
async def test_search_ticketmaster_events_country_code_param():
    """country_code is passed uppercase to the API as countryCode."""
    with respx.mock as mock:
        mock.get(TICKETMASTER_BASE).respond(json={"page": {"totalElements": 0}})
        await search_ticketmaster_events(city="Santiago", country_code="cl")
        assert mock.calls.last.request.url.params["countryCode"] == "CL"


@pytest.mark.asyncio
async def test_search_ticketmaster_events_parameter_validation():
    """Invalid parameters raise ValueError."""
    with pytest.raises(ValueError, match="at least one of"):
        await search_ticketmaster_events()

    with pytest.raises(ValueError, match="at least one of"):
        await search_ticketmaster_events(city="")

    with pytest.raises(ValueError, match="size must be between 1 and 100"):
        await search_ticketmaster_events(city="Chicago", size=0)

    with pytest.raises(ValueError, match="page must be >= 0"):
        await search_ticketmaster_events(city="Chicago", page=-1)


@pytest.mark.asyncio
async def test_search_ticketmaster_events_city_is_optional():
    """city can be omitted entirely — needed because it is an exact match
    against Ticketmaster's own registry (see the module docstring: "São
    Paulo" and "Sao Paulo" both returned zero live results while
    "Rio de Janeiro" worked). A caller must be able to fall back to
    country_code/keyword alone rather than guess spellings."""
    with respx.mock as mock:
        mock.get(TICKETMASTER_BASE).respond(json={"page": {"totalElements": 0}})
        await search_ticketmaster_events(country_code="BR")
        assert "city" not in mock.calls.last.request.url.params
        assert mock.calls.last.request.url.params["countryCode"] == "BR"


@pytest.mark.asyncio
async def test_search_ticketmaster_events_startTBA_without_startDateTime():
    """A real captured event (see fixture) has `startTBA: true` and no
    `startDateTime` at all — startTBD alone would misreport it as a known
    sale date. Exercised directly here (not only via the fixture test) so
    this specific field survives independent of fixture edits."""
    payload = {
        "_embedded": {
            "events": [
                {
                    "name": "Some Future Game",
                    "sales": {"public": {"startTBD": False, "startTBA": True}},
                }
            ]
        },
        "page": {"totalElements": 1},
    }
    with respx.mock as mock:
        mock.get(TICKETMASTER_BASE).respond(json=payload)
        result = await search_ticketmaster_events(keyword="game")

    sale = result["events"][0]["sales"]["public"]
    assert sale["start_tbd"] is False
    assert sale["start_tba"] is True
    assert "start_date_time" not in sale
