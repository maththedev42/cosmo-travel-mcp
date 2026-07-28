"""Unit tests for flight search tools — zero real network calls."""

from __future__ import annotations

import os

import pytest
import respx
from httpx import Response

from cosmo_travel_mcp.tools.flights import (
    _call_serpapi,
    _get_api_key,
    _map_cabin_class,
    _map_stops,
    _parse_flight_item,
    _parse_flights_response,
    search_flights,
    search_multi_city,
)

SERPAPI_URL = "https://serpapi.com/search.json"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_api_key() -> None:
    """Ensure no SERPAPI_API_KEY leaks between tests."""
    os.environ.pop("SERPAPI_API_KEY", None)


def _set_api_key() -> None:
    os.environ["SERPAPI_API_KEY"] = "test-key-123"


# ---------------------------------------------------------------------------
# Helpers: cabin class / stops mapping
# ---------------------------------------------------------------------------


def test_map_cabin_class_valid() -> None:
    assert _map_cabin_class("economy") == 1
    assert _map_cabin_class("premium_economy") == 2
    assert _map_cabin_class("business") == 3
    assert _map_cabin_class("first") == 4


def test_map_cabin_class_invalid() -> None:
    with pytest.raises(ValueError, match="Invalid cabin_class"):
        _map_cabin_class("supersonic")


def test_map_stops_valid() -> None:
    assert _map_stops("any") == 0
    assert _map_stops("nonstop") == 1
    assert _map_stops("one_or_fewer") == 2
    assert _map_stops("two_or_fewer") == 3
    assert _map_stops(None) is None


def test_map_stops_invalid() -> None:
    with pytest.raises(ValueError, match="Invalid max_stops"):
        _map_stops("exactly_three")


# ---------------------------------------------------------------------------
# Helpers: API key
# ---------------------------------------------------------------------------


def test_get_api_key_missing() -> None:
    with pytest.raises(ValueError, match="SERPAPI_API_KEY is not set"):
        _get_api_key()


def test_get_api_key_present() -> None:
    _set_api_key()
    assert _get_api_key() == "test-key-123"


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def test_parse_flight_item_one_leg() -> None:
    raw = {
        "price": 1234,
        "currency": "BRL",
        "total_duration": 480,
        "flights": [
            {
                "airline": "Latam",
                "flight_number": "LA1234",
                "departure_airport": {
                    "id": "GRU",
                    "name": "São Paulo Guarulhos",
                    "time": "2026-08-01 08:00",
                },
                "arrival_airport": {
                    "id": "JFK",
                    "name": "New York JFK",
                    "time": "2026-08-01 16:00",
                },
                "duration": 480,
            }
        ],
        "departure_token": "tok-abc",
    }
    parsed = _parse_flight_item(raw, "best_flights")
    assert parsed["price"] == 1234
    assert parsed["currency"] == "BRL"
    assert parsed["total_duration_minutes"] == 480
    assert parsed["stops"] == 0
    assert parsed["bucket"] == "best_flights"
    assert parsed["departure_token"] == "tok-abc"
    assert len(parsed["legs"]) == 1
    assert parsed["legs"][0]["airline"] == "Latam"
    assert parsed["legs"][0]["flight_number"] == "LA1234"
    assert parsed["layovers"] == []


def test_parse_flight_item_with_layover() -> None:
    raw = {
        "price": 2500,
        "currency": "USD",
        "total_duration": 900,
        "flights": [
            {
                "airline": "United",
                "flight_number": "UA100",
                "departure_airport": {
                    "id": "SFO",
                    "name": "San Francisco",
                    "time": "2026-08-01 06:00",
                },
                "arrival_airport": {
                    "id": "ORD",
                    "name": "Chicago O'Hare",
                    "time": "2026-08-01 12:00",
                },
                "duration": 360,
                "layover_duration": 90,
            },
            {
                "airline": "United",
                "flight_number": "UA200",
                "departure_airport": {
                    "id": "ORD",
                    "name": "Chicago O'Hare",
                    "time": "2026-08-01 13:30",
                },
                "arrival_airport": {
                    "id": "JFK",
                    "name": "New York JFK",
                    "time": "2026-08-01 17:30",
                },
                "duration": 240,
            },
        ],
        "booking_token": "book-xyz",
    }
    parsed = _parse_flight_item(raw, "other_flights")
    assert parsed["stops"] == 1
    assert parsed["booking_token"] == "book-xyz"
    assert len(parsed["legs"]) == 2
    assert len(parsed["layovers"]) == 1
    assert parsed["layovers"][0]["airport"] == "ORD"
    assert parsed["layovers"][0]["duration_minutes"] == 90


