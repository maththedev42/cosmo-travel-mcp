"""Compare trip windows around a fixed date — flights AND lodging priced together.

Every call to this tool costs exactly **2 SerpAPI searches per window priced**
— one ``google_flights``, one ``google_hotels``. With ``max_windows``
defaulting to 3 and hard-capped at 5 that is up to 10 searches against a
100/month free tier: a tenth of the month in one call. Callers (human or LLM)
should understand this before invoking it lightly.

The first half of this module (``_generate_windows`` / ``_combine`` /
``_rank``) is pure and network-free; the ``compare_trip_windows`` tool below
is the SerpAPI half that prices the windows it produces.

The anchor rule is the point. The traveller has to be in the destination on
the night of ``anchor_date`` (e.g. the night of a concert). A window qualifies
only when ``depart <= anchor_date < return``. A window that returns *on* the
anchor date means flying home the day of the event — it is not a cheaper
option, it is a different trip. (Measured 2026-08-20: a fixed-date concert,
where a window with an extra night was R$ 320 cheaper on airfare and whether
the trip was cheaper overall depended on the hotel, not the date.)
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

from .flights import (
    _build_base_params,
    _call_serpapi,
    _inject_quota_warning,
    _parse_flights_response,
)
from .hotels import _parse_property, _validate_hotel_class, _validate_min_rating

# ---------------------------------------------------------------------------
# Window generation
# ---------------------------------------------------------------------------


def _closeness_to_anchor(
    window: dict[str, Any],
    anchor_date: date,
) -> tuple[int, int, int]:
    """Ordering key for "nearest window to the anchor" first.

    Fewest nights, then fewest nights spent before the anchor date (the
    traveller arrives as close to the event as possible), then fewest nights
    after it.
    """
    nights_before = (anchor_date - window["depart"]).days
    nights_after = (window["return"] - anchor_date - timedelta(days=1)).days
    return (window["nights"], nights_before, nights_after)


def _generate_windows(
    anchor_date: date,
    earliest_depart: date,
    latest_return: date,
    min_nights: int,
    max_nights: int,
    max_windows: int,
) -> list[dict[str, Any]]:
    """Generate candidate ``{"depart", "return", "nights"}`` windows.

    A window qualifies only when ``depart <= anchor_date < return`` — the
    traveller must be in the destination on the night of ``anchor_date``, and
    a window returning *on* it means flying home the day of the event, which
    is a different trip, not a cheaper option. ``nights`` is always derived
    from ``return - depart``; there is no parameter for it. The result is
    capped at ``max_windows``, keeping the windows closest to the anchor.
    """
    candidates: list[dict[str, Any]] = []

    depart = earliest_depart
    while depart <= anchor_date:
        # Earliest return that both satisfies min_nights and covers the anchor
        # night. The anchor guard lives here: ``anchor_date + 1`` is the first
        # date on which returning still means the anchor night was spent in the
        # destination.
        return_date = max(
            depart + timedelta(days=min_nights),
            anchor_date + timedelta(days=1),
        )
        while return_date <= latest_return:
            nights = (return_date - depart).days
            if nights > max_nights:
                break
            candidates.append({
                "depart": depart,
                "return": return_date,
                "nights": nights,
            })
            return_date += timedelta(days=1)
        depart += timedelta(days=1)

    candidates.sort(key=lambda w: _closeness_to_anchor(w, anchor_date))
    if max_windows > 0:
        return candidates[:max_windows]
    return []


# ---------------------------------------------------------------------------
# Combining flights + lodging
# ---------------------------------------------------------------------------


def _combine(
    window: dict[str, Any],
    flights_total: float,
    lodging_total: float,
) -> dict[str, Any]:
    """Add ``flights_total``, ``lodging_total`` and their sum to *window*.

    A total that disagrees with its parts fails loudly rather than renders:
    combining a window that already carries a ``combined_total`` (a stale
    annotation, or a double-combine with different parts) raises AssertionError
    instead of silently overwriting a number that does not add up.
    """
    combined_total = flights_total + lodging_total
    existing = window.get("combined_total")
    assert existing is None or existing == combined_total, (
        f"combined_total would be {combined_total} but the window already "
        f"carries {existing!r} — the parts do not sum to the total."
    )
    out = dict(window)
    out["flights_total"] = flights_total
    out["lodging_total"] = lodging_total
    out["combined_total"] = combined_total
    return out


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def _rank(combined: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort ascending by ``combined_total`` and annotate every entry.

    Each entry gains ``delta_vs_best`` (0 for the winner). Entries with more
    nights than the winner also gain ``break_even_nightly`` — the per-night
    price at which that window would tie the winner:

    ``break_even_nightly = flight_saving / extra_nights``

    where ``flight_saving`` is what the longer window saves on airfare against
    the winner. It is ``None`` when ``extra_nights == 0``; the division never
    happens with a zero denominator.
    """
    ranked = sorted(combined, key=lambda w: w["combined_total"])
    if not ranked:
        return ranked

    best_total = ranked[0]["combined_total"]
    best_nights = ranked[0]["nights"]
    best_flights = ranked[0]["flights_total"]

    for w in ranked:
        w["delta_vs_best"] = w["combined_total"] - best_total
        extra_nights = w["nights"] - best_nights
        if extra_nights == 0:
            w["break_even_nightly"] = None
        else:
            w["break_even_nightly"] = (best_flights - w["flights_total"]) / extra_nights
    return ranked


