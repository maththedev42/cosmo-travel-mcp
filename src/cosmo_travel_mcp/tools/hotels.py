"""Accommodation search tool backed by SerpAPI's google_hotels engine.

Uses SerpAPI's ``google_hotels`` engine with a ``vacation_rentals`` flag that
surfaces whole-property/vacation-rental listings sourced from Airbnb, Vrbo,
Booking.com, etc. — NOT Airbnb-exclusive. This is a deliberate tradeoff: broader,
more reliable coverage through a licensed provider, at the cost of not filtering by
a single source. Callers should not assume results are Airbnb-only.
"""

from __future__ import annotations

from typing import Any

from .flights import SERPAPI_BASE, _call_serpapi, _get_api_key

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

# SerpAPI google_hotels sort_by codes (verified 2026-07-31):
#   3  = lowest price
#   8  = highest rating
#   13 = most reviewed
_SORT_MAP: dict[str, int] = {
    "lowest_price": 3,
    "highest_rating": 8,
    "most_reviewed": 13,
}

# SerpAPI google_hotels rating codes:
#   7 = 3.5+
#   8 = 4.0+
#   9 = 4.5+
_RATING_MAP: dict[float, int] = {3.5: 7, 4.0: 8, 4.5: 9}

_VALID_RATINGS_S = ", ".join(str(r) for r in sorted(_RATING_MAP))


def _validate_sort_by(value: str) -> int:
    """Map a human-friendly sort name to the engine code, or raise ValueError."""
    code = _SORT_MAP.get(value)
    if code is None:
        raise ValueError(
            f"sort_by must be one of {sorted(_SORT_MAP)!r}, got {value!r}"
        )
    return code


def _validate_min_rating(value: float) -> int:
    """Map a rating threshold (3.5/4.0/4.5) to the engine code, or raise ValueError."""
    code = _RATING_MAP.get(value)
    if code is None:
        raise ValueError(
            f"min_rating must be one of {_VALID_RATINGS_S}, got {value}"
        )
    return code


def _validate_hotel_class(value: str, *, vacation_rentals: bool) -> list[int]:
    """Parse hotel_class string and raise if used with vacation_rentals=True."""
    if vacation_rentals:
        raise ValueError(
            "hotel_class cannot be used with vacation_rentals=True — "
            "the engine does not apply class filters to vacation rentals."
        )
    classes: list[int] = []
    for part in value.split(","):
        part = part.strip()
        try:
            cls_ = int(part)
        except ValueError:
            raise ValueError(
                f"hotel_class values must be integers 2–5, got {part!r}"
            ) from None
        if cls_ < 2 or cls_ > 5:
            raise ValueError(
                f"hotel_class values must be 2–5, got {cls_}"
            )
        classes.append(cls_)
    return classes


def _parse_property(item: dict[str, Any]) -> dict[str, Any]:
    """Parse a single property from a SerpAPI google_hotels response."""
    parsed: dict[str, Any] = {
        "name": item.get("name", ""),
        "type": item.get("type", ""),
    }

    rate = item.get("rate_per_night")
    if isinstance(rate, dict):
        parsed["rate_per_night"] = {
            "lowest": rate.get("lowest", ""),
        }
        if "extracted_lowest" in rate:
            parsed["rate_per_night"]["extracted_lowest"] = rate["extracted_lowest"]
        if "before_taxes_fees" in rate:
            parsed["rate_per_night"]["before_taxes_fees"] = rate["before_taxes_fees"]
        if "extracted_before_taxes_fees" in rate:
            parsed["rate_per_night"]["extracted_before_taxes_fees"] = rate[
                "extracted_before_taxes_fees"
            ]

    total = item.get("total_rate")
    if isinstance(total, dict):
        parsed["total_rate"] = {
            "lowest": total.get("lowest", ""),
        }
        if "extracted_lowest" in total:
            parsed["total_rate"]["extracted_lowest"] = total["extracted_lowest"]

    if "rating" in item:
        parsed["rating"] = item["rating"]
    if "reviews" in item:
        parsed["reviews"] = item["reviews"]

    if "link" in item:
        parsed["link"] = item["link"]
    if "property_token" in item:
        parsed["property_token"] = item["property_token"]

    return parsed


