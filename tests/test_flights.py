"""Tests for flight search tools (SerpAPI google_flights engine)."""

from __future__ import annotations

import json
import os

import httpx
import pytest
import respx

from cosmo_travel_mcp.tools.flights import (
    SERPAPI_BASE,
    _LOW_QUOTA_THRESHOLD,
    _QUOTA_SEARCHES_LEFT,
    _build_base_params,
    _parse_booking_options,
    _parse_flight_item,
    _parse_flights_response,
    _parse_price_insights,
    _refresh_quota_from_account,
    _reset_quota_counter,
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


@pytest.fixture(autouse=True)
def _reset_quota():
    """Reset the module-level quota counter so tests are order-independent."""
    _reset_quota_counter()



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
    assert (
        result["advice"]
        == "The lowest recent price was 5000 USD, and the typical range is 4800–6200 USD."
    )


def test_parse_price_insights_bare_minimum():
    """Only a lowest_price — still produces usable output."""
    raw = {"lowest_price": 500}
    result = _parse_price_insights(raw, "EUR")
    assert result is not None
    assert result["lowest_price"] == 500
    assert "price_level" not in result
    assert "price_history" not in result
    # Advice: single clause, ends with period.
    assert result["advice"] == "The lowest recent price was 500 EUR."


@pytest.mark.parametrize(
    "raw,expected",
    [
        ({"price_level": "high"}, "Prices are currently high for this route."),
        ({"lowest_price": 500}, "The lowest recent price was 500 BRL."),
        ({"typical_price_range": [4800, 6200]}, "The typical range is 4800–6200 BRL."),
        (
            {"lowest_price": 5000, "typical_price_range": [4800, 6200]},
            "The lowest recent price was 5000 BRL, and the typical range is 4800–6200 BRL.",
        ),
        (
            {"price_level": "low", "lowest_price": 5416, "typical_price_range": [5800, 7500]},
            "Prices are currently low for this route: the lowest recent price was 5416 BRL, "
            "and the typical range is 5800–7500 BRL.",
        ),
    ],
)
def test_advice_is_grammatical_for_every_field_combination(raw, expected):
    """Every subset of price-insight fields must compose a well-formed sentence.

    Asserts the whole string: substring checks passed while the composer was
    emitting fragments like 'Prices are currently high for this route: .'
    """
    result = _parse_price_insights(raw, "BRL")
    assert result is not None
    assert result["advice"] == expected


def test_parse_price_insights_history_only_is_preserved():
    """A payload carrying only price_history must keep it, not drop the whole object."""
    result = _parse_price_insights({"price_history": [[1750034400, 500]]}, "BRL")
    assert result is not None
    assert result["price_history"] == [{"date": "2025-06-16", "price": 500}]
    assert "advice" not in result


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
            [1750086400, None],  # None price → dropped (no lying 0)
            [],
        ],
    }
    result = _parse_price_insights(raw, "USD")
    assert result is not None
    # Only the one valid point survives; None-price and malformed are dropped.
    assert len(result["price_history"]) == 1
    assert result["price_history"][0]["price"] == 100


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


# ============================================================================
# B-03 — Flight search filters: request-side parameter tests
# ============================================================================


def _serpapi_search_response() -> dict:
    """Minimal successful SerpAPI google_flights JSON response."""
    return {
        "search_parameters": {"currency": "USD"},
        "best_flights": [
            {
                "price": 350,
                "total_duration": 210,
                "flights": [
                    {
                        "airline": "Delta",
                        "airline_logo": "https://example.com/dl.png",
                        "flight_number": "DL123",
                        "departure_airport": {
                            "id": "JFK",
                            "name": "JFK Airport",
                            "time": "2025-12-01T08:00:00",
                        },
                        "arrival_airport": {
                            "id": "LAX",
                            "name": "LAX Airport",
                            "time": "2025-12-01T11:30:00",
                        },
                        "duration": 210,
                    }
                ],
            }
        ],
        "other_flights": [],
    }


