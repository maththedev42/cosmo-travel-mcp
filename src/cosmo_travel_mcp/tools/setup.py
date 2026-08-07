"""Onboarding / health-check tool for cosmo-travel-mcp.

Call ``check_setup`` first — before any real search — to see which capabilities
are usable right now. The SerpAPI account check is free (does NOT count against
monthly quota). The Google Maps check makes one real ``computeRouteMatrix`` call
against the key's quota, which Google's free monthly credit comfortably covers.

The two ``probe_*`` helpers take a key as an argument rather than reading the
environment, so the ``cosmo-travel-mcp setup --register`` CLI can validate a key
the user just typed — before it gets baked into their MCP client config.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from ..onboarding import (
    GOOGLE_CONSOLE_URL,
    SERPAPI_SIGNUP_URL,
    setup_guide,
)
from .driving import FIELD_MASK, ROUTES_API_BASE
from .flights import _refresh_quota_from_account

SERPAPI_ACCOUNT_URL = "https://serpapi.com/account.json"

# Re-exported so callers (and tests) can import every onboarding anchor from
# one place alongside check_setup itself.
__all__ = [
    "GOOGLE_CONSOLE_URL",
    "ROUTES_API_BASE",
    "SERPAPI_ACCOUNT_URL",
    "SERPAPI_SIGNUP_URL",
    "check_setup",
    "probe_maps",
    "probe_serpapi",
    "register",
]

# A minimal route between two well-known cities for the Maps key check.
_CHECK_ORIGIN = {"waypoint": {"address": "San Francisco, CA"}}
_CHECK_DEST = {"waypoint": {"address": "Los Angeles, CA"}}


# ---------------------------------------------------------------------------
# Key probes
# ---------------------------------------------------------------------------


async def probe_serpapi(api_key: str) -> dict[str, Any]:
    """Validate a SerpAPI key against the free account endpoint.

    Returns ``{"ok": bool, "reason": str, "account": dict}``. Costs nothing —
    ``account.json`` does not count against the monthly search quota.
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                SERPAPI_ACCOUNT_URL,
                params={"api_key": api_key},
            )
            resp.raise_for_status()
            account: dict[str, Any] = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "ok": False,
            "reason": (
                f"SerpAPI account check could not complete "
                f"(HTTP {exc.response.status_code}). The key may be fine — "
                "SerpAPI servers may be temporarily unreachable."
            ),
            "account": {},
        }
    except Exception as exc:
        return {
            "ok": False,
            "reason": (
                f"SerpAPI account check could not complete: {exc}. The key may "
                "be fine — SerpAPI servers may be temporarily unreachable."
            ),
            "account": {},
        }

    if "error" in account:
        return {
            "ok": False,
            "reason": (
                f"SerpAPI key was rejected: {account['error']}. The key may be "
                f"invalid or expired — get a new one at {SERPAPI_SIGNUP_URL}"
            ),
            "account": account,
        }

    return {"ok": True, "reason": "", "account": account}


async def probe_maps(api_key: str) -> dict[str, Any]:
    """Validate a Google Maps key with one real Routes API call.

    Returns ``{"ok": bool, "reason": str}``. Costs a fraction of a cent against
    the key's quota — there is no free validation endpoint for Maps keys.
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                ROUTES_API_BASE,
                headers={
                    "X-Goog-Api-Key": api_key,
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
    except httpx.HTTPStatusError as exc:
        return {
            "ok": False,
            "reason": (
                f"Maps API check could not complete "
                f"(HTTP {exc.response.status_code}). The key may be rejected or "
                "the Routes API may not be enabled — verify the key at "
                f"{GOOGLE_CONSOLE_URL}"
            ),
        }
    except Exception as exc:
        return {
            "ok": False,
            "reason": (
                f"Maps API check could not complete: {exc}. The key may be "
                "fine — Google servers may be temporarily unreachable."
            ),
        }

    if not elements or not isinstance(elements, list):
        return {"ok": False, "reason": "Maps API returned unexpected response"}

    status = elements[0].get("status", {})
    if isinstance(status, dict) and status:
        return {"ok": False, "reason": f"Maps API error: {status}"}

    return {"ok": True, "reason": ""}


# ---------------------------------------------------------------------------
# check_setup
# ---------------------------------------------------------------------------


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
    accommodation_details_status: dict[str, Any] = {
        "tool": "get_accommodation_details",
        "ready": False,
    }
    events_status: dict[str, Any] = {
        "tool": "search_events",
        "ready": False,
    }
    things_to_do_status: dict[str, Any] = {
        "tool": "search_things_to_do",
        "ready": False,
    }
    car_rentals_status: dict[str, Any] = {
        "tool": "search_car_rentals",
        "ready": False,
    }
    driving_status: dict[str, Any] = {
        "tool": "compare_drive_or_fly",
        "ready": False,
    }

    # No key, no network, no quota — these are always usable, and saying so
    # matters: this is the "what can I use right now" surface, and a tool
    # missing from it reads as a tool that does not exist.
    keyless_statuses: list[dict[str, Any]] = [
        {"tool": "check_itinerary", "ready": True, "reason": "no API key required"},
        {"tool": "build_calendar", "ready": True, "reason": "no API key required"},
    ]

    serpapi_statuses = [
        flights_status,
        multi_city_status,
        accommodations_status,
        accommodation_details_status,
        cheapest_dates_status,
        events_status,
        things_to_do_status,
        car_rentals_status,
    ]

    # --- SerpAPI check (free --- does not count against monthly quota) ---
    serpapi_key = os.environ.get("SERPAPI_API_KEY", "")

    if not serpapi_key:
        for s in serpapi_statuses:
            s["reason"] = (
                "SERPAPI_API_KEY is not set — free key (100 searches/month) at "
                f"{SERPAPI_SIGNUP_URL}; see the `setup` field for the exact "
                "command to register it"
            )
    else:
        probe = await probe_serpapi(serpapi_key)
        if not probe["ok"]:
            for s in serpapi_statuses:
                s["reason"] = probe["reason"]
        else:
            account = probe["account"]
            for s in serpapi_statuses:
                s["ready"] = True
                s["plan_searches_left"] = account.get("plan_searches_left", "?")
                s["this_month_usage"] = account.get("this_month_usage", "?")
                s["total_searches_left"] = account.get("total_searches_left", "?")

            # Re-anchor the local quota estimate so tool warnings
            # use the latest real number.
            _refresh_quota_from_account(account)

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
        probe = await probe_maps(maps_key)
        driving_status["ready"] = probe["ok"]
        if not probe["ok"]:
            driving_status["reason"] = probe["reason"]

    result_tools = [
        flights_status,
        multi_city_status,
        accommodations_status,
        accommodation_details_status,
        cheapest_dates_status,
        events_status,
        things_to_do_status,
        car_rentals_status,
        driving_status,
        *keyless_statuses,
    ]

    # Build a human-readable summary, one line per tool.
    summary_lines: list[str] = []
    for t in result_tools:
        if t["ready"]:
            if t["tool"] == "compare_drive_or_fly":
                summary_lines.append(f"{t['tool']}: ready (Maps key valid)")
            elif "plan_searches_left" not in t:
                summary_lines.append(f"{t['tool']}: ready (no API key needed, costs nothing)")
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
        out["setup"] = setup_guide(need_serpapi=need_serpapi, need_maps=need_maps)

    return out


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(mcp: Any) -> None:
    """Register setup tool on a FastMCP instance."""
    mcp.tool()(check_setup)
