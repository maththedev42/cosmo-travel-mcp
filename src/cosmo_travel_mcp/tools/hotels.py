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
    """
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

    data = await _call_serpapi(params, engine="google_hotels")

    properties = data.get("properties", [])
    parsed = [_parse_property(p) for p in properties]

    return {
        "results": parsed,
        "vacation_rentals": vacation_rentals,
        "total_results": len(parsed),
    }


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(mcp: Any) -> None:
    """Register accommodations tool on a FastMCP instance."""
    mcp.tool()(search_accommodations)