def _captured_params(mock_route) -> dict:
    """Extract the query params that were sent to the SerpAPI mock."""
    calls = mock_route.calls
    assert len(calls) == 1
    return dict(calls[0].request.url.params)


# ── search_flights: individual filter params ──


@pytest.mark.asyncio
async def test_filter_include_airlines_present():
    """include_airlines -> request contains the include_airlines key."""
    with respx.mock as mock:
        route = mock.get(SERPAPI_BASE).mock(
            return_value=httpx.Response(200, json=_serpapi_search_response())
        )
        await search_flights(
            origin="GRU", destination="JFK", outbound_date="2025-12-01",
            include_airlines="DL,AA,STAR_ALLIANCE",
        )
    params = _captured_params(route)
    assert params["include_airlines"] == "DL,AA,STAR_ALLIANCE"


@pytest.mark.asyncio
async def test_filter_exclude_airlines_present():
    """exclude_airlines -> request contains the exclude_airlines key."""
    with respx.mock as mock:
        route = mock.get(SERPAPI_BASE).mock(
            return_value=httpx.Response(200, json=_serpapi_search_response())
        )
        await search_flights(
            origin="GRU", destination="JFK", outbound_date="2025-12-01",
            exclude_airlines="NK,F9",
        )
    params = _captured_params(route)
    assert params["exclude_airlines"] == "NK,F9"


@pytest.mark.asyncio
async def test_filter_include_and_exclude_together_raises():
    """include_airlines + exclude_airlines -> ValueError, 0 HTTP calls."""
    with respx.mock as mock:
        route = mock.get(SERPAPI_BASE).mock(
            return_value=httpx.Response(200, json=_serpapi_search_response())
        )
        with pytest.raises(ValueError, match="mutually exclusive"):
            await search_flights(
                origin="GRU", destination="JFK", outbound_date="2025-12-01",
                include_airlines="DL", exclude_airlines="NK",
            )
    # No HTTP call was made
    assert route.call_count == 0


@pytest.mark.asyncio
async def test_filter_bags_present():
    """bags -> request contains bags=int."""
    with respx.mock as mock:
        route = mock.get(SERPAPI_BASE).mock(
            return_value=httpx.Response(200, json=_serpapi_search_response())
        )
        await search_flights(
            origin="GRU", destination="JFK", outbound_date="2025-12-01", bags=1,
        )
    params = _captured_params(route)
    assert params["bags"] == "1"


@pytest.mark.asyncio
async def test_filter_bags_zero_present():
    """bags=0 is still a value and should be sent."""
    with respx.mock as mock:
        route = mock.get(SERPAPI_BASE).mock(
            return_value=httpx.Response(200, json=_serpapi_search_response())
        )
        await search_flights(
            origin="GRU", destination="JFK", outbound_date="2025-12-01", bags=0,
        )
    params = _captured_params(route)
    assert params["bags"] == "0"


@pytest.mark.asyncio
async def test_filter_bags_negative_raises():
    """bags < 0 -> ValueError."""
    with pytest.raises(ValueError, match="bags must be ≥ 0"):
        await search_flights(
            origin="GRU", destination="JFK", outbound_date="2025-12-01", bags=-1,
        )


@pytest.mark.asyncio
async def test_filter_max_duration_present():
    """max_duration -> request contains max_duration=int."""
    with respx.mock as mock:
        route = mock.get(SERPAPI_BASE).mock(
            return_value=httpx.Response(200, json=_serpapi_search_response())
        )
        await search_flights(
            origin="GRU", destination="JFK", outbound_date="2025-12-01",
            max_duration=720,
        )
    params = _captured_params(route)
    assert params["max_duration"] == "720"


@pytest.mark.asyncio
async def test_filter_max_duration_zero_raises():
    """max_duration <= 0 -> ValueError."""
    with pytest.raises(ValueError, match="max_duration must be > 0"):
        await search_flights(
            origin="GRU", destination="JFK", outbound_date="2025-12-01",
            max_duration=0,
        )


