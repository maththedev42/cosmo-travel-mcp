"""Flight search tools backed by SerpAPI's google_flights engine."""

from __future__ import annotations

import os
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SERPAPI_BASE = "https://serpapi.com/search.json"

CABIN_CLASS_MAP: dict[str, int] = {
    "economy": 1,
    "premium_economy": 2,
    "business": 3,
    "first": 4,
}

STOPS_MAP: dict[str, int] = {
    "any": 0,
    "nonstop": 1,
    "one_or_fewer": 2,
    "two_or_fewer": 3,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_api_key() -> str:
    key = os.environ.get("SERPAPI_API_KEY", "")
    if not key:
        raise ValueError(
            "SERPAPI_API_KEY is not set — sign up for a free account at "
            "serpapi.com and export the variable."
        )
    return key


def _map_cabin_class(cabin_class: str) -> int:
    mapped = CABIN_CLASS_MAP.get(cabin_class)
    if mapped is None:
        raise ValueError(
            f"Invalid cabin_class: {cabin_class!r}. "
            f"Must be one of: {', '.join(CABIN_CLASS_MAP)}."
        )
    return mapped


def _map_stops(max_stops: str | None) -> int | None:
    if max_stops is None:
        return None
    mapped = STOPS_MAP.get(max_stops)
    if mapped is None:
        raise ValueError(
            f"Invalid max_stops: {max_stops!r}. "
            f"Must be one of: {', '.join(STOPS_MAP)}."
        )
    return mapped


def _parse_leg(leg: dict[str, Any]) -> dict[str, Any]:
    """Parse a single flight leg from SerpAPI."""
    return {
        "airline": leg.get("airline", ""),
        "flight_number": leg.get("flight_number", ""),
        "departure_airport": leg.get("departure_airport", {}).get("id", ""),
        "departure_airport_name": leg.get("departure_airport", {}).get("name", ""),
        "departure_time": leg.get("departure_airport", {}).get("time", ""),
        "arrival_airport": leg.get("arrival_airport", {}).get("id", ""),
        "arrival_airport_name": leg.get("arrival_airport", {}).get("name", ""),
        "arrival_time": leg.get("arrival_airport", {}).get("time", ""),
        "duration_minutes": leg.get("duration", 0),
    }


def _parse_layovers(legs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract layovers between consecutive legs."""
    layovers: list[dict[str, Any]] = []
    for i in range(len(legs) - 1):
        current_arrival = legs[i].get("arrival_airport", {})
        next_departure = legs[i + 1].get("departure_airport", {})
        layover_duration = legs[i].get("layover_duration", 0)
        layovers.append({
            "airport": current_arrival.get("id", ""),
            "airport_name": current_arrival.get("name", ""),
            "duration_minutes": layover_duration,
        })
    return layovers


def _parse_flight_item(
    item: dict[str, Any], bucket: str
) -> dict[str, Any]:
    """Parse a single flight result item from SerpAPI."""
    legs_raw = item.get("flights", [])
    legs = [_parse_leg(leg) for leg in legs_raw]
    layovers = _parse_layovers(legs_raw)

    parsed: dict[str, Any] = {
        "price": item.get("price", 0),
        "currency": item.get("currency", ""),
        "total_duration_minutes": item.get("total_duration", 0),
        "stops": len(legs) - 1 if legs else 0,
        "legs": legs,
        "layovers": layovers,
        "bucket": bucket,
    }

    # Round-trip phase 1: expose departure_token for follow-up
    if "departure_token" in item:
        parsed["departure_token"] = item["departure_token"]

    # Round-trip phase 2 or one-way: expose booking_token when present
    if "booking_token" in item:
        parsed["booking_token"] = item["booking_token"]

    return parsed


def _parse_flights_response(
    data: dict[str, Any],
) -> dict[str, Any]:
    """Combine best_flights + other_flights into one list."""
    best = data.get("best_flights", [])
    other = data.get("other_flights", [])
    results: list[dict[str, Any]] = []

    for item in best:
        results.append(_parse_flight_item(item, "best_flights"))
    for item in other:
        results.append(_parse_flight_item(item, "other_flights"))

    return {
        "flights": results,
        "total_count": len(results),
        "best_flights_count": len(best),
        "other_flights_count": len(other),
    }


# ---------------------------------------------------------------------------
# SerpAPI client
# ---------------------------------------------------------------------------


async def _call_serpapi(params: dict[str, Any]) -> dict[str, Any]:
    """Call SerpAPI and return the JSON body. Raises on HTTP or API errors."""
    api_key = _get_api_key()
    params = {**params, "api_key": api_key, "engine": "google_flights"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(SERPAPI_BASE, params=params)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()

    # SerpAPI returns {"error": "..."} on failures — propagate verbatim.
    if "error" in data:
        raise RuntimeError(data["error"])

    return data


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


async def search_flights(
    origin: str,
    destination: str,
    outbound_date: str,
    return_date: str | None = None,
    adults: int = 1,
    children: int = 0,
    cabin_class: str = "economy",
    max_stops: str | None = None,
    departure_token: str | None = None,
    currency: str = "BRL",
    country: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Search flights via SerpAPI google_flights engine.

    Parameters
    ----------
    origin : str
        IATA airport/city code(s), comma-separated for multiple (e.g. "JFK,EWR,LGA").
    destination : str
        IATA airport/city code(s) for arrival.
    outbound_date : str
        Departure date as YYYY-MM-DD.
    return_date : str, optional
        Return date as YYYY-MM-DD. If provided, performs a round-trip search.
    adults : int, default 1
    children : int, default 0
    cabin_class : str, default "economy"
        One of: economy, premium_economy, business, first.
    max_stops : str, optional
        One of: any, nonstop, one_or_fewer, two_or_fewer.
    departure_token : str, optional
        Round-trip phase 2: token from a phase-1 outbound result to fetch return flights.
    currency : str, default "BRL"
    country : str, optional
        Two-letter country code for results localization (SerpAPI `gl`).
    language : str, optional
        Two-letter language code for results localization (SerpAPI `hl`).
    """
    # Determine flight type
    if departure_token:
        flight_type = 1  # round-trip phase 2
    elif return_date:
        flight_type = 1  # round-trip phase 1
    else:
        flight_type = 2  # one-way

    params: dict[str, Any] = {
        "departure_id": origin,
        "arrival_id": destination,
        "outbound_date": outbound_date,
        "type": flight_type,
        "travel_class": _map_cabin_class(cabin_class),
        "adults": adults,
        "children": children,
        "currency": currency,
    }

    if return_date:
        params["return_date"] = return_date

    if departure_token:
        params["departure_token"] = departure_token

    stops_val = _map_stops(max_stops)
    if stops_val is not None:
        params["stops"] = stops_val

    if country:
        params["gl"] = country
    if language:
        params["hl"] = language

    data = await _call_serpapi(params)
    result = _parse_flights_response(data)

    # Annotate phase info for round trips
    if flight_type == 1 and not departure_token:
        result["phase"] = "outbound"
        result["note"] = (
            "These are outbound options with round-trip total prices. "
            "Pass departure_token from any item to fetch return flights."
        )
    elif departure_token:
        result["phase"] = "return"
        result["note"] = (
            "These are return-flight options for the selected outbound. "
            "Prices shown are the round-trip total (same as phase 1)."
        )

    return result


async def search_multi_city(
    legs: list[dict[str, str]],
    adults: int = 1,
    children: int = 0,
    cabin_class: str = "economy",
    currency: str = "BRL",
    country: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Search multi-city flights via SerpAPI google_flights engine.

    Parameters
    ----------
    legs : list of {origin, destination, date}
        Flight legs in order (2-6 legs). Each dict must have:
        - origin (str): IATA departure code
        - destination (str): IATA arrival code
        - date (str): YYYY-MM-DD
    adults : int, default 1
    children : int, default 0
    cabin_class : str, default "economy"
    currency : str, default "BRL"
    country : str, optional
    language : str, optional
    """
    if len(legs) < 2:
        raise ValueError("Multi-city search requires at least 2 legs.")
    if len(legs) > 6:
        raise ValueError("Multi-city search supports at most 6 legs.")

    multi_city_json = []
    for leg in legs:
        entry: dict[str, str] = {
            "departure_id": leg["origin"],
            "arrival_id": leg["destination"],
            "date": leg["date"],
        }
        multi_city_json.append(entry)

    params: dict[str, Any] = {
        "type": 3,
        "multi_city_json": multi_city_json,
        "travel_class": _map_cabin_class(cabin_class),
        "adults": adults,
        "children": children,
        "currency": currency,
    }

    if country:
        params["gl"] = country
    if language:
        params["hl"] = language

    data = await _call_serpapi(params)
    return _parse_flights_response(data)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(mcp: Any) -> None:
    """Register flight tools on a FastMCP instance."""
    mcp.tool()(search_flights)
    mcp.tool()(search_multi_city)
