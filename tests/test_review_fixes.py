"""Regression tests for the pre-release correctness review (2026-08-01).

Each test pins a defect found by reading the source against the documented
contracts — all four shipped inside a green suite, so each assertion here is
written to fail on the pre-fix code.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from cosmo_travel_mcp.tools.driving import ROUTES_API_BASE, COMPUTE_ROUTES_URL, compare_drive_or_fly
from cosmo_travel_mcp.tools.events import search_events
from cosmo_travel_mcp.tools.flights import SERPAPI_BASE, search_multi_city
from cosmo_travel_mcp.tools.hotels import get_accommodation_details, search_accommodations

SERPAPI_ACCOUNT_URL = "https://serpapi.com/account.json"


@pytest.fixture(autouse=True)
def _set_fake_api_keys(monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "fake-test-key")
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "fake-maps-key")


# ---------------------------------------------------------------------------
# 1. Quota warning is a contract over EVERY SerpAPI-backed tool
#    (B-07 injected it in flights + cheapest_dates only).
# ---------------------------------------------------------------------------


def _low_quota_account() -> None:
    respx.get(SERPAPI_ACCOUNT_URL).respond(
        json={"plan_searches_left": 8, "this_month_usage": 92}
    )


@respx.mock
@pytest.mark.asyncio
async def test_quota_warning_on_search_accommodations():
    _low_quota_account()
    respx.get(SERPAPI_BASE).respond(json={"properties": []})

    result = await search_accommodations(
        location="Miami, FL",
        check_in_date="2026-09-01",
        check_out_date="2026-09-05",
    )
    assert "quota_warning" in result


@respx.mock
@pytest.mark.asyncio
async def test_quota_warning_on_get_accommodation_details():
    _low_quota_account()
    respx.get(SERPAPI_BASE).respond(json={"name": "Hotel Test"})

    result = await get_accommodation_details(
        property_token="tok-1",
        location="Miami, FL",
        check_in_date="2026-09-01",
        check_out_date="2026-09-05",
    )
    assert "quota_warning" in result


@respx.mock
@pytest.mark.asyncio
async def test_quota_warning_on_search_events():
    _low_quota_account()
    respx.get(SERPAPI_BASE).respond(json={"events_results": []})

    result = await search_events(query="concerts in Miami")
    assert "quota_warning" in result


# ---------------------------------------------------------------------------
# 2. Tolls alone must not masquerade as the total cost of driving.
# ---------------------------------------------------------------------------

_ROUTE_RESPONSE = [
    {
        "originIndex": 0,
        "destinationIndex": 0,
        "status": {},
        "condition": "ROUTE_EXISTS",
        "distanceMeters": 380_000,
        "duration": "12600s",
    }
]

_TOLL_RESPONSE = {
    "routes": [
        {
            "travelAdvisory": {
                "tollInfo": {
                    "estimatedPrice": [
                        {"currencyCode": "BRL", "units": "15", "nanos": 500_000_000}
                    ]
                }
            }
        }
    ]
}


@respx.mock
@pytest.mark.asyncio
async def test_tolls_without_cost_inputs_do_not_create_a_total():
    """No fuel/rental inputs → tolls are reported, but never as a 'total'.

    Pre-fix, tolls alone set estimated_total_driving_cost, and the flight
    comparison then presented 15.50 of tolls as the full cost of driving —
    flight_price 500 showed cost_difference 484.50 in driving's favour while
    fuel was never counted.
    """
    respx.post(ROUTES_API_BASE).respond(json=_ROUTE_RESPONSE)
    respx.post(COMPUTE_ROUTES_URL).respond(json=_TOLL_RESPONSE)

    result = await compare_drive_or_fly(
        origin="Orlando, FL",
        destination="Miami, FL",
        flight_price=500.0,
        currency="BRL",
    )

    assert result["estimated_toll_cost"] == 15.5
    assert "estimated_total_driving_cost" not in result
    assert "cost_difference" not in result.get("comparison", {})


@respx.mock
@pytest.mark.asyncio
async def test_tolls_still_fold_into_a_real_total():
    """With fuel inputs, matching-currency tolls still join the total."""
    respx.post(ROUTES_API_BASE).respond(json=_ROUTE_RESPONSE)
    respx.post(COMPUTE_ROUTES_URL).respond(json=_TOLL_RESPONSE)

    result = await compare_drive_or_fly(
        origin="Orlando, FL",
        destination="Miami, FL",
        fuel_price_per_liter=5.50,
        fuel_efficiency_km_per_liter=12.0,
        currency="BRL",
    )

    fuel = result["estimated_fuel_cost"]
    assert result["estimated_total_driving_cost"] == pytest.approx(fuel + 15.5)


# ---------------------------------------------------------------------------
# 3. Multi-city legs: times normalized on the wire, missing keys named.
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_multi_city_times_are_normalized_on_the_wire():
    route = respx.get(SERPAPI_BASE).respond(
        json={"search_metadata": {"status": "Success"}, "best_flights": []}
    )
    respx.get(SERPAPI_ACCOUNT_URL).respond(json={"plan_searches_left": 50})

    await search_multi_city(
        legs=[
            {"origin": "GRU", "destination": "JFK", "date": "2026-12-01", "times": "18, 23"},
            {"origin": "JFK", "destination": "GRU", "date": "2026-12-10"},
        ]
    )

    sent = json.loads(route.calls[0].request.url.params["multi_city_json"])
    assert sent[0]["times"] == "18,23", "times must be sent parsed, not raw"
    assert "times" not in sent[1]


@pytest.mark.asyncio
async def test_multi_city_invalid_times_rejected_with_leg_label():
    with pytest.raises(ValueError, match=r"legs\[1\]\.times"):
        await search_multi_city(
            legs=[
                {"origin": "GRU", "destination": "JFK", "date": "2026-12-01", "times": "25,3"},
                {"origin": "JFK", "destination": "GRU", "date": "2026-12-10"},
            ]
        )


@pytest.mark.asyncio
async def test_multi_city_missing_leg_key_is_a_valueerror_not_keyerror():
    with pytest.raises(ValueError, match="Leg 2 is missing required key"):
        await search_multi_city(
            legs=[
                {"origin": "GRU", "destination": "JFK", "date": "2026-12-01"},
                {"origin": "JFK", "date": "2026-12-10"},
            ]
        )


# ---------------------------------------------------------------------------
# 4. cheapest_dates surfaces which sampled dates were served from cache.
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_cheapest_dates_marks_cached_results():
    from cosmo_travel_mcp.tools.cheapest_dates import search_cheapest_dates
    from tests.test_flights import _flight_item_fixture

    route = respx.get(SERPAPI_BASE).respond(
        json={
            "search_parameters": {"currency": "BRL"},
            "best_flights": [_flight_item_fixture(price=500)],
            "other_flights": [],
        }
    )
    respx.get(SERPAPI_ACCOUNT_URL).respond(json={"plan_searches_left": 50})

    kwargs = dict(
        origin="GRU", destination="JFK",
        earliest_departure="2026-12-01",
        latest_return="2026-12-20",
        trip_duration_days=7,
        max_calls=3,
    )
    first = await search_cheapest_dates(**kwargs)
    calls_after_first = route.call_count

    second = await search_cheapest_dates(**kwargs)

    assert route.call_count == calls_after_first, "second sampling must be fully cached"
    assert all("cached" not in r for r in first["results"])
    assert all(r["cached"] is True for r in second["results"])