def test_parse_flights_response_combines_buckets() -> None:
    data = {
        "best_flights": [
            {"price": 100, "flights": [], "total_duration": 60},
        ],
        "other_flights": [
            {"price": 200, "flights": [], "total_duration": 120},
            {"price": 300, "flights": [], "total_duration": 180},
        ],
    }
    result = _parse_flights_response(data)
    assert result["total_count"] == 3
    assert result["best_flights_count"] == 1
    assert result["other_flights_count"] == 2
    assert result["flights"][0]["bucket"] == "best_flights"
    assert result["flights"][1]["bucket"] == "other_flights"
    assert result["flights"][2]["bucket"] == "other_flights"


# ---------------------------------------------------------------------------
# SerpAPI error propagation
# ---------------------------------------------------------------------------


@respx.mock
async def test_serpapi_error_response() -> None:
    """SerpAPI returns {"error": "..."} — tool must propagate, not mask."""
    _set_api_key()
    respx.get(SERPAPI_URL).mock(
        return_value=Response(200, json={"error": "Invalid API key"})
    )
    with pytest.raises(RuntimeError, match="Invalid API key"):
        await _call_serpapi({"type": 2})


# ---------------------------------------------------------------------------
# Tool: search_flights
# ---------------------------------------------------------------------------


@respx.mock
async def test_search_flights_one_way() -> None:
    """One-way search: type=2, no return_date."""
    _set_api_key()
    respx.get(SERPAPI_URL).mock(
        return_value=Response(
            200,
            json={
                "best_flights": [
                    {
                        "price": 450,
                        "currency": "BRL",
                        "total_duration": 300,
                        "flights": [
                            {
                                "airline": "Gol",
                                "flight_number": "G31234",
                                "departure_airport": {
                                    "id": "GRU",
                                    "name": "São Paulo",
                                    "time": "2026-09-01 10:00",
                                },
                                "arrival_airport": {
                                    "id": "SSA",
                                    "name": "Salvador",
                                    "time": "2026-09-01 12:30",
                                },
                                "duration": 150,
                            }
                        ],
                        "booking_token": "book-oneway-1",
                    }
                ],
                "other_flights": [],
            },
        )
    )

    result = await search_flights(
        origin="GRU",
        destination="SSA",
        outbound_date="2026-09-01",
    )

    assert result["total_count"] == 1
    assert result["best_flights_count"] == 1
    assert result["other_flights_count"] == 0
    flight = result["flights"][0]
    assert flight["price"] == 450
    assert flight["currency"] == "BRL"
    assert flight["stops"] == 0
    assert flight["booking_token"] == "book-oneway-1"
    assert "phase" not in result  # one-way has no phase annotation

    # Verify the request params sent to SerpAPI
    req = respx.calls.last.request
    assert req.url.params["type"] == "2"
    assert req.url.params["departure_id"] == "GRU"
    assert req.url.params["arrival_id"] == "SSA"
    assert req.url.params["outbound_date"] == "2026-09-01"


@respx.mock
async def test_search_flights_round_trip_phase1() -> None:
    """Round trip phase 1: type=1, departure_token on items, phase annotation."""
    _set_api_key()
    respx.get(SERPAPI_URL).mock(
        return_value=Response(
            200,
            json={
                "best_flights": [
                    {
                        "price": 2100,
                        "currency": "BRL",
                        "total_duration": 720,
                        "flights": [
                            {
                                "airline": "Latam",
                                "flight_number": "LA8000",
                                "departure_airport": {
                                    "id": "GRU",
                                    "name": "São Paulo",
                                    "time": "2026-10-01 22:00",
                                },
                                "arrival_airport": {
                                    "id": "MIA",
                                    "name": "Miami",
                                    "time": "2026-10-02 06:00",
                                },
                                "duration": 480,
                            }
                        ],
                        "departure_token": "rt-tok-phase1-abc",
                    }
                ],
                "other_flights": [
                    {
                        "price": 1950,
                        "currency": "BRL",
                        "total_duration": 780,
                        "flights": [
                            {
                                "airline": "American",
                                "flight_number": "AA900",
                                "departure_airport": {
                                    "id": "GRU",
                                    "name": "São Paulo",
                                    "time": "2026-10-01 08:00",
                                },
                                "arrival_airport": {
                                    "id": "MIA",
                                    "name": "Miami",
                                    "time": "2026-10-01 19:00",
                                },
                                "duration": 660,
                            }
                        ],
                        "departure_token": "rt-tok-phase1-def",
                    }
                ],
            },
        )
    )

    result = await search_flights(
        origin="GRU",
        destination="MIA",
        outbound_date="2026-10-01",
        return_date="2026-10-15",
    )

    assert result["phase"] == "outbound"
    assert "departure_token" in result["note"]
    assert result["total_count"] == 2
    assert result["best_flights_count"] == 1
    assert result["other_flights_count"] == 1

    # Both items have departure_token
    for flight in result["flights"]:
        assert "departure_token" in flight
        assert flight["departure_token"].startswith("rt-tok-phase1-")

    # Verify the request
    req = respx.calls.last.request
    assert req.url.params["type"] == "1"
    assert req.url.params["return_date"] == "2026-10-15"


