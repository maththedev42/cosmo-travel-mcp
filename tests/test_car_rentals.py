"""Tests for search_car_rentals (SerpAPI google_maps engine).

Shape assertions run against `tests/fixtures/google_maps_car_rentals*.json`,
captured live on 2026-08-07. Two fixtures are kept because the airport and the
neighbourhood shapes differ in the one field this tool exists for: every
airport counter in the first runs long or 24-hour days, while the second
contains a branch that is shut on Sundays. A fixture with only airport
counters would never have exercised the closed-day path.

The tool promises no prices. `test_price_level_never_leaks` is the guard on
that promise and should not be relaxed.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from cosmo_travel_mcp.tools.car_rentals import search_car_rentals
from cosmo_travel_mcp.tools.flights import SERPAPI_BASE

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


AIRPORT = _fixture("google_maps_car_rentals.json")
BRANCH = _fixture("google_maps_car_rentals_branch.json")


@pytest.fixture(autouse=True)
def _set_fake_api_key(monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "fake-test-key")


# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_sends_google_maps_engine_and_car_rental_query():
    route = respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=AIRPORT))

    await search_car_rentals(location="Miami International Airport")

    params = route.calls[0].request.url.params
    assert params["engine"] == "google_maps"
    assert params["type"] == "search"
    assert params["q"] == "car rental in Miami International Airport"


@respx.mock
@pytest.mark.asyncio
async def test_gl_and_hl_absent_unless_requested():
    route = respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=AIRPORT))

    await search_car_rentals(location="Miami")

    params = route.calls[0].request.url.params
    assert "gl" not in params
    assert "hl" not in params


@respx.mock
@pytest.mark.asyncio
async def test_gl_and_hl_sent_when_given():
    route = respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=AIRPORT))

    await search_car_rentals(location="Miami", country="us", language="en")

    params = route.calls[0].request.url.params
    assert params["gl"] == "us"
    assert params["hl"] == "en"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, -1, 21])
async def test_limit_out_of_range_rejected(limit):
    with pytest.raises(ValueError, match="limit must be between 1 and 20"):
        await search_car_rentals(location="Miami", limit=limit)


@pytest.mark.asyncio
@pytest.mark.parametrize("rating", [-0.5, 5.5])
async def test_min_rating_out_of_range_rejected(rating):
    with pytest.raises(ValueError, match="min_rating must be between 0 and 5"):
        await search_car_rentals(location="Miami", min_rating=rating)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_parses_office_from_captured_fixture():
    respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=AIRPORT))

    result = await search_car_rentals(location="Miami International Airport")

    assert result["location"] == "Miami International Airport"
    first = result["results"][0]
    assert first["name"] == "SIXT Car Rental - Miami International Airport (MIA)"
    assert first["category"] == "Car rental agency"
    assert first["rating"] == 4.2
    assert first["reviews"] == 11964
    assert first["address"] == "Rental Car Center, 3900 NW 25th St #414, Miami, FL 33142"


@respx.mock
@pytest.mark.asyncio
async def test_website_and_phone_are_surfaced():
    """The whole handoff: the traveller quotes the rate themselves.

    Without a link there is nowhere to get the price this tool cannot fetch,
    and without a phone there is no way to confirm holiday hours.
    """
    respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=AIRPORT))

    result = await search_car_rentals(location="Miami International Airport")

    for office in result["results"]:
        assert office["website"].startswith("http")
        assert office["phone"]


@respx.mock
@pytest.mark.asyncio
async def test_coordinates_are_flattened():
    respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=AIRPORT))

    result = await search_car_rentals(location="Miami International Airport")

    assert result["results"][0]["coordinates"] == {
        "lat": 25.7969552, "lng": -80.260881,
    }


@respx.mock
@pytest.mark.asyncio
async def test_closed_weekday_survives():
    """The reason this tool costs a search.

    A neighbourhood branch shut on Sunday looks identical to a 24-hour airport
    counter until the hours are read. Dropping or flattening this field would
    strand a traveller at a locked door on the day of the pickup.
    """
    respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=BRANCH))

    result = await search_car_rentals(location="Miami, FL")

    branch = next(
        o for o in result["results"] if o["address"] == "940 NW 27th Ave, Miami, FL 33125"
    )
    assert branch["operating_hours"]["sunday"] == "Closed"
    # The engine separates time and meridiem with U+202F (narrow no-break
    # space), not U+0020. Asserted with the raw character, as in test_places —
    # a plain space here is a real failure, not an invisible typo to tidy.
    assert branch["operating_hours"]["monday"] == "7:30 AM–6 PM"


@respx.mock
@pytest.mark.asyncio
async def test_airport_counters_run_long_days():
    """The contrast the two fixtures exist to pin."""
    respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=AIRPORT))

    result = await search_car_rentals(location="Miami International Airport")

    hours = result["results"][0]["operating_hours"]
    assert set(hours) == {
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    }
    assert all(v == "Open 24 hours" for v in hours.values())


# ---------------------------------------------------------------------------
# The no-price promise
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_price_level_never_leaks():
    """Hand-built payload: the captured fixtures carry no `price` at all.

    Google Maps reports a price *level* for some businesses. On a rental
    office that is a vague expensiveness hint, never a daily rate — and a
    caller seeing `price_from: 2` would be one step from presenting "R$ 2/day".
    The shared place normalizer maps both keys, so this tool has to strip them.
    """
    payload = {
        "local_results": [
            {
                "title": "Somewhere Rent A Car",
                "type": "Car rental agency",
                "rating": 4.1,
                "price": "$$",
                "extracted_price": 2,
            }
        ]
    }
    respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=payload))

    result = await search_car_rentals(location="Miami")

    office = result["results"][0]
    assert "price_range" not in office
    assert "price_from" not in office


@respx.mock
@pytest.mark.asyncio
async def test_notes_explain_the_two_gaps():
    """Both notes are load-bearing and must reach the client verbatim."""
    respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=AIRPORT))

    result = await search_car_rentals(location="Miami")

    assert "website" in result["notes"]["pricing"]
    assert "holiday" in result["notes"]["holiday_hours"].lower()


@respx.mock
@pytest.mark.asyncio
async def test_pricing_note_does_not_promise_a_one_way_fee():
    """The drop fee is a maybe, not a given.

    An earlier wording said it "is usually the number that decides", which a
    client model can read as an instruction to go find one. On a
    fleet-rebalancing direction there is none — measured on MIA -> MCO, whose
    itemisation carries facility, surcharge, licence, concession and tax lines
    and no drop fee. Asserting the hedge keeps the note from sliding back.
    """
    respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=AIRPORT))

    note = (await search_car_rentals(location="Miami"))["notes"]["pricing"]

    assert "do not assume" in note
    assert "rebalancing" in note
    # The reverse direction is a separate question, and the note must say so.
    assert "reverse direction" in note


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_min_rating_filters_and_drops_unrated():
    payload = {
        "local_results": [
            {"title": "Well rated", "rating": 4.5},
            {"title": "Poorly rated", "rating": 2.0},
            {"title": "Unrated"},  # unknown is not the same as good
        ]
    }
    respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=payload))

    result = await search_car_rentals(location="Miami", min_rating=4.0)

    assert [o["name"] for o in result["results"]] == ["Well rated"]


@respx.mock
@pytest.mark.asyncio
async def test_limit_caps_results():
    respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=AIRPORT))

    result = await search_car_rentals(location="Miami", limit=2)

    assert result["total_results"] == 2
    assert len(result["results"]) == 2


@respx.mock
@pytest.mark.asyncio
async def test_malformed_local_results_do_not_crash():
    """Degenerate case: the engine sends a non-list, or non-dict members."""
    respx.get(SERPAPI_BASE).mock(
        return_value=httpx.Response(200, json={"local_results": "nope"})
    )

    result = await search_car_rentals(location="Miami")

    assert result["results"] == []
    assert result["total_results"] == 0