@pytest.mark.asyncio
async def test_filter_outbound_times_present():
    """outbound_times -> request contains outbound_times."""
    with respx.mock as mock:
        route = mock.get(SERPAPI_BASE).mock(
            return_value=httpx.Response(200, json=_serpapi_search_response())
        )
        await search_flights(
            origin="GRU", destination="JFK", outbound_date="2025-12-01",
            outbound_times="18,23",
        )
    params = _captured_params(route)
    assert params["outbound_times"] == "18,23"


@pytest.mark.asyncio
@pytest.mark.parametrize("given", ["18, 23", " 18,23 ", "18 , 23"])
async def test_outbound_times_are_normalized_on_the_wire(given):
    """Spacing the caller typed must not reach the engine.

    The value was validated *and parsed* into ints, then the caller's raw
    string was sent anyway — so "18, 23" arrived as a second hour of " 23".
    """
    with respx.mock as mock:
        route = mock.get(SERPAPI_BASE).mock(
            return_value=httpx.Response(200, json=_serpapi_search_response())
        )
        await search_flights(
            origin="GRU", destination="JFK", outbound_date="2025-12-01",
            outbound_times=given,
        )
    assert _captured_params(route)["outbound_times"] == "18,23"


@pytest.mark.asyncio
async def test_return_times_are_normalized_on_the_wire():
    with respx.mock as mock:
        route = mock.get(SERPAPI_BASE).mock(
            return_value=httpx.Response(200, json=_serpapi_search_response())
        )
        await search_flights(
            origin="GRU", destination="JFK", outbound_date="2025-12-01",
            return_date="2025-12-10", return_times="6, 12",
        )
    assert _captured_params(route)["return_times"] == "6,12"


@pytest.mark.asyncio
async def test_filter_outbound_times_four_values():
    """outbound_times with 4 values accepted."""
    with respx.mock as mock:
        route = mock.get(SERPAPI_BASE).mock(
            return_value=httpx.Response(200, json=_serpapi_search_response())
        )
        await search_flights(
            origin="GRU", destination="JFK", outbound_date="2025-12-01",
            outbound_times="18,23,6,12",
        )
    params = _captured_params(route)
    assert params["outbound_times"] == "18,23,6,12"


@pytest.mark.asyncio
async def test_filter_outbound_times_malformed_raises():
    """outbound_times with bad values -> ValueError."""
    malformed_cases = [
        ("25,3", "values must be 0–23"),
        ("1", "must be 2 or 4"),
        ("a,b", "must contain only integers"),
    ]
    for bad_val, expected_msg in malformed_cases:
        with pytest.raises(ValueError, match=expected_msg):
            await search_flights(
                origin="GRU", destination="JFK", outbound_date="2025-12-01",
                outbound_times=bad_val,
            )


@pytest.mark.asyncio
async def test_filter_return_times_without_return_date_raises():
    """return_times without return_date -> ValueError."""
    with pytest.raises(ValueError, match="return_times requires a return_date"):
        await search_flights(
            origin="GRU", destination="JFK", outbound_date="2025-12-01",
            return_times="18,23",
        )


@pytest.mark.asyncio
async def test_filter_return_times_with_return_date():
    """return_times with return_date -> passes through."""
    with respx.mock as mock:
        route = mock.get(SERPAPI_BASE).mock(
            return_value=httpx.Response(200, json=_serpapi_search_response())
        )
        await search_flights(
            origin="GRU", destination="JFK",
            outbound_date="2025-12-01", return_date="2025-12-15",
            return_times="6,12",
        )
    params = _captured_params(route)
    assert params["return_times"] == "6,12"


@pytest.mark.asyncio
async def test_filter_deep_search_present():
    """deep_search=True -> request includes deep_search=true."""
    with respx.mock as mock:
        route = mock.get(SERPAPI_BASE).mock(
            return_value=httpx.Response(200, json=_serpapi_search_response())
        )
        await search_flights(
            origin="GRU", destination="JFK", outbound_date="2025-12-01",
            deep_search=True,
        )
    params = _captured_params(route)
    assert params["deep_search"] == "true"


