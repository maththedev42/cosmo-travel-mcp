"""Drive-or-fly comparison tool using Google Maps Routes API.

This tool does exactly one job: given two places, return driving distance + duration,
and optionally fold in flight numbers the caller already has to produce a side-by-side
comparison. It does NOT fetch flight prices itself — use ``search_flights`` for that.

This is deliberately narrower than a general-purpose Google Maps integration. Use a
dedicated Maps MCP server for mapping, geocoding, or places needs.

Limitation: toll costs are not estimated in this version. The Routes API supports
``extraComputations: ["TOLLS"]``, but coverage is regional and it complicates response
parsing. Future extension: add a ``include_tolls`` parameter that appends toll fields
to the field mask and parses the tolls array from the response.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ROUTES_API_BASE = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"

FIELD_MASK = "originIndex,destinationIndex,status,condition,distanceMeters,duration"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_maps_api_key() -> str:
    """Read GOOGLE_MAPS_API_KEY from the environment."""
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not key:
        raise ValueError(
            "GOOGLE_MAPS_API_KEY is not set — see the README for how to get one."
        )
    return key


def _parse_duration(duration_str: str) -> int:
    """Parse a protobuf Duration string like ``"12345s"`` into whole minutes.

    The Routes API returns protobuf ``Duration`` values in JSON form, whose
    spec allows a fractional part (``"123.5s"``, ``"3.000000001s"``) as well as
    plain integer seconds. Parse as float so a fractional response cannot crash
    the tool, then floor to whole minutes.
    """
    if duration_str.endswith("s"):
        try:
            return int(float(duration_str[:-1]) // 60)
        except ValueError:
            raise ValueError(f"Unexpected duration format: {duration_str!r}") from None
    raise ValueError(f"Unexpected duration format: {duration_str!r}")


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


async def compare_drive_or_fly(
    origin: str,
    destination: str,
    fuel_price_per_liter: float | None = None,
    fuel_efficiency_km_per_liter: float | None = None,
    rental_car_cost_total: float | None = None,
    flight_price: float | None = None,
    flight_duration_minutes: float | None = None,
    currency: str = "BRL",
) -> dict[str, Any]:
    """Compare driving vs flying between two places.

    Uses the Google Maps Routes API (``computeRouteMatrix``) to get driving distance
    and duration. Optionally folds in flight numbers the caller already has to build a
    side-by-side comparison.

    Args:
        origin: Free-text origin (e.g. ``"Orlando, FL"``). Google geocodes it.
        destination: Free-text destination (e.g. ``"Miami, FL"``).
        fuel_price_per_liter: Price per liter of fuel. No default — prices vary by
                              country/time.
        fuel_efficiency_km_per_liter: Vehicle fuel efficiency in km per liter.
        rental_car_cost_total: Flat rental-car cost already known or estimated.
        flight_price: Flight price the caller already knows (from ``search_flights``).
        flight_duration_minutes: Flight duration the caller already knows.
        currency: Currency label for the caller-supplied numbers (default ``"BRL"``).
                  No currency conversion is performed.

    Returns:
        A dict with ``distance_km``, ``driving_duration_minutes``, and optionally
        ``estimated_fuel_cost``, ``estimated_total_driving_cost``, and ``comparison``.
    """
    api_key = _get_maps_api_key()

    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": FIELD_MASK,
        "Content-Type": "application/json",
    }

    body = {
        "origins": [{"waypoint": {"address": origin}}],
        "destinations": [{"waypoint": {"address": destination}}],
        "travelMode": "DRIVE",
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(ROUTES_API_BASE, headers=headers, json=body)
        resp.raise_for_status()
    data: list[dict[str, Any]] = resp.json()

    # The response is an array of elements, one per origin×destination pair.
    if not data:
        raise ValueError("No route found between the given locations.")

    element = data[0]

    # Check for error statuses or conditions — never mask as zero-result.
    status = element.get("status", {})
    condition = element.get("condition", "")

    # Routes API uses a status object with a "code" field, or a plain string.
    # Common non-OK codes include "ROUTE_NOT_FOUND" and "ZERO_RESULTS".
    if isinstance(status, dict):
        status_code = status.get("code", "")
    else:
        status_code = str(status) if status else ""

    if condition == "ROUTE_NOT_FOUND" or status_code == "ROUTE_NOT_FOUND":
        raise ValueError("ROUTE_NOT_FOUND: no driving route found between the given locations.")
    if status_code and status_code != "OK":
        raise ValueError(f"Routes API returned status {status_code!r} — {status}")

    distance_meters: int = element.get("distanceMeters", 0)
    duration_str: str = element.get("duration", "0s")

    distance_km = round(distance_meters / 1000.0, 1)
    driving_minutes = _parse_duration(duration_str)

    result: dict[str, Any] = {
        "distance_km": distance_km,
        "driving_duration_minutes": driving_minutes,
    }

    # Fuel cost: only when both inputs are provided.
    fuel_cost: float | None = None
    if fuel_price_per_liter is not None and fuel_efficiency_km_per_liter is not None:
        fuel_cost = round((distance_km / fuel_efficiency_km_per_liter) * fuel_price_per_liter, 2)
        result["estimated_fuel_cost"] = fuel_cost

    # Total driving cost: fuel cost (if computed) + rental (if provided).
    if fuel_cost is not None or rental_car_cost_total is not None:
        total_driving = (fuel_cost or 0) + (rental_car_cost_total or 0)
        result["estimated_total_driving_cost"] = total_driving

    # Comparison: only when flight numbers are provided.
    if flight_price is not None or flight_duration_minutes is not None:
        comparison: dict[str, Any] = {}
        if flight_price is not None:
            total_driving_cost = result.get("estimated_total_driving_cost")
            if total_driving_cost is not None:
                comparison["cost_difference"] = round(flight_price - total_driving_cost, 2)
                comparison["currency"] = currency
        if flight_duration_minutes is not None:
            comparison["time_difference_minutes"] = round(flight_duration_minutes - driving_minutes)
        result["comparison"] = comparison

    return result


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(mcp: Any) -> None:
    """Register driving tool on a FastMCP instance."""
    mcp.tool()(compare_drive_or_fly)
