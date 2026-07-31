"""Tests for search_events tool (SerpAPI google_events engine)."""

from __future__ import annotations

import pytest
import respx

from cosmo_travel_mcp.tools.flights import SERPAPI_BASE
from cosmo_travel_mcp.tools.events import search_events


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _set_fake_api_key(monkeypatch):
    """Set a fake SERPAPI_API_KEY so tests never hit the real guard."""
    monkeypatch.setenv("SERPAPI_API_KEY", "fake-test-key")


# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_events_normal_query():
    """Normal query returns normalized events with all fields mapped."""
    mock_response = {
        "events_results": [
            {
                "title": "Rock Concert",
                "date": {"when": "Sat, Dec 13, 8:00 PM", "start_date": "Dec 13"},
                "venue": {"name": "Madison Square Garden", "address": "4 Pennsylvania Plaza"},
                "link": "https://example.com/rock-concert",
                "ticket_info": [
                    {"source": "Ticketmaster", "link": "https://tm.example.com/1"},
                    {"source": "StubHub", "link": "https://sh.example.com/1"},
                ],
            },
        ],
    }

    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=mock_response)
        result = await search_events("New York")

    assert len(result["events"]) == 1
    assert result["total_results"] == 1

    ev = result["events"][0]
    assert ev["title"] == "Rock Concert"
    assert "Dec 13" in ev["date"]
    assert "Sat" in ev["date"]
    assert ev["venue"] == "Madison Square Garden"
    assert ev["address"] == "4 Pennsylvania Plaza"
    assert ev["link"] == "https://example.com/rock-concert"
    assert len(ev["tickets"]) == 2
    assert ev["tickets"][0]["source"] == "Ticketmaster"
    assert ev["tickets"][1]["source"] == "StubHub"


@pytest.mark.asyncio
async def test_search_events_when_filter():
    """when='weekend' -> request params include htichips date filter."""
    mock_response = {
        "events_results": [
            {"title": "Fest", "date": {"when": "This weekend"}},
        ],
    }

    with respx.mock as mock:
        route = mock.get(SERPAPI_BASE).respond(json=mock_response)
        await search_events("Chicago", when="weekend")

    req = route.calls[0].request
    assert "htichips" in req.url.params
    assert req.url.params["htichips"] == "date:weekend"


@pytest.mark.asyncio
async def test_search_events_when_invalid():
    """Invalid when value raises ValueError naming valid values."""
    with pytest.raises(ValueError, match="Invalid when"):
        await search_events("Miami", when="next_year")


@pytest.mark.asyncio
async def test_search_events_bare_city_gets_prefix():
    """Bare city name gets events-shaped query prefix."""
    mock_response = {"events_results": []}

    with respx.mock as mock:
        route = mock.get(SERPAPI_BASE).respond(json=mock_response)
        await search_events("Boston")

    req = route.calls[0].request
    assert "Events in Boston" in req.url.params["q"]


@pytest.mark.asyncio
async def test_search_events_complex_query_unchanged():
    """Query with spaces or commas is passed as-is, no prefix."""
    mock_response = {"events_results": []}

    with respx.mock as mock:
        route = mock.get(SERPAPI_BASE).respond(json=mock_response)
        await search_events("jazz concerts in New Orleans")

    req = route.calls[0].request
    assert req.url.params["q"] == "jazz concerts in New Orleans"


@pytest.mark.asyncio
async def test_search_events_country_and_language():
    """country + language parameters are forwarded as gl/hl."""
    mock_response = {"events_results": []}

    with respx.mock as mock:
        route = mock.get(SERPAPI_BASE).respond(json=mock_response)
        await search_events("Paris", country="FR", language="fr")

    req = route.calls[0].request
    assert req.url.params["gl"] == "FR"
    assert req.url.params["hl"] == "fr"


@pytest.mark.asyncio
async def test_search_events_event_without_tickets():
    """Event without ticket_info -> tickets omitted, no crash."""
    mock_response = {
        "events_results": [
            {
                "title": "Workshop",
                "date": {"when": "Jan 2026"},
                "venue": {"name": "Convention Center"},
            },
        ],
    }

    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=mock_response)
        result = await search_events("Austin")

    ev = result["events"][0]
    assert ev["title"] == "Workshop"
    assert "tickets" not in ev


@pytest.mark.asyncio
async def test_search_events_empty_results():
    """Empty events_results -> empty list, no error."""
    mock_response = {"events_results": []}

    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=mock_response)
        result = await search_events("Nowhere")

    assert result["events"] == []
    assert result["total_results"] == 0


@pytest.mark.asyncio
async def test_search_events_no_events_results_key():
    """Response without events_results key -> empty list, no error."""
    mock_response = {"search_metadata": {"status": "Success"}}

    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=mock_response)
        result = await search_events("Somewhere")

    assert result["events"] == []
    assert result["total_results"] == 0


@pytest.mark.asyncio
async def test_search_events_missing_key_error(monkeypatch):
    """Missing SERPAPI_API_KEY raises ValueError."""
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="SERPAPI_API_KEY"):
        await search_events("Tokyo")


@pytest.mark.asyncio
async def test_search_events_date_only_when():
    """Event with date.when but no start_date — only shows when string."""
    mock_response = {
        "events_results": [
            {"title": "Festival", "date": {"when": "This weekend"}},
        ],
    }

    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=mock_response)
        result = await search_events("Denver")

    ev = result["events"][0]
    assert ev["date"] == "This weekend"


@pytest.mark.asyncio
async def test_search_events_date_start_only():
    """Event with start_date but no when string — only shows date."""
    mock_response = {
        "events_results": [
            {"title": "Workshop", "date": {"start_date": "Jan 15"}},
        ],
    }

    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=mock_response)
        result = await search_events("Seattle")

    ev = result["events"][0]
    assert ev["date"] == "Jan 15"


@pytest.mark.asyncio
async def test_search_events_venue_name_only():
    """Event with venue name but no address — address omitted."""
    mock_response = {
        "events_results": [
            {"title": "Game Night", "venue": {"name": "Local Arena"}},
        ],
    }

    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=mock_response)
        result = await search_events("Dallas")

    ev = result["events"][0]
    assert ev["venue"] == "Local Arena"
    assert "address" not in ev


@pytest.mark.asyncio
async def test_search_events_minimal_event():
    """An event with only a title — no extra keys beyond title."""
    mock_response = {
        "events_results": [
            {"title": "Minimal Event"},
        ],
    }

    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=mock_response)
        result = await search_events("Portland")

    ev = result["events"][0]
    assert ev == {"title": "Minimal Event"}