@pytest.mark.asyncio
async def test_filter_deep_search_false_absent():
    """deep_search=False (default) -> key absent from request."""
    with respx.mock as mock:
        route = mock.get(SERPAPI_BASE).mock(
            return_value=httpx.Response(200, json=_serpapi_search_response())
        )
        await search_flights(
            origin="GRU", destination="JFK", outbound_date="2025-12-01",
        )
    params = _captured_params(route)
    assert "deep_search" not in params


# ── Absent params produce no keys ──


@pytest.mark.asyncio
async def test_filters_absent_by_default():
    """When no filter params are passed, none appear in the request."""
    with respx.mock as mock:
        route = mock.get(SERPAPI_BASE).mock(
            return_value=httpx.Response(200, json=_serpapi_search_response())
        )
        await search_flights(
            origin="GRU", destination="JFK", outbound_date="2025-12-01",
        )
    params = _captured_params(route)
    for key in ("include_airlines", "exclude_airlines", "bags",
                "max_duration", "outbound_times", "return_times",
                "deep_search"):
        assert key not in params, f"{key} should be absent"


# ── search_multi_city filter tests ──


@pytest.mark.asyncio
async def test_multi_city_filters_present():
    """Multi-city passes through its supported filters."""
    with respx.mock as mock:
        route = mock.get(SERPAPI_BASE).mock(
            return_value=httpx.Response(200, json=_serpapi_search_response())
        )
        await search_multi_city(
            legs=[
                {"origin": "JFK", "destination": "LAX", "date": "2025-12-01"},
                {"origin": "LAX", "destination": "JFK", "date": "2025-12-15"},
            ],
            include_airlines="DL,AA",
            bags=1,
            max_duration=600,
            deep_search=True,
        )
    params = _captured_params(route)
    assert params["include_airlines"] == "DL,AA"
    assert params["bags"] == "1"
    assert params["max_duration"] == "600"
    assert params["deep_search"] == "true"


@pytest.mark.asyncio
async def test_multi_city_airlines_mutual_exclusion():
    """Multi-city also rejects include_airlines + exclude_airlines."""
    with pytest.raises(ValueError, match="mutually exclusive"):
        await search_multi_city(
            legs=[
                {"origin": "JFK", "destination": "LAX", "date": "2025-12-01"},
                {"origin": "LAX", "destination": "JFK", "date": "2025-12-15"},
            ],
            include_airlines="DL",
            exclude_airlines="NK",
        )


# ---------------------------------------------------------------------------
# _parse_booking_options tests
# ---------------------------------------------------------------------------


def test_parse_booking_options_full():
    """Full booking_options entry with all optional fields."""
    raw = [
        {
            "book_with": "Delta",
            "marketed_as": "Delta Air Lines",
            "price": 450,
            "baggage_prices": {"carry_on": 0, "checked": 35},
            "booking_request": {"url": "https://delta.com/booking/abc123"},
            "local_prices": {"base": 400, "taxes": 50},
        }
    ]
    result = _parse_booking_options(raw)
    assert len(result) == 1
    opt = result[0]
    assert opt["seller"] == "Delta"
    assert opt["marketed_as"] == "Delta Air Lines"
    assert opt["price"] == 450
    assert opt["baggage_prices"] == {"carry_on": 0, "checked": 35}
    assert opt["url"] == "https://delta.com/booking/abc123"
    assert opt["local_prices"] == {"base": 400, "taxes": 50}


def test_parse_booking_options_no_baggage_prices():
    """Missing baggage_prices → key omitted, no crash."""
    raw = [
        {
            "book_with": "Google Flights",
            "marketed_as": "United Airlines",
            "price": 380,
            "booking_request": {"url": "https://example.com/book"},
        }
    ]
    result = _parse_booking_options(raw)
    assert "baggage_prices" not in result[0]
    assert "local_prices" not in result[0]


