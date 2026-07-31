"""Tests for flight search tools (SerpAPI google_flights engine)."""

from __future__ import annotations

import json
import os

import httpx
import pytest
import respx

from cosmo_travel_mcp.tools.flights import (
    SERPAPI_BASE,
    _build_base_params,
    _parse_flight_item,
    _parse_flights_response,
    _parse_price_insights,
    search_flights,
    search_multi_city,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _set_fake_api_key(monkeypatch):
    """Set a fake SERPAPI_API_KEY so tests never hit the real guard."""
    monkeypatch.setenv("SERPAPI_API_KEY", "fake-test-key")



def _leg_fixture(
    departure_id: str = "JFK",
    arrival_id: str = "LAX",
    departure_time: str = "2025-12-01T08:00:00",
    arrival_time: str = "2025-12-01T11:30:00",
    airline: str = "Delta",
    flight_number: str = "DL123",
    duration: int = 210,
) -> dict:
    """Build a single-leg dict in the real SerpAPI shape (no per-leg layover fields)."""
    return {
        "airline": airline,
        "airline_logo": "https://example.com/dl.png",
        "flight_number": flight_number,
        "departure_airport": {
            "id": departure_id,
            "name": f"{departure_id} Airport",
            "time": departure_time,
        },
        "arrival_airport": {
            "id": arrival_id,
            "name": f"{arrival_id} Airport",
            "time": arrival_time,
        },
        "duration": duration,
    }


def _flight_item_fixture(
    price: int = 350,
    total_duration: int = 210,
    legs: list[dict] | None = None,
    departure_token: str = "",
    booking_token: str = "",
    layovers: list[dict] | None = None,
) -> dict:
    """Build a flight item in the real SerpAPI shape with a top-level ``layovers`` array."""
    item: dict = {
        "price": price,
        "total_duration": total_duration,
        "flights": legs or [_leg_fixture()],
    }
    if departure_token:
        item["departure_token"] = departure_token
    if booking_token:
        item["booking_token"] = booking_token
    if layovers is not None:
        item["layovers"] = layovers
    return item


# ---------------------------------------------------------------------------
# _build_base_params tests
# ---------------------------------------------------------------------------


def test_build_base_params_minimal():
    params = _build_base_params(
        adults=1, children=0, cabin_class="economy", currency="BRL",
        country=None, language=None, max_stops=None,
    )
    assert params["adults"] == 1
    assert params["children"] == 0
    assert params["travel_class"] == 1
    assert params["currency"] == "BRL"
    assert "stops" not in params


def test_build_base_params_with_locale():
    params = _build_base_params(
        adults=2, children=0, cabin_class="business", currency="USD",
        country="US", language="en", max_stops="nonstop",
    )
    assert params["adults"] == 2
    assert params["travel_class"] == 3
    assert params["currency"] == "USD"
    assert params["gl"] == "US"
    assert params["hl"] == "en"
    assert params["stops"] == 1


# ---------------------------------------------------------------------------
# _parse_flight_item tests
# ---------------------------------------------------------------------------


def test_parse_flight_item_currency_from_top_level():
    """Defect 3 fix: currency comes from top-level search_parameters, not per-item."""
    item = _flight_item_fixture(price=500)
    parsed = _parse_flight_item(item, "best_flights", "USD")
    assert parsed["currency"] == "USD"
    assert parsed["price"] == 500


def test_parse_flight_item_departure_token():
    item = _flight_item_fixture(departure_token="tok_abc123")
    parsed = _parse_flight_item(item, "best_flights", "BRL")
    assert parsed["departure_token"] == "tok_abc123"


def test_parse_flight_item_booking_token():
    item = _flight_item_fixture(booking_token="book_xyz")
    parsed = _parse_flight_item(item, "other_flights", "BRL")
    assert parsed["booking_token"] == "book_xyz"


def test_parse_flight_item_layovers_from_array():
    """Defect 2 fix: layovers come from item['layovers'] array with duration."""
    layovers = [
        {"duration": 90, "name": "Miami International Airport", "id": "MIA"},
        {"duration": 45, "name": "Dallas/Fort Worth International Airport", "id": "DFW"},
    ]
    item = _flight_item_fixture(
        legs=[_leg_fixture("JFK", "MIA"), _leg_fixture("MIA", "DFW"), _leg_fixture("DFW", "LAX")],
        layovers=layovers,
    )
    parsed = _parse_flight_item(item, "best_flights", "BRL")
    assert len(parsed["layovers"]) == 2
    assert parsed["layovers"][0]["airport"] == "MIA"
    assert parsed["layovers"][0]["duration_minutes"] == 90
    assert parsed["layovers"][1]["airport"] == "DFW"
    assert parsed["layovers"][1]["duration_minutes"] == 45
    assert parsed["stops"] == 2


def test_parse_flight_item_layovers_fallback():
    """When no layovers array, infer from consecutive legs (no duration)."""
    item = _flight_item_fixture(
        legs=[_leg_fixture("JFK", "ATL"), _leg_fixture("ATL", "LAX")],
    )
    parsed = _parse_flight_item(item, "best_flights", "BRL")
    assert len(parsed["layovers"]) == 1
    assert parsed["layovers"][0]["airport"] == "ATL"
    assert "duration_minutes" not in parsed["layovers"][0]


def test_parse_flight_item_layovers_empty_when_no_array_and_direct():
    """Direct flight with no layovers array -> empty layovers list."""
    item = _flight_item_fixture(legs=[_leg_fixture("JFK", "LAX")])
    parsed = _parse_flight_item(item, "best_flights", "BRL")
    assert parsed["layovers"] == []


# ---------------------------------------------------------------------------
# _parse_flights_response tests
# ---------------------------------------------------------------------------


def test_parse_flights_response_combines_best_and_other():
    data = {
        "search_parameters": {"currency": "USD"},
        "best_flights": [_flight_item_fixture(price=300)],
        "other_flights": [_flight_item_fixture(price=250)],
    }
    result = _parse_flights_response(data)
    assert result["total_best"] == 1
    assert result["total_other"] == 1
    assert len(result["flights"]) == 2
    assert result["flights"][0]["source"] == "best_flights"
    assert result["flights"][1]["source"] == "other_flights"
    assert result["flights"][0]["currency"] == "USD"
    assert result["flights"][1]["currency"] == "USD"


def test_parse_flights_response_currency_fallback():
    """When search_parameters.currency is absent, fall back to requested_currency."""
    data = {
        "search_parameters": {},
        "best_flights": [_flight_item_fixture()],
        "other_flights": [],
    }
    result = _parse_flights_response(data, requested_currency="EUR")
    assert result["flights"][0]["currency"] == "EUR"


# ---------------------------------------------------------------------------
# _parse_price_insights tests
# ---------------------------------------------------------------------------


def test_parse_price_insights_full():
    """Full price_insights with history -> normalized, advice composed."""
    raw = {
        "lowest_price": 5416,
        "price_level": "low",
        "typical_price_range": [5800, 7500],
        "price_history": [
            [1750000000, 5500],
            [1750086400, 5400],
            [1750172800, 5416],
        ],
    }
    result = _parse_price_insights(raw, "BRL")
    assert result is not None
    assert result["lowest_price"] == 5416
    assert result["price_level"] == "low"
    assert result["typical_price_range"] == [5800, 7500]
    assert len(result["price_history"]) == 3
    # Dates are ISO-8601 UTZ.
    assert result["price_history"][0]["date"] == "2025-06-15"
    assert result["price_history"][0]["price"] == 5500
    # Advice string mentions price level + typical range.
    assert "low" in result["advice"]
    assert "5416" in result["advice"]
    assert "5800" in result["advice"]
    assert "BRL" in result["advice"]


def test_parse_price_insights_none():
    """None / empty dict -> returns None (absent)."""
    assert _parse_price_insights({}, "USD") is None
    assert _parse_price_insights(None, "USD") is None


def test_parse_price_insights_no_level():
    """price_insights without price_level still produces advice from available fields."""
    raw = {
        "lowest_price": 5000,
        "typical_price_range": [4800, 6200],
    }
    result = _parse_price_insights(raw, "USD")
    assert result is not None
    assert "price_level" not in result
    assert "lowest_price" in result
    assert "advice" in result


def test_parse_price_insights_bare_minimum():
    """Only a lowest_price — still produces usable output."""
    raw = {"lowest_price": 500}
    result = _parse_price_insights(raw, "EUR")
    assert result is not None
    assert result["lowest_price"] == 500
    assert "price_level" not in result
    assert "price_history" not in result
    # Advice still mentions the price.
    assert "500" in result["advice"]


def test_parse_price_insights_no_meaningful_fields():
    """price_insights dict with no useful keys -> None."""
    raw = {"some_unknown_key": 123}
    assert _parse_price_insights(raw, "USD") is None


def test_parse_price_insights_history_bad_points():
    """Malformed history points are skipped, good ones kept."""
    raw = {
        "lowest_price": 100,
        "price_history": [
            [1750000000, 100],
            "not_a_list",
            [1750086400, None],  # None price → 0
            [],
        ],
    }
    result = _parse_price_insights(raw, "USD")
    assert result is not None
    # Only two valid points survive (the None-price one becomes price=0).
    assert len(result["price_history"]) == 2
    assert result["price_history"][0]["price"] == 100
    assert result["price_history"][1]["price"] == 0


# ---------------------------------------------------------------------------
# _parse_flights_response — price_insights passthrough
# ---------------------------------------------------------------------------


def test_parse_flights_response_with_price_insights():
    """price_insights in SerpAPI data -> appears in parsed response."""
    data = {
        "search_parameters": {"currency": "USD"},
        "best_flights": [_flight_item_fixture(price=300)],
        "other_flights": [],
        "price_insights": {
            "lowest_price": 300,
            "price_level": "low",
            "typical_price_range": [280, 400],
        },
    }
    result = _parse_flights_response(data)
    assert "price_insights" in result
    assert result["price_insights"]["lowest_price"] == 300
    assert "advice" in result["price_insights"]


def test_parse_flights_response_without_price_insights():
    """No price_insights key -> field absent, no error."""
    data = {
        "search_parameters": {},
        "best_flights": [_flight_item_fixture()],
        "other_flights": [],
    }
    result = _parse_flights_response(data)
    assert "price_insights" not in result


def test_parse_flights_response_empty_price_insights():
    """price_insights is empty dict -> field absent."""
    data = {
        "search_parameters": {},
        "best_flights": [_flight_item_fixture()],
        "other_flights": [],
        "price_insights": {},
    }
    result = _parse_flights_response(data)
    assert "price_insights" not in result


# ---------------------------------------------------------------------------
# _parse_flight_item — carbon_emissions passthrough
# ---------------------------------------------------------------------------


def test_parse_flight_item_with_carbon_emissions():
    """Flight item with carbon_emissions -> kg conversion, all fields present."""
    item = _flight_item_fixture(price=350)
    item["carbon_emissions"] = {
        "this_flight": 85000,
        "typical_for_this_route": 78000,
        "difference_percent": 9,
    }
    parsed = _parse_flight_item(item, "best_flights", "USD")
    assert "carbon_emissions" in parsed
    ce = parsed["carbon_emissions"]
    assert ce["this_flight_kg"] == 85  # 85000 / 1000
    assert ce["typical_for_route_kg"] == 78  # 78000 / 1000
    assert ce["difference_percent"] == 9


def test_parse_flight_item_without_carbon_emissions():
    """Flight item without carbon_emissions -> field absent, no error."""
    item = _flight_item_fixture()
    parsed = _parse_flight_item(item, "best_flights", "USD")
    assert "carbon_emissions" not in parsed


def test_parse_flight_item_carbon_emissions_mixed():
    """One item with emissions, one without — only the first gets the field."""
    data = {
        "search_parameters": {"currency": "USD"},
        "best_flights": [],
        "other_flights": [
            {
                "price": 450,
                "total_duration": 180,
                "flights": [_leg_fixture()],
                "carbon_emissions": {
                    "this_flight": 120000,
                    "typical_for_this_route": 110000,
                    "difference_percent": 9,
                },
            },
            {
                "price": 320,
                "total_duration": 200,
                "flights": [_leg_fixture("JFK", "MIA")],
            },
        ],
    }
    result = _parse_flights_response(data)
    assert len(result["flights"]) == 2
    assert "carbon_emissions" in result["flights"][0]
    assert result["flights"][0]["carbon_emissions"]["this_flight_kg"] == 120
    assert "carbon_emissions" not in result["flights"][1]


# ---------------------------------------------------------------------------
# search_flights tests (mocked HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_flights_one_way():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(
            return_value=httpx.Response(
                200,
                json={
                    "search_parameters": {"currency": "BRL"},
                    "best_flights": [_flight_item_fixture(price=450)],
                    "other_flights": [],
                },
            )
        )

        result = await search_flights(
            origin="GRU", destination="JFK", outbound_date="2025-12-01",
        )

        req = mock.calls.last.request
        assert req.url.params["engine"] == "google_flights"
        assert req.url.params["departure_id"] == "GRU"
        assert req.url.params["arrival_id"] == "JFK"
        assert req.url.params["type"] == "2"
        assert "return_date" not in req.url.params
        assert len(result["flights"]) == 1
        assert result["flights"][0]["price"] == 450


