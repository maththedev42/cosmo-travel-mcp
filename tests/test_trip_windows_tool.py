"""Tests for the compare_trip_windows MCP tool (mocked HTTP)."""

from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from cosmo_travel_mcp.tools.flights import SERPAPI_BASE
from cosmo_travel_mcp.tools.trip_windows import compare_trip_windows

from .test_flights import _flight_item_fixture


@pytest.fixture(autouse=True)
def _set_fake_api_key(monkeypatch):
    """Set a fake SERPAPI_API_KEY so tests never hit the real guard."""
    monkeypatch.setenv("SERPAPI_API_KEY", "fake-test-key")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _flight_response(price: int) -> dict:
    return {
        "search_parameters": {"currency": "BRL"},
        "best_flights": [_flight_item_fixture(price=price)],
        "other_flights": [],
    }


def _hotel_item(name: str, nightly: int, total: int) -> dict:
    """A property in the real SerpAPI shape with both per-night and stay totals."""
    return {
        "name": name,
        "type": "hotel",
        "rate_per_night": {"lowest": f"R${nightly}", "extracted_lowest": nightly},
        "total_rate": {"lowest": f"R${total}", "extracted_lowest": total},
        "overall_rating": 4.2,
        "link": f"https://example.com/{name}",
    }


def _hotel_response(*items: dict) -> dict:
    return {"properties": list(items)}


def _happy_router(flight_price: int = 1000, hotel_total: int = 900):
    """Side-effect returning a fixed flight price and one hotel for every window."""

    def _response(request: httpx.Request):
        if request.url.params["engine"] == "google_flights":
            return httpx.Response(200, json=_flight_response(flight_price))
        return httpx.Response(
            200, json=_hotel_response(_hotel_item("hotel", hotel_total, hotel_total))
        )

    return _response


# ---------------------------------------------------------------------------
# Cost accounting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exactly_two_searches_per_window():
    """3 windows (default max_windows) -> exactly 6 SerpAPI requests."""
    calls: list[httpx.Request] = []

    def _response(request: httpx.Request):
        calls.append(request)
        if request.url.params["engine"] == "google_flights":
            return httpx.Response(200, json=_flight_response(1000))
        return httpx.Response(200, json=_hotel_response(_hotel_item("h", 300, 900)))

    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(side_effect=_response)
        result = await compare_trip_windows(
            origin="POA",
            destination="GIG",
            anchor_date="2027-01-15",
            lodging_location="Rio de Janeiro",
            adults=2,
        )

    assert len(result["windows"]) == 3
    assert len(calls) == 6
    assert result["searches_spent"] == 6
    flights = [c for c in calls if c.url.params["engine"] == "google_flights"]
    hotels = [c for c in calls if c.url.params["engine"] == "google_hotels"]
    assert len(flights) == 3
    assert len(hotels) == 3


@pytest.mark.asyncio
async def test_max_windows_raises_instead_of_spending_12_searches():
    """max_windows > 5 raises before any HTTP request is issued."""
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(ValueError, match="5 or fewer"):
            await compare_trip_windows(
                origin="POA",
                destination="GIG",
                anchor_date="2027-01-15",
                lodging_location="Rio de Janeiro",
                adults=2,
                max_windows=6,
            )
        assert not mock.calls


# ---------------------------------------------------------------------------
# The passenger count is threaded into both sides
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adults_appears_in_both_flight_and_hotel_requests():
    """Same passenger count on both sides, or the comparison is not one."""
    flight_adults: list[str] = []
    hotel_adults: list[str] = []

    def _response(request: httpx.Request):
        if request.url.params["engine"] == "google_flights":
            flight_adults.append(request.url.params["adults"])
            return httpx.Response(200, json=_flight_response(1000))
        hotel_adults.append(request.url.params["adults"])
        return httpx.Response(200, json=_hotel_response(_hotel_item("h", 300, 900)))

    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(side_effect=_response)
        await compare_trip_windows(
            origin="POA",
            destination="GIG",
            anchor_date="2027-01-15",
            lodging_location="Rio de Janeiro",
            adults=2,
            max_windows=2,
        )

    assert len(flight_adults) == 2
    assert len(hotel_adults) == 2
    assert all(a == "2" for a in flight_adults + hotel_adults)