async def search_accommodations(
    location: str,
    check_in_date: str,
    check_out_date: str,
    adults: int = 2,
    children: int = 0,
    children_ages: list[int] | None = None,
    vacation_rentals: bool = True,
    currency: str = "BRL",
    country: str | None = None,
    language: str | None = None,
    min_price: int | None = None,
    max_price: int | None = None,
    sort_by: str | None = None,
    min_rating: float | None = None,
    hotel_class: str | None = None,
    free_cancellation: bool = False,
) -> dict[str, Any]:
    """Search for accommodations via SerpAPI's Google Hotels engine.

    With ``vacation_rentals=True`` (the default), results include whole-property
    listings from Airbnb, Vrbo, Booking.com, etc. — **not Airbnb-exclusive**.
    Set ``vacation_rentals=False`` to search standard hotels instead.

    Args:
        location: Free-text search (e.g. "Miami, FL" or "Orlando near Universal").
        check_in_date: YYYY-MM-DD.
        check_out_date: YYYY-MM-DD.
        adults: Number of adults (default 2).
        children: Number of children (default 0).
        children_ages: Ages of children.
        vacation_rentals: Search vacation rentals (True) or standard hotels (False).
        currency: Currency code (default BRL).
        country: Country code (gl parameter).
        language: Language code (hl parameter).
        min_price: Minimum nightly rate.
        max_price: Maximum nightly rate.
        sort_by: One of "lowest_price", "highest_rating", "most_reviewed".
        min_rating: Minimum guest rating — one of 3.5, 4.0, 4.5.
        hotel_class: Comma-separated hotel star ratings 2–5 (e.g. "3,4,5").
            Cannot be used with ``vacation_rentals=True``.
        free_cancellation: Filter to properties with free cancellation.
    """
    # Validate hotel_class early (raises on conflict with vacation_rentals).
    _hotel_classes: list[int] | None = None
    if hotel_class is not None:
        _hotel_classes = _validate_hotel_class(hotel_class, vacation_rentals=vacation_rentals)

    params: dict[str, Any] = {
        "q": location,
        "check_in_date": check_in_date,
        "check_out_date": check_out_date,
        "adults": adults,
        "children": children,
        "currency": currency,
        "vacation_rentals": str(vacation_rentals).lower(),
    }

    if children_ages:
        params["children_ages"] = ",".join(str(a) for a in children_ages)
    if country:
        params["gl"] = country
    if language:
        params["hl"] = language
    if min_price is not None:
        params["min_price"] = min_price
    if max_price is not None:
        params["max_price"] = max_price
    if sort_by is not None:
        params["sort_by"] = _validate_sort_by(sort_by)
    if min_rating is not None:
        params["rating"] = _validate_min_rating(min_rating)
    if _hotel_classes is not None:
        params["hotel_class"] = ",".join(str(c) for c in _hotel_classes)
    if free_cancellation:
        params["free_cancellation"] = "true"

    data = await _call_serpapi(params, engine="google_hotels")

    properties = data.get("properties", [])
    parsed = [_parse_property(p) for p in properties]

    return {
        "results": parsed,
        "vacation_rentals": vacation_rentals,
        "total_results": len(parsed),
    }


async def get_accommodation_details(
    property_token: str,
    check_in_date: str,
    check_out_date: str,
    adults: int = 2,
    children: int = 0,
    children_ages: list[int] | None = None,
    currency: str = "BRL",
    country: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Get full details for a single property via SerpAPI's Google Hotels engine.

    Costs **1 SerpAPI search**. Use ``property_token`` from a
    ``search_accommodations`` result to drill into a property.

    Args:
        property_token: Token from a ``search_accommodations`` result item.
        check_in_date: YYYY-MM-DD (required by the engine).
        check_out_date: YYYY-MM-DD (required by the engine).
        adults: Number of adults (default 2).
        children: Number of children (default 0).
        children_ages: Ages of children.
        currency: Currency code (default BRL).
        country: Country code (gl parameter).
        language: Language code (hl parameter).
    """
    params: dict[str, Any] = {
        "property_token": property_token,
        "check_in_date": check_in_date,
        "check_out_date": check_out_date,
        "adults": adults,
        "children": children,
        "currency": currency,
    }

    if children_ages:
        params["children_ages"] = ",".join(str(a) for a in children_ages)
    if country:
        params["gl"] = country
    if language:
        params["hl"] = language

    data = await _call_serpapi(params, engine="google_hotels")

    prop = data.get("property", {})
    if not isinstance(prop, dict):
        prop = {}

    result: dict[str, Any] = {}

    if "name" in prop:
        result["name"] = prop["name"]
    if "description" in prop:
        result["description"] = prop["description"]
    if "overall_rating" in prop:
        result["overall_rating"] = prop["overall_rating"]
    if "reviews" in prop:
        result["reviews"] = prop["reviews"]

    # Rating breakdown: per-category scores.
    breakdown: list[dict[str, Any]] = prop.get("rating_breakdown", [])
    if breakdown:
        result["rating_breakdown"] = [
            {"category": b.get("name", ""), "score": b.get("score")}
            for b in breakdown
        ]

    # Amenities: list of strings.
    amenities: list[str] = prop.get("amenities", [])
    if amenities:
        result["amenities"] = amenities

    if "check_in_time" in prop:
        result["check_in_time"] = prop["check_in_time"]
    if "check_out_time" in prop:
        result["check_out_time"] = prop["check_out_time"]

    # Images: up to ~10 thumbnail links.
    images: list[dict[str, Any]] = prop.get("images", [])
    if images:
        result["images"] = [img.get("thumbnail", "") for img in images[:10]]

    # Per-source prices.
    raw_prices: list[dict[str, Any]] = prop.get("prices", [])
    if raw_prices:
        result["prices"] = [
            {"source": p.get("source", ""), "rate_per_night": p.get("rate_per_night")}
            for p in raw_prices
        ]

    if "link" in prop:
        result["link"] = prop["link"]

    return result


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(mcp: Any) -> None:
    """Register accommodations tools on a FastMCP instance."""
    mcp.tool()(search_accommodations)
    mcp.tool()(get_accommodation_details)
