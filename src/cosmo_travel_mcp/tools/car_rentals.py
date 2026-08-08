"""Car rental office lookup backed by SerpAPI's google_maps engine.

This tool answers "where do I collect the car, and will the counter be open"
— deliberately not "what does it cost".

No free source carries rental *rates*. SerpAPI has no car rental engine at
all; Amadeus' Self-Service "Cars and Transfers" category is chauffeured
transfers (``/shopping/transfer-offers``), not self-drive, and its self-drive
Cars API sits in the Enterprise catalogue; the Booking.com Demand and Expedia
Rapid car APIs are gated behind vetted partner programmes. Rental rates are
contracted per partner, so the gap is structural rather than an oversight.

Returning an invented daily rate would be worse than returning none, so this
tool hands the traveller what they need to fetch the quote themselves — the
office's ``website``, ``phone`` and per-weekday ``operating_hours`` — and
leaves the price to come back from them.

``operating_hours`` is what earns this tool its search. An airport counter
commonly runs 24 hours while a neighbourhood branch closes on Sundays, and a
pickup booked at a branch that is shut on the day fails exactly when nothing
can be done about it. The two captured fixtures pin both shapes.

One limit worth stating plainly: these are the *regular weekly* hours. Google
does not report holiday exceptions here, so a 25 December pickup has to be
confirmed by phone — which is why ``phone`` is surfaced next to the hours.
"""

from __future__ import annotations

from typing import Any

from .flights import _call_serpapi, _inject_quota_warning
from .places import _parse_place

# The engine returns ~20 per page; beyond that is noise for a pickup decision
# and burns client context.
_MAX_LIMIT = 20

# Google Maps reports a *price level* ("$$") for some businesses. On a rental
# office that is a vague expensiveness hint, never a daily rate — and the whole
# reason this tool exists is that the rate is not available for free. Passing
# it through under a name like `price_from` would invite precisely the invented
# number the tool is built to prevent, so both keys are dropped.
_PRICE_KEYS = ("price_range", "price_from")

_NOTES = {
    "pricing": (
        "No rates. No licensed provider exposes car rental pricing without a "
        "commercial agreement, so this tool returns offices, hours and contact "
        "details only. Quote on the office's own `website`. When comparing "
        "pickup points the one-way drop fee is usually the number that decides, "
        "but do not assume it is charged: on a fleet-rebalancing direction the "
        "carrier wants the car moved and there is no fee at all. Read the "
        "quote's itemisation rather than inferring one, and quote the reverse "
        "direction separately — it does not inherit the answer."
    ),
    "holiday_hours": (
        "`operating_hours` are the regular weekly hours. Google does not report "
        "holiday exceptions here, so a pickup on Christmas, New Year's Day or a "
        "local holiday must be confirmed on `phone`. Airport counters usually "
        "stay open on holidays; neighbourhood branches usually do not."
    ),
}


async def search_car_rentals(
    location: str,
    min_rating: float | None = None,
    limit: int = 10,
    country: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Find car rental offices near a place — locations, hours and contacts.

    Costs 1 SerpAPI search per call.

    **This tool returns no prices.** Rental rates are not available from any
    free provider, so do not infer, estimate or present a daily rate from its
    output. Give the traveller the ``website`` and let them quote it; treat the
    rate as unmeasured until they report one back.

    What it is good for: choosing *where* to collect the car. Airport counters
    and neighbourhood branches differ sharply in opening hours, and picking the
    wrong one strands a traveller on the day of travel.

    Args:
        location: Free text — a city, an area, or an airport
            (e.g. ``"Miami International Airport"``, ``"Orlando, FL"``).
            The engine geocodes it.
        min_rating: Drop offices rated below this (e.g. ``4.0``). Applied to
            the returned page, not engine-side, so a high threshold can leave
            few results. An unrated office is dropped too — unknown is not the
            same as good, but it cannot be shown to clear the bar either.
        limit: Maximum results to return (1–20, default 10).
        country: Country code (``gl`` parameter).
        language: Language code (``hl`` parameter). **This localizes the keys
            of ``operating_hours``, not just the values** — ``hl="pt-br"``
            returns ``{"segunda-feira": "08:00–18:00", "domingo": "Fechado"}``.
            Read whatever keys come back; do not assume English weekdays.

    Returns:
        ``{"location", "results", "total_results", "notes"}``. Each result
        carries name, address, rating, reviews, coordinates, ``website``,
        ``phone``, ``operating_hours`` and ``open_state``. ``notes`` states why
        there is no price and why the hours do not cover holidays — relay both
        rather than dropping them.
    """
    if limit < 1 or limit > _MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {_MAX_LIMIT}, got {limit}")
    if min_rating is not None and not (0 <= min_rating <= 5):
        raise ValueError(f"min_rating must be between 0 and 5, got {min_rating}")

    params: dict[str, Any] = {
        "q": f"car rental in {location}",
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

    offices: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        parsed = _parse_place(item, food=False)
        for key in _PRICE_KEYS:
            parsed.pop(key, None)
        offices.append(parsed)

    if min_rating is not None:
        offices = [
            o for o in offices
            if isinstance(o.get("rating"), (int, float)) and o["rating"] >= min_rating
        ]

    offices = offices[:limit]

    result: dict[str, Any] = {
        "location": location,
        "results": offices,
        "total_results": len(offices),
        "notes": dict(_NOTES),
    }
    if from_cache:
        result["cached"] = True
    _inject_quota_warning(result)
    return result


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(mcp: Any) -> None:
    """Register the car-rental lookup tool on a FastMCP instance."""
    mcp.tool()(search_car_rentals)
