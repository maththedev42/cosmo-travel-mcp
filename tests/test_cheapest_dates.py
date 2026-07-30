"""Tests for cheapest-dates search tool."""

from __future__ import annotations

import os
from datetime import date

import httpx
import pytest
import respx

from cosmo_travel_mcp.tools.cheapest_dates import (
    _extract_cheapest_price,
    _generate_candidate_dates,
    search_cheapest_dates,
)
from cosmo_travel_mcp.tools.flights import SERPAPI_BASE

# Reuse the flight-item fixture from test_flights
from .test_flights import _flight_item_fixture


# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _set_fake_api_key(monkeypatch):
    """Set a fake SERPAPI_API_KEY so tests never hit the real guard."""
    monkeypatch.setenv("SERPAPI_API_KEY", "fake-test-key")


# _generate_candidate_dates tests
# ---------------------------------------------------------------------------


def test_generate_candidate_dates_full_window():
    """Evenly spaced candidates spanning the full window."""
    candidates = _generate_candidate_dates(
        earliest_departure=date(2025, 12, 1),
        latest_return=date(2025, 12, 20),
        trip_duration_days=7,
        max_calls=6,
    )
    assert len(candidates) == 6
    assert candidates[0] == date(2025, 12, 1)
    assert candidates[-1] == date(2025, 12, 13)  # latest_return - trip_duration_days


def test_generate_candidate_dates_exact_fit():
    """Window exactly trip_duration_days long -> only one candidate."""
    candidates = _generate_candidate_dates(
        earliest_departure=date(2025, 12, 1),
        latest_return=date(2025, 12, 8),
        trip_duration_days=7,
        max_calls=6,
    )
    assert candidates == [date(2025, 12, 1)]


def test_generate_candidate_dates_single_call():
    candidates = _generate_candidate_dates(
        earliest_departure=date(2025, 12, 1),
        latest_return=date(2025, 12, 20),
        trip_duration_days=7,
        max_calls=1,
    )
    assert candidates == [date(2025, 12, 1)]


def test_generate_candidate_dates_two_calls():
    candidates = _generate_candidate_dates(
        earliest_departure=date(2025, 12, 1),
        latest_return=date(2025, 12, 20),
        trip_duration_days=7,
        max_calls=2,
    )
    assert len(candidates) == 2
    assert candidates[0] == date(2025, 12, 1)
    assert candidates[1] == date(2025, 12, 13)


def test_generate_candidate_dates_impossible_window_returns_empty():
    candidates = _generate_candidate_dates(
        earliest_departure=date(2025, 12, 10),
        latest_return=date(2025, 12, 15),
        trip_duration_days=7,
        max_calls=6,
    )
    assert candidates == []


# ---------------------------------------------------------------------------
# _extract_cheapest_price tests
# ---------------------------------------------------------------------------


def test_extract_cheapest_price_picks_min():
    result = {
        "flights": [
            {"price": 500, "currency": "BRL", "stops": 1},
            {"price": 300, "currency": "BRL", "stops": 2},
            {"price": 450, "currency": "BRL", "stops": 0},
        ]
    }
    cheapest = _extract_cheapest_price(result)
    assert cheapest is not None
    assert cheapest["price"] == 300


def test_extract_cheapest_price_empty():
    result = {"flights": []}
    assert _extract_cheapest_price(result) is None


def test_extract_cheapest_price_ignores_priceless_items():
    """A missing price is unknown, not free — it must never win the comparison."""
    result = {
        "flights": [
            {"price": None, "currency": "BRL", "stops": 0},
            {"price": 700, "currency": "BRL", "stops": 1},
        ]
    }
    cheapest = _extract_cheapest_price(result)
    assert cheapest is not None
    assert cheapest["price"] == 700


def test_extract_cheapest_price_all_priceless():
    """If nothing carries a price, report nothing rather than inventing zero."""
    result = {"flights": [{"price": None, "currency": "BRL", "stops": 0}]}
    assert _extract_cheapest_price(result) is None


# ---------------------------------------------------------------------------
# search_cheapest_dates tests (mocked HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_cheapest_dates_sampling():
    """Correct candidate-date generation + results sorted by price ascending."""
    call_count = 0

    def _response(request: httpx.Request):
        nonlocal call_count
        call_count += 1
        # Each call returns a different price so we can verify sorting.
        prices = [800, 600, 950, 700, 500, 850]
        idx = min(call_count - 1, len(prices) - 1)
        return httpx.Response(
            200,
            json={
                "search_parameters": {"currency": "BRL"},
                "best_flights": [_flight_item_fixture(price=prices[idx])],
                "other_flights": [],
            },
        )

    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(side_effect=_response)

        result = await search_cheapest_dates(
            origin="GRU", destination="JFK",
            earliest_departure="2025-12-01",
            latest_return="2025-12-20",
            trip_duration_days=7,
            max_calls=6,
        )

    assert call_count == 6
    prices = [r["cheapest_price"] for r in result["results"]]
    assert prices == sorted(prices)
    assert "note" in result
    assert "not an exhaustive scan" in result["note"]
    # Nothing failed, so no `unavailable` key should appear at all.
    assert "unavailable" not in result