@respx.mock
async def test_search_flights_round_trip_phase2() -> None:
    """Round trip phase 2: departure_token sent, return-flight options parsed."""
    _set_api_key()
    respx.get(SERPAPI_URL).mock(
        return_value=Response(
            200,
            json={
                "best_flights": [
                    {
                        "price": 2100,
                        "currency": "BRL",
                        "total_duration": 500,
                        "flights": [
                            {
                                "airline": "Latam",
                                "flight_number": "LA8001",
                                "departure_airport": {
                                    "id": "MIA",
                                    "name": "Miami",
                                    "time": "2026-10-15 20:00",
                                },
                                "arrival_airport": {
                                    "id": "GRU",
                                    "name": "São Paulo",
                                    "time": "2026-10-16 05:00",
                                },
                                "duration": 540,
                            }
                        ],
                        "booking_token": "book-rt-phase2-xyz",
                    }
                ],
                "other_flights": [],
            },
        )
    )

    result = await search_flights(
        origin="GRU",
        destination="MIA",
        outbound_date="2026-10-01",
        return_date="2026-10-15",
        departure_token="rt-tok-phase1-abc",
    )

    assert result["phase"] == "return"
    assert "return-flight options" in result["note"]
    assert result["total_count"] == 1
    flight = result["flights"][0]
    assert flight["booking_token"] == "book-rt-phase2-xyz"

    # Verify departure_token is passed to SerpAPI
    req = respx.calls.last.request
    assert req.url.params["departure_token"] == "rt-tok-phase1-abc"
    assert req.url.params["type"] == "1"


@respx.mock
async def test_search_flights_missing_api_key() -> None:
    """When SERPAPI_API_KEY is missing, return a clear error (not a stack trace)."""
    with pytest.raises(ValueError, match="SERPAPI_API_KEY is not set"):
        await search_flights(
            origin="GRU",
            destination="SSA",
            outbound_date="2026-09-01",
        )


@respx.mock
async def test_search_flights_cabin_class_mapping() -> None:
    """Verify cabin_class values map to correct SerpAPI travel_class."""
    _set_api_key()
    respx.get(SERPAPI_URL).mock(
        return_value=Response(
            200,
            json={"best_flights": [], "other_flights": []},
        )
    )

    await search_flights(
        origin="GRU", destination="JFK", outbound_date="2026-11-01",
        cabin_class="business",
    )
    req = respx.calls.last.request
    assert req.url.params["travel_class"] == "3"

    await search_flights(
        origin="GRU", destination="JFK", outbound_date="2026-11-01",
        cabin_class="first",
    )
    req = respx.calls.last.request
    assert req.url.params["travel_class"] == "4"


@respx.mock
async def test_search_flights_max_stops_mapping() -> None:
    """Verify max_stops values map to correct SerpAPI stops param."""
    _set_api_key()
    respx.get(SERPAPI_URL).mock(
        return_value=Response(
            200,
            json={"best_flights": [], "other_flights": []},
        )
    )

    await search_flights(
        origin="GRU", destination="JFK", outbound_date="2026-11-01",
        max_stops="nonstop",
    )
    req = respx.calls.last.request
    assert req.url.params["stops"] == "1"

    await search_flights(
        origin="GRU", destination="JFK", outbound_date="2026-11-01",
        max_stops="one_or_fewer",
    )
    req = respx.calls.last.request
    assert req.url.params["stops"] == "2"


@respx.mock
async def test_search_flights_localization_params() -> None:
    """Verify country/language map to gl/hl."""
    _set_api_key()
    respx.get(SERPAPI_URL).mock(
        return_value=Response(
            200,
            json={"best_flights": [], "other_flights": []},
        )
    )

    await search_flights(
        origin="GRU", destination="JFK", outbound_date="2026-11-01",
        country="BR", language="pt",
    )
    req = respx.calls.last.request
    assert req.url.params["gl"] == "BR"
    assert req.url.params["hl"] == "pt"


