"""FastMCP server for cosmo-travel-mcp."""

from importlib.metadata import PackageNotFoundError, version

from fastmcp import FastMCP

try:
    __version__ = version("cosmo-travel-mcp")
except PackageNotFoundError:
    __version__ = "unknown"

_INSTRUCTIONS = """\
cosmo-travel-mcp bundles twelve travel-planning tools backed by licensed data
providers (SerpAPI for flights/hotels/events/places, Google Maps Routes API
for driving).

For a full trip — flights, lodging, what to do, and a day-by-day itinerary —
use the `plan_trip` prompt, which sequences the tools and explains how to
assemble the result.

Call `check_setup` first when a travel request needs a tool and setup hasn't
been confirmed this session — the SerpAPI half of that check is free (doesn't
count against the monthly search quota).

Env vars and which tools they gate:
- SERPAPI_API_KEY → search_flights, search_multi_city, search_accommodations,
  get_accommodation_details, search_cheapest_dates, search_events,
  search_things_to_do, search_car_rentals.
  Free key (100 searches/month):
  https://serpapi.com/users/sign_up
- GOOGLE_MAPS_API_KEY → compare_drive_or_fly. Create at
  https://console.cloud.google.com/ with the Routes API enabled.

If a key is missing, `check_setup` returns a `setup` field with the exact
commands to register it — relay those steps to the user rather than improvising
setup instructions.

Cost warning: search_cheapest_dates spends up to `max_calls` SerpAPI searches
per invocation (default 6, hard cap 15) against a 100/month free tier. Never
call it casually or in a loop — sample a small number of dates, not every day
in a window.

Round-trip flights are two-phase: phase-1 results are outbound options whose
prices are already round-trip totals. Pass a `departure_token` from one of
them back as a parameter to get the return legs for that outbound.

`search_car_rentals` returns offices, hours and contacts — never rates. No
free provider exposes car rental pricing, so hand the traveller the office's
`website` to quote it and treat the rate as unmeasured until they report one
back. Do not estimate a daily rate from this tool's output."""

mcp = FastMCP(
    "cosmo-travel-mcp",
    instructions=_INSTRUCTIONS,
    version=__version__,
)


def main() -> None:
    """Run the MCP server over stdio."""
    # Import and register tool modules.
    from .tools import (
        car_rentals,
        cheapest_dates,
        driving,
        events,
        flights,
        hotels,
        itinerary,
        places,
        prompts,
        setup,
    )

    flights.register(mcp)
    car_rentals.register(mcp)
    cheapest_dates.register(mcp)
    driving.register(mcp)
    events.register(mcp)
    hotels.register(mcp)
    itinerary.register(mcp)
    places.register(mcp)
    setup.register(mcp)
    prompts.register(mcp)

    mcp.run()