@pytest.mark.asyncio
async def test_search_cheapest_dates_surfaces_failed_dates():
    """A date whose search fails on transport must be reported, not dropped.

    SerpAPI-level errors (``{"error": ...}``) intentionally propagate — see
    ``_call_serpapi``. This covers the other case: a per-date network failure,
    which used to vanish from the output and read as "no flights on that date".
    """
    call_count = 0

    def _response(request: httpx.Request):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise httpx.ConnectError("connection reset")
        return httpx.Response(
            200,
            json={
                "search_parameters": {"currency": "BRL"},
                "best_flights": [_flight_item_fixture(price=600)],
                "other_flights": [],
            },
        )

    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(side_effect=_response)

        result = await search_cheapest_dates(
            origin="GRU", destination="JFK",
            earliest_departure="2025-12-01",
            latest_return="2025-12-20",
            trip_duration_days=7,
            max_calls=3,
        )

    assert len(result["results"]) == 2
    assert "unavailable" in result
    assert len(result["unavailable"]) == 1
    assert "ConnectError" in result["unavailable"][0]["error"]
    # The note must admit the gap rather than implying full coverage.
    assert "no usable price" in result["note"]


@pytest.mark.asyncio
async def test_search_cheapest_dates_exact_fit_window():
    """Window exactly trip_duration_days long -> one candidate, one call."""
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(
            return_value=httpx.Response(
                200,
                json={
                    "search_parameters": {"currency": "BRL"},
                    "best_flights": [_flight_item_fixture(price=500)],
                    "other_flights": [],
                },
            )
        )

        result = await search_cheapest_dates(
            origin="GRU", destination="JFK",
            earliest_departure="2025-12-01",
            latest_return="2025-12-08",
            trip_duration_days=7,
            max_calls=6,
        )

        assert len(mock.calls) == 1
        assert mock.calls.last.request.url.params["engine"] == "google_flights"
        assert len(result["results"]) == 1
        assert result["results"][0]["outbound_date"] == "2025-12-01"
        assert result["results"][0]["return_date"] == "2025-12-08"


@pytest.mark.asyncio
async def test_search_cheapest_dates_max_calls_hard_cap():
    """max_calls > 15 -> rejected with clear error."""
    with pytest.raises(ValueError, match="15 or fewer"):
        await search_cheapest_dates(
            origin="GRU", destination="JFK",
            earliest_departure="2025-12-01",
            latest_return="2025-12-20",
            trip_duration_days=7,
            max_calls=20,
        )


@pytest.mark.asyncio
async def test_search_cheapest_dates_impossible_window():
    """earliest_departure + trip_duration_days > latest_return -> rejected."""
    with pytest.raises(ValueError, match="Impossible window"):
        await search_cheapest_dates(
            origin="GRU", destination="JFK",
            earliest_departure="2025-12-10",
            latest_return="2025-12-15",
            trip_duration_days=7,
        )


@pytest.mark.asyncio
async def test_search_cheapest_dates_missing_api_key(monkeypatch):
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="SERPAPI_API_KEY is not set"):
        await search_cheapest_dates(
            origin="GRU", destination="JFK",
            earliest_departure="2025-12-01",
            latest_return="2025-12-20",
            trip_duration_days=7,
        )


@pytest.mark.asyncio
async def test_search_cheapest_dates_never_exceeds_max_calls():
    """max_calls=3 -> at most 3 SerpAPI calls."""
    call_count = 0

    def _response(request: httpx.Request):
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            200,
            json={
                "search_parameters": {"currency": "BRL"},
                "best_flights": [_flight_item_fixture(price=500)],
                "other_flights": [],
            },
        )

    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(side_effect=_response)

        await search_cheapest_dates(
            origin="GRU", destination="JFK",
            earliest_departure="2025-12-01",
            latest_return="2025-12-20",
            trip_duration_days=7,
            max_calls=3,
        )

    assert call_count == 3


@pytest.mark.asyncio
async def test_search_cheapest_dates_handles_failed_call():
    """When one call fails, it's filtered out; valid results still returned."""
    call_count = 0

    def _response(request: httpx.Request):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            return httpx.Response(500)
        return httpx.Response(
            200,
            json={
                "search_parameters": {"currency": "BRL"},
                "best_flights": [_flight_item_fixture(price=500)],
                "other_flights": [],
            },
        )

    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(side_effect=_response)

        result = await search_cheapest_dates(
            origin="GRU", destination="JFK",
            earliest_departure="2025-12-01",
            latest_return="2025-12-20",
            trip_duration_days=7,
            max_calls=3,
        )

    assert call_count == 3
    # One call failed -> 2 valid results
    assert len(result["results"]) == 2