@pytest.mark.asyncio
async def test_search_flights_round_trip_phase1():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(
            return_value=httpx.Response(
                200,
                json={
                    "search_parameters": {"currency": "BRL"},
                    "best_flights": [
                        _flight_item_fixture(price=1200, departure_token="tok1")
                    ],
                    "other_flights": [
                        _flight_item_fixture(price=980, departure_token="tok2")
                    ],
                },
            )
        )

        result = await search_flights(
            origin="GRU", destination="MIA",
            outbound_date="2025-12-01", return_date="2025-12-10",
        )

        req = mock.calls.last.request
        assert req.url.params["type"] == "1"
        assert result["flights"][0]["departure_token"] == "tok1"
        assert result["flights"][1]["departure_token"] == "tok2"
        assert "outbound options" in result["phase"]


@pytest.mark.asyncio
async def test_search_flights_round_trip_phase2():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(
            return_value=httpx.Response(
                200,
                json={
                    "search_parameters": {"currency": "BRL"},
                    "best_flights": [
                        _flight_item_fixture(price=850, booking_token="book_abc")
                    ],
                    "other_flights": [],
                },
            )
        )

        result = await search_flights(
            origin="GRU", destination="MIA",
            outbound_date="2025-12-01", return_date="2025-12-10",
            departure_token="tok1",
        )

        req = mock.calls.last.request
        assert req.url.params["departure_token"] == "tok1"
        assert req.url.params["type"] == "1"
        assert result["flights"][0]["booking_token"] == "book_abc"
        assert "return options" in result["phase"]


