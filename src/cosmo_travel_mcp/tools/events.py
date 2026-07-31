"""Event search tool backed by SerpAPI's google_events engine.

Shows concerts, shows, sports, and festivals at a destination — the "what's on
while you're there" half of trip planning.
"""

from __future__ import annotations

from typing import Any

from .flights import _call_serpapi

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
    """Parse a single event from a SerpAPI google_events response."""
    parsed: dict[str, Any] = {
        "title": item.get("title", ""),
    }

    when_info = item.get("date", {})
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

    venue = item.get("venue", {})
    if isinstance(venue, dict):
        name = venue.get("name", "")
        if name:
            parsed["venue"] = name
        for field, key in (("rating", "venue_rating"), ("reviews", "venue_reviews")):
            if venue.get(field) is not None:
                parsed[key] = venue[field]

    # `address` is a top-level array of strings on the event — the venue
    # object carries only {name, rating, reviews, link}. Verified against the
    # live API 2026-07-31: e.g.
    # ["Smoke Jazz & Supper Club, 2751 Broadway", "New York, NY"].
    address = item.get("address")
    if isinstance(address, list):
        addr_parts = [a for a in address if isinstance(a, str) and a]
        if addr_parts:
            parsed["address"] = ", ".join(addr_parts)
    elif isinstance(address, str) and address:
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


async def search_events(
    query: str,
    when: str | None = None,
    country: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Search for events (concerts, shows, sports, festivals) via SerpAPI.

    Costs 1 SerpAPI search per call against the monthly quota.

    Args:
        query: Free-text search (e.g. "New York" or "jazz concerts in New
               Orleans"). A bare city/location name is prefixed automatically.
        when: Optional date filter — one of today, tomorrow, week, weekend,
              next_week, month, next_month.
        country: Country code (``gl`` parameter).
        language: Language code (``hl`` parameter).
    """
    if when is not None and when not in WHEN_VALUES:
        raise ValueError(
            f"Invalid when: {when!r}. "
            f"Must be one of: {', '.join(sorted(WHEN_VALUES))}."
        )

    params: dict[str, Any] = {"q": _events_shaped_query(query)}

    if when is not None:
        params["htichips"] = f"date:{when}"

    if country:
        params["gl"] = country
    if language:
        params["hl"] = language

    data = await _call_serpapi(params, engine="google_events")

    raw_events = data.get("events_results", [])
    if not isinstance(raw_events, list):
        raw_events = []

    parsed = [_parse_event(e) for e in raw_events]

    return {
        "events": parsed,
        "total_results": len(parsed),
    }


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(mcp: Any) -> None:
    """Register events tool on a FastMCP instance."""
    mcp.tool()(search_events)
