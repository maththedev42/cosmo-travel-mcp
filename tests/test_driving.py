"""Tests for the drive-or-fly comparison tool (Google Maps Routes API)."""

from __future__ import annotations

import os

import pytest
import respx

from cosmo_travel_mcp.tools.driving import (
    COMPUTE_ROUTES_URL,
    FX_BASE,
    ROUTES_API_BASE,
    _fetch_toll_info,
    _parse_duration,
    _parse_money,
    compare_drive_or_fly,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _set_fake_maps_api_key(monkeypatch):
    """Set a fake GOOGLE_MAPS_API_KEY so tests never hit the real guard."""
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "fake-maps-key")


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------


def test_parse_duration_seconds():
    assert _parse_duration("12345s") == 205  # 12345 / 60 = 205.75 -> 205


def test_parse_duration_zero():
    assert _parse_duration("0s") == 0


def test_parse_duration_one_minute():
    assert _parse_duration("60s") == 1


def test_parse_duration_no_suffix():
    with pytest.raises(ValueError, match="Unexpected duration format"):
        _parse_duration("12345")


def test_parse_duration_fractional_seconds():
    """Protobuf Duration JSON allows a fractional part — it must not crash."""
    assert _parse_duration("123.5s") == 2  # 123.5s -> 2.058 min -> floor 2
    assert _parse_duration("3.000000001s") == 0
    assert _parse_duration("12345.678s") == 205


def test_parse_duration_garbage_suffix_still_raises():
    with pytest.raises(ValueError, match="Unexpected duration format"):
        _parse_duration("abcs")


# ---------------------------------------------------------------------------
# Successful route
# ---------------------------------------------------------------------------


_ROUTE_RESPONSE = [
    {
        "originIndex": 0,
        "destinationIndex": 0,
        "status": {"code": "OK"},
        "distanceMeters": 379000,
        "duration": "13200s",
        "condition": "ROUTE_EXISTS",
    }
]


@pytest.mark.asyncio
async def test_compare_drive_or_fly_success():
    with respx.mock as mock:
        mock.post(ROUTES_API_BASE).respond(json=_ROUTE_RESPONSE)
        result = await compare_drive_or_fly(origin="Orlando, FL", destination="Miami, FL")

    assert result["distance_km"] == 379.0
    assert result["driving_duration_minutes"] == 220  # 13200 / 60
    assert "estimated_fuel_cost" not in result
    assert "estimated_total_driving_cost" not in result
    assert "comparison" not in result


# ---------------------------------------------------------------------------
# Fuel cost computation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compare_drive_or_fly_with_fuel():
    with respx.mock as mock:
        mock.post(ROUTES_API_BASE).respond(json=_ROUTE_RESPONSE)
        result = await compare_drive_or_fly(
            origin="Orlando, FL",
            destination="Miami, FL",
            fuel_price_per_liter=5.50,
            fuel_efficiency_km_per_liter=12.0,
        )

    # distance_km=379, efficiency=12 km/L -> 379/12 = 31.58 L
    # 31.58 * 5.50 = 173.71
    assert result["estimated_fuel_cost"] == 173.71
    assert result["estimated_total_driving_cost"] == 173.71
    assert "comparison" not in result


@pytest.mark.asyncio
async def test_compare_drive_or_fly_with_fuel_and_rental():
    with respx.mock as mock:
        mock.post(ROUTES_API_BASE).respond(json=_ROUTE_RESPONSE)
        result = await compare_drive_or_fly(
            origin="Orlando, FL",
            destination="Miami, FL",
            fuel_price_per_liter=5.50,
            fuel_efficiency_km_per_liter=12.0,
            rental_car_cost_total=200.0,
        )

    assert result["estimated_fuel_cost"] == 173.71
    assert result["estimated_total_driving_cost"] == pytest.approx(373.71)  # 173.71 + 200