# ---------------------------------------------------------------------------
# Pricing a window (the SerpAPI half)
# ---------------------------------------------------------------------------

# Hard cap on windows priced per call — 2 searches each, so 5 windows means 10
# SerpAPI searches against a 100/month free tier.
_MAX_WINDOWS_CAP: int = 5

# How many cheapest lodging options to surface per window.
_MAX_LODGING_OPTIONS: int = 10


def _stay_total(prop: dict[str, Any]) -> float | None:
    """Numeric stay total for a parsed property, or ``None`` when unknown.

    ``_parse_property`` keeps ``total_rate`` as ``{lowest, extracted_lowest}``
    where ``lowest`` is the rendered string (``"R$ 1.234"``) and
    ``extracted_lowest`` is the number. Only a real number can be summed into
    ``combined_total``; a string would silently corrupt the arithmetic.
    """
    tr = prop.get("total_rate") or {}
    raw = tr.get("extracted_lowest")
    if raw is None:
        raw = tr.get("lowest")
    return raw if isinstance(raw, (int, float)) else None


async def _price_window(
    window: dict[str, Any],
    *,
    base_flight_params: dict[str, Any],
    hotel_params: dict[str, Any],
    origin: str,
    destination: str,
    currency: str,
) -> tuple[dict[str, Any], int]:
    """Price one window: one flight search + one hotel search.

    Returns ``(entry, searches_spent)``. When the window cannot be priced the
    entry carries ``error`` and the caller drops it with a note — one bad
    window must not sink the comparison. Configuration errors (missing API
    key) and SerpAPI-level errors still propagate: the first reads as a setup
    problem, the second as an engine problem, and neither should be papered
    over by a per-window note.
    """
    searches_spent = 0
    try:
        flight_params: dict[str, Any] = {
            **base_flight_params,
            "departure_id": origin,
            "arrival_id": destination,
            "outbound_date": window["depart"].isoformat(),
            "return_date": window["return"].isoformat(),
            "type": 1,
        }
        flights_data, flights_cached = await _call_serpapi(flight_params)
        if not flights_cached:
            searches_spent += 1

        window_hotel_params: dict[str, Any] = {
            **hotel_params,
            "check_in_date": window["depart"].isoformat(),
            "check_out_date": window["return"].isoformat(),
        }
        hotels_data, hotels_cached = await _call_serpapi(
            window_hotel_params, engine="google_hotels"
        )
        if not hotels_cached:
            searches_spent += 1
    except ValueError:
        raise  # Configuration errors (missing API key) must propagate.
    except Exception as exc:
        return {"window": window, "error": f"{type(exc).__name__}: {exc}"}, searches_spent

    parsed_flights = _parse_flights_response(flights_data, requested_currency=currency)
    priced = [f for f in parsed_flights.get("flights", []) if f.get("price") is not None]
    if not priced:
        return (
            {"window": window, "error": "no priced flight returned for this window"},
            searches_spent,
        )
    flights_best = min(priced, key=lambda f: f["price"])

    properties = [_parse_property(p) for p in hotels_data.get("properties", [])]
    priced_properties = [p for p in properties if _stay_total(p) is not None]
    if not priced_properties:
        return (
            {"window": window, "error": "no priced lodging returned for this window"},
            searches_spent,
        )
    lodging_total = min(_stay_total(p) for p in priced_properties)
    options = sorted(priced_properties, key=_stay_total)[:_MAX_LODGING_OPTIONS]

    entry = _combine(
        window,
        flights_total=flights_best["price"],
        lodging_total=lodging_total,
    )
    entry["flights_best"] = flights_best
    entry["lodging_options"] = options
    return entry, searches_spent


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


