"""Tests for search_events tool (SerpAPI google_events engine)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
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
    """Normal query returns normalized events with all fields mapped.

    The event shape here matches the real engine: ``address`` is a top-level
    array of strings and the ``venue`` object carries only
    {name, rating, reviews, link}. The previous version of this test put the
    address inside ``venue``, which the engine never does — so the parser read
    a field that is always missing and the test still passed.
    """
    mock_response = {
        "events_results": [
            {
                "title": "Rock Concert",
                "date": {"when": "Sat, Dec 13, 8:00 PM", "start_date": "Dec 13"},
                "address": ["Madison Square Garden, 4 Pennsylvania Plaza", "New York, NY"],
                "venue": {"name": "Madison Square Garden", "rating": 4.4, "reviews": 12000},
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
    assert ev["address"] == "Madison Square Garden, 4 Pennsylvania Plaza, New York, NY"
    assert ev["link"] == "https://example.com/rock-concert"
    assert len(ev["tickets"]) == 2
    assert ev["tickets"][0]["source"] == "Ticketmaster"
    assert ev["tickets"][1]["source"] == "StubHub"


@pytest.mark.asyncio
async def test_search_events_parses_the_real_captured_response():
    """Parse a real google_events body end to end (tests/fixtures/README.md)."""
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "google_events_search.json").read_text()
    )
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=fixture)
        result = await search_events("New York")

    assert result["total_results"] >= 1
    ev = result["events"][0]
    assert ev["title"]
    # address is joined from the top-level array, so it must be non-empty for
    # a real event — the bug this pins made it permanently absent.
    assert ev["address"]
    assert ev["venue"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query,expected_q",
    [
        ("New York", "Events in New York"),
        ("Porto Alegre", "Events in Porto Alegre"),
        ("Boston", "Events in Boston"),
        ("San Francisco, CA", "Events in San Francisco, CA"),
        ("jazz concerts in New Orleans", "jazz concerts in New Orleans"),
        ("Chicago festivals", "Chicago festivals"),
    ],
)
async def test_multi_word_cities_still_get_the_events_prefix(query, expected_q):
    """A space in the query does not make it events-shaped.

    The prior heuristic skipped the prefix whenever the query contained a
    space, so every multi-word city — including the spec's own "New York"
    example — was sent unprefixed.
    """
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json={"events_results": []})
        await search_events(query)
        assert mock.calls.last.request.url.params["q"] == expected_q


@pytest.mark.asyncio
async def test_search_events_uses_the_events_engine():
    """Guards the engine choice for search_events (google engine returning events_results)."""
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json={"events_results": []})
        await search_events("Lisbon")
        assert mock.calls.last.request.url.params["engine"] == "google"


@pytest.mark.asyncio
async def test_search_events_new_google_engine_item_shape():
    """Item from google engine (flat date string, time, implicit venue in address[0])."""
    mock_response = {
        "events_results": [
            {
                "title": "Birdland Big Band",
                "type": "Live big band jazz",
                "date": "Dec 31",
                "time": "7:00 PM",
                "address": ["Birdland Jazz Club", "Midtown South"],
            },
        ],
    }

    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=mock_response)
        result = await search_events("New York")

    assert len(result["events"]) == 1
    ev = result["events"][0]
    assert ev["title"] == "Birdland Big Band"
    assert ev["date"] == "Dec 31 | 7:00 PM"
    assert ev["venue"] == "Birdland Jazz Club"
    assert ev["address"] == "Midtown South"
    assert "link" not in ev


@pytest.mark.asyncio
async def test_search_events_string_date_without_time():
    """Item with string date and no time -> date string unmodified, no time invented."""
    mock_response = {
        "events_results": [
            {
                "title": "Times Square NYE",
                "date": "Dec 31",
                "address": ["Times Square", "New York, NY"],
            },
        ],
    }

    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=mock_response)
        result = await search_events("New York")

    ev = result["events"][0]
    assert ev["date"] == "Dec 31"
    assert ev["venue"] == "Times Square"
    assert "link" not in ev


@pytest.mark.asyncio
async def test_htichips_absent_when_when_is_omitted():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json={"events_results": []})
        await search_events("Lisbon")
        assert "htichips" not in mock.calls.last.request.url.params


@pytest.mark.asyncio
async def test_search_events_when_filter():
    """when='weekend' is accepted for signature compatibility but does not send htichips."""
    mock_response = {
        "events_results": [
            {"title": "Fest", "date": {"when": "This weekend"}},
        ],
    }

    with respx.mock as mock:
        route = mock.get(SERPAPI_BASE).respond(json=mock_response)
        res = await search_events("Chicago", when="weekend")

    req = route.calls[0].request
    assert "htichips" not in req.url.params
    assert len(res["events"]) == 1


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


# ---------------------------------------------------------------------------
# Coverage sweep: pagination, extra angles, dedupe, barren angles
#
# Measured against the live engine for Porto Alegre on 2026-08-01: one query
# returns ~10 results and one slice of the corpus. Page 2 of the *first* query
# alone returned 8 events that four different phrasings had all missed, and a
# "free events" angle returned SerpAPI's no-results error body — which used to
# raise straight out of the tool.
# ---------------------------------------------------------------------------


def _ev(title: str, when: str = "sáb., 1 de ago.") -> dict:
    return {"title": title, "date": {"when": when}}


def _page(*titles: str) -> dict:
    return {"events_results": [_ev(t) for t in titles]}


_NO_RESULTS = {"error": "Google hasn't returned any results for this query."}


@pytest.mark.asyncio
async def test_no_results_body_is_empty_not_an_exception():
    """SerpAPI reports "nothing matched" as an error body, not an empty array.

    The old tests mocked {"events_results": []}, a shape the engine does not
    send for this case, so the real path raised ValueError out of the tool.
    """
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=_NO_RESULTS)

        result = await search_events(query="Quietville")

    assert result["events"] == []
    assert result["total_results"] == 0
    assert result["searches_used"] == 1


@pytest.mark.asyncio
async def test_one_barren_angle_does_not_sink_the_sweep():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(
            side_effect=[
                httpx.Response(200, json=_page("Helloween")),
                httpx.Response(200, json=_NO_RESULTS),
                httpx.Response(200, json=_page("CATS RUN RS 2026")),
            ]
        )

        result = await search_events(
            query="Porto Alegre",
            also_search=["festas gratuitas em Porto Alegre", "esportes em Porto Alegre"],
        )

    titles = [e["title"] for e in result["events"]]
    assert titles == ["Helloween", "CATS RUN RS 2026"]
    assert result["searches_used"] == 3


@pytest.mark.asyncio
async def test_pages_and_when_are_unbilled_no_ops():
    """pages > 1 and when parameters do not perform extra calls, send htichips, or inflate searches_used."""
    with respx.mock as mock:
        route = mock.get(SERPAPI_BASE).respond(
            json=_page("Concert 1", "Concert 2")
        )

        result = await search_events("Porto Alegre", when="weekend", pages=3)

    assert result["searches_used"] == 1, "pages=3 must spend only 1 search per query angle"
    assert route.call_count == 1, "pages=3 must execute exactly 1 HTTP call per query angle"
    assert "htichips" not in route.calls[0].request.url.params, "when parameter must not send htichips"


@pytest.mark.asyncio
async def test_duplicates_across_angles_are_merged_case_and_accent_insensitively():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(
            side_effect=[
                httpx.Response(200, json=_page("ROUPA NOVA")),
                httpx.Response(200, json=_page("Roupa Nova")),
            ]
        )

        result = await search_events(query="Porto Alegre", also_search=["shows"])

    assert result["total_results"] == 1


@pytest.mark.asyncio
async def test_differently_titled_events_are_not_merged():
    """Losing a real event is worse than showing a near-duplicate."""
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(
            side_effect=[
                httpx.Response(200, json=_page("TIAGO IORC")),
                httpx.Response(200, json=_page("TIAGO IORC - TURNÊ TROCO LIKES 10 ANOS")),
            ]
        )

        result = await search_events(query="Porto Alegre", also_search=["shows"])

    assert result["total_results"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("pages", [0, 6])
async def test_pages_out_of_range_rejected(pages):
    with pytest.raises(ValueError, match="pages must be between 1 and 5"):
        await search_events(query="Porto Alegre", pages=pages)


@pytest.mark.asyncio
async def test_too_many_extra_angles_rejected():
    with pytest.raises(ValueError, match="at most 6 extra angles"):
        await search_events(query="X", also_search=[f"a{i}" for i in range(7)])


@pytest.mark.asyncio
async def test_queries_actually_run_are_reported():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=_page("A"))

        result = await search_events(query="Porto Alegre", also_search=["shows", "  "])

    assert result["queries"] == ["Porto Alegre", "shows"]


@pytest.mark.asyncio
async def test_cached_pages_do_not_count_as_searches_spent():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=_page("A"))

        first = await search_events(query="Porto Alegre")
        second = await search_events(query="Porto Alegre")

    assert first["searches_used"] == 1
    assert second["searches_used"] == 0
    assert second["cached"] is True
