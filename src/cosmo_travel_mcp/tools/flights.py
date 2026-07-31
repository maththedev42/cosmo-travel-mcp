"""Flight search tools backed by SerpAPI's google_flights engine."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from ..onboarding import SERPAPI_ENV, missing_key_message

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SERPAPI_BASE = "https://serpapi.com/search.json"

# Exposed as module-level so tests can zero it (avoid real waits in CI).
_RETRY_BACKOFF_SECONDS: float = 1.0

_RETRYABLE_STATUSES: frozenset[int] = frozenset({502, 503, 504})

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


def _validate_airlines(
    include_airlines: str | None,
    exclude_airlines: str | None,
) -> None:
    """Raise ValueError if both include_airlines and exclude_airlines are given.

    The SerpAPI google_flights engine does not support supplying both at once.
    """
    if include_airlines and exclude_airlines:
        raise ValueError(
            "include_airlines and exclude_airlines are mutually exclusive — "
            "provide at most one."
        )


def _validate_times(
    outbound_times: str | None,
    return_times: str | None,
    *,
    has_return_date: bool,
) -> None:
    """Validate outbound_times / return_times strings.

    Format: comma-separated 2 or 4 integers, each 0–23 (hours).
    ``return_times`` requires a ``return_date`` to be meaningful.
    """
    if outbound_times is not None:
        _parse_times_arg(outbound_times, label="outbound_times")
    if return_times is not None:
        if not has_return_date:
            raise ValueError(
                "return_times requires a return_date — the engine applies "
                "return-time filters to the return leg only."
            )
        _parse_times_arg(return_times, label="return_times")


def _parse_times_arg(value: str, *, label: str) -> list[int]:
    """Parse a times argument string (e.g. ``\"18,23\"``) into a list of ints.

    Raises ValueError with a message that names *label* if the value is
    malformed.
    """
    parts = value.split(",")
    if len(parts) not in (2, 4):
        raise ValueError(
            f"{label} must be 2 or 4 comma-separated integers (hours 0–23), "
            f"got {len(parts)} part(s): {value!r}"
        )
    result: list[int] = []
    for p in parts:
        try:
            hour = int(p.strip())
        except ValueError:
            raise ValueError(
                f"{label} must contain only integers (hours 0–23), "
                f"got {value!r}"
            ) from None
        if hour < 0 or hour > 23:
            raise ValueError(
                f"{label} values must be 0–23 (hours), got {hour} in {value!r}"
            )
        result.append(hour)
    return result


async def _call_serpapi(params: dict[str, Any], *, engine: str = "google_flights") -> dict[str, Any]:
    """Call SerpAPI and return the JSON response, propagating errors.

    Retries exactly once (with a short backoff) on transient failures:
    httpx transport errors (connect/read timeouts, connection errors) and
    HTTP 502/503/504 responses.  SerpAPI ``{"error": ...}`` JSON bodies and
    any other 4xx/5xx status are *never* retried — they propagate immediately.
    """
    api_key = _get_api_key()
    params = {**params, "engine": engine, "api_key": api_key}

    for attempt in (1, 2):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(SERPAPI_BASE, params=params)
            if resp.status_code in _RETRYABLE_STATUSES and attempt == 1:
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
                continue
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            if "error" in data:
                raise ValueError(data["error"])
            return data
        except httpx.TransportError:
            if attempt == 1:
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
                continue
            raise


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

    # ── carbon emissions (SerpAPI reports grams; we convert to kg) ──
    ce = item.get("carbon_emissions")
    if ce and isinstance(ce, dict):
        emissions: dict[str, Any] = {}
        tf = ce.get("this_flight")
        if isinstance(tf, (int, float)):
            emissions["this_flight_kg"] = max(0, round(tf / 1000))
        tr = ce.get("typical_for_this_route")
        if isinstance(tr, (int, float)):
            emissions["typical_for_route_kg"] = max(0, round(tr / 1000))
        dp = ce.get("difference_percent")
        if isinstance(dp, (int, float)):
            emissions["difference_percent"] = dp
        if emissions:
            parsed["carbon_emissions"] = emissions

    return parsed


def _compose_advice(
    level: str | None,
    lowest: int | float | None,
    trange: list[Any] | None,
    currency: str,
) -> str | None:
    """Compose a human-readable buy-advice sentence from price-insight fields.

    Each clause is independently optional — only present fields contribute.
    Returns ``None`` when no fields are available to form a sentence.
    """
    clauses: list[str] = []
    currency_label = currency or ""

    if level:
        clauses.append(f"prices are currently {level} for this route")

    if lowest is not None:
        clauses.append(f"the lowest recent price was {lowest} {currency_label}".rstrip())

    if trange and isinstance(trange, list) and len(trange) == 2:
        clauses.append(f"the typical range is {trange[0]}–{trange[1]} {currency_label}".rstrip())

    if not clauses:
        return None

    # Each clause is a standalone fragment, so the connectors depend on how many
    # survived: the price-level clause (when present) heads the sentence and the
    # figures that support it follow the colon.
    if len(clauses) == 1:
        sentence = clauses[0]
    elif len(clauses) == 2:
        sentence = f"{clauses[0]}, and {clauses[1]}"
    else:
        sentence = f"{clauses[0]}: {clauses[1]}, and {clauses[2]}"

    return sentence[0].upper() + sentence[1:] + "."


def _parse_price_insights(
    price_insights: dict[str, Any],
    currency: str,
) -> dict[str, Any] | None:
    """Normalize SerpAPI ``price_insights`` into a structured object.

    Converts ``price_history`` unix-second timestamps into ISO-8601 UTC dates
    and composes a human-readable ``advice`` string from the available data.

    Returns ``None`` when the input is missing or the dict carries no usable
    fields (SerpAPI sometimes omits the key entirely, and sometimes returns an
    empty ``{}`` — both are treated as absent).
    """
    if not price_insights or not isinstance(price_insights, dict):
        return None

    lowest = price_insights.get("lowest_price")
    level = price_insights.get("price_level")
    trange = price_insights.get("typical_price_range")

    result: dict[str, Any] = {}

    if lowest is not None:
        result["lowest_price"] = lowest
    if level is not None:
        result["price_level"] = level
    if trange and isinstance(trange, list) and len(trange) == 2:
        result["typical_price_range"] = trange

    # Price history: unix seconds → ISO-8601 UTC date string.
    raw_history = price_insights.get("price_history")
    if raw_history and isinstance(raw_history, list):
        history: list[dict[str, Any]] = []
        for point in raw_history:
            if not isinstance(point, list) or len(point) < 2:
                continue
            try:
                ts = int(point[0])
                price = float(point[1])
            except (ValueError, TypeError):
                continue
            date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            history.append({"date": date_str, "price": int(round(price))})
        if history:
            result["price_history"] = history

    # Compose a human-readable advice sentence from available fields.
    # Each clause is optional — only present fields contribute to the output.
    advice = _compose_advice(level, lowest, trange, currency)
    if advice:
        result["advice"] = advice

    return result if result else None


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

    parsed: dict[str, Any] = {
        "flights": flights,
        "total_best": len(best),
        "total_other": len(other),
        "search_parameters": data.get("search_parameters", {}),
    }

    pi = _parse_price_insights(data.get("price_insights", {}), currency)
    if pi is not None:
        parsed["price_insights"] = pi

    return parsed


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
    include_airlines: str | None = None,
    exclude_airlines: str | None = None,
    bags: int | None = None,
    max_duration: int | None = None,
    outbound_times: str | None = None,
    return_times: str | None = None,
    deep_search: bool = False,
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
    include_airlines : str, optional
        Comma-separated IATA airline codes or alliance names
        (``STAR_ALLIANCE``, ``SKYTEAM``, ``ONEWORLD``).
        Mutually exclusive with ``exclude_airlines``.
    exclude_airlines : str, optional
        Comma-separated IATA airline codes to exclude.
        Mutually exclusive with ``include_airlines``.
    bags : int, optional
        Number of carry-on bags the fare must include (≥ 0).
    max_duration : int, optional
        Maximum total itinerary duration in minutes.
    outbound_times : str, optional
        Departure/arrival hour window, e.g. ``"18,23"`` (2 values) or
        ``"18,23,6,12"`` (4 values) where hours are 0–23.
    return_times : str, optional
        Same format as ``outbound_times``, applied to the return leg.
        Requires ``return_date``.
    deep_search : bool, default False
        Slower but more accurate search mode. Adds latency proportional
        to route complexity.
    """
    # Validate: departure_token requires return_date for a round-trip phase 2.
    if departure_token and not return_date:
        raise ValueError(
            "departure_token requires return_date — re-send the same params "
            "as the original round-trip search (including return_date) plus the token."
        )

    _validate_airlines(include_airlines, exclude_airlines)
    _validate_times(outbound_times, return_times, has_return_date=bool(return_date))
    if bags is not None and bags < 0:
        raise ValueError(f"bags must be ≥ 0, got {bags}")
    if max_duration is not None and max_duration <= 0:
        raise ValueError(f"max_duration must be > 0, got {max_duration}")

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

    # ── Filters (omitted when absent / falsy — SerpAPI treats empty strings as values) ──
    if include_airlines:
        params["include_airlines"] = include_airlines
    if exclude_airlines:
        params["exclude_airlines"] = exclude_airlines
    if bags is not None:
        params["bags"] = bags
    if max_duration is not None:
        params["max_duration"] = max_duration
    if outbound_times:
        params["outbound_times"] = outbound_times
    if return_times:
        params["return_times"] = return_times
    if deep_search:
        params["deep_search"] = "true"

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
    include_airlines: str | None = None,
    exclude_airlines: str | None = None,
    bags: int | None = None,
    max_duration: int | None = None,
    deep_search: bool = False,
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
    include_airlines : str, optional
        Comma-separated IATA airline codes or alliance names.
        Mutually exclusive with ``exclude_airlines``.
    exclude_airlines : str, optional
        Comma-separated IATA airline codes to exclude.
        Mutually exclusive with ``include_airlines``.
    bags : int, optional
        Number of carry-on bags the fare must include (≥ 0).
    max_duration : int, optional
        Maximum total itinerary duration in minutes.
    deep_search : bool, default False
        Slower but more accurate search mode.
    """
    if len(legs) < 2:
        raise ValueError(f"Multi-city requires at least 2 legs, got {len(legs)}.")
    if len(legs) > 6:
        raise ValueError(f"Multi-city supports at most 6 legs, got {len(legs)}.")

    _validate_airlines(include_airlines, exclude_airlines)
    if bags is not None and bags < 0:
        raise ValueError(f"bags must be ≥ 0, got {bags}")
    if max_duration is not None and max_duration <= 0:
        raise ValueError(f"max_duration must be > 0, got {max_duration}")

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

    if include_airlines:
        params["include_airlines"] = include_airlines
    if exclude_airlines:
        params["exclude_airlines"] = exclude_airlines
    if bags is not None:
        params["bags"] = bags
    if max_duration is not None:
        params["max_duration"] = max_duration
    if deep_search:
        params["deep_search"] = "true"

    data = await _call_serpapi(params)
    return _parse_flights_response(data, requested_currency=currency)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(mcp: Any) -> None:
    """Register flight tools on a FastMCP instance."""
    mcp.tool()(search_flights)
    mcp.tool()(search_multi_city)