@pytest.mark.asyncio
async def test_compare_drive_or_fly_rental_only():
    """Only rental cost provided — total driving cost uses rental only."""
    with respx.mock as mock:
        mock.post(ROUTES_API_BASE).respond(json=_ROUTE_RESPONSE)
        result = await compare_drive_or_fly(
            origin="Orlando, FL",
            destination="Miami, FL",
            rental_car_cost_total=200.0,
        )

    assert "estimated_fuel_cost" not in result
    assert result["estimated_total_driving_cost"] == 200.0


@pytest.mark.asyncio
async def test_compare_drive_or_fly_fuel_only_one_input():
    """Only one fuel param given → no fuel-cost field."""
    with respx.mock as mock:
        mock.post(ROUTES_API_BASE).respond(json=_ROUTE_RESPONSE)
        result = await compare_drive_or_fly(
            origin="Orlando, FL",
            destination="Miami, FL",
            fuel_price_per_liter=5.50,
        )

    assert "estimated_fuel_cost" not in result
    assert "estimated_total_driving_cost" not in result


# ---------------------------------------------------------------------------
# Flight comparison
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compare_drive_or_fly_flight_comparison_full():
    """Both flight price and duration → full comparison."""
    with respx.mock as mock:
        mock.post(ROUTES_API_BASE).respond(json=_ROUTE_RESPONSE)
        result = await compare_drive_or_fly(
            origin="Orlando, FL",
            destination="Miami, FL",
            flight_price=450.0,
            flight_duration_minutes=75.0,
        )

    assert "comparison" in result
    assert result["comparison"]["time_difference_minutes"] == -145  # 75 - 220
    # No fuel/rental → no cost_difference
    assert "cost_difference" not in result["comparison"]


@pytest.mark.asyncio
async def test_compare_drive_or_fly_comparison_with_costs():
    """Flight price + driving costs → cost_difference present."""
    with respx.mock as mock:
        mock.post(ROUTES_API_BASE).respond(json=_ROUTE_RESPONSE)
        result = await compare_drive_or_fly(
            origin="Orlando, FL",
            destination="Miami, FL",
            fuel_price_per_liter=5.50,
            fuel_efficiency_km_per_liter=12.0,
            rental_car_cost_total=200.0,
            flight_price=450.0,
            flight_duration_minutes=75.0,
        )

    assert result["comparison"]["cost_difference"] == 76.29  # 450 - 373.71
    assert result["comparison"]["currency"] == "BRL"
    assert result["comparison"]["time_difference_minutes"] == -145


@pytest.mark.asyncio
async def test_compare_drive_or_fly_flight_duration_only():
    """Only flight duration → comparison with time only."""
    with respx.mock as mock:
        mock.post(ROUTES_API_BASE).respond(json=_ROUTE_RESPONSE)
        result = await compare_drive_or_fly(
            origin="Orlando, FL",
            destination="Miami, FL",
            flight_duration_minutes=75.0,
        )

    assert "comparison" in result
    assert result["comparison"]["time_difference_minutes"] == -145
    assert "cost_difference" not in result["comparison"]


# ---------------------------------------------------------------------------
# Missing API key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compare_drive_or_fly_missing_api_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GOOGLE_MAPS_API_KEY is not set"):
        await compare_drive_or_fly(origin="Orlando, FL", destination="Miami, FL")


# ---------------------------------------------------------------------------
# ROUTE_NOT_FOUND
# ---------------------------------------------------------------------------


_ROUTE_NOT_FOUND_RESPONSE = [
    {
        "originIndex": 0,
        "destinationIndex": 0,
        "status": {},
        "condition": "ROUTE_NOT_FOUND",
    }
]


@pytest.mark.asyncio
async def test_compare_drive_or_fly_route_not_found():
    with respx.mock as mock:
        mock.post(ROUTES_API_BASE).respond(json=_ROUTE_NOT_FOUND_RESPONSE)
        with pytest.raises(ValueError, match="ROUTE_NOT_FOUND"):
            await compare_drive_or_fly(origin="Atlantis", destination="Shangri-La")


