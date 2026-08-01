"""MCP prompts for cosmo-travel-mcp."""

from __future__ import annotations

from typing import Any


def plan_trip(
    origin: str = "",
    destinations: str = "",
    dates: str = "",
    travelers: int = 1,
) -> str:
    """Plan a complete trip — flights, lodging, things to do, and a day-by-day itinerary.

    Supply whatever you already know — every argument is optional. The prompt
    adapts to what you provide and tells you which tool to call next.
    """
    parts: list[str] = []

    parts.append(
        "You are planning a trip with cosmo-travel-mcp. Work through the "
        "research phase in order, then assemble the itinerary.\n"
    )

    # ── User-supplied context ────────────────────────────────────────
    context: list[str] = []
    if origin:
        context.append(f"- Origin: {origin}")
    if destinations:
        context.append(f"- Destination(s): {destinations}")
    if dates:
        context.append(f"- Dates / window: {dates}")
    if travelers > 0:
        context.append(f"- Travelers: {travelers}")
    if context:
        parts.append("## Trip context\n" + "\n".join(context) + "\n")

    parts.append(
        "## Budget your searches first\n"
        "Every tool below except `check_setup` spends SerpAPI quota — the free "
        "tier is 100 searches/month. Before calling anything, count what the "
        "plan needs: roughly 1 flight search + 1 lodging search + 1 things-to-do "
        "search + 1 events search **per city**. A three-city trip is ~12 "
        "searches. Tell the traveller that estimate if it is large, and never "
        "loop a tool to explore variations."
    )

    # ── Step 1 ────────────────────────────────────────────────────────
    parts.append(
        "\n## 1. `check_setup`\n"
        "Confirms which tools are usable. The SerpAPI half is free (no quota "
        "spent) and reports how many searches remain this month. Only proceed "
        "with tools that report ready."
    )

    # ── Step 2 ────────────────────────────────────────────────────────
    parts.append(
        "\n## 2. Dates — `search_cheapest_dates` (only if dates are flexible)\n"
        "Provide `origin`, `destination`, `earliest_departure`, "
        "`latest_return`, `trip_duration_days`. **This one tool spends up to "
        "`max_calls` searches per invocation** (default 6, hard cap 15). It "
        "samples dates; it does not scan every day. Skip it entirely when the "
        "traveller already has fixed dates."
    )

    # ── Step 3 ────────────────────────────────────────────────────────
    parts.append(
        "\n## 3. Flights — `search_flights` / `search_multi_city`\n"
        "- **Round trip:** `search_flights` with `origin`, `destination`, "
        "`outbound_date`, `return_date`. The first response is **outbound "
        "options only**, and their prices are already round-trip totals. To "
        "see return legs for a chosen outbound, call again with the same "
        "params plus `departure_token` from that option.\n"
        "- **Which seller is cheapest:** take a `booking_token` from a "
        "phase-2 option and call again with it — that returns the actual "
        "sellers and their prices.\n"
        "- **One-way:** omit `return_date`. **Multi-stop:** "
        "`search_multi_city` with 2–6 `legs`, each `{origin, destination, date}`."
    )

    # ── Step 4 ────────────────────────────────────────────────────────
    parts.append(
        "\n## 4. Short hops — `compare_drive_or_fly`\n"
        "For legs under ~400 km, driving often wins once airport overhead is "
        "counted. Pass `origin` and `destination` as free text; add "
        "`fuel_price_per_liter` and `fuel_efficiency_km_per_liter` to get a "
        "real cost, and the flight price/duration from step 3 for a "
        "side-by-side comparison. Toll estimates are included when the route "
        "has them."
    )

    # ── Step 5 ────────────────────────────────────────────────────────
    parts.append(
        "\n## 5. Lodging — `search_accommodations`, then `get_accommodation_details`\n"
        "Search with `location`, `check_in_date`, `check_out_date`. With "
        "`vacation_rentals=True` (default) results include whole-property "
        "listings from Airbnb, Vrbo, Booking.com and others — not "
        "Airbnb-exclusive; set it to `False` for standard hotels. Narrow with "
        "`sort_by`, `min_rating`, `hotel_class`. To drill into one property, "
        "pass its `property_token` to `get_accommodation_details` together "
        "with the same `location` and dates."
    )

    # ── Step 6 ────────────────────────────────────────────────────────
    parts.append(
        "\n## 6. Things to do — `search_things_to_do`\n"
        "One call per city. `category` is one of attractions, museums, parks, "
        "landmarks, shopping, nightlife, restaurants, cafes, bars — default "
        "`attractions`. Use `min_rating` to keep the list short and good.\n"
        "Two fields on each result are what the itinerary is built from:\n"
        "- `operating_hours` — per weekday. **Check it before scheduling "
        "anything.** A museum closed on Mondays is the most common way a "
        "generated itinerary turns out to be useless. Note that its keys are "
        "localized by the `language` argument (`pt-br` returns "
        "`segunda-feira`, and `Fechado` means closed) — read the keys you "
        "actually get back rather than assuming English weekday names.\n"
        "- `coordinates` — group stops that sit near each other into the same "
        "day rather than crossing the city twice."
    )

    # ── Step 7 ────────────────────────────────────────────────────────
    parts.append(
        "\n## 7. What's on while they're there — `search_events`\n"
        "Concerts, shows, sports and festivals, with dates and ticket links. "
        "Pass the city and a `when` window when the travel dates fall inside "
        "one (today, tomorrow, week, weekend, next_week, month, next_month). "
        "Events are date-bound, so they anchor the itinerary: build the day "
        "around the show, not the other way round."
    )

    # ── Assembly ──────────────────────────────────────────────────────
    parts.append(
        "\n## 8. Assemble the itinerary\n"
        "Only now, with the research done, write the day-by-day plan. Build "
        "it in this order, because each step constrains the next:\n"
        "1. **Fix the skeleton** — arrival and departure times from the "
        "chosen flights. The first and last day are partial days; do not "
        "schedule a full programme on either.\n"
        "2. **Anchor the date-bound items** — events with fixed dates, and "
        "any attraction closed on specific weekdays. These cannot move.\n"
        "3. **Cluster by geography** — use `coordinates` to group each day's "
        "stops into one area. A day that bounces across the city wastes hours "
        "in transit.\n"
        "4. **Respect opening hours** — place each stop inside its "
        "`operating_hours` for that weekday, and leave travel time between "
        "them.\n"
        "5. **Anchor meals near the stops** — pick restaurants/cafes from the "
        "food categories whose `coordinates` are close to that day's cluster.\n"
        "6. **Leave slack.** Two or three substantial stops per day is a real "
        "plan; six is a list nobody can follow.\n\n"
        "Present it as one section per day: date, area, stops in time order "
        "with opening hours noted, where to eat, and the night's lodging. "
        "State the total estimated cost separately, and say plainly which "
        "numbers are quotes you retrieved and which are estimates.\n\n"
        "**Do not invent specifics.** Every place, price, time and ticket "
        "link must come from a tool result. If something was not returned — "
        "an admission price, a reservation — say it needs checking rather "
        "than filling it in."
    )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(mcp: Any) -> None:
    """Register MCP prompts on a FastMCP instance."""
    mcp.prompt()(plan_trip)
