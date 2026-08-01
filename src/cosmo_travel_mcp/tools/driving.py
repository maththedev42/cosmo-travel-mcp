"""Drive-or-fly comparison tool using Google Maps Routes API.

This tool does exactly one job: given two places, return driving distance + duration,
and optionally fold in flight numbers the caller already has to produce a side-by-side
comparison. It does NOT fetch flight prices itself — use ``search_flights`` for that.

This is deliberately narrower than a general-purpose Google Maps integration. Use a
dedicated Maps MCP server for mapping, geocoding, or places needs.

Toll estimates are fetched via ``computeRoutes`` with ``extraComputations: ["TOLLS"]``
(added alongside the primary ``computeRouteMatrix`` call).  Toll data is enrichment —
when the computeRoutes call fails the tool degrades gracefully to the matrix-only
result.  Toll currency is returned as-is; no conversion is performed.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from ..onboarding import MAPS_ENV, missing_key_message

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ROUTES_API_BASE = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
COMPUTE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"

FIELD_MASK = "originIndex,destinationIndex,status,condition,distanceMeters,duration"
TOLL_FIELD_MASK = "routes.distanceMeters,routes.duration,routes.travelAdvisory.tollInfo"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_maps_api_key() -> str:
    """Read GOOGLE_MAPS_API_KEY from the environment."""
    key = os.environ.get(MAPS_ENV, "")
    if not key:
        raise ValueError(missing_key_message(MAPS_ENV))
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


def _parse_money(money_obj: dict[str, Any]) -> float:
    """Parse a Routes API Money object (``{currencyCode, units, nanos}``) into a float.

    ``units`` is a string of the whole-currency amount (e.g. ``"15"``).
    ``nanos`` is the fractional part in nanounits (e.g. 500_000_000 → 0.50).
    """
    units = int(money_obj.get("units", "0"))
    nanos = money_obj.get("nanos", 0)
    return units + nanos / 1_000_000_000


async def _fetch_toll_info(
    api_key: str, origin: str, destination: str
) -> tuple[float | None, str | None]:
    """Fetch toll estimates from ``computeRoutes`` with ``TOLLS`` extra computation.

    Returns ``(estimated_toll_cost, toll_currency)`` or ``(None, None)`` when
    toll data is unavailable or the API call fails.  Toll data is enrichment;
    callers must degrade gracefully on ``(None, None)``.

    Uses a minimal field mask to keep response size small:
    ``routes.distanceMeters,routes.duration,routes.travelAdvisory.tollInfo``.
    """
    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": TOLL_FIELD_MASK,
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {
        "origin": {"address": origin},
        "destination": {"address": destination},
        "travelMode": "DRIVE",
        "extraComputations": ["TOLLS"],
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(COMPUTE_ROUTES_URL, headers=headers, json=body)
            resp.raise_for_status()
        data: dict[str, Any] = resp.json()
    except Exception:
        return None, None

    routes: list[dict[str, Any]] = data.get("routes", [])
    if not routes:
        return None, None

    toll_info: dict[str, Any] | None = (
        routes[0].get("travelAdvisory", {}).get("tollInfo")
    )
    if not toll_info:
        return None, None

    estimated_prices: list[dict[str, Any]] = toll_info.get("estimatedPrice", [])
    if not estimated_prices:
        return None, None

    price = estimated_prices[0]
    toll_cost = _parse_money(price)
    toll_currency = price.get("currencyCode", "")
    return toll_cost, toll_currency


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
    and duration. Also fetches toll estimates via ``computeRoutes`` with
    ``extraComputations: ["TOLLS"]``; toll data is enrichment — when unavailable the
    tool degrades gracefully to the matrix-only result.

    Optionally folds in flight numbers the caller already has to build a
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
        ``estimated_fuel_cost``, ``estimated_toll_cost`` + ``toll_currency``,
        ``estimated_total_driving_cost`` (fuel + tolls + rental when currencies
        match), and ``comparison``.
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

    # Toll enrichment: fetch asynchronously after the matrix call succeeds.
    # Degrades gracefully — when computeRoutes fails or returns no toll data,
    # the result is identical to today's matrix-only output.
    toll_cost, toll_currency = await _fetch_toll_info(api_key, origin, destination)

    # Fuel cost: only when both inputs are provided.
    fuel_cost: float | None = None
    if fuel_price_per_liter is not None and fuel_efficiency_km_per_liter is not None:
        fuel_cost = round((distance_km / fuel_efficiency_km_per_liter) * fuel_price_per_liter, 2)
        result["estimated_fuel_cost"] = fuel_cost

    # Toll data: attach when present and non-zero.
    if toll_cost is not None and toll_cost > 0:
        result["estimated_toll_cost"] = toll_cost
        result["toll_currency"] = toll_currency

    # Total driving cost: fuel (if computed) + rental (if provided) + tolls
    # when the toll currency matches the caller's fuel currency.  When they
    # differ, keep the total fuel+rental-only and add a note so the client
    # can reconcile.
    if fuel_cost is not None or rental_car_cost_total is not None or (
        toll_cost is not None and toll_cost > 0
    ):
        base_total = (fuel_cost or 0) + (rental_car_cost_total or 0)
        if toll_cost and toll_currency and toll_currency == currency:
            total_driving = base_total + toll_cost
            result["estimated_total_driving_cost"] = round(total_driving, 2)
        elif toll_cost and toll_currency:
            # Currencies differ: total is fuel+rental only; tolls listed separately.
            result["estimated_total_driving_cost"] = base_total
            result["toll_note"] = (
                f"Tolls ({toll_cost} {toll_currency}) are listed separately "
                f"— the caller's fuel currency is {currency}."
            )
        else:
            result["estimated_total_driving_cost"] = base_total

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
