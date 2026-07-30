"""Tests for MCP prompts (prompt 09)."""

from __future__ import annotations

import pytest

from cosmo_travel_mcp.tools.prompts import plan_trip

# ---------------------------------------------------------------------------
# Prompt registration
# ---------------------------------------------------------------------------


def test_plan_trip_is_callable():
    """plan_trip is a regular callable (decorator-wrapped by FastMCP)."""
    assert callable(plan_trip)


@pytest.mark.asyncio
async def test_plan_trip_registered_on_mcp():
    """The prompt is registered and discoverable on the server instance."""
    from cosmo_travel_mcp.server import mcp
    from cosmo_travel_mcp.tools import prompts as prompts_mod

    prompts_mod.register(mcp)
    prompts = await mcp.list_prompts()
    names = [p.name for p in prompts]
    assert "plan_trip" in names


# ---------------------------------------------------------------------------
# Rendered output
# ---------------------------------------------------------------------------


def test_plan_trip_with_full_args():
    """Rendering with representative arguments produces expected content."""
    result = plan_trip(
        origin="POA",
        destinations="New York, Orlando, Miami",
        dates="December to January",
        travelers=2,
    )

    assert isinstance(result, str)
    # Trip context
    assert "POA" in result
    assert "New York, Orlando, Miami" in result
    assert "December to January" in result
    assert "2" in result
    # Tools in flow order
    assert "check_setup" in result
    assert "search_cheapest_dates" in result
    assert "search_flights" in result
    assert "search_multi_city" in result
    assert "compare_drive_or_fly" in result
    assert "search_accommodations" in result
    # Cost caveat
    assert "100/month free tier" in result
    # Two-phase round-trip
    assert "departure_token" in result


def test_plan_trip_with_minimum_args():
    """Rendering with minimum arguments does not raise."""
    result = plan_trip()
    assert isinstance(result, str)
    assert "check_setup" in result
    assert "search_flights" in result
    # travelers defaults to 1 so context section is always rendered