@pytest.mark.asyncio
async def test_search_flights_departure_token_without_return_date():
    """Defect 4 fix: departure_token without return_date raises clear error."""
    with pytest.raises(ValueError, match="departure_token requires return_date"):
        await search_flights(
            origin="GRU", destination="MIA",
            outbound_date="2025-12-01", departure_token="tok1",
        )


@pytest.mark.asyncio
async def test_search_flights_missing_api_key():
    """Missing SERPAPI_API_KEY -> clear error, no raw stack trace."""
    old = os.environ.pop("SERPAPI_API_KEY", None)
    try:
        with pytest.raises(ValueError, match="SERPAPI_API_KEY is not set"):
            await search_flights(
                origin="GRU", destination="JFK", outbound_date="2025-12-01",
            )
    finally:
        if old is not None:
            os.environ["SERPAPI_API_KEY"] = old
        else:
            os.environ.pop("SERPAPI_API_KEY", None)


@pytest.mark.asyncio
async def test_search_flights_cabin_class_mapping():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(
            return_value=httpx.Response(
                200,
                json={"search_parameters": {}, "best_flights": [], "other_flights": []},
            )
        )
        await search_flights(
            origin="GRU", destination="JFK", outbound_date="2025-12-01",
            cabin_class="business",
        )
        req = mock.calls.last.request
        assert req.url.params["travel_class"] == "3"