# ---------------------------------------------------------------------------
# Non-OK status code
# ---------------------------------------------------------------------------


_NON_OK_STATUS_RESPONSE = [
    {
        "originIndex": 0,
        "destinationIndex": 0,
        "status": {"code": "ZERO_RESULTS"},
        "condition": "",
    }
]


@pytest.mark.asyncio
async def test_compare_drive_or_fly_zero_results():
    with respx.mock as mock:
        mock.post(ROUTES_API_BASE).respond(json=_NON_OK_STATUS_RESPONSE)
        with pytest.raises(ValueError, match="ZERO_RESULTS"):
            await compare_drive_or_fly(origin="North Pole", destination="South Pole")


# ---------------------------------------------------------------------------
# Empty response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compare_drive_or_fly_empty_response():
    with respx.mock as mock:
        mock.post(ROUTES_API_BASE).respond(json=[])
        with pytest.raises(ValueError, match="No route found"):
            await compare_drive_or_fly(origin="Nowhere", destination="Noplace")


# ---------------------------------------------------------------------------
# Money parsing
# ---------------------------------------------------------------------------


def test_parse_money_whole_dollars():
    assert _parse_money({"currencyCode": "USD", "units": "15", "nanos": 0}) == 15.0


def test_parse_money_with_cents():
    assert _parse_money({"currencyCode": "USD", "units": "15", "nanos": 500_000_000}) == 15.5


def test_parse_money_zero():
    assert _parse_money({"currencyCode": "BRL", "units": "0", "nanos": 0}) == 0.0


# ---------------------------------------------------------------------------
# Toll info — computeRoutes mock payloads
# ---------------------------------------------------------------------------

_COMPUTE_ROUTES_WITH_TOLL = {
    "routes": [
        {
            "distanceMeters": 379000,
            "duration": "13200s",
            "travelAdvisory": {
                "tollInfo": {
                    "estimatedPrice": [
                        {"currencyCode": "USD", "units": "15", "nanos": 500_000_000}
                    ]
                }
            },
        }
    ]
}

_COMPUTE_ROUTES_NO_TOLL = {
    "routes": [
        {
            "distanceMeters": 379000,
            "duration": "13200s",
        }
    ]
}

_COMPUTE_ROUTES_EMPTY_TOLL_PRICE = {
    "routes": [
        {
            "distanceMeters": 379000,
            "duration": "13200s",
            "travelAdvisory": {"tollInfo": {"estimatedPrice": []}},
        }
    ]
}

_COMPUTE_ROUTES_NO_ROUTES = {"routes": []}


# ---------------------------------------------------------------------------
# Toll info — fetch helper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_toll_info_returns_cost_and_currency():
    with respx.mock as mock:
        mock.post(COMPUTE_ROUTES_URL).respond(json=_COMPUTE_ROUTES_WITH_TOLL)
        cost, currency = await _fetch_toll_info(
            "fake-key", "Orlando, FL", "Miami, FL"
        )
    assert cost == 15.5
    assert currency == "USD"


@pytest.mark.asyncio
async def test_fetch_toll_info_no_toll_data():
    with respx.mock as mock:
        mock.post(COMPUTE_ROUTES_URL).respond(json=_COMPUTE_ROUTES_NO_TOLL)
        cost, currency = await _fetch_toll_info(
            "fake-key", "Orlando, FL", "Miami, FL"
        )
    assert cost is None
    assert currency is None


@pytest.mark.asyncio
async def test_fetch_toll_info_empty_routes():
    with respx.mock as mock:
        mock.post(COMPUTE_ROUTES_URL).respond(json=_COMPUTE_ROUTES_NO_ROUTES)
        cost, currency = await _fetch_toll_info(
            "fake-key", "Orlando, FL", "Miami, FL"
        )
    assert cost is None
    assert currency is None


