"""Things-to-do search backed by SerpAPI's google_maps engine.

Answers "what should I do in this city" — attractions, museums, parks,
nightlife and food — so an AI client can assemble a day-by-day itinerary
alongside flights (``search_flights``), lodging (``search_accommodations``)
and time-bound happenings (``search_events``).

Two fields here are what make real itinerary planning possible, and both are
returned verbatim rather than summarised away:

* ``operating_hours`` — per-weekday opening times, so a stop is not scheduled
  on a day it is closed.
* ``coordinates`` — so nearby stops can be clustered into the same day
  instead of criss-crossing the city.

This is deliberately narrower than a general-purpose places API: it returns
what a traveller needs to choose and schedule a stop, not the full Google
Maps record.
"""

from __future__ import annotations

from typing import Any

from .flights import _call_serpapi, _inject_quota_warning

# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

# Each category maps to the query phrasing that returns the right result set.
# `{location}` is substituted; the engine geocodes the free text itself.
CATEGORY_QUERIES: dict[str, str] = {
    "attractions": "things to do in {location}",
    "museums": "museums in {location}",
    "parks": "parks and nature in {location}",
    "landmarks": "landmarks in {location}",
    "shopping": "shopping in {location}",
    "nightlife": "nightlife in {location}",
    "restaurants": "restaurants in {location}",
    "cafes": "cafes in {location}",
    "bars": "bars in {location}",
}

DEFAULT_CATEGORY = "attractions"

# Categories whose results carry price/menu-shaped fields worth surfacing.
_FOOD_CATEGORIES = frozenset({"restaurants", "cafes", "bars", "nightlife"})

# Cap on returned results. The engine returns ~20 per page; more than this in
# one response is noise for an itinerary and burns client context.
_MAX_LIMIT = 20


def _validate_category(value: str) -> str:
    """Return the query template for *value*, or raise ValueError."""
    template = CATEGORY_QUERIES.get(value)
    if template is None:
        raise ValueError(
            f"category must be one of {sorted(CATEGORY_QUERIES)!r}, got {value!r}"
        )
    return template


def _extension_values(extensions: Any, key: str) -> list[str]:
    """Pull one named list out of the ``extensions`` array.

    ``extensions`` is a list of single-key dicts —
    ``[{"service_options": [...]}, {"highlights": [...]}]`` — so a named
    group has to be searched for rather than indexed.
    """
    if not isinstance(extensions, list):
        return []
    for group in extensions:
        if isinstance(group, dict) and key in group:
            values = group[key]
            if isinstance(values, list):
                return [v for v in values if isinstance(v, str) and v]
    return []


def _parse_place(item: dict[str, Any], *, food: bool) -> dict[str, Any]:
    """Normalize one ``local_results`` entry from the google_maps engine."""
    parsed: dict[str, Any] = {"name": item.get("title", "")}

    for src, dst in (
        ("type", "category"),
        ("address", "address"),
        ("rating", "rating"),
        ("reviews", "reviews"),
        ("website", "website"),
        ("phone", "phone"),
    ):
        value = item.get(src)
        if value is not None:
            parsed[dst] = value

    types = item.get("types")
    if isinstance(types, list):
        named = [t for t in types if isinstance(t, str) and t]
        if named:
            parsed["types"] = named

    # Coordinates: flattened, because a caller clustering stops by proximity
    # should not have to reach through a nested object to get them.
    gps = item.get("gps_coordinates")
    if isinstance(gps, dict):
        lat, lng = gps.get("latitude"), gps.get("longitude")
        if lat is not None and lng is not None:
            parsed["coordinates"] = {"lat": lat, "lng": lng}

    # Hours are the scheduling constraint — pass the per-weekday map through
    # untouched, plus the human-readable "Open · Closes 5 PM" line.
    #
    # Both the keys AND the values are localized by `hl`: with hl=pt-br the
    # engine returns {"segunda-feira": "10:00–19:00", "sábado": "Fechado"}.
    # They are NOT normalized to English here — inventing a day-name mapping
    # for every possible `hl` would guess, and a wrong guess would silently
    # drop the one field the itinerary depends on. Callers must read the keys
    # they were given rather than assuming "monday".
    hours = item.get("operating_hours")
    if isinstance(hours, dict) and hours:
        parsed["operating_hours"] = hours
    open_state = item.get("open_state")
    if open_state:
        parsed["open_state"] = open_state

    highlights = _extension_values(item.get("extensions"), "highlights")
    if highlights:
        parsed["highlights"] = highlights

    accessibility = _extension_values(item.get("extensions"), "accessibility")
    if accessibility:
        parsed["accessibility"] = accessibility

    # Food-shaped fields. Present on restaurant/cafe/bar results and absent
    # from attractions, so they are attached only when the engine sent them.
    description = item.get("description")
    if description:
        parsed["description"] = description

    price = item.get("price")
    if price:
        parsed["price_range"] = price
    extracted = item.get("extracted_price")
    if extracted is not None:
        parsed["price_from"] = extracted

    if food:
        service = item.get("service_options")
        if isinstance(service, dict) and service:
            parsed["service_options"] = {
                k: v for k, v in service.items() if isinstance(v, bool)
            }
        if item.get("reserve_a_table"):
            parsed["reservation_link"] = item["reserve_a_table"]

    review_quote = item.get("user_review")
    if review_quote:
        parsed["review_quote"] = review_quote

    return parsed


