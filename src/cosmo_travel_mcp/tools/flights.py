"""Flight search tools backed by SerpAPI's google_flights engine."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from ..onboarding import SERPAPI_ENV, missing_key_message

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
    key = os.environ.get(SERPAPI_ENV, "")
    if not key:
        raise ValueError(missing_key_message(SERPAPI_ENV))
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


async def _call_serpapi(params: dict[str, Any], *, engine: str = "google_flights") -> dict[str, Any]:
    """Call SerpAPI and return the JSON response, propagating errors."""
    api_key = _get_api_key()
    params = {**params, "engine": engine, "api_key": api_key}
    async with httpx.AsyncClient() as client:
        resp = await client.get(SERPAPI_BASE, params=params)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
    if "error" in data:
        raise ValueError(data["error"])
    return data


def _build_base_params(
    *,
    adults: int,
    children: int,
    cabin_class: str,
    currency: str,
    country: str | None,
    language: str | None,
    max_stops: str | None,
) -> dict[str, Any]:
    """Build the shared query-param dict for SerpAPI flight searches."""
    params: dict[str, Any] = {
        "adults": adults,
        "children": children,
        "travel_class": _map_cabin_class(cabin_class),
        "currency": currency,
    }
    stops = _map_stops(max_stops)
    if stops is not None:
        params["stops"] = stops
    if country:
        params["gl"] = country
    if language:
        params["hl"] = language
    return params


def _parse_leg(leg: dict[str, Any]) -> dict[str, Any]:
    """Parse a single flight leg from SerpAPI."""
    return {
        "airline": leg.get("airline", ""),
        "airline_logo": leg.get("airline_logo", ""),
        "flight_number": leg.get("flight_number", ""),
        "departure_airport": leg.get("departure_airport", {}).get("id", ""),
        "departure_airport_name": leg.get("departure_airport", {}).get("name", ""),
        "departure_time": leg.get("departure_airport", {}).get("time", ""),
        "arrival_airport": leg.get("arrival_airport", {}).get("id", ""),
        "arrival_airport_name": leg.get("arrival_airport", {}).get("name", ""),
        "arrival_time": leg.get("arrival_airport", {}).get("time", ""),
        "duration_minutes": leg.get("duration", 0),
    }


def _parse_layovers(legs: list[dict[str, Any]], item: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse layovers from a flight item.

    Real SerpAPI responses carry a top-level ``layovers`` array on the flight item
    with ``duration``, ``name``, and ``id`` keys.  If that array is absent, fall
    back to inferring layover airports from consecutive legs (no duration).
    """
    raw_layovers: list[dict[str, Any]] = item.get("layovers", [])
    if raw_layovers:
        result: list[dict[str, Any]] = []
        for lo in raw_layovers:
            parsed: dict[str, Any] = {
                "airport": lo.get("id", ""),
                "airport_name": lo.get("name", ""),
            }
            dur = lo.get("duration")
            if dur is not None:
                parsed["duration_minutes"] = dur
            result.append(parsed)
        return result

    # Fallback: infer layover airports from consecutive legs (no duration data).
    # _parse_leg maps airport fields to plain IATA strings (not dicts), so we
    # compare directly instead of calling .get("id") on them.
    fallback: list[dict[str, Any]] = []
    for i in range(len(legs) - 1):
        this_arr = legs[i].get("arrival_airport", "")
        next_dep = legs[i + 1].get("departure_airport", "")
        if this_arr and next_dep and this_arr == next_dep:
            fallback.append({
                "airport": this_arr if isinstance(this_arr, str) else this_arr.get("id", ""),
                "airport_name": this_arr.get("name", "") if isinstance(this_arr, dict) else "",
            })
    return fallback


def _parse_flight_item(
    item: dict[str, Any],
    bucket: str,
    currency: str,
) -> dict[str, Any]:
    """Parse a single flight item from a SerpAPI google_flights response."""
    legs_raw: list[dict[str, Any]] = item.get("flights", [])
    legs = [_parse_leg(leg) for leg in legs_raw]
    layovers = _parse_layovers(legs, item)

    parsed: dict[str, Any] = {
        "source": bucket,
        # No default: SerpAPI omits `price` on some items, and defaulting to 0
        # makes a priceless itinerary look free — it would then win any
        # cheapest-price comparison. None means "unknown", which callers can
        # detect; 0 silently lies.
        "price": item.get("price"),
        "currency": currency,
        "total_duration_minutes": item.get("total_duration", 0),
        "stops": len(legs) - 1 if legs else 0,
        "legs": legs,
        "layovers": layovers,
    }

    dt = item.get("departure_token")
    if dt:
        parsed["departure_token"] = dt

    bt = item.get("booking_token")
    if bt:
        parsed["booking_token"] = bt

    return parsed


