"""Cheapest-dates search — sampled date grid on top of SerpAPI google_flights.

WARNING — cost profile: every call to `search_cheapest_dates` makes up to
`max_calls` SerpAPI searches (one per candidate date), not one.  On the free
tier (100 searches/month) a single call with max_calls=15 burns 15 % of the
monthly quota.  Call this tool deliberately, not as a conversational default.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

from .flights import (
    CABIN_CLASS_MAP,
    STOPS_MAP,
    _call_serpapi,
    _parse_flights_response,
)

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


def _generate_candidate_dates(
    earliest_departure: str,
    latest_return: str,
    trip_duration_days: int,
    max_calls: int,
) -> list[str]:
    """Generate evenly spaced outbound-date candidates.

    Always includes the first and last possible departure dates.
    """
    start = date.fromisoformat(earliest_departure)
    end = date.fromisoformat(latest_return)
    last_departure = end - timedelta(days=trip_duration_days)

    if start > last_departure:
        raise ValueError(
            f"Impossible window: earliest_departure ({earliest_departure}) + "
            f"trip_duration_days ({trip_duration_days}) > "
            f"latest_return ({latest_return})."
        )

    total_days = (last_departure - start).days

    if max_calls == 1 or total_days == 0:
        return [start.isoformat()]

    # Evenly space, always include first and last
    candidates: list[str] = [start.isoformat()]
    for i in range(1, max_calls - 1):
        offset = round(total_days * i / (max_calls - 1))
        candidates.append((start + timedelta(days=offset)).isoformat())
    candidates.append(last_departure.isoformat())

    # Deduplicate in case rounding produces duplicates
    seen: set[str] = set()
    unique: list[str] = []
    for d in candidates:
        if d not in seen:
            seen.add(d)
            unique.append(d)
    return unique


def _extract_cheapest_price(
    parsed: dict[str, Any],
) -> tuple[float, str] | None:
    """Return (price, currency) of the cheapest item across all buckets."""
    items = parsed.get("flights", [])
    if not items:
        return None

    best: tuple[float, str] | None = None
    for item in items:
        price = item.get("price", 0)
        currency = item.get("currency", "")
        if best is None or price < best[0]:
            best = (price, currency)
    return best


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


async def search_cheapest_dates(
    origin: str,
    destination: str,
    earliest_departure: str,
    latest_return: str,
    trip_duration_days: int,
    max_calls: int = 6,
    adults: int = 1,
    children: int = 0,
    cabin_class: str = "economy",
    max_stops: str | None = None,
    currency: str = "BRL",
    country: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Sample round-trip prices across a flexible date window.

    Generates up to `max_calls` candidate outbound dates evenly spaced
    across [earliest_departure, latest_return - trip_duration_days] and
    calls SerpAPI once per candidate to get the round-trip total price.
    Results are sorted by price ascending.

    Args:
        origin: IATA code(s), e.g. "JFK,EWR,LGA".
        destination: IATA code(s).
        earliest_departure: Earliest outbound date (YYYY-MM-DD).
        latest_return: Latest possible return date (YYYY-MM-DD).
        trip_duration_days: Fixed trip length in days.
        max_calls: Max SerpAPI calls (default 6, hard cap 15).
        adults: Number of adult passengers.
        children: Number of child passengers.
        cabin_class: economy | premium_economy | business | first.
        max_stops: any | nonstop | one_or_fewer | two_or_fewer.
        currency: ISO 4217 currency code (default BRL).
        country: 2-letter country code for localization (gl param).
        language: Language code (hl param).

    Returns:
        A dict with:
        - candidates_checked: number of dates actually queried.
        - max_calls_requested: the max_calls the caller asked for.
        - results: list of {outbound_date, return_date, cheapest_price,
          currency}, sorted by price.
        - note: states that this is a sample, not an exhaustive scan.
    """
    if max_calls < 1:
        raise ValueError("max_calls must be at least 1.")
    if max_calls > 15:
        raise ValueError(
            f"max_calls={max_calls} exceeds the hard cap of 15. "
            "Reduce max_calls to 15 or fewer."
        )
    if trip_duration_days < 1:
        raise ValueError("trip_duration_days must be at least 1.")

    _get_api_key()  # fail early

    candidates = _generate_candidate_dates(
        earliest_departure, latest_return, trip_duration_days, max_calls
    )

    results: list[dict[str, Any]] = []

    for outbound in candidates:
        outbound_date = date.fromisoformat(outbound)
        return_date = (outbound_date + timedelta(days=trip_duration_days)).isoformat()

        params: dict[str, Any] = {
            "engine": "google_flights",
            "departure_id": origin,
            "arrival_id": destination,
            "outbound_date": outbound,
            "return_date": return_date,
            "type": 1,  # round trip — phase-1 prices are round-trip totals
            "travel_class": _map_cabin_class(cabin_class),
            "adults": adults,
            "children": children,
            "currency": currency,
        }

        stops_val = _map_stops(max_stops)
        if stops_val is not None:
            params["stops"] = stops_val
        if country:
            params["gl"] = country
        if language:
            params["hl"] = language

        data = await _call_serpapi(params)
        parsed = _parse_flights_response(data)
        cheapest = _extract_cheapest_price(parsed)

        results.append({
            "outbound_date": outbound,
            "return_date": return_date,
            "cheapest_price": cheapest[0] if cheapest else None,
            "currency": cheapest[1] if cheapest else currency,
        })

    # Sort by price ascending (None prices at the end)
    results.sort(key=lambda r: (
        r["cheapest_price"] is None,
        r["cheapest_price"] or float("inf"),
    ))

    return {
        "candidates_checked": len(candidates),
        "max_calls_requested": max_calls,
        "results": results,
        "note": (
            "This is a SAMPLE of candidate dates, NOT an exhaustive scan of every "
            "day in the window. Prices may vary on un-sampled dates."
        ),
    }


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(mcp: Any) -> None:
    """Register cheapest-dates tool on a FastMCP instance."""
    mcp.tool()(search_cheapest_dates)