async def compare_trip_windows(
    origin: str,
    destination: str,
    anchor_date: str,
    lodging_location: str,
    adults: int,
    min_nights: int = 2,
    max_nights: int = 4,
    max_windows: int = 3,
    currency: str = "BRL",
    country: str | None = None,
    language: str | None = None,
    max_stops: str | None = None,
    min_rating: float | None = None,
    hotel_class: str | None = None,
    vacation_rentals: bool = False,
) -> dict[str, Any]:
    """Compare trip windows around a fixed date — flights AND lodging priced.

    The traveller has to be in the destination on the night of ``anchor_date``
    (e.g. the night of a concert). Each candidate window around that night is
    priced with one flight search and one hotel search, and the windows are
    ranked by their **combined** total — a date window is only cheaper once
    the extra nights are paid for.

    Costs exactly 2 SerpAPI searches per window priced.

    Parameters
    ----------
    origin : str
        IATA airport/city code(s) for departure.
    destination : str
        IATA airport/city code(s) for arrival.
    anchor_date : str
        The night that must be covered, YYYY-MM-DD. A window qualifies only
        when ``depart <= anchor_date < return``.
    lodging_location : str
        Free-text search text for the hotel engine (e.g. "Rio de Janeiro
        near Maracanã").
    adults : int
        Passenger count for BOTH the flight and the hotel search. Required and
        deliberate: measured 2026-08-20, a per-person fare quoted for one
        traveller was not the per-person fare for two (R$ 798 vs R$ 957,50 each
        on the same date).
    min_nights : int, default 2
        Minimum trip length in nights.
    max_nights : int, default 4
        Maximum trip length in nights.
    max_windows : int, default 3, hard cap 5
        Maximum windows to price. Each costs 2 searches.
    currency : str, default "BRL"
    country : str, optional
        Two-letter country code for geo-localization (SerpAPI ``gl``).
    language : str, optional
        Two-letter language code for localization (SerpAPI ``hl``).
    max_stops : str, optional
        One of: any, nonstop, one_or_fewer, two_or_fewer.
    min_rating : float, optional
        Minimum guest rating — one of 3.5, 4.0, 4.5.
    hotel_class : str, optional
        Comma-separated hotel star ratings 2–5. Cannot be used with
        ``vacation_rentals=True``.
    vacation_rentals : bool, default False
        Search vacation rentals instead of standard hotels.
    """
    if max_windows > _MAX_WINDOWS_CAP:
        raise ValueError(
            f"max_windows must be {_MAX_WINDOWS_CAP} or fewer (2 SerpAPI "
            f"searches per window, so {_MAX_WINDOWS_CAP} costs "
            f"{2 * _MAX_WINDOWS_CAP}), got {max_windows}."
        )
    if min_nights < 1:
        raise ValueError(f"min_nights must be at least 1, got {min_nights}")
    if max_nights < min_nights:
        raise ValueError(
            f"max_nights ({max_nights}) cannot be less than min_nights ({min_nights})"
        )

    anchor = date.fromisoformat(anchor_date)

    # Validate the lodging filters once, before any search is spent.
    hotel_filters: dict[str, Any] = {}
    if min_rating is not None:
        hotel_filters["rating"] = _validate_min_rating(min_rating)
    if hotel_class is not None:
        classes = _validate_hotel_class(hotel_class, vacation_rentals=vacation_rentals)
        hotel_filters["hotel_class"] = ",".join(str(c) for c in classes)

    # Windows span up to max_nights in either direction around the anchor;
    # _generate_windows keeps the ones closest to the anchor.
    earliest_depart = anchor - timedelta(days=max_nights - 1)
    latest_return = anchor + timedelta(days=max_nights)
    windows = _generate_windows(
        anchor_date=anchor,
        earliest_depart=earliest_depart,
        latest_return=latest_return,
        min_nights=min_nights,
        max_nights=max_nights,
        max_windows=max_windows,
    )
    if not windows:
        raise ValueError(
            f"No valid trip window covers the anchor night {anchor_date} with "
            f"{min_nights}–{max_nights} nights — check the date and night ranges."
        )

    base_flight_params = _build_base_params(
        adults=adults,
        children=0,
        cabin_class="economy",
        currency=currency,
        country=country,
        language=language,
        max_stops=max_stops,
    )
    hotel_params: dict[str, Any] = {
        "q": lodging_location,
        "adults": adults,
        "children": 0,
        "currency": currency,
        "vacation_rentals": str(vacation_rentals).lower(),
    }
    if country:
        hotel_params["gl"] = country
    if language:
        hotel_params["hl"] = language
    hotel_params.update(hotel_filters)

    results = await asyncio.gather(*(
        _price_window(
            w,
            base_flight_params=base_flight_params,
            hotel_params=hotel_params,
            origin=origin,
            destination=destination,
            currency=currency,
        )
        for w in windows
    ))

    entries = [entry for entry, _ in results]
    searches_spent = sum(spent for _, spent in results)

    priced = [e for e in entries if "error" not in e]
    failed = [e for e in entries if "error" in e]
    if not priced:
        raise ValueError(
            "No window could be priced — every SerpAPI call for this comparison "
            "failed. See the individual errors before retrying."
        )

    ranked = _rank(priced)

    notes: list[str] = [
        "lodging_basis is 'unverified': a single Google Hotels call cannot tell "
        "a per-room rate from a per-bed one. A rate that scales with adults is "
        "probably per-bed — compare rate_per_night against total_rate on the "
        "top lodging option before quoting a total."
    ]
    if failed:
        notes.append(
            f"{len(failed)} of {len(windows)} window(s) could not be priced and "
            "were dropped; see `unavailable` for why."
        )

    out: dict[str, Any] = {
        "anchor_date": anchor_date,
        "adults": adults,
        "currency": currency,
        "windows": ranked,
        "lodging_basis": "unverified",
        "notes": notes,
        "searches_spent": searches_spent,
    }
    if failed:
        out["unavailable"] = failed
    _inject_quota_warning(out)
    return out


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(mcp: Any) -> None:
    """Register trip-window tool on a FastMCP instance."""
    mcp.tool()(compare_trip_windows)