def _parse_flights_response(
    data: dict[str, Any],
    requested_currency: str = "",
) -> dict[str, Any]:
    """Parse a SerpAPI google_flights response into a structured result."""
    currency = data.get("search_parameters", {}).get("currency", requested_currency)

    best: list[dict[str, Any]] = data.get("best_flights", [])
    other: list[dict[str, Any]] = data.get("other_flights", [])

    flights: list[dict[str, Any]] = []
    for item in best:
        flights.append(_parse_flight_item(item, "best_flights", currency))
    for item in other:
        flights.append(_parse_flight_item(item, "other_flights", currency))

    return {
        "flights": flights,
        "total_best": len(best),
        "total_other": len(other),
        "search_parameters": data.get("search_parameters", {}),
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


async def search_flights(
    origin: str,
    destination: str,
    outbound_date: str,
    return_date: str = "",
    adults: int = 1,
    children: int = 0,
    cabin_class: str = "economy",
    max_stops: str | None = None,
    departure_token: str = "",
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
        Round-trip phase 2 token from a previous phase-1 result.
    currency : str, default "BRL"
    country : str, optional
        Two-letter country code for geo-localization (SerpAPI ``gl``).
    language : str, optional
        Two-letter language code for localization (SerpAPI ``hl``).
    """
    # Validate: departure_token requires return_date for a round-trip phase 2.
    if departure_token and not return_date:
        raise ValueError(
            "departure_token requires return_date — re-send the same params "
            "as the original round-trip search (including return_date) plus the token."
        )

    params = _build_base_params(
        adults=adults,
        children=children,
        cabin_class=cabin_class,
        currency=currency,
        country=country,
        language=language,
        max_stops=max_stops,
    )
    params["departure_id"] = origin
    params["arrival_id"] = destination
    params["outbound_date"] = outbound_date

    if departure_token and return_date:
        # Round-trip phase 2
        params["type"] = 1
        params["return_date"] = return_date
        params["departure_token"] = departure_token
    elif return_date:
        # Round-trip phase 1
        params["type"] = 1
        params["return_date"] = return_date
    else:
        # One-way
        params["type"] = 2

    data = await _call_serpapi(params)
    result = _parse_flights_response(data, requested_currency=currency)

    # Annotate phase for round-trip
    if departure_token:
        result["phase"] = "return options — these are the return-flight options for the selected outbound."
    elif return_date:
        result["phase"] = (
            "outbound options — prices are round-trip totals; "
            "pass departure_token to fetch return flights for one of them."
        )

    return result


async def search_multi_city(
    legs: list[dict[str, Any]],
    adults: int = 1,
    children: int = 0,
    cabin_class: str = "economy",
    currency: str = "BRL",
    country: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Search multi-city flights via SerpAPI google_flights engine (type=3).

    Parameters
    ----------
    legs : list[dict]
        List of leg objects, each with ``origin``, ``destination``, ``date`` keys
        (and optionally ``times``). Must be 2-6 legs.
    adults : int, default 1
    children : int, default 0
    cabin_class : str, default "economy"
    currency : str, default "BRL"
    country : str, optional
    language : str, optional
    """
    if len(legs) < 2:
        raise ValueError(f"Multi-city requires at least 2 legs, got {len(legs)}.")
    if len(legs) > 6:
        raise ValueError(f"Multi-city supports at most 6 legs, got {len(legs)}.")

    multi_city_json: list[dict[str, Any]] = []
    for leg in legs:
        entry: dict[str, Any] = {
            "departure_id": leg["origin"],
            "arrival_id": leg["destination"],
            "date": leg["date"],
        }
        if "times" in leg:
            entry["times"] = leg["times"]
        multi_city_json.append(entry)

    params = _build_base_params(
        adults=adults,
        children=children,
        cabin_class=cabin_class,
        currency=currency,
        country=country,
        language=language,
        max_stops=None,
    )
    params["type"] = 3
    params["multi_city_json"] = json.dumps(multi_city_json)

    data = await _call_serpapi(params)
    return _parse_flights_response(data, requested_currency=currency)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(mcp: Any) -> None:
    """Register flight tools on a FastMCP instance."""
    mcp.tool()(search_flights)
    mcp.tool()(search_multi_city)
