"""Cheapest-dates search built on top of the flight-search logic.

Every call to this tool costs up to ``max_calls`` SerpAPI searches — not one.
Callers (human or LLM) should understand this before invoking it lightly.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

from .flights import (
    _build_base_params,
    _call_serpapi,
    _get_api_key,
    _inject_quota_warning,
    _map_cabin_class,
    _map_stops,
    _parse_flights_response,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_candidate_dates(
    earliest_departure: date,
    latest_return: date,
    trip_duration_days: int,
    max_calls: int,
) -> list[date]:
    """Generate up to ``max_calls`` evenly spaced outbound-date candidates.

    Candidates span ``[earliest_departure, latest_return - trip_duration_days]``,
    always including the first and last date of that range.
    """
    last_possible = latest_return - timedelta(days=trip_duration_days)
    total_days = (last_possible - earliest_departure).days

    if total_days < 0:
        return []

    if total_days == 0 or max_calls == 1:
        return [earliest_departure]

    candidates: list[date] = [earliest_departure]
    for i in range(1, max_calls - 1):
        offset = round(i * total_days / (max_calls - 1))
        candidates.append(earliest_departure + timedelta(days=offset))
    candidates.append(last_possible)

    # Deduplicate (consecutive rounding can produce duplicates when window is small).
    seen: set[date] = set()
    unique: list[date] = []
    for d in candidates:
        if d not in seen:
            seen.add(d)
            unique.append(d)
    return unique[:max_calls]


def _extract_cheapest_price(
    result: dict[str, Any],
) -> dict[str, Any] | None:
    """Extract the cheapest flight from a parsed SerpAPI response."""
    flights: list[dict[str, Any]] = result.get("flights", [])
    # Only itineraries with a real price can be compared. An item whose price
    # SerpAPI omitted is unknown, not free — including it would let it win the
    # comparison and report a bogus cheapest date.
    priced = [f for f in flights if f.get("price") is not None]
    if not priced:
        return None
    cheapest = min(priced, key=lambda f: f["price"])
    return {
        "price": cheapest["price"],
        "currency": cheapest.get("currency", ""),
        "stops": cheapest.get("stops", 0),
    }


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
    currency: str = "BRL",
    country: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Sample round-trip flight prices across a flexible date window.

    This calls the SerpAPI google_flights engine once per candidate date (up to
    ``max_calls`` times).  The phase-1 outbound response already carries round-trip
    total prices, so one call per date is enough — no departure_token follow-ups.

    Parameters
    ----------
    origin : str
        IATA airport/city code(s).
    destination : str
        IATA airport/city code(s).
    earliest_departure : str
        Earliest outbound date as YYYY-MM-DD.
    latest_return : str
        Latest return date as YYYY-MM-DD.
    trip_duration_days : int
        Fixed trip length in days.
    max_calls : int, default 6, hard cap 15
        Maximum number of SerpAPI calls (candidate dates).
    adults : int, default 1
    children : int, default 0
    cabin_class : str, default "economy"
    currency : str, default "BRL"
    country : str, optional
    language : str, optional
    """
    if max_calls > 15:
        raise ValueError(
            f"max_calls must be 15 or fewer (to protect SerpAPI quota), got {max_calls}."
        )

    dep = date.fromisoformat(earliest_departure)
    ret = date.fromisoformat(latest_return)

    if dep + timedelta(days=trip_duration_days) > ret:
        raise ValueError(
            f"Impossible window: earliest_departure ({earliest_departure}) + "
            f"trip_duration_days ({trip_duration_days}) > latest_return ({latest_return})."
        )

    candidates = _generate_candidate_dates(dep, ret, trip_duration_days, max_calls)
    if not candidates:
        raise ValueError(
            "No valid candidate dates in the given window — "
            "window may be shorter than trip_duration_days."
        )

    # Build the shared base params once.
    base_params = _build_base_params(
        adults=adults,
        children=children,
        cabin_class=cabin_class,
        currency=currency,
        country=country,
        language=language,
        max_stops=None,
    )

    async def _search_one(outbound: date) -> dict[str, Any] | None:
        return_date = outbound + timedelta(days=trip_duration_days)
        params: dict[str, Any] = {
            **base_params,
            "departure_id": origin,
            "arrival_id": destination,
            "outbound_date": outbound.isoformat(),
            "return_date": return_date.isoformat(),
            "type": 1,
        }
        try:
            data, from_cache = await _call_serpapi(params)
            result = _parse_flights_response(data, requested_currency=currency)
        except ValueError:
            raise  # Configuration errors (missing API key) must propagate.
        except Exception as exc:
            # Do not drop this date silently — a quota or network failure that
            # vanishes from the output reads as "no flights on that date".
            return {
                "outbound_date": outbound.isoformat(),
                "return_date": return_date.isoformat(),
                "error": f"{type(exc).__name__}: {exc}",
            }
        cheapest = _extract_cheapest_price(result)
        if cheapest is None:
            return {
                "outbound_date": outbound.isoformat(),
                "return_date": return_date.isoformat(),
                "error": "no priced itineraries returned for this date",
            }
        entry: dict[str, Any] = {
            "outbound_date": outbound.isoformat(),
            "return_date": return_date.isoformat(),
            "cheapest_price": cheapest["price"],
            "currency": cheapest["currency"],
            "stops": cheapest["stops"],
        }
        if from_cache:
            # This date's search was served from the session cache and cost
            # no quota — without the flag, a fully cached sampling still
            # reads as having spent max_calls searches.
            entry["cached"] = True
        return entry

    results = await asyncio.gather(*(_search_one(d) for d in candidates))

    priced: list[dict[str, Any]] = [r for r in results if "error" not in r]
    failed: list[dict[str, Any]] = [r for r in results if "error" in r]
    priced.sort(key=lambda r: r["cheapest_price"])

    note = (
        f"Sampled {len(candidates)} outbound dates (max_calls={max_calls}) "
        f"across the requested window — not an exhaustive scan of every date."
    )
    if failed:
        note += (
            f" {len(failed)} of those dates returned no usable price; "
            "see `unavailable` for why."
        )

    out: dict[str, Any] = {"note": note, "results": priced}
    if failed:
        out["unavailable"] = failed
    _inject_quota_warning(out)
    return out


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(mcp: Any) -> None:
    """Register cheapest-dates tool on a FastMCP instance."""
    mcp.tool()(search_cheapest_dates)