# ---------------------------------------------------------------------------
# Anchor rule end to end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anchor_failing_windows_never_requested():
    """Every requested window covers the anchor night: depart <= anchor < return."""
    anchor = date(2027, 1, 15)

    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(side_effect=_happy_router())
        await compare_trip_windows(
            origin="POA",
            destination="GIG",
            anchor_date="2027-01-15",
            lodging_location="Rio de Janeiro",
            adults=2,
            max_windows=5,
        )

        # Read mock.calls inside the `with` — respx clears its stats on exit.
        assert mock.calls
        for call in mock.calls:
            params = call.request.url.params
            # Flights name the dates outbound_date/return_date; the hotel
            # engine calls the same days check_in_date/check_out_date.
            depart = date.fromisoformat(params.get("outbound_date") or params.get("check_in_date"))
            return_date = date.fromisoformat(params.get("return_date") or params.get("check_out_date"))
            assert depart <= anchor < return_date


# ---------------------------------------------------------------------------
# Totals and ranking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_combined_total_sums_flights_and_lodging():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(side_effect=_happy_router(flight_price=1595, hotel_total=923))
        result = await compare_trip_windows(
            origin="POA",
            destination="GIG",
            anchor_date="2027-01-15",
            lodging_location="Rio de Janeiro",
            adults=2,
        )

    for w in result["windows"]:
        assert w["combined_total"] == w["flights_total"] + w["lodging_total"]
        assert w["flights_total"] == 1595
        assert w["lodging_total"] == 923


@pytest.mark.asyncio
async def test_ranking_puts_lower_combined_first_even_when_airfare_higher():
    """The whole point: a window is only cheaper once the nights are paid for.

    The 3-night window (return 18 Jan) has *cheaper* airfare (1000 vs 1200)
    but its extra night costs 400, so its combined total loses to the 2-night
    windows — which still rank first despite the higher airfare.
    """

    def _response(request: httpx.Request):
        engine = request.url.params["engine"]
        # Flights key on return_date; the hotel engine names the same day
        # check_out_date.
        day = request.url.params.get("return_date") or request.url.params.get("check_out_date")
        if engine == "google_flights":
            price = 1000 if day == "2027-01-18" else 1200
            return httpx.Response(200, json=_flight_response(price))
        total = 400 if day == "2027-01-18" else 0
        return httpx.Response(200, json=_hotel_response(_hotel_item("h", total, total)))

    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(side_effect=_response)
        result = await compare_trip_windows(
            origin="POA",
            destination="GIG",
            anchor_date="2027-01-15",
            lodging_location="Rio de Janeiro",
            adults=2,
            max_windows=3,
        )

    ranked = result["windows"]
    # Winner: a 2-night window, combined 1200 on airfare 1200.
    assert ranked[0]["combined_total"] == 1200
    assert ranked[0]["flights_total"] == 1200
    assert ranked[0]["delta_vs_best"] == 0
    # The longer window has cheaper airfare but a higher combined total.
    longer = next(w for w in ranked if w["nights"] == 3)
    assert longer["flights_total"] == 1000
    assert longer["combined_total"] == 1400
    assert ranked.index(longer) == 2


# ---------------------------------------------------------------------------
# Degradation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_window_erroring_degrades_to_note_not_exception():
    """A window whose search fails is dropped with a note, not fatal."""

    def _response(request: httpx.Request):
        if request.url.params["engine"] == "google_flights":
            if request.url.params["return_date"] == "2027-01-18":
                return httpx.Response(500)
            return httpx.Response(200, json=_flight_response(1000))
        return httpx.Response(200, json=_hotel_response(_hotel_item("h", 300, 900)))

    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(side_effect=_response)
        result = await compare_trip_windows(
            origin="POA",
            destination="GIG",
            anchor_date="2027-01-15",
            lodging_location="Rio de Janeiro",
            adults=2,
            max_windows=3,
        )

    assert len(result["windows"]) == 2
    assert "unavailable" in result
    assert len(result["unavailable"]) == 1
    assert any("could not be priced" in n for n in result["notes"])


# ---------------------------------------------------------------------------
# lodging_basis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lodging_basis_is_unverified():
    """The tool cannot tell per-room from per-bed — it must say so, not guess."""
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).mock(side_effect=_happy_router())
        result = await compare_trip_windows(
            origin="POA",
            destination="GIG",
            anchor_date="2027-01-15",
            lodging_location="Rio de Janeiro",
            adults=2,
        )

    assert result["lodging_basis"] == "unverified"
    assert any("per-bed" in n for n in result["notes"])
    # Both rates are surfaced so the caller can judge the basis themselves.
    option = result["windows"][0]["lodging_options"][0]
    assert option["rate_per_night"]["extracted_lowest"] is not None
    assert option["total_rate"]["extracted_lowest"] is not None
