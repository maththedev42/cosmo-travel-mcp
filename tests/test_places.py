"""Tests for search_things_to_do (SerpAPI google_maps engine).

Shape assertions run against `tests/fixtures/google_maps_*.json`, captured
from the live engine on 2026-08-01. Attractions and food return different
field sets — food adds price/description/reservation, attractions omit them
entirely — so both are pinned from real bodies rather than one invented shape.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from cosmo_travel_mcp.tools.flights import SERPAPI_BASE
from cosmo_travel_mcp.tools.places import (
    CATEGORY_QUERIES,
    _extension_values,
    search_things_to_do,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


ATTRACTIONS = _fixture("google_maps_things_to_do.json")
RESTAURANTS = _fixture("google_maps_restaurants.json")


@pytest.fixture(autouse=True)
def _set_fake_api_key(monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "fake-test-key")


# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_sends_google_maps_engine_and_category_query():
    route = respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=ATTRACTIONS))

    await search_things_to_do(location="Miami")

    params = route.calls[0].request.url.params
    assert params["engine"] == "google_maps"
    assert params["type"] == "search"
    assert params["q"] == "things to do in Miami"


@respx.mock
@pytest.mark.asyncio
async def test_food_category_changes_the_query():
    route = respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=RESTAURANTS))

    await search_things_to_do(location="Miami", category="restaurants")

    assert route.calls[0].request.url.params["q"] == "restaurants in Miami"


@respx.mock
@pytest.mark.asyncio
async def test_gl_and_hl_absent_unless_requested():
    route = respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=ATTRACTIONS))

    await search_things_to_do(location="Miami")

    params = route.calls[0].request.url.params
    assert "gl" not in params
    assert "hl" not in params


@respx.mock
@pytest.mark.asyncio
async def test_gl_and_hl_sent_when_given():
    route = respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=ATTRACTIONS))

    await search_things_to_do(location="Miami", country="us", language="en")

    params = route.calls[0].request.url.params
    assert params["gl"] == "us"
    assert params["hl"] == "en"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_category_rejected():
    with pytest.raises(ValueError, match="category must be one of"):
        await search_things_to_do(location="Miami", category="teleporters")


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, -1, 21])
async def test_limit_out_of_range_rejected(limit):
    with pytest.raises(ValueError, match="limit must be between 1 and 20"):
        await search_things_to_do(location="Miami", limit=limit)


@pytest.mark.asyncio
@pytest.mark.parametrize("rating", [-0.5, 5.5])
async def test_min_rating_out_of_range_rejected(rating):
    with pytest.raises(ValueError, match="min_rating must be between 0 and 5"):
        await search_things_to_do(location="Miami", min_rating=rating)


def test_every_category_has_a_location_placeholder():
    """A template without {location} would silently search the wrong thing."""
    for name, template in CATEGORY_QUERIES.items():
        assert "{location}" in template, f"{name} template drops the location"


# ---------------------------------------------------------------------------
# Parsing — attractions
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_parses_attraction_from_captured_fixture():
    respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=ATTRACTIONS))

    result = await search_things_to_do(location="Miami")

    assert result["location"] == "Miami"
    assert result["category"] == "attractions"
    first = result["results"][0]
    assert first["name"] == "Jungle Island"
    assert first["category"] == "Tourist attraction"
    assert first["rating"] == 4.1
    assert first["reviews"] == 6554
    assert first["address"] == "1111 Parrot Jungle Trail, Miami, FL 33132"
    assert "Zoo" in first["types"]


@respx.mock
@pytest.mark.asyncio
async def test_coordinates_are_flattened():
    """Callers cluster stops by proximity — they must not have to unwrap a nest."""
    respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=ATTRACTIONS))

    result = await search_things_to_do(location="Miami")

    coords = result["results"][0]["coordinates"]
    assert coords == {"lat": 25.786206999999997, "lng": -80.175026}


@respx.mock
@pytest.mark.asyncio
async def test_operating_hours_survive_per_weekday():
    """The scheduling constraint: a per-weekday map, passed through intact."""
    respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=ATTRACTIONS))

    result = await search_things_to_do(location="Miami")

    hours = result["results"][0]["operating_hours"]
    assert set(hours) == {
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    }
    # The engine separates time and meridiem with U+202F (narrow no-break
    # space), not U+0020. Asserted literally so the value stays byte-exact:
    # normalizing it here would hide a change in what the engine actually sends.
    assert hours["monday"] == "9:30 AM–5 PM"


@respx.mock
@pytest.mark.asyncio
async def test_localized_weekday_keys_survive_untranslated():
    """`hl` localizes the KEYS of operating_hours, not only the values.

    Shape transcribed from a live hl=pt-br call for "museums in Porto Alegre"
    (2026-08-01): MARGS returned exactly these keys, and "Fechado" for a
    closed day. Normalizing them to English would mean guessing a day-name
    map per language — and a wrong guess silently drops the one field the
    itinerary depends on, which is precisely how a display bug in review
    made every Porto Alegre result look like it had no hours at all.
    """
    payload = {
        "local_results": [
            {
                "title": "Museu de Arte do Rio Grande do Sul – MARGS",
                "rating": 4.7,
                "operating_hours": {
                    "sábado": "10:00–19:00",
                    "domingo": "Fechado",
                    "segunda-feira": "Fechado",
                },
            }
        ]
    }
    respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=payload))

    result = await search_things_to_do(location="Porto Alegre", language="pt-br")

    hours = result["results"][0]["operating_hours"]
    assert hours["segunda-feira"] == "Fechado"
    assert hours["sábado"] == "10:00–19:00"
    assert "monday" not in hours


@respx.mock
@pytest.mark.asyncio
async def test_attractions_carry_no_food_fields():
    """The engine omits price/description on attractions — do not invent them."""
    respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=ATTRACTIONS))

    result = await search_things_to_do(location="Miami")

    for place in result["results"]:
        assert "price_range" not in place
        assert "description" not in place
        assert "service_options" not in place
        assert "reservation_link" not in place


# ---------------------------------------------------------------------------
# Parsing — food
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_parses_restaurant_price_and_description():
    respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=RESTAURANTS))

    result = await search_things_to_do(location="Miami", category="restaurants")

    first = result["results"][0]
    assert first["name"] == "Crazy About You"
    assert first["price_range"] == "$30–80"
    assert first["price_from"] == 30
    assert first["description"].startswith("International bistro with bay views")
    assert first["category"] == "American restaurant"


@respx.mock
@pytest.mark.asyncio
async def test_food_categories_expose_service_options_and_reservation():
    respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=RESTAURANTS))

    result = await search_things_to_do(location="Miami", category="restaurants")

    first = result["results"][0]
    assert first["service_options"] == {"dine_in": True, "takeout": True, "delivery": True}
    assert first["reservation_link"].startswith("https://www.google.com/maps/reserve/")


@respx.mock
@pytest.mark.asyncio
async def test_highlights_pulled_out_of_the_extensions_array():
    """`extensions` is a list of single-key dicts, not a dict — it must be searched."""
    respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=RESTAURANTS))

    result = await search_things_to_do(location="Miami", category="restaurants")

    assert "Great cocktails" in result["results"][0]["highlights"]


def test_extension_values_returns_empty_for_absent_group():
    extensions = [{"service_options": ["Dine-in"]}]
    assert _extension_values(extensions, "highlights") == []
    assert _extension_values(None, "highlights") == []
    assert _extension_values("not a list", "highlights") == []


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_limit_truncates_results():
    respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=ATTRACTIONS))

    result = await search_things_to_do(location="Miami", limit=2)

    assert len(result["results"]) == 2
    assert result["total_results"] == 2


@respx.mock
@pytest.mark.asyncio
async def test_min_rating_drops_lower_rated_places():
    respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=ATTRACTIONS))

    unfiltered = await search_things_to_do(location="Miami", limit=20)
    filtered = await search_things_to_do(location="Miami", min_rating=4.5, limit=20)

    assert all(p["rating"] >= 4.5 for p in filtered["results"])
    assert len(filtered["results"]) < len(unfiltered["results"]), (
        "fixture must contain a sub-4.5 place for this test to mean anything"
    )


@respx.mock
@pytest.mark.asyncio
async def test_unrated_place_excluded_by_min_rating():
    """Unknown is not good enough — an unrated place cannot clear a threshold."""
    payload = {"local_results": [{"title": "Mystery Spot", "address": "somewhere"}]}
    respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=payload))

    result = await search_things_to_do(location="Miami", min_rating=4.0)

    assert result["results"] == []


# ---------------------------------------------------------------------------
# Response contract
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_empty_results_are_not_an_error():
    respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json={"local_results": []}))

    result = await search_things_to_do(location="Nowheresville")

    assert result["results"] == []
    assert result["total_results"] == 0


@respx.mock
@pytest.mark.asyncio
async def test_non_list_local_results_does_not_crash():
    respx.get(SERPAPI_BASE).mock(
        return_value=httpx.Response(200, json={"local_results": {"unexpected": "shape"}})
    )

    result = await search_things_to_do(location="Miami")

    assert result["results"] == []


@respx.mock
@pytest.mark.asyncio
async def test_second_identical_search_is_served_from_cache():
    route = respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=ATTRACTIONS))

    first = await search_things_to_do(location="Miami")
    second = await search_things_to_do(location="Miami")

    assert route.call_count == 1
    assert "cached" not in first
    assert second["cached"] is True


@respx.mock
@pytest.mark.asyncio
async def test_quota_warning_injected_when_low():
    respx.get("https://serpapi.com/account.json").respond(json={"plan_searches_left": 8})
    respx.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json=ATTRACTIONS))

    result = await search_things_to_do(location="Miami")

    assert "quota_warning" in result