@pytest.mark.asyncio
async def test_search_flights_max_stops_mapping():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(
            return_value=httpx.Response(
                200,
                json={"search_parameters": {}, "best_flights": [], "other_flights": []},
            )
        )
        await search_flights(
            origin="GRU", destination="JFK", outbound_date="2025-12-01",
            max_stops="nonstop",
        )
        req = mock.calls.last.request
        assert req.url.params["stops"] == "1"


@pytest.mark.asyncio
async def test_search_flights_localization_params():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(
            return_value=httpx.Response(
                200,
                json={"search_parameters": {}, "best_flights": [], "other_flights": []},
            )
        )
        await search_flights(
            origin="GRU", destination="JFK", outbound_date="2025-12-01",
            country="US", language="en",
        )
        req = mock.calls.last.request
        assert req.url.params["gl"] == "US"
        assert req.url.params["hl"] == "en"


@pytest.mark.asyncio
async def test_serpapi_error_response():
    """SerpAPI returns {"error": "..."} -> tool propagates it, doesn't mask."""
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(
            return_value=httpx.Response(200, json={"error": "Invalid API key"})
        )
        with pytest.raises(ValueError, match="Invalid API key"):
            await search_flights(
                origin="GRU", destination="JFK", outbound_date="2025-12-01",
            )