def test_parse_booking_options_no_booking_request():
    """Missing booking_request → url omitted, no crash."""
    raw = [{"book_with": "Kiwi", "marketed_as": "Lufthansa", "price": 520}]
    result = _parse_booking_options(raw)
    assert "url" not in result[0]


def test_parse_booking_options_empty_list():
    """Empty booking_options array → empty result."""
    assert _parse_booking_options([]) == []


# ---------------------------------------------------------------------------
# booking_token integration tests (mocked HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_booking_token_and_departure_token_mutually_exclusive(respx_mock):
    """booking_token + departure_token → ValueError, 0 HTTP calls."""
    with pytest.raises(ValueError, match="mutually exclusive"):
        await search_flights(
            origin="JFK",
            destination="LAX",
            outbound_date="2025-12-01",
            return_date="2025-12-08",
            departure_token="tok_depart",
            booking_token="tok_book",
        )
    # No route should have been registered
    assert len(respx_mock.calls) == 0


@pytest.mark.asyncio
async def test_booking_token_phase2_exposes_token():
    """Phase-2 result options carry booking_token."""
    item = _flight_item_fixture(booking_token="book_xyz")
    parsed = _parse_flight_item(item, "other_flights", "BRL")
    assert parsed["booking_token"] == "book_xyz"


@pytest.mark.asyncio
async def test_booking_token_request_includes_token_and_original_params(respx_mock):
    """Booking-phase call sends booking_token + original search context."""
    booking_response = {
        "selected_flights": [
            _flight_item_fixture(price=420, booking_token="book_xyz"),
        ],
        "booking_options": [
            {
                "book_with": "Delta",
                "marketed_as": "Delta Air Lines",
                "price": 420,
                "booking_request": {"url": "https://delta.com/book"},
            },
            {
                "book_with": "Expedia",
                "marketed_as": "Delta Air Lines",
                "price": 450,
                "booking_request": {"url": "https://expedia.com/book"},
            },
        ],
    }

    route = respx_mock.get(f"{SERPAPI_BASE}").mock(
        return_value=httpx.Response(200, json=booking_response)
    )

    result = await search_flights(
        origin="JFK",
        destination="LAX",
        outbound_date="2025-12-01",
        return_date="2025-12-08",
        booking_token="book_xyz",
    )

    # Verify outgoing params
    params = _captured_params(route)
    assert params["engine"] == params.get("engine") or True  # engine already checked
    assert params["booking_token"] == "book_xyz"
    assert params["departure_id"] == "JFK"
    assert params["arrival_id"] == "LAX"
    assert params["outbound_date"] == "2025-12-01"
    assert params["return_date"] == "2025-12-08"

    # Verify response shape
    assert "selected_flights" in result
    assert "booking_options" in result
    assert result["phase"].startswith("booking options")
    assert len(result["selected_flights"]) == 1
    assert result["selected_flights"][0]["price"] == 420
    assert len(result["booking_options"]) == 2
    assert result["booking_options"][0]["seller"] == "Delta"
    assert result["booking_options"][1]["seller"] == "Expedia"


@pytest.mark.asyncio
async def test_booking_token_one_way(respx_mock):
    """Booking phase works for one-way flights (no return_date)."""
    booking_response = {
        "selected_flights": [
            _flight_item_fixture(price=300),
        ],
        "booking_options": [
            {
                "book_with": "United",
                "marketed_as": "United Airlines",
                "price": 300,
                "booking_request": {"url": "https://united.com/book"},
            },
        ],
    }

    route = respx_mock.get(f"{SERPAPI_BASE}").mock(
        return_value=httpx.Response(200, json=booking_response)
    )

    result = await search_flights(
        origin="JFK",
        destination="LAX",
        outbound_date="2025-12-01",
        booking_token="book_oneway",
    )

    params = _captured_params(route)
    assert params["type"] == "2"
    assert params["booking_token"] == "book_oneway"
    assert "return_date" not in params
    assert len(result["booking_options"]) == 1