async def search_things_to_do(
    location: str,
    category: str = DEFAULT_CATEGORY,
    min_rating: float | None = None,
    limit: int = 10,
    country: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Find things to do in a city — attractions, museums, parks, food, nightlife.

    Costs 1 SerpAPI search per call. Planning a three-city trip means three
    calls, one per city — do not call this once per category per city unless
    the traveller actually asked for that breakdown.

    Results carry ``operating_hours`` (per weekday) and ``coordinates``, which
    are what a day-by-day itinerary needs: schedule around closing days, and
    group stops that sit near each other into the same day.

    Args:
        location: City or area, free text (e.g. ``"Miami"``, ``"Miami Beach, FL"``).
        category: One of attractions, museums, parks, landmarks, shopping,
            nightlife, restaurants, cafes, bars. Defaults to ``attractions``.
        min_rating: Drop results rated below this (e.g. ``4.0``). Applied
            locally to the returned page — it is not an engine-side filter, so
            a high threshold can leave few results.
        limit: Maximum results to return (1–20, default 10).
        country: Country code (``gl`` parameter).
        language: Language code (``hl`` parameter). **This localizes the keys
            of ``operating_hours``, not just the values** — ``hl="pt-br"``
            returns ``{"segunda-feira": "10:00–19:00", "sábado": "Fechado"}``.
            Read whatever keys come back; do not assume English weekdays.

    Returns:
        ``{"location", "category", "results", "total_results"}``; ``results``
        entries carry name, category, rating, reviews, address, coordinates,
        operating_hours and — for food categories — price range, description
        and a reservation link when the engine has one.
    """
    template = _validate_category(category)

    if limit < 1 or limit > _MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {_MAX_LIMIT}, got {limit}")
    if min_rating is not None and not (0 <= min_rating <= 5):
        raise ValueError(f"min_rating must be between 0 and 5, got {min_rating}")

    params: dict[str, Any] = {
        "q": template.format(location=location),
        "type": "search",
    }
    if country:
        params["gl"] = country
    if language:
        params["hl"] = language

    data, from_cache = await _call_serpapi(params, engine="google_maps")

    raw = data.get("local_results", [])
    if not isinstance(raw, list):
        raw = []

    food = category in _FOOD_CATEGORIES
    places = [_parse_place(item, food=food) for item in raw if isinstance(item, dict)]

    if min_rating is not None:
        # An unrated place is unknown, not bad — but it cannot be shown to
        # clear the bar either, so a rating filter excludes it.
        places = [p for p in places if isinstance(p.get("rating"), (int, float))
                  and p["rating"] >= min_rating]

    places = places[:limit]

    result: dict[str, Any] = {
        "location": location,
        "category": category,
        "results": places,
        "total_results": len(places),
    }
    if from_cache:
        result["cached"] = True
    _inject_quota_warning(result)
    return result


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(mcp: Any) -> None:
    """Register the things-to-do tool on a FastMCP instance."""
    mcp.tool()(search_things_to_do)
