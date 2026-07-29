"""Tests for the drive-or-fly comparison tool (Google Maps Routes API)."""

from __future__ import annotations

import os

import pytest
import respx

from cosmo_travel_mcp.tools.driving import (
    ROUTES_API_BASE,
    _parse_duration,
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
