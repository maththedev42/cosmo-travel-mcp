"""MCP prompts for cosmo-travel-mcp."""

from __future__ import annotations

from typing import Any


def plan_trip(
    origin: str = "",
    destinations: str = "",
    dates: str = "",
    travelers: int = 1,
) -> str:
    """Walk through the trip-planning flow this server supports.

    Supply whatever you already know — every argument is optional. The prompt
    adapts to what you provide and tells you which tool to call next.
    """
    parts: list[str] = []

    parts.append(
        "You are planning a trip with cosmo-travel-mcp. "
        "Here is the recommended flow, in order:\n"
    )

    # ── Step 1: check_setup ──────────────────────────────────────────
    parts.append(
        "## 1. Call `check_setup` first\n"
        "This confirms which tools are usable right now. The SerpAPI half is "
        "free (does not count against the monthly search quota). "
        "You will get a per-tool ready/not-ready summary — only proceed "
        "with tools that report ready."
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
        parts.append("\n## Trip context\n" + "\n".join(context))

    # ── Step 2: cheapest dates (when flexible) ────────────────────────
    parts.append(
        "\n## 2. If dates are flexible, call `search_cheapest_dates`\n"
        "Provide `origin`, `destination`, `earliest_departure`, "
        "`latest_return`, and `trip_duration_days`. **Cost caveat:** "
        "every call to this tool spends up to `max_calls` SerpAPI searches "
        "(default 6, hard cap 15) against a 100/month free tier. "
        "Sample a small number of dates — do not call it in a loop or ask "
        "for more than 15. The output is a sample, not an exhaustive scan."
    )

    # ── Step 3: search_flights / search_multi_city ────────────────────
    parts.append(
        "\n## 3. Search flights\n"
        "- **Round trip:** `search_flights` with `origin`, `destination`, "
        "`outbound_date`, `return_date`. The first response contains "
        "**outbound options only** — prices shown are already round-trip "
        "totals. To get return legs for a chosen outbound, call "
        "`search_flights` again with the same params plus "
        "`departure_token=<token>` from that outbound option.\n"
        "- **One-way:** omit `return_date`.\n"
        "- **Multi-stop:** use `search_multi_city` with a `legs` list "
        "(2-6 legs, each with `origin`, `destination`, `date`)."
    )

    # ── Step 4: compare_drive_or_fly ──────────────────────────────────
    parts.append(
        "\n## 4. For short domestic hops, call `compare_drive_or_fly`\n"
        "Provide `origin` and `destination` as free-text addresses. "
        "Optionally pass flight price and duration (from step 3) to get "
        "a side-by-side cost/time comparison. Driving often beats flying "
        "for legs under ~400 km once airport overhead is counted."
    )

    # ── Step 5: search_accommodations ─────────────────────────────────
    parts.append(
        "\n## 5. Search accommodations per destination\n"
        "Call `search_accommodations` with `location`, `check_in_date`, "
        "`check_out_date`. With `vacation_rentals=True` (default), results "
        "include whole-property listings sourced from Airbnb, Vrbo, "
        "Booking.com, etc. — not Airbnb-exclusive. Set "
        "`vacation_rentals=False` for standard hotels."
    )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(mcp: Any) -> None:
    """Register MCP prompts on a FastMCP instance."""
    mcp.prompt()(plan_trip)