# ---------------------------------------------------------------------------
# search_multi_city tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_multi_city_three_legs():
    """Defect 1 fix: multi_city_json is sent as JSON, not Python repr."""
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(
            return_value=httpx.Response(
                200,
                json={
                    "search_parameters": {"currency": "USD"},
                    "best_flights": [_flight_item_fixture(price=1500)],
                    "other_flights": [],
                },
            )
        )

        result = await search_multi_city(
            legs=[
                {"origin": "SEA", "destination": "JFK", "date": "2025-12-01"},
                {"origin": "JFK", "destination": "LHR", "date": "2025-12-10"},
                {"origin": "LHR", "destination": "SEA", "date": "2025-12-20"},
            ],
            currency="USD",
        )

        req = mock.calls.last.request
    assert req.url.params["type"] == "3"

    mcj_raw = req.url.params["multi_city_json"]
    # Must be valid JSON (the old code sent Python repr with single quotes).
    mcj = json.loads(mcj_raw)
    assert len(mcj) == 3
    assert mcj[0] == {"departure_id": "SEA", "arrival_id": "JFK", "date": "2025-12-01"}
    assert mcj[1] == {"departure_id": "JFK", "arrival_id": "LHR", "date": "2025-12-10"}
    assert mcj[2] == {"departure_id": "LHR", "arrival_id": "SEA", "date": "2025-12-20"}

    assert len(result["flights"]) == 1
    assert result["flights"][0]["currency"] == "USD"


@pytest.mark.asyncio
async def test_search_multi_city_with_price_insights():
    """Multi-city response with price_insights -> same passthrough works."""
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(
            return_value=httpx.Response(
                200,
                json={
                    "search_parameters": {"currency": "USD"},
                    "best_flights": [_flight_item_fixture(price=1200)],
                    "other_flights": [],
                    "price_insights": {
                        "lowest_price": 1100,
                        "price_level": "typical",
                        "typical_price_range": [1050, 1300],
                    },
                },
            )
        )

        result = await search_multi_city(
            legs=[
                {"origin": "SEA", "destination": "JFK", "date": "2025-12-01"},
                {"origin": "JFK", "destination": "LHR", "date": "2025-12-10"},
            ],
            currency="USD",
        )

    assert "price_insights" in result
    assert result["price_insights"]["lowest_price"] == 1100
    assert "advice" in result["price_insights"]


@pytest.mark.asyncio
async def test_search_multi_city_too_few_legs():
    with pytest.raises(ValueError, match="at least 2 legs"):
        await search_multi_city(
            legs=[{"origin": "SEA", "destination": "JFK", "date": "2025-12-01"}],
        )


