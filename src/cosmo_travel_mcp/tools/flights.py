"""Flight search tools backed by SerpAPI's google_flights engine."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import OrderedDict
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
# Quota warning (best-effort, one account fetch per session)
# ---------------------------------------------------------------------------

# Threshold below which every SerpAPI-backed response gets a quota_warning.
_LOW_QUOTA_THRESHOLD: int = 10

# Cached estimate of searches remaining.  ``None`` means "not yet fetched";
# ``_QUOTA_DISABLED`` means the account fetch failed and the feature is off
# for the rest of the session.  The two must stay distinct — collapsing them
# makes every subsequent search re-attempt the fetch.  Decremented on every
# successful SerpAPI search once the counter is live.
_QUOTA_DISABLED = object()

_QUOTA_SEARCHES_LEFT: int | None | object = None

_ENGINE_ERRORS: dict[str, tuple[str, str]] = {}


def _record_engine_error(engine: str, error_msg: str) -> None:
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    _ENGINE_ERRORS[engine] = (error_msg, now_iso)


def _clear_engine_error(engine: str) -> None:
    _ENGINE_ERRORS.pop(engine, None)


def get_engine_error(engine: str) -> tuple[str, str] | None:
    """Return (error_msg, timestamp_iso) if engine failed in last call in this session."""
    return _ENGINE_ERRORS.get(engine)


def _reset_engine_errors() -> None:
    """Reset tracked engine errors (for tests)."""
    global _ENGINE_ERRORS
    _ENGINE_ERRORS = {}

# ---------------------------------------------------------------------------
# Session-scoped SerpAPI response cache
# ---------------------------------------------------------------------------

def _cache_ttl_from_env() -> int:
    """Read COSMO_TRAVEL_CACHE_TTL, falling back to the default on garbage.

    This runs at import: a malformed value must degrade to the default, not
    kill the server before the MCP handshake — the client would only show
    "server died" with the real cause buried in a traceback.
    """
    raw = os.environ.get("COSMO_TRAVEL_CACHE_TTL", "600")
    try:
        return max(0, int(raw))
    except ValueError:
        return 600


# TTL in seconds — power-user knob, default 10 min (600 s).  0 disables.
_CACHE_TTL_SECONDS: int = _cache_ttl_from_env()

# Max entries before eviction (oldest ejected, FIFO).
_CACHE_MAX_ENTRIES: int = 128

# OrderedDict so eviction is cheap.  Key: canonical param tuple; value:
# (expiry_monotonic: float, response_data: dict[str, Any]).
_RESPONSE_CACHE: OrderedDict[tuple[tuple[str, Any], ...], tuple[float, dict[str, Any]]] = OrderedDict()


def _reset_cache() -> None:
    """Reset the module-level response cache (for tests)."""
    global _RESPONSE_CACHE
    _RESPONSE_CACHE = OrderedDict()


# SerpAPI reports "nothing matched" as an ``error`` body rather than an empty
# result array, so ``_call_serpapi`` raises for it like any other error. For a
# search that is *allowed* to match nothing — an events query for a quiet city,
# one angle of a multi-angle sweep — that is a crash where an empty list is the
# right answer. Callers that can legitimately come back empty test for it.
_NO_RESULTS_MARKERS: tuple[str, ...] = (
    "hasn't returned any results",
    "has not returned any results",
    "no results found",
)


def is_no_results_error(exc: Exception) -> bool:
    """True when *exc* is SerpAPI's "nothing matched" response, not a failure."""
    message = str(exc).casefold()
    return any(marker.casefold() in message for marker in _NO_RESULTS_MARKERS)


def _cache_key(params: dict[str, Any]) -> tuple[tuple[str, Any], ...]:
    """Build a canonical cache key from the outgoing params, minus the API key."""
    stable = {k: v for k, v in params.items() if k != "api_key"}
    return tuple(sorted(stable.items()))


def _reset_quota_counter() -> None:
    """Reset the module-level quota counter (for tests)."""
    global _QUOTA_SEARCHES_LEFT
    _QUOTA_SEARCHES_LEFT = None


