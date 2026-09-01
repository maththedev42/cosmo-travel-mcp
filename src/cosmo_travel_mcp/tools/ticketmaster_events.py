"""Event search tool backed by Ticketmaster Discovery API v2.

Provides concert, sports, and theatre event search with **public sales dates**
(`sales.public.startDateTime`/`startTBA`, `sales.presales`), direct booking
URLs, and venue coordinates. `priceRanges` is real but uncommon: sampled live
across 116 New York events on 2026-09-01 (Music, Sports, Arts & Theatre,
Film), only 3 carried it — treat its absence as normal, not a parsing gap.

Country coverage — live-tested 2026-09-01, `countryCode=<CC>`, no city filter:

    US  10000+ events   CA  8,433   MX  2,948   GB  10000+
    PE  44              CL  9       BR  141     AR  0 (confirmed, not a filter artifact)

Ticketmaster's own docs list more countries than this; only the ones above
were independently verified against a live call, so treat "etc." claims about
other countries as unconfirmed rather than as coverage.

**`city` requires an exact match against Ticketmaster's own city registry —
it does not fuzzy-match, and does not tolerate diacritics.** Live-tested:
`countryCode=BR` alone returns 141 events, but `city=São Paulo` *and*
`city=Sao Paulo` (unaccented) both returned zero, while `city=Rio de Janeiro`
worked. A caller getting zero results cannot assume "no coverage here" — it
may just be the wrong city string. Prefer a bare `countryCode` filter, or
`keyword`, over guessing a city's exact registered spelling.

Coverage Note:
- Ticketmaster covers Broadway venues operated by Nederlander and ATG.
- The Shubert Organization (the largest Broadway group) sells via Telecharge and
  does NOT appear here.
- Argentina returns zero events (confirmed live) — for that and any other
  country, `search_events` (Google engine) is the universal-coverage tool.
  This tool is a supplement where it has data, never a replacement.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from ..onboarding import TICKETMASTER_ENV, missing_key_message

TICKETMASTER_BASE = "https://app.ticketmaster.com/discovery/v2/events.json"


def _get_ticketmaster_key() -> str:
    key = os.environ.get(TICKETMASTER_ENV, "")
    if not key:
        raise ValueError(missing_key_message(TICKETMASTER_ENV))
    return key


def _redact_apikey(text: str) -> str:
    """Redact apikey parameters from strings, URLs, and exception messages."""
    if not text:
        return ""
    return re.sub(r"apikey=[^&\s\"']*", "apikey=***", text)


def _parse_ticketmaster_event(item: dict[str, Any]) -> dict[str, Any]:
    """Parse a single event item from a Ticketmaster Discovery API response."""
    parsed: dict[str, Any] = {
        "title": item.get("name", ""),
    }

    url = item.get("url", "")
    if url:
        parsed["url"] = url

    # Event date & time
    dates = item.get("dates", {})
    start = dates.get("start", {}) if isinstance(dates, dict) else {}
    if isinstance(start, dict):
        local_date = start.get("localDate", "")
        local_time = start.get("localTime", "")
        if local_date and local_time:
            parsed["date"] = f"{local_date} | {local_time}"
        elif local_date:
            parsed["date"] = local_date

    # Ticket sales metadata (public & presales)
    sales = item.get("sales", {})
    if isinstance(sales, dict):
        sales_data: dict[str, Any] = {}
        pub = sales.get("public", {})
        if isinstance(pub, dict):
            pub_sale: dict[str, Any] = {}
            if pub.get("startDateTime"):
                pub_sale["start_date_time"] = pub["startDateTime"]
            if pub.get("endDateTime"):
                pub_sale["end_date_time"] = pub["endDateTime"]
            if "startTBD" in pub:
                pub_sale["start_tbd"] = pub["startTBD"]
            # Distinct from startTBD, and NOT redundant with it: captured live
            # (New York Knicks vs. Boston Celtics, 2026-09-01), a real event had
            # `{"startTBD": false, "startTBA": true}` — no startDateTime at all.
            # Reading only startTBD there would have reported `start_tbd: false`
            # for an event whose sale date is genuinely unannounced, which is
            # the one thing this tool exists to get right.
            if "startTBA" in pub:
                pub_sale["start_tba"] = pub["startTBA"]
            if pub_sale:
                sales_data["public"] = pub_sale

        presales = sales.get("presales")
        if isinstance(presales, list) and presales:
            parsed_presales: list[dict[str, Any]] = []
            for ps in presales:
                if isinstance(ps, dict):
                    entry: dict[str, Any] = {}
                    if ps.get("name"):
                        entry["name"] = ps["name"]
                    if ps.get("startDateTime"):
                        entry["start_date_time"] = ps["startDateTime"]
                    if ps.get("endDateTime"):
                        entry["end_date_time"] = ps["endDateTime"]
                    if entry:
                        parsed_presales.append(entry)
            if parsed_presales:
                sales_data["presales"] = parsed_presales

        if sales_data:
            parsed["sales"] = sales_data

    # Price ranges
    price_ranges = item.get("priceRanges")
    if isinstance(price_ranges, list) and price_ranges:
        parsed_prices: list[dict[str, Any]] = []
        for pr in price_ranges:
            if isinstance(pr, dict):
                p_entry: dict[str, Any] = {}
                if pr.get("currency"):
                    p_entry["currency"] = pr["currency"]
                if pr.get("min") is not None:
                    p_entry["min"] = pr["min"]
                if pr.get("max") is not None:
                    p_entry["max"] = pr["max"]
                if p_entry:
                    parsed_prices.append(p_entry)
        if parsed_prices:
            parsed["price_ranges"] = parsed_prices

    # Venue & location details
    embedded = item.get("_embedded", {})
    if isinstance(embedded, dict):
        venues = embedded.get("venues")
        if isinstance(venues, list) and venues:
            v0 = venues[0]
            if isinstance(v0, dict):
                v_name = v0.get("name", "")
                if v_name:
                    parsed["venue"] = v_name
                addr = v0.get("address", {})
                if isinstance(addr, dict):
                    # Captured live: a real venue ("Berlin", NYC) carries both
                    # line1 and line2 ("The Lower-Level of 2A Bar" / "25 Avenue
                    # A") — line1 alone was an incomplete address for it.
                    addr_parts = [
                        addr[k] for k in ("line1", "line2") if addr.get(k)
                    ]
                    if addr_parts:
                        parsed["address"] = ", ".join(addr_parts)
                loc = v0.get("location", {})
                if isinstance(loc, dict) and loc.get("latitude") and loc.get("longitude"):
                    try:
                        parsed["location"] = {
                            "lat": float(loc["latitude"]),
                            "lng": float(loc["longitude"]),
                        }
                    except (ValueError, TypeError):
                        pass

    # Category / segment
    classifications = item.get("classifications")
    if isinstance(classifications, list) and classifications:
        c0 = classifications[0]
        if isinstance(c0, dict):
            seg = c0.get("segment", {})
            if isinstance(seg, dict) and seg.get("name"):
                parsed["category"] = seg["name"]

    return parsed


async def _call_ticketmaster(params: dict[str, Any]) -> dict[str, Any]:
    """Execute a request to Ticketmaster Discovery API v2 with API key redaction."""
    api_key = _get_ticketmaster_key()
    query_params = {**params, "apikey": api_key}

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(TICKETMASTER_BASE, params=query_params)
            if resp.status_code == 401:
                try:
                    err_data = resp.json()
                    fault_msg = (
                        err_data.get("fault", {})
                        .get("faultstring", "Invalid ApiKey")
                    )
                except Exception:
                    fault_msg = "Invalid ApiKey"
                raise ValueError(f"Ticketmaster API key rejected: {fault_msg}")
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return data
    except httpx.HTTPStatusError as exc:
        sanitized_url = (
            re.sub(r"apikey=[^&]+", "apikey=***", str(exc.request.url))
            if exc.request
            else ""
        )
        req = (
            httpx.Request(
                exc.request.method, sanitized_url, headers=exc.request.headers
            )
            if exc.request
            else None
        )
        msg = re.sub(r"apikey=[^&]+", "apikey=***", str(exc))
        raise httpx.HTTPStatusError(msg, request=req, response=exc.response) from None
    except httpx.RequestError as exc:
        sanitized_url = (
            re.sub(r"apikey=[^&]+", "apikey=***", str(exc.request.url))
            if exc.request
            else ""
        )
        req = (
            httpx.Request(
                exc.request.method, sanitized_url, headers=exc.request.headers
            )
            if exc.request
            else None
        )
        msg = re.sub(r"apikey=[^&]+", "apikey=***", str(exc))
        cls = type(exc)
        raise cls(msg, request=req) from None


async def search_ticketmaster_events(
    city: str | None = None,
    country_code: str | None = None,
    keyword: str | None = None,
    start_date_time: str | None = None,
    end_date_time: str | None = None,
    classification_name: str | None = None,
    size: int = 20,
    page: int = 0,
) -> dict[str, Any]:
    """Search for events via Ticketmaster Discovery API v2 (includes sale dates & ticket URLs).

    Costs 1 call against Ticketmaster's free daily limit (5,000 requests/day, 5 req/s).
    Does NOT share quota with SerpAPI.

    Key Features:
    - Provides **public sale start dates** (`sales.public.startDateTime`,
      `startTBA`) and **presales** — this is the reason to call this tool
      instead of (or alongside) `search_events`, which has neither.
    - Returns direct booking links (`url`) and venue details. `priceRanges`
      is present on a minority of events (roughly 1 in 40, sampled live) —
      absence is normal, not a sign something failed.

    Coverage Notice:
    - Live-tested 2026-09-01: US, CA, MX, GB, PE, CL, BR all return events.
      **Argentina returns zero** (confirmed, not a query artifact). Other
      countries Ticketmaster's docs claim were not independently verified.
    - `city` needs an exact match to Ticketmaster's own registry — it is not
      fuzzy and does not tolerate diacritics (`"São Paulo"` and `"Sao Paulo"`
      both returned zero live; `"Rio de Janeiro"` worked). Zero results does
      not mean zero coverage — try a bare `country_code`, or `keyword`,
      before concluding the country isn't covered.
    - In New York Broadway, covers Nederlander & ATG theatres; Shubert
      Organization theatres (Telecharge) are excluded.
    - For universal event coverage regardless of country or ticketing
      provider, use `search_events` — this tool supplements it where it has
      sale-date data, never replaces it.

    Args:
        city: Destination city (e.g. "New York", "Chicago"). Optional — see
            the coverage notice above: `city` is an exact match against
            Ticketmaster's own registry, not a fuzzy search, so a call that
            gets zero results should retry with `city` omitted (use
            `country_code` and/or `keyword` instead) before concluding
            there's no coverage. At least one of `city`, `country_code`,
            `keyword`, `classification_name` is required.
        country_code: ISO 2-letter country code (e.g. "US", "BR"). Recommended to prevent mixing countries.
        keyword: Event keyword, artist, or show title (e.g. "Hamilton", "jazz").
        start_date_time: ISO 8601 start date filter (e.g. "2026-10-01T00:00:00Z").
        end_date_time: ISO 8601 end date filter (e.g. "2026-10-31T23:59:59Z").
        classification_name: Category filter (e.g. "Music", "Sports", "Arts & Theatre").
        size: Results per page (1 to 100, default 20).
        page: Page number (0-indexed).

    Returns:
        `events` list, `total_results`, `page`, `size`, and `provider: "ticketmaster"`.
    """
    if not any([
        city and city.strip(),
        country_code and country_code.strip(),
        keyword and keyword.strip(),
        classification_name and classification_name.strip(),
    ]):
        raise ValueError(
            "at least one of city, country_code, keyword, or "
            "classification_name is required"
        )
    if size < 1 or size > 100:
        raise ValueError(f"size must be between 1 and 100, got {size}")
    if page < 0:
        raise ValueError(f"page must be >= 0, got {page}")

    params: dict[str, Any] = {
        "size": size,
        "page": page,
    }
    if city and city.strip():
        params["city"] = city.strip()
    if country_code:
        params["countryCode"] = country_code.strip().upper()
    if keyword and keyword.strip():
        params["keyword"] = keyword.strip()
    if start_date_time:
        params["startDateTime"] = start_date_time.strip()
    if end_date_time:
        params["endDateTime"] = end_date_time.strip()
    if classification_name and classification_name.strip():
        params["classificationName"] = classification_name.strip()

    raw_data = await _call_ticketmaster(params)

    embedded = raw_data.get("_embedded")
    raw_events = (
        embedded.get("events", [])
        if isinstance(embedded, dict)
        else []
    )

    events: list[dict[str, Any]] = [
        _parse_ticketmaster_event(item)
        for item in raw_events
        if isinstance(item, dict)
    ]

    page_info = raw_data.get("page", {}) if isinstance(raw_data.get("page"), dict) else {}
    total_elements = page_info.get("totalElements", len(events))

    return {
        "events": events,
        "total_results": total_elements,
        "page": page,
        "size": size,
        "provider": "ticketmaster",
    }


def register(mcp: Any) -> None:
    """Register Ticketmaster events tool on a FastMCP instance."""
    mcp.tool()(search_ticketmaster_events)
