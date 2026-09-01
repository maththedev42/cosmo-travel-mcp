"""Event search tool backed by SerpAPI's google engine (events_results block).

Shows concerts, shows, sports, and festivals at a destination — the "what's on
while you're there" half of trip planning.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .flights import _call_serpapi, _inject_quota_warning, is_no_results_error

# In the google engine, events appear in the events_results SERP block on the
# first page. Paging via start=10 paginates organic SERP results, not events_results.
# _MAX_PAGES is retained for contract compatibility.
_MAX_PAGES = 5
_MAX_EXTRA_QUERIES = 6
_RESULTS_PER_PAGE = 10

WHEN_VALUES = frozenset({
    "today", "tomorrow", "week", "weekend",
    "next_week", "month", "next_month",
})

# Words that make a query already events-shaped. Anything without one of these
# is treated as a bare location and gets the "Events in …" prefix — testing for
# a space instead would leave every multi-word city ("New York", "Porto
# Alegre") unprefixed, which is exactly the case the prefix exists for.
_EVENT_WORDS = frozenset({
    "event", "events", "concert", "concerts", "show", "shows", "festival",
    "festivals", "game", "games", "match", "matches", "gig", "gigs",
    "tour", "conference", "conferences", "expo", "exhibition", "eventos",
    "concerto", "concertos", "shows", "festa", "festas",
})


def _events_shaped_query(query: str) -> str:
    """Return *query* as an events-shaped search string.

    A query that already names an event kind is passed through; a bare
    location is prefixed.
    """
    words = {w.strip(",.:;!?").lower() for w in query.split()}
    if words & _EVENT_WORDS:
        return query
    return f"Events in {query}"


def _parse_event(item: dict[str, Any]) -> dict[str, Any]:
    """Parse a single event from a SerpAPI response (google or google_events)."""
    parsed: dict[str, Any] = {
        "title": item.get("title", ""),
    }

    when_info = item.get("date")
    if isinstance(when_info, dict):
        date_parts: list[str] = []
        when_str = when_info.get("when", "")
        if when_str:
            date_parts.append(when_str)
        start = when_info.get("start_date", "")
        if start:
            date_parts.append(start)
        if date_parts:
            parsed["date"] = " | ".join(date_parts) if len(date_parts) > 1 else date_parts[0]
    elif isinstance(when_info, str) and when_info:
        time_info = item.get("time")
        if isinstance(time_info, str) and time_info:
            parsed["date"] = f"{when_info} | {time_info}"
        else:
            parsed["date"] = when_info

    venue = item.get("venue")
    if isinstance(venue, dict):
        name = venue.get("name", "")
        if name:
            parsed["venue"] = name
        for field, key in (("rating", "venue_rating"), ("reviews", "venue_reviews")):
            if venue.get(field) is not None:
                parsed[key] = venue[field]

    # `address` is a top-level array of strings on the event (or a string).
    # On the google engine, the venue name is returned as address[0] and the rest as address.
    address = item.get("address")
    if isinstance(address, list):
        addr_parts = [a for a in address if isinstance(a, str) and a]
        if addr_parts:
            if "venue" in parsed:
                parsed["address"] = ", ".join(addr_parts)
            else:
                parsed["venue"] = addr_parts[0]
                if len(addr_parts) > 1:
                    parsed["address"] = ", ".join(addr_parts[1:])
    elif isinstance(address, str) and address:
        if "venue" not in parsed:
            parsed["venue"] = address
        else:
            parsed["address"] = address

    link = item.get("link", "")
    if link:
        parsed["link"] = link

    tickets = item.get("ticket_info", [])
    if tickets and isinstance(tickets, list):
        ticket_list: list[dict[str, str]] = []
        for t in tickets:
            if isinstance(t, dict):
                entry: dict[str, str] = {}
                src = t.get("source", "")
                if src:
                    entry["source"] = src
                tl = t.get("link", "")
                if tl:
                    entry["link"] = tl
                if entry:
                    ticket_list.append(entry)
        if ticket_list:
            parsed["tickets"] = ticket_list

    return parsed


def _dedupe_key(event: dict[str, Any]) -> tuple[str, str]:
    """Conservative identity for an event: normalized title + its date string.

    Deliberately strict. Merging "TIAGO IORC" with "TIAGO IORC - TURNÊ TROCO
    LIKES 10 ANOS" would be wrong when they are different nights, and losing a
    real event is worse than showing a near-duplicate — so only case, accents
    and spacing are normalized away, never words.
    """
    title = event.get("title", "")
    folded = unicodedata.normalize("NFKD", title)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = re.sub(r"\s+", " ", folded).strip().casefold()
    return folded, str(event.get("date", ""))


async def _fetch_page(
    query: str,
    *,
    country: str | None,
    language: str | None,
) -> tuple[list[dict[str, Any]], bool, bool]:
    """Fetch events for *query*. Returns ``(raw_events, from_cache, spent_a_search)``.

    A "no results" body is a valid empty page, not a failure — one barren
    angle of a sweep must not sink the other angles.
    """
    params: dict[str, Any] = {"q": _events_shaped_query(query)}
    if country:
        params["gl"] = country
    if language:
        params["hl"] = language

    try:
        data, from_cache = await _call_serpapi(params, engine="google")
    except ValueError as exc:
        if is_no_results_error(exc):
            # The search was still spent upstream; report it honestly.
            return [], False, True
        raise

    raw = data.get("events_results", [])
    if not isinstance(raw, list):
        raw = []
    return raw, from_cache, not from_cache


async def search_events(
    query: str,
    when: str | None = None,
    also_search: list[str] | None = None,
    pages: int = 1,
    country: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Search for events (concerts, shows, sports, festivals) via SerpAPI's google engine.

    **Costs one SerpAPI search per query angle** — that is
    ``1 + len(also_search)`` searches. A default call costs 1.

    One query returns about ten results from Google's event SERP block.
    Different phrasings reach different corpora — this is how niche events
    surface at all: a local sports fixture or a free street party rarely
    appears under a generic "events in <city>" query, but does under
    "esportes em <city>" or "festival de rua <city>". Use ``also_search`` to
    surface more events.

    Note on deprecated parameters (retained for contract compatibility):
    * ``pages``: Treated as an unbilled no-op. Google's SERP returns all
      ``events_results`` on the first page. Extra pages are not requested and
      do not consume quota.
    * ``when``: Treated as a no-op. Google's SERP ``events_results`` block
      does not apply date filters.

    Args:
        query: Free-text search (e.g. "New York" or "jazz concerts in New
               Orleans"). A bare city/location name is prefixed automatically.
        when: Deprecated no-op date filter.
        also_search: Up to 6 additional query angles, deduplicated against the
               main query's results.
        pages: Deprecated no-op (1 search spent per query angle).
        country: Country code (``gl`` parameter).
        language: Language code (``hl`` parameter).

    Returns:
        ``events`` (deduplicated, in discovery order), ``total_results``,
        ``searches_used``, and ``queries`` listing every angle actually run.
    """
    if when is not None and when not in WHEN_VALUES:
        raise ValueError(
            f"Invalid when: {when!r}. "
            f"Must be one of: {', '.join(sorted(WHEN_VALUES))}."
        )
    if pages < 1 or pages > _MAX_PAGES:
        raise ValueError(f"pages must be between 1 and {_MAX_PAGES}, got {pages}")
    if also_search is not None and len(also_search) > _MAX_EXTRA_QUERIES:
        raise ValueError(
            f"also_search accepts at most {_MAX_EXTRA_QUERIES} extra angles "
            f"(each costs 1 search), got {len(also_search)}"
        )

    queries = [query] + [q for q in (also_search or []) if q and q.strip()]

    collected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    searches_used = 0
    any_cached = False

    for q in queries:
        raw, from_cache, spent = await _fetch_page(
            q,
            country=country,
            language=language,
        )
        searches_used += int(spent)
        any_cached = any_cached or from_cache

        for item in raw:
            parsed = _parse_event(item)
            key = _dedupe_key(parsed)
            if key in seen:
                continue
            seen.add(key)
            collected.append(parsed)

    result: dict[str, Any] = {
        "events": collected,
        "total_results": len(collected),
        "searches_used": searches_used,
        "queries": queries,
    }
    if any_cached:
        result["cached"] = True
    _inject_quota_warning(result)
    return result


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(mcp: Any) -> None:
    """Register events tool on a FastMCP instance."""
    mcp.tool()(search_events)
