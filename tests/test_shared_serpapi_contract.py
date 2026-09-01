"""The shared ``_call_serpapi`` contract, exercised by every consuming module.

``_call_serpapi`` lives in ``tools.flights`` but is imported across modules by
``tools.hotels`` and ``tools.events``. When B-08 changed its return type from
``dict`` to ``(dict, from_cache)``, the flights-module call sites were updated
and the cross-module ones were not — every hotels and events call raised
``AttributeError: 'tuple' object has no attribute 'get'`` at runtime.

The unpacking half of that regression is caught incidentally by the existing
per-tool suites. The caching half is not: nothing asserted that a tool outside
``flights.py`` skips the second HTTP call or reports ``cached``. These tests
pin the whole contract at the seam, so the next change to the shared helper
fails here rather than in three unrelated files.
"""

from __future__ import annotations

import pytest
import respx
import httpx

from cosmo_travel_mcp.tools.flights import SERPAPI_BASE, _cache_key
from cosmo_travel_mcp.tools.events import search_events
from cosmo_travel_mcp.tools.hotels import search_accommodations, get_accommodation_details


@pytest.fixture(autouse=True)
def _set_fake_api_key(monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "fake-test-key")


@pytest.mark.asyncio
async def test_search_events_second_identical_call_is_served_from_cache():
    with respx.mock as mock:
        route = mock.get(SERPAPI_BASE).mock(
            return_value=httpx.Response(200, json={"events_results": []})
        )

        first = await search_events(query="concerts in New York")
        second = await search_events(query="concerts in New York")

    assert route.call_count == 1, "second identical events search must not re-request"
    assert "cached" not in first
    assert second["cached"] is True


@pytest.mark.asyncio
async def test_search_accommodations_second_identical_call_is_served_from_cache():
    with respx.mock as mock:
        route = mock.get(SERPAPI_BASE).mock(
            return_value=httpx.Response(200, json={"properties": []})
        )

        kwargs = dict(
            location="Paris",
            check_in_date="2026-09-01",
            check_out_date="2026-09-05",
        )
        first = await search_accommodations(**kwargs)
        second = await search_accommodations(**kwargs)

    assert route.call_count == 1, "second identical hotel search must not re-request"
    assert "cached" not in first
    assert second["cached"] is True


@pytest.mark.asyncio
async def test_get_accommodation_details_second_identical_call_is_served_from_cache():
    with respx.mock as mock:
        route = mock.get(SERPAPI_BASE).mock(
            return_value=httpx.Response(200, json={"name": "Hotel Test"})
        )

        kwargs = dict(
            property_token="tok-123",
            location="Paris",
            check_in_date="2026-09-01",
            check_out_date="2026-09-05",
        )
        first = await get_accommodation_details(**kwargs)
        second = await get_accommodation_details(**kwargs)

    assert route.call_count == 1, "second identical details lookup must not re-request"
    assert "cached" not in first
    assert second["cached"] is True


def test_engine_discriminates_the_cache_key():
    """``engine`` must key the cache, and the API key must not.

    Hotels and events are distinct engines reached through the same helper and
    the same base URL, so a key that ignored ``engine`` would serve one the
    other's body. Asserting this through the public tools would not isolate it
    — those calls differ in several params — so compare keys directly.
    """
    base = {"q": "Paris", "api_key": "k1"}

    events_key = _cache_key({**base, "engine": "google_events"})
    hotels_key = _cache_key({**base, "engine": "google_hotels"})
    assert events_key != hotels_key, "engine must discriminate the cache key"

    rotated_key = _cache_key({**base, "engine": "google_events", "api_key": "k2"})
    assert rotated_key == events_key, "the API key must not be part of the cache key"


@pytest.mark.asyncio
async def test_call_serpapi_redacts_api_key_on_http_error(monkeypatch):
    """HTTP errors must never leak the raw API key in exception strings or request URLs."""
    from cosmo_travel_mcp.tools.flights import _call_serpapi

    secret_key = "super-secret-api-key-12345"
    monkeypatch.setenv("SERPAPI_API_KEY", secret_key)
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(status_code=400, text="Bad request")
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await _call_serpapi({"q": "test"}, engine="google")

    err_str = str(exc_info.value)
    url_str = str(exc_info.value.request.url)
    assert secret_key not in err_str, "API key must be redacted from exception message"
    assert secret_key not in url_str, "API key must be redacted from exception request URL"
    assert "api_key=***" in err_str
    assert "api_key=***" in url_str