@pytest.mark.asyncio
async def test_fetch_toll_info_empty_price_list():
    with respx.mock as mock:
        mock.post(COMPUTE_ROUTES_URL).respond(json=_COMPUTE_ROUTES_EMPTY_TOLL_PRICE)
        cost, currency = await _fetch_toll_info(
            "fake-key", "Orlando, FL", "Miami, FL"
        )
    assert cost is None
    assert currency is None


@pytest.mark.asyncio
async def test_fetch_toll_info_api_failure_returns_none():
    with respx.mock as mock:
        mock.post(COMPUTE_ROUTES_URL).respond(status_code=500)
        cost, currency = await _fetch_toll_info(
            "fake-key", "Orlando, FL", "Miami, FL"
        )
    assert cost is None
    assert currency is None


# ---------------------------------------------------------------------------
# Toll integration — compare_drive_or_fly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compare_drive_or_fly_with_tolls_same_currency():
    """Toll currency matches fuel currency → total = fuel + tolls + rental."""
    with respx.mock as mock:
        mock.post(ROUTES_API_BASE).respond(json=_ROUTE_RESPONSE)
        mock.post(COMPUTE_ROUTES_URL).respond(json=_COMPUTE_ROUTES_WITH_TOLL)
        result = await compare_drive_or_fly(
            origin="Orlando, FL",
            destination="Miami, FL",
            fuel_price_per_liter=5.50,
            fuel_efficiency_km_per_liter=12.0,
            rental_car_cost_total=200.0,
            currency="USD",
        )

    assert result["estimated_fuel_cost"] == 173.71
    assert result["estimated_toll_cost"] == 15.5
    assert result["toll_currency"] == "USD"
    # 173.71 + 200.0 + 15.5 = 389.21
    assert result["estimated_total_driving_cost"] == 389.21
    assert "toll_note" not in result


@pytest.mark.asyncio
async def test_compare_drive_or_fly_with_tolls_currency_mismatch():
    """Toll currency ≠ fuel currency → total stays fuel-only + note field."""
    with respx.mock as mock:
        mock.post(ROUTES_API_BASE).respond(json=_ROUTE_RESPONSE)
        mock.post(COMPUTE_ROUTES_URL).respond(json=_COMPUTE_ROUTES_WITH_TOLL)
        result = await compare_drive_or_fly(
            origin="Orlando, FL",
            destination="Miami, FL",
            fuel_price_per_liter=5.50,
            fuel_efficiency_km_per_liter=12.0,
            currency="BRL",
        )

    assert result["estimated_fuel_cost"] == 173.71
    assert result["estimated_toll_cost"] == 15.5
    assert result["toll_currency"] == "USD"
    # Fuel-only total (no tolls added because currency mismatch)
    assert result["estimated_total_driving_cost"] == 173.71
    assert "toll_note" in result
    assert "15.5 USD" in result["toll_note"]
    assert "BRL" in result["toll_note"]


@pytest.mark.asyncio
async def test_compare_drive_or_fly_no_toll_info_backward_compatible():
    """No tollInfo → output shape identical to current behavior."""
    with respx.mock as mock:
        mock.post(ROUTES_API_BASE).respond(json=_ROUTE_RESPONSE)
        mock.post(COMPUTE_ROUTES_URL).respond(json=_COMPUTE_ROUTES_NO_TOLL)
        result = await compare_drive_or_fly(
            origin="Orlando, FL",
            destination="Miami, FL",
            fuel_price_per_liter=5.50,
            fuel_efficiency_km_per_liter=12.0,
        )

    assert "estimated_fuel_cost" in result
    assert "estimated_toll_cost" not in result
    assert "toll_currency" not in result
    assert "toll_note" not in result
    # Total = fuel only (no rental, no tolls)
    assert result["estimated_total_driving_cost"] == 173.71