def _refresh_quota_from_account(account: dict[str, Any]) -> None:
    """Re-anchor the cached counter from a live account response.

    Called by ``check_setup`` after each probe so the local estimate
    syncs to the real number without wasting a search.
    """
    global _QUOTA_SEARCHES_LEFT
    raw = account.get("plan_searches_left")
    # check_setup just proved the account endpoint works, so a live number
    # re-anchors the estimate and re-enables warnings even if an earlier
    # fetch had disabled them.
    _QUOTA_SEARCHES_LEFT = int(raw) if raw is not None else _QUOTA_DISABLED


async def _maybe_fetch_quota() -> None:
    """Fetch the account status once per session to seed the local counter.

    Only called on the first successful SerpAPI search.  If the fetch
    fails, the feature is silently disabled for the session — warnings
    are best-effort and must never break searches.
    """
    global _QUOTA_SEARCHES_LEFT
    if _QUOTA_SEARCHES_LEFT is not None:
        return  # Already seeded, or disabled after a failed fetch.

    key = _get_api_key()
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://serpapi.com/account.json",
                params={"api_key": key},
            )
            resp.raise_for_status()
            account: dict[str, Any] = resp.json()
            raw = account.get("plan_searches_left")
            # A response without the field is as unusable as a failed
            # fetch — disable rather than retry it on every search.
            _QUOTA_SEARCHES_LEFT = int(raw) if raw is not None else _QUOTA_DISABLED
    except Exception:
        _QUOTA_SEARCHES_LEFT = _QUOTA_DISABLED