@pytest.mark.asyncio
async def test_search_multi_city_too_many_legs():
    with pytest.raises(ValueError, match="at most 6 legs"):
        await search_multi_city(
            legs=[{"origin": f"C{i}", "destination": f"C{i+1}", "date": "2025-12-01"}
                  for i in range(7)],
        )


@pytest.mark.asyncio
async def test_search_multi_city_missing_api_key():
    old = os.environ.pop("SERPAPI_API_KEY", None)
    try:
        with pytest.raises(ValueError, match="SERPAPI_API_KEY is not set"):
            await search_multi_city(
                legs=[
                    {"origin": "SEA", "destination": "JFK", "date": "2025-12-01"},
                    {"origin": "JFK", "destination": "LHR", "date": "2025-12-10"},
                ],
            )
    finally:
        if old is not None:
            os.environ["SERPAPI_API_KEY"] = old
        else:
            os.environ.pop("SERPAPI_API_KEY", None)

# ---------------------------------------------------------------------------
# Retry-on-transient-failure tests (prompt A-01)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retry_timeout_then_success(monkeypatch):
    """First call raises a timeout, second succeeds -> caller returns parsed result; 2 calls."""
    monkeypatch.setattr("cosmo_travel_mcp.tools.flights._RETRY_BACKOFF_SECONDS", 0)

    call_count = 0

    def handler(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.TimeoutException("read timeout")
        return httpx.Response(
            200,
            json={"search_parameters": {}, "best_flights": [], "other_flights": []},
        )

    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(side_effect=handler)

        result = await search_flights(
            origin="GRU", destination="JFK", outbound_date="2025-12-01",
        )

    assert call_count == 2
    assert "flights" in result


@pytest.mark.asyncio
async def test_retry_both_timeouts_raise(monkeypatch):
    """Both calls raise timeouts -> the error propagates; exactly 2 calls."""
    monkeypatch.setattr("cosmo_travel_mcp.tools.flights._RETRY_BACKOFF_SECONDS", 0)

    call_count = 0

    def handler(request):
        nonlocal call_count
        call_count += 1
        raise httpx.TimeoutException("read timeout")

    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(side_effect=handler)

        with pytest.raises(httpx.TimeoutException):
            await search_flights(
                origin="GRU", destination="JFK", outbound_date="2025-12-01",
            )

    assert call_count == 2


@pytest.mark.asyncio
async def test_retry_503_then_200(monkeypatch):
    """503 then 200 -> success; 2 calls."""
    monkeypatch.setattr("cosmo_travel_mcp.tools.flights._RETRY_BACKOFF_SECONDS", 0)

    call_count = 0

    def handler(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={"search_parameters": {}, "best_flights": [], "other_flights": []},
        )

    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(side_effect=handler)

        result = await search_flights(
            origin="GRU", destination="JFK", outbound_date="2025-12-01",
        )

    assert call_count == 2
    assert "flights" in result


@pytest.mark.asyncio
async def test_retry_serpapi_error_json_no_retry(monkeypatch):
    """SerpAPI error JSON (HTTP 200) -> ValueError; exactly 1 call, no retry."""
    monkeypatch.setattr("cosmo_travel_mcp.tools.flights._RETRY_BACKOFF_SECONDS", 0)

    call_count = 0

    def handler(request):
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"error": "rate limit exceeded"})

    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(side_effect=handler)

        with pytest.raises(ValueError, match="rate limit exceeded"):
            await search_flights(
                origin="GRU", destination="JFK", outbound_date="2025-12-01",
            )

    assert call_count == 1


@pytest.mark.asyncio
async def test_retry_http_400_no_retry(monkeypatch):
    """HTTP 400 -> error; exactly 1 call, no retry."""
    monkeypatch.setattr("cosmo_travel_mcp.tools.flights._RETRY_BACKOFF_SECONDS", 0)

    call_count = 0

    def handler(request):
        nonlocal call_count
        call_count += 1
        return httpx.Response(400)

    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(side_effect=handler)

        with pytest.raises(httpx.HTTPStatusError):
            await search_flights(
                origin="GRU", destination="JFK", outbound_date="2025-12-01",
            )

    assert call_count == 1
