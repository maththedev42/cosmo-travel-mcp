"""Onboarding / health-check tool for cosmo-travel-mcp.

Call ``check_setup`` first — before any real search — to see which capabilities
are usable right now. The SerpAPI account check is free (does NOT count against
monthly quota). The Google Maps check makes one real ``computeRouteMatrix`` call
against the key's quota, which Google's free monthly credit comfortably covers.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from .driving import FIELD_MASK, ROUTES_API_BASE, _get_maps_api_key

SERPAPI_ACCOUNT_URL = "https://serpapi.com/account.json"
SERPAPI_SIGNUP_URL = "https://serpapi.com/users/sign_up"
GOOGLE_CONSOLE_URL = "https://console.cloud.google.com/"

# A minimal route between two well-known cities for the Maps key check.
_CHECK_ORIGIN = {"waypoint": {"address": "San Francisco, CA"}}
_CHECK_DEST = {"waypoint": {"address": "Los Angeles, CA"}}


def _setup_guide(*, need_serpapi: bool, need_maps: bool) -> str:
    """Build the walk-through shown when a key is missing.

    Returned once at the top level rather than repeated on every affected tool:
    the same 40-word remediation echoed five times is noise, and the caller of
    this tool is often an LLM relaying it to a human. "See the README" is a dead
    end inside a chat client, so give the actual commands.
    """
    steps: list[str] = []

    if need_serpapi:
        steps.append(
            f"1. Get a free SerpAPI key — sign up at {SERPAPI_SIGNUP_URL} "
            "(free tier: 100 searches/month), then copy the key from your "
            "dashboard. This unlocks flights, multi-city, accommodations and "
            "cheapest-dates."
        )
    if need_maps:
        steps.append(
            f"{len(steps) + 1}. Get a Google Maps key — create one at "
            f"{GOOGLE_CONSOLE_URL} with the **Routes API** enabled (not the "
            "legacy Distance Matrix API). Billing must be enabled on the "
            "project, though the free monthly credit covers normal use. This "
            "unlocks drive-or-fly comparison."
        )

    env_pairs = []
    if need_serpapi:
        env_pairs.append("-e SERPAPI_API_KEY=<your-serpapi-key>")
    if need_maps:
        env_pairs.append("-e GOOGLE_MAPS_API_KEY=<your-google-maps-key>")
    env_line = " \\\n    ".join(env_pairs)

    steps.append(
        f"{len(steps) + 1}. Give the key(s) to this server. If it is NOT yet "
        "registered with Claude Code:\n"
        "  claude mcp add cosmo-travel --scope user \\\n"
        f"    {env_line} \\\n"
        "    -- uvx --from git+https://github.com/maththedev42/cosmo-travel-mcp "
        "cosmo-travel-mcp\n"
        "If it IS already registered (which it is, since you are calling this "
        "tool), re-register it so the keys are attached:\n"
        "  claude mcp remove cosmo-travel --scope user\n"
        "  …then run the add command above."
    )
    steps.append(
        f"{len(steps) + 1}. Restart the MCP client, then call check_setup "
        "again to confirm."
    )

    return "\n\n".join(steps)


async def check_setup() -> dict[str, Any]:
    """Check which cosmo-travel-mcp capabilities are ready.

    Verifies SERPAPI_API_KEY (free account check) and GOOGLE_MAPS_API_KEY
    (one real Routes API call). Returns a per-tool status summary so the
    caller knows what will work before spending quota on real searches.

    ``search_cheapest_dates`` costs up to *n* SerpAPI searches per call
    (where *n* is the number of candidate dates sampled).
    """
    flights_status: dict[str, Any] = {"tool": "search_flights", "ready": False}
    multi_city_status: dict[str, Any] = {"tool": "search_multi_city", "ready": False}
    accommodations_status: dict[str, Any] = {
        "tool": "search_accommodations",
        "ready": False,
    }
    cheapest_dates_status: dict[str, Any] = {
        "tool": "search_cheapest_dates",
        "ready": False,
    }
    driving_status: dict[str, Any] = {
        "tool": "compare_drive_or_fly",
        "ready": False,
    }

    # --- SerpAPI check (free --- does not count against monthly quota) ---
    serpapi_key = os.environ.get("SERPAPI_API_KEY", "")

    if not serpapi_key:
        for s in [
            flights_status,
            multi_city_status,
            accommodations_status,
            cheapest_dates_status,
        ]:
            s["reason"] = (
                "SERPAPI_API_KEY is not set — free key (100 searches/month) at "
                f"{SERPAPI_SIGNUP_URL}; see the `setup` field for the exact "
                "command to register it"
            )
    else:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    SERPAPI_ACCOUNT_URL,
                    params={"api_key": serpapi_key},
                )
                resp.raise_for_status()
                account: dict[str, Any] = resp.json()

            if "error" in account:
                reason = account["error"]
                for s in [
                    flights_status,
                    multi_city_status,
                    accommodations_status,
                    cheapest_dates_status,
                ]:
                    s["reason"] = f"SerpAPI key was rejected: {reason}. The key may be invalid or expired — get a new one at https://serpapi.com/users/sign_up"
            else:
                plan_searches_left = account.get("plan_searches_left", "?")
                this_month_usage = account.get("this_month_usage", "?")
                total_searches_left = account.get("total_searches_left", "?")

                for s in [
                    flights_status,
                    multi_city_status,
                    accommodations_status,
                    cheapest_dates_status,
                ]:
                    s["ready"] = True
                    s["plan_searches_left"] = plan_searches_left
                    s["this_month_usage"] = this_month_usage
                    s["total_searches_left"] = total_searches_left
        except httpx.HTTPStatusError as exc:
            reason = f"HTTP {exc.response.status_code}"
            for s in [
                flights_status,
                multi_city_status,
                accommodations_status,
                cheapest_dates_status,
            ]:
                s["reason"] = f"SerpAPI account check could not complete (HTTP {reason}). The key may be fine — SerpAPI servers may be temporarily unreachable."
        except Exception as exc:
            for s in [
                flights_status,
                multi_city_status,
                accommodations_status,
                cheapest_dates_status,
            ]:
                s["reason"] = f"SerpAPI account check could not complete: {exc}. The key may be fine — SerpAPI servers may be temporarily unreachable."

    # --- Google Maps check (one real API call) ---
    maps_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")

    if not maps_key:
        driving_status["reason"] = (
            "GOOGLE_MAPS_API_KEY is not set — create a key at "
            f"{GOOGLE_CONSOLE_URL} with the Routes API enabled (not the legacy "
            "Distance Matrix API); see the `setup` field for the exact command "
            "to register it"
        )
    else:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    ROUTES_API_BASE,
                    headers={
                        "X-Goog-Api-Key": maps_key,
                        "X-Goog-FieldMask": FIELD_MASK,
                        "Content-Type": "application/json",
                    },
                    json={
                        "origins": [_CHECK_ORIGIN],
                        "destinations": [_CHECK_DEST],
                        "travelMode": "DRIVE",
                    },
                )
                resp.raise_for_status()
                elements: list[dict[str, Any]] = resp.json()

            if not elements or not isinstance(elements, list):
                driving_status["reason"] = (
                    "Maps API returned unexpected response"
                )
            else:
                el = elements[0]
                status = el.get("status", {})
                if isinstance(status, dict) and status:
                    driving_status["reason"] = (
                        f"Maps API error: {status}"
                    )
                else:
                    driving_status["ready"] = True
        except httpx.HTTPStatusError as exc:
            driving_status["reason"] = (
                f"Maps API check could not complete (HTTP {exc.response.status_code}). The key may be rejected or the Routes API may not be enabled — verify the key at https://console.cloud.google.com/"
            )
        except Exception as exc:
            driving_status["reason"] = f"Maps API check could not complete: {exc}. The key may be fine — Google servers may be temporarily unreachable."

    result_tools = [
        flights_status,
        multi_city_status,
        accommodations_status,
        cheapest_dates_status,
        driving_status,
    ]

    # Build a human-readable summary, one line per tool.
    summary_lines: list[str] = []
    for t in result_tools:
        if t["ready"]:
            if t["tool"] == "compare_drive_or_fly":
                summary_lines.append(f"{t['tool']}: ready (Maps key valid)")
            elif t["tool"] == "search_cheapest_dates":
                left = t.get("plan_searches_left", "?")
                summary_lines.append(
                    f"{t['tool']}: ready ({left} searches left; "
                    "each call costs up to max_calls searches (default 6, hard cap 15))"
                )
            else:
                left = t.get("plan_searches_left", "?")
                summary_lines.append(
                    f"{t['tool']}: ready ({left} searches left this month)"
                )
        else:
            reason = t.get("reason", "unknown")
            summary_lines.append(f"{t['tool']}: NOT ready — {reason}")

    out: dict[str, Any] = {
        "tools": result_tools,
        "summary": "\n".join(summary_lines),
    }

    # Only present when something needs configuring, so a healthy setup check
    # stays short. Keyed off the missing env var rather than `ready`, because a
    # key that is set but rejected needs a different message than a missing one.
    need_serpapi = not os.environ.get("SERPAPI_API_KEY", "")
    need_maps = not os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if need_serpapi or need_maps:
        out["setup"] = _setup_guide(need_serpapi=need_serpapi, need_maps=need_maps)

    return out


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(mcp: Any) -> None:
    """Register setup tool on a FastMCP instance."""
    mcp.tool()(check_setup)