def _inject_quota_warning(result: dict[str, Any]) -> None:
    """Add a ``quota_warning`` top-level key when searches are running low."""
    if isinstance(_QUOTA_SEARCHES_LEFT, int) and _QUOTA_SEARCHES_LEFT <= _LOW_QUOTA_THRESHOLD:
        result["quota_warning"] = (
            f"About {_QUOTA_SEARCHES_LEFT} SerpAPI searches left this month "
            "on your plan — check_setup shows the exact number."
        )

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
    """Parse a times argument string (e.g. ``"18,23"``) into a list of ints.

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


def _format_times_arg(value: str, *, label: str) -> str:
    """Normalize a times argument for transmission (``"18, 23"`` → ``"18,23"``)."""
    return ",".join(str(h) for h in _parse_times_arg(value, label=label))


async def _call_serpapi(
    params: dict[str, Any], *, engine: str = "google_flights",
) -> tuple[dict[str, Any], bool]:
    """Call SerpAPI and return ``(data, from_cache)``.

    ``from_cache`` is ``True`` when the response was served from the
    session-scoped in-memory cache — the caller should surface
    ``cached: true`` on the tool response so the AI client can tell the
    user.

    Retries exactly once (with a short backoff) on transient failures:
    httpx transport errors (connect/read timeouts, connection errors) and
    HTTP 502/503/504 responses.  SerpAPI ``{"error": ...}`` JSON bodies and
    any other 4xx/5xx status are *never* retried — they propagate immediately.

    Responses from successful upstream calls are cached in memory for the
    session (TTL controlled by ``COSMO_TRAVEL_CACHE_TTL``, default 10 min).
    Cache hits do not decrement the quota counter.
    """
    api_key = _get_api_key()
    params = {**params, "engine": engine, "api_key": api_key}

    # --- cache lookup (before any HTTP call) ---
    if _CACHE_TTL_SECONDS > 0:
        key = _cache_key(params)
        now_mono = time.monotonic()
        entry = _RESPONSE_CACHE.get(key)
        if entry is not None:
            expiry, cached_data = entry
            if now_mono < expiry:
                return cached_data, True
            # Expired — evict.
            del _RESPONSE_CACHE[key]

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
                _record_engine_error(engine, data["error"])
                raise ValueError(data["error"])
            _clear_engine_error(engine)
            # --- cache the successful response ---
            if _CACHE_TTL_SECONDS > 0:
                now_mono = time.monotonic()
                # Evict oldest if at capacity.
                while len(_RESPONSE_CACHE) >= _CACHE_MAX_ENTRIES:
                    _RESPONSE_CACHE.popitem(last=False)
                _RESPONSE_CACHE[key] = (now_mono + _CACHE_TTL_SECONDS, data)
            # Best-effort quota tracking (never breaks searches).
            await _maybe_fetch_quota()
            global _QUOTA_SEARCHES_LEFT
            if isinstance(_QUOTA_SEARCHES_LEFT, int):
                _QUOTA_SEARCHES_LEFT -= 1
            return data, False
        except httpx.HTTPStatusError as exc:
            sanitized_url = re.sub(r"api_key=[^&]+", "api_key=***", str(exc.request.url)) if exc.request else ""
            req = httpx.Request(exc.request.method, sanitized_url, headers=exc.request.headers) if exc.request else None
            msg = re.sub(r"api_key=[^&]+", "api_key=***", str(exc))
            _record_engine_error(engine, msg)
            raise httpx.HTTPStatusError(msg, request=req, response=exc.response) from None
        except httpx.RequestError as exc:
            if attempt == 1:
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
                continue
            sanitized_url = re.sub(r"api_key=[^&]+", "api_key=***", str(exc.request.url)) if exc.request else ""
            req = httpx.Request(exc.request.method, sanitized_url, headers=exc.request.headers) if exc.request else None
            msg = re.sub(r"api_key=[^&]+", "api_key=***", str(exc))
            _record_engine_error(engine, msg)
            cls = type(exc)
            raise cls(msg, request=req) from None


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
    price_insights: dict[str, Any] | None,
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


# SerpAPI nests each booking option one level down, under a key that says how
# the ticket is sold: "together" for a single booking, or "departing"/"returning"
# when the two directions are sold as separate tickets.  Reading book_with/price
# off the outer dict yields an entry with a blank seller and a null price.
_BOOKING_SLOTS = ("together", "departing", "returning")


def _parse_booking_options(
    raw_options: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Parse SerpAPI ``booking_options`` array into normalized seller entries.

    The engine returns ``booking_options`` when a ``booking_token`` is sent.
    Each option carries a seller, fare name, price, baggage prices, fare
    conditions, and a booking URL — all nested under a slot key naming how the
    ticket is sold (see ``_BOOKING_SLOTS``).
    """
    result: list[dict[str, Any]] = []
    for opt in raw_options:
        for slot in _BOOKING_SLOTS:
            node = opt.get(slot)
            if not isinstance(node, dict):
                continue
            entry: dict[str, Any] = {
                "seller": node.get("book_with", ""),
                "marketed_as": node.get("marketed_as", ""),
                "price": node.get("price"),
                # How this ticket is sold: one booking, or one per direction.
                "sold_as": slot,
            }
            # Fare tier ("Basic Economy", "Main Cabin", ...) — the field that
            # explains why two rows for the same flight differ in price.
            title = node.get("option_title")
            if title:
                entry["fare"] = title
            # Fare conditions: seat selection, change rules, mileage.
            ext = node.get("extensions")
            if ext:
                entry["fare_conditions"] = ext
            booking_request = node.get("booking_request", {})
            if isinstance(booking_request, dict) and booking_request.get("url"):
                entry["url"] = booking_request["url"]
            # baggage_prices is optional — omit the key entirely when absent
            bp = node.get("baggage_prices")
            if bp is not None:
                entry["baggage_prices"] = bp
            # local_prices (price breakdown) — optional
            lp = node.get("local_prices")
            if lp is not None:
                entry["local_prices"] = lp
            result.append(entry)
    return result


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
    booking_token: str = "",
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
    booking_token : str, optional
        Booking-phase token from a phase-2 result option. Pass the same
        origin/destination/dates as the search that produced the token
        (the engine requires the original context). Costs 1 search — this is
        where "which seller is cheapest" comes from. Mutually exclusive with
        ``departure_token``.
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
        Number of **carry-on** bags the fare must include (≥ 0).
        This does not filter or price checked baggage - for hold
        luggage, run the booking phase (``booking_token``), whose
        response carries ``baggage_prices``.
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

    # booking_token and departure_token are mutually exclusive — the engine
    # serves different response shapes for each and cannot combine them.
    if booking_token and departure_token:
        raise ValueError(
            "booking_token and departure_token are mutually exclusive — "
            "pass one token at a time."
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

    if booking_token:
        # Booking phase — which sellers have this exact ticket and at what price.
        # The engine requires the original search context (origin, destination,
        # dates) plus the booking_token from a phase-2 option.
        if return_date:
            params["type"] = 1
            params["return_date"] = return_date
        else:
            params["type"] = 2
        params["booking_token"] = booking_token
    elif departure_token and return_date:
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
    # Booking phase re-queries the same ticket; filters are irrelevant.
    if not booking_token:
        if include_airlines:
            params["include_airlines"] = include_airlines
        if exclude_airlines:
            params["exclude_airlines"] = exclude_airlines
        if bags is not None:
            params["bags"] = bags
        if max_duration is not None:
            params["max_duration"] = max_duration
        # Send the parsed form, not the caller's string: "18, 23" would
        # otherwise reach the engine as " 23" for the second hour.
        if outbound_times:
            params["outbound_times"] = _format_times_arg(outbound_times, label="outbound_times")
        if return_times:
            params["return_times"] = _format_times_arg(return_times, label="return_times")
        if deep_search:
            params["deep_search"] = "true"

    data, from_cache = await _call_serpapi(params)

    if booking_token:
        # Booking-phase response: selected_flights + booking_options.
        selected_raw = data.get("selected_flights", [])
        selected = [
            _parse_flight_item(f, "selected_flights", currency)
            for f in selected_raw
        ]
        booking_raw = data.get("booking_options", [])
        booking_options = _parse_booking_options(booking_raw)
        booking_result: dict[str, Any] = {
            "selected_flights": selected,
            "booking_options": booking_options,
            "adults": adults,
            "phase": (
                "booking options — each entry is a seller/fare that has this "
                "exact ticket, with its price, baggage prices, fare conditions "
                "and a booking link. Prices cover all "
                f"{adults} passenger(s); baggage prices are per passenger."
            ),
        }
        # Checked-bag prices for the itinerary as a whole. This is the only
        # place the engine reports hold luggage — the `bags` search parameter
        # filters on carry-on only.
        baggage = data.get("baggage_prices")
        if baggage:
            booking_result["baggage_prices"] = baggage
        if from_cache:
            booking_result["cached"] = True
        _inject_quota_warning(booking_result)
        return booking_result

    result = _parse_flights_response(data, requested_currency=currency)
    result["adults"] = adults
    if from_cache:
        result["cached"] = True

    # Annotate phase for round-trip
    if departure_token:
        result["phase"] = "return options — these are the return-flight options for the selected outbound."
    elif return_date:
        result["phase"] = (
            "outbound options — prices are round-trip totals; "
            "pass departure_token to fetch return flights for one of them."
        )

    _inject_quota_warning(result)
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
        Number of **carry-on** bags the fare must include (≥ 0).
        This does not filter or price checked baggage - for hold
        luggage, run the booking phase (``booking_token``), whose
        response carries ``baggage_prices``.
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
    for i, leg in enumerate(legs, start=1):
        missing = [k for k in ("origin", "destination", "date") if not leg.get(k)]
        if missing:
            raise ValueError(
                f"Leg {i} is missing required key(s): {', '.join(missing)} — "
                "each leg needs origin, destination, and date."
            )
        entry: dict[str, Any] = {
            "departure_id": leg["origin"],
            "arrival_id": leg["destination"],
            "date": leg["date"],
        }
        if "times" in leg:
            # Same rule as search_flights' outbound_times: validate and send
            # the parsed form, so "18, 23" never reaches the engine raw.
            entry["times"] = _format_times_arg(str(leg["times"]), label=f"legs[{i}].times")
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

    data, from_cache = await _call_serpapi(params)
    result = _parse_flights_response(data, requested_currency=currency)
    result["adults"] = adults
    if from_cache:
        result["cached"] = True
    _inject_quota_warning(result)
    return result


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(mcp: Any) -> None:
    """Register flight tools on a FastMCP instance."""
    mcp.tool()(search_flights)
    mcp.tool()(search_multi_city)
