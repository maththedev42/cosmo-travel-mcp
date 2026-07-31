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
        addr_parts: list[str] = []
        for field in ("address", "city", "region", "zip"):
            v = venue.get(field, "")
            if v:
                addr_parts.append(v)
        if addr_parts:
            parsed["address"] = ", ".join(addr_parts)

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

    params: dict[str, Any] = {}

    # Bare city/location gets an events-shaped prefix so the engine returns
    # relevant results instead of generic location info.
    if " " not in query and "," not in query:
        params["q"] = f"Events in {query}"
    else:
        params["q"] = query

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