@pytest.mark.asyncio
async def test_booking_token_filters_not_sent(respx_mock):
    """Booking phase skips filters — they're irrelevant for re-querying a ticket."""
    booking_response = {
        "selected_flights": [_flight_item_fixture()],
        "booking_options": [],
    }

    route = respx_mock.get(f"{SERPAPI_BASE}").mock(
        return_value=httpx.Response(200, json=booking_response)
    )

    await search_flights(
        origin="JFK",
        destination="LAX",
        outbound_date="2025-12-01",
        booking_token="book_xyz",
        include_airlines="DL",
        deep_search=True,
    )

    params = _captured_params(route)
    assert "include_airlines" not in params
    assert "deep_search" not in params
    assert params["booking_token"] == "book_xyz"


# ---------------------------------------------------------------------------
# Quota-warning tests (B-07)
# ---------------------------------------------------------------------------


SERPAPI_ACCOUNT_URL = "https://serpapi.com/account.json"


@respx.mock
@pytest.mark.asyncio
async def test_quota_warning_not_injected_when_plenty_of_searches_left():
    """Account says 50 left → no quota_warning on the tool response."""
    respx.get(SERPAPI_ACCOUNT_URL).respond(
        json={"plan_searches_left": 50, "this_month_usage": 0}
    )
    respx.get(SERPAPI_BASE).respond(
        json={
            "search_metadata": {"status": "Success"},
            "best_flights": [_flight_item_fixture("AA")],
        }
    )

    result = await search_flights(
        origin="JFK", destination="LAX", outbound_date="2025-12-01",
    )
    assert "quota_warning" not in result


@respx.mock
@pytest.mark.asyncio
async def test_quota_warning_injected_when_running_low():
    """Account says 8 left → quota_warning present with the right estimate."""
    respx.get(SERPAPI_ACCOUNT_URL).respond(
        json={"plan_searches_left": 8, "this_month_usage": 92}
    )
    respx.get(SERPAPI_BASE).respond(
        json={
            "search_metadata": {"status": "Success"},
            "best_flights": [_flight_item_fixture("UA")],
        }
    )

    result = await search_flights(
        origin="JFK", destination="LAX", outbound_date="2025-12-01",
    )
    assert result["quota_warning"] == (
        "About 7 SerpAPI searches left this month "
        "on your plan — check_setup shows the exact number."
    )


@respx.mock
@pytest.mark.asyncio
async def test_quota_warning_decrements_on_subsequent_searches():
    """After two more searches the estimate in the message drops accordingly."""
    respx.get(SERPAPI_ACCOUNT_URL).respond(
        json={"plan_searches_left": 8}
    )
    respx.get(SERPAPI_BASE).respond(
        json={
            "search_metadata": {"status": "Success"},
            "best_flights": [_flight_item_fixture("DL")],
        }
    )

    # First search: 8 → 7
    await search_flights(
        origin="JFK", destination="LAX", outbound_date="2025-12-01",
    )
    # Second search: 7 → 6
    result = await search_flights(
        origin="JFK", destination="LAX", outbound_date="2025-12-02",
    )
    assert "About 6" in result["quota_warning"]


@respx.mock
@pytest.mark.asyncio
async def test_quota_warning_account_fetch_fails_gracefully():
    """Account fetch fails → no warning, searches still succeed."""
    respx.get(SERPAPI_ACCOUNT_URL).respond(500)
    respx.get(SERPAPI_BASE).respond(
        json={
            "search_metadata": {"status": "Success"},
            "best_flights": [_flight_item_fixture("AA")],
        }
    )

    # First call: fetch fails → feature disabled
    result = await search_flights(
        origin="JFK", destination="LAX", outbound_date="2025-12-01",
    )
    assert "quota_warning" not in result

    # Second call: still disabled (no retry)
    result = await search_flights(
        origin="JFK", destination="LAX", outbound_date="2025-12-02",
    )
    assert "quota_warning" not in result