@pytest.mark.asyncio
async def test_compare_drive_or_fly_compute_routes_failure_graceful():
    """computeRoutes fails + matrix succeeds → today's result, no error."""
    with respx.mock as mock:
        mock.post(ROUTES_API_BASE).respond(json=_ROUTE_RESPONSE)
        mock.post(COMPUTE_ROUTES_URL).respond(status_code=503)
        result = await compare_drive_or_fly(
            origin="Orlando, FL",
            destination="Miami, FL",
            fuel_price_per_liter=5.50,
            fuel_efficiency_km_per_liter=12.0,
        )

    assert result["distance_km"] == 379.0
    assert "estimated_fuel_cost" in result
    assert result["estimated_total_driving_cost"] == 173.71
    assert "estimated_toll_cost" not in result
    assert "toll_note" not in result


@pytest.mark.asyncio
async def test_compare_drive_or_fly_tolls_only_no_fuel():
    """Tolls present but no fuel params → toll fields attached, NO total.

    This expectation changed deliberately (2026-08-01 review): the original
    B-10 behavior set ``estimated_total_driving_cost = tolls`` here, and the
    flight comparison then presented tolls alone as the full cost of driving
    — a 500 flight vs 15.50 of tolls read as "driving saves 484.50" with
    fuel never counted. Tolls without fuel/rental inputs stay visible via
    ``estimated_toll_cost`` but never become a "total".
    """
    with respx.mock as mock:
        mock.post(ROUTES_API_BASE).respond(json=_ROUTE_RESPONSE)
        mock.post(COMPUTE_ROUTES_URL).respond(json=_COMPUTE_ROUTES_WITH_TOLL)
        result = await compare_drive_or_fly(
            origin="Orlando, FL",
            destination="Miami, FL",
            currency="USD",
        )

    assert result["estimated_toll_cost"] == 15.5
    assert result["toll_currency"] == "USD"
    assert "estimated_fuel_cost" not in result
    assert "estimated_total_driving_cost" not in result
    assert "toll_note" not in result


# ---------------------------------------------------------------------------
# Currency conversion — tolls arrive in the road's currency, not the caller's
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_caller_supplied_fx_rate_folds_tolls_into_total():
    """A caller-supplied rate converts the toll and completes the total."""
    with respx.mock as mock:
        mock.post(ROUTES_API_BASE).respond(json=_ROUTE_RESPONSE)
        mock.post(COMPUTE_ROUTES_URL).respond(json=_COMPUTE_ROUTES_WITH_TOLL)
        result = await compare_drive_or_fly(
            origin="Miami, FL",
            destination="Orlando, FL",
            fuel_price_per_liter=5.50,
            fuel_efficiency_km_per_liter=12.0,
            currency="BRL",
            fx_rate=5.5,
        )

    assert result["toll_cost_converted"] == 85.25  # 15.5 USD * 5.5
    assert result["fx_rate_used"] == 5.5
    assert result["fx_source"] == "caller"
    # 173.71 fuel + 85.25 tolls — the total no longer silently drops the tolls.
    assert result["estimated_total_driving_cost"] == 258.96
    assert "toll_note" not in result


@pytest.mark.asyncio
async def test_missing_fx_rate_is_fetched_from_ecb():
    """With no rate supplied, fall back to the daily ECB reference rate."""
    with respx.mock as mock:
        mock.post(ROUTES_API_BASE).respond(json=_ROUTE_RESPONSE)
        mock.post(COMPUTE_ROUTES_URL).respond(json=_COMPUTE_ROUTES_WITH_TOLL)
        mock.get(FX_BASE).respond(json={"base": "USD", "rates": {"BRL": 5.4}})
        result = await compare_drive_or_fly(
            origin="Miami, FL",
            destination="Orlando, FL",
            fuel_price_per_liter=5.50,
            fuel_efficiency_km_per_liter=12.0,
            currency="BRL",
        )

    assert result["fx_rate_used"] == 5.4
    assert result["fx_source"] == "ecb_daily"
    assert result["toll_cost_converted"] == 83.7