# ---------------------------------------------------------------------------
# Tool: search_multi_city
# ---------------------------------------------------------------------------


@respx.mock
async def test_search_multi_city_three_legs() -> None:
    """Multi-city search with 3 legs: type=3, multi_city_json built correctly."""
    _set_api_key()
    respx.get(SERPAPI_URL).mock(
        return_value=Response(
            200,
            json={
                "best_flights": [
                    {
                        "price": 3200,
                        "currency": "BRL",
                        "total_duration": 1500,
                        "flights": [
                            {
                                "airline": "Latam",
                                "flight_number": "LA100",
                                "departure_airport": {
                                    "id": "GRU",
                                    "name": "São Paulo",
                                    "time": "2026-12-01 08:00",
                                },
                                "arrival_airport": {
                                    "id": "LIM",
                                    "name": "Lima",
                                    "time": "2026-12-01 13:00",
                                },
                                "duration": 300,
                            },
                            {
                                "airline": "Latam",
                                "flight_number": "LA200",
                                "departure_airport": {
                                    "id": "LIM",
                                    "name": "Lima",
                                    "time": "2026-12-05 10:00",
                                },
                                "arrival_airport": {
                                    "id": "SCL",
                                    "name": "Santiago",
                                    "time": "2026-12-05 15:00",
                                },
                                "duration": 300,
                            },
                            {
                                "airline": "Latam",
                                "flight_number": "LA300",
                                "departure_airport": {
                                    "id": "SCL",
                                    "name": "Santiago",
                                    "time": "2026-12-10 18:00",
                                },
                                "arrival_airport": {
                                    "id": "GRU",
                                    "name": "São Paulo",
                                    "time": "2026-12-10 23:00",
                                },
                                "duration": 300,
                            },
                        ],
                        "booking_token": "book-multi-1",
                    }
                ],
                "other_flights": [],
            },
        )
    )

    result = await search_multi_city(
        legs=[
            {"origin": "GRU", "destination": "LIM", "date": "2026-12-01"},
            {"origin": "LIM", "destination": "SCL", "date": "2026-12-05"},
            {"origin": "SCL", "destination": "GRU", "date": "2026-12-10"},
        ],
        cabin_class="economy",
    )

    assert result["total_count"] == 1
    flight = result["flights"][0]
    assert flight["price"] == 3200
    assert flight["stops"] == 2  # 3 legs → 2 layovers
    assert len(flight["legs"]) == 3
    assert flight["booking_token"] == "book-multi-1"

    # Verify multi_city_json in the request
    req = respx.calls.last.request
    assert req.url.params["type"] == "3"
    # httpx serializes list params — check raw query string contains the structure
    assert "multi_city_json" in req.url.query.decode()


@respx.mock
async def test_search_multi_city_too_few_legs() -> None:
    """Multi-city with fewer than 2 legs should raise."""
    _set_api_key()
    with pytest.raises(ValueError, match="at least 2 legs"):
        await search_multi_city(
            legs=[{"origin": "GRU", "destination": "JFK", "date": "2026-12-01"}],
        )


@respx.mock
async def test_search_multi_city_too_many_legs() -> None:
    """Multi-city with more than 6 legs should raise."""
    _set_api_key()
    with pytest.raises(ValueError, match="at most 6 legs"):
        await search_multi_city(
            legs=[
                {"origin": "A", "destination": "B", "date": "2026-01-01"},
                {"origin": "B", "destination": "C", "date": "2026-01-02"},
                {"origin": "C", "destination": "D", "date": "2026-01-03"},
                {"origin": "D", "destination": "E", "date": "2026-01-04"},
                {"origin": "E", "destination": "F", "date": "2026-01-05"},
                {"origin": "F", "destination": "G", "date": "2026-01-06"},
                {"origin": "G", "destination": "H", "date": "2026-01-07"},
            ],
        )


@respx.mock
async def test_search_multi_city_missing_api_key() -> None:
    """Multi-city with missing key: clear error."""
    with pytest.raises(ValueError, match="SERPAPI_API_KEY is not set"):
        await search_multi_city(
            legs=[
                {"origin": "GRU", "destination": "JFK", "date": "2026-01-01"},
                {"origin": "JFK", "destination": "GRU", "date": "2026-01-15"},
            ],
        )