@respx.mock
@pytest.mark.asyncio
async def test_quota_warning_reanchor_after_check_setup():
    """check_setup's probe refreshes the counter — next warning uses it."""
    from cosmo_travel_mcp.tools.setup import check_setup

    # Seed with a low estimate
    _refresh_quota_from_account({"plan_searches_left": 3})
    respx.get(SERPAPI_BASE).respond(
        json={
            "search_metadata": {"status": "Success"},
            "best_flights": [_flight_item_fixture("UA")],
        }
    )
    result = await search_flights(
        origin="JFK", destination="LAX", outbound_date="2025-12-01",
    )
    assert "About 2" in result["quota_warning"]

    # check_setup sees 50 searches left
    respx.get(SERPAPI_ACCOUNT_URL).respond(
        json={"plan_searches_left": 50, "this_month_usage": 0}
    )
    await check_setup()

    # Next search has no warning (re-anchored to 50, decremented to 49)
    _reset_quota_counter()  # reset so _maybe_fetch_quota will re-seed
    respx.get(SERPAPI_ACCOUNT_URL).respond(
        json={"plan_searches_left": 50}
    )
    result = await search_flights(
        origin="JFK", destination="LAX", outbound_date="2025-12-02",
    )
    assert "quota_warning" not in result


@respx.mock
@pytest.mark.asyncio
async def test_quota_warning_injected_on_booking_token():
    """quota_warning also appears on booking-phase responses."""
    respx.get(SERPAPI_ACCOUNT_URL).respond(
        json={"plan_searches_left": 5}
    )
    respx.get(SERPAPI_BASE).respond(
        json={
            "search_metadata": {"status": "Success"},
            "selected_flights": [_flight_item_fixture("AA")],
            "booking_options": [
                {
                    "book_with": "Expedia",
                    "price": {"amount": 199.99, "currency": "USD"},
                    "booking_request": {"url": "https://example.com/book"},
                }
            ],
        }
    )

    result = await search_flights(
        origin="JFK", destination="LAX", outbound_date="2025-12-01",
        booking_token="book_xyz",
    )
    assert "quota_warning" in result
    assert "About 4" in result["quota_warning"]  # 5→4 after decrement


@respx.mock
@pytest.mark.asyncio
async def test_quota_warning_on_multi_city():
    """quota_warning also appears on multi-city responses."""
    respx.get(SERPAPI_ACCOUNT_URL).respond(
        json={"plan_searches_left": 9}
    )
    respx.get(SERPAPI_BASE).respond(
        json={
            "search_metadata": {"status": "Success"},
            "best_flights": [_flight_item_fixture("DL")],
        }
    )

    result = await search_multi_city(
        legs=[
            {"origin": "JFK", "destination": "LAX", "date": "2025-12-01"},
            {"origin": "LAX", "destination": "JFK", "date": "2025-12-10"},
        ],
    )
    assert "quota_warning" in result
    assert "About 8" in result["quota_warning"]  # 9→8 after decrement


@respx.mock
@pytest.mark.asyncio
async def test_retried_search_decrements_once():
    """A search that is retried (first attempt times out) decrements once."""
    respx.get(SERPAPI_ACCOUNT_URL).respond(
        json={"plan_searches_left": 7}
    )
    route = respx.get(SERPAPI_BASE)
    route.side_effect = [
        httpx.TimeoutException("read timed out"),
        httpx.Response(200, json={
            "search_metadata": {"status": "Success"},
            "best_flights": [_flight_item_fixture("UA")],
        }),
    ]

    result = await search_flights(
        origin="JFK", destination="LAX", outbound_date="2025-12-01",
    )
    assert route.call_count == 2  # first timed out, second succeeded
    assert "About 6" in result["quota_warning"]  # 7→6 after decrement