@pytest.mark.asyncio
async def test_caller_fx_rate_wins_over_the_fetched_one():
    """The caller may be pricing at a card rate; their number takes priority."""
    with respx.mock as mock:
        mock.post(ROUTES_API_BASE).respond(json=_ROUTE_RESPONSE)
        mock.post(COMPUTE_ROUTES_URL).respond(json=_COMPUTE_ROUTES_WITH_TOLL)
        fx = mock.get(FX_BASE).respond(json={"base": "USD", "rates": {"BRL": 5.4}})
        result = await compare_drive_or_fly(
            origin="Miami, FL",
            destination="Orlando, FL",
            fuel_price_per_liter=5.50,
            fuel_efficiency_km_per_liter=12.0,
            currency="BRL",
            fx_rate=6.0,
        )

    assert not fx.called, "should not spend a request when the caller gave a rate"
    assert result["fx_rate_used"] == 6.0
    assert result["fx_source"] == "caller"


@pytest.mark.asyncio
async def test_fx_failure_degrades_to_a_note_and_claims_no_conversion():
    """A dead FX host must not break the comparison, nor invent a rate."""
    with respx.mock as mock:
        mock.post(ROUTES_API_BASE).respond(json=_ROUTE_RESPONSE)
        mock.post(COMPUTE_ROUTES_URL).respond(json=_COMPUTE_ROUTES_WITH_TOLL)
        mock.get(FX_BASE).respond(status_code=500)
        result = await compare_drive_or_fly(
            origin="Miami, FL",
            destination="Orlando, FL",
            fuel_price_per_liter=5.50,
            fuel_efficiency_km_per_liter=12.0,
            currency="BRL",
        )

    assert "toll_cost_converted" not in result
    assert "fx_rate_used" not in result
    assert result["estimated_total_driving_cost"] == 173.71
    assert "15.5 USD" in result["toll_note"]


# ---------------------------------------------------------------------------
# Rental break-even — the rental is the number the caller does not have
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rental_breakeven_reported_when_rental_unknown():
    """Omitting the rental yields the ceiling that keeps driving cheaper."""
    with respx.mock as mock:
        mock.post(ROUTES_API_BASE).respond(json=_ROUTE_RESPONSE)
        mock.post(COMPUTE_ROUTES_URL).respond(json=_COMPUTE_ROUTES_WITH_TOLL)
        result = await compare_drive_or_fly(
            origin="Miami, FL",
            destination="Orlando, FL",
            fuel_price_per_liter=5.50,
            fuel_efficiency_km_per_liter=12.0,
            currency="BRL",
            fx_rate=5.5,
            flight_price=500.0,
        )

    # 500 flight - (173.71 fuel + 85.25 tolls) = 241.04
    assert result["comparison"]["rental_breakeven"] == 241.04
    assert "241.04 BRL" in result["comparison"]["rental_breakeven_note"]


@pytest.mark.asyncio
async def test_rental_breakeven_omitted_when_rental_is_known():
    """With a real rental cost there is nothing to solve for."""
    with respx.mock as mock:
        mock.post(ROUTES_API_BASE).respond(json=_ROUTE_RESPONSE)
        mock.post(COMPUTE_ROUTES_URL).respond(json=_COMPUTE_ROUTES_WITH_TOLL)
        result = await compare_drive_or_fly(
            origin="Miami, FL",
            destination="Orlando, FL",
            fuel_price_per_liter=5.50,
            fuel_efficiency_km_per_liter=12.0,
            rental_car_cost_total=200.0,
            currency="BRL",
            fx_rate=5.5,
            flight_price=500.0,
        )

    assert "rental_breakeven" not in result["comparison"]
    assert result["comparison"]["cost_difference"] == round(500 - 458.96, 2)
