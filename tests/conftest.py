"""Shared test configuration."""

from __future__ import annotations

import pytest
import respx

from cosmo_travel_mcp.tools.flights import _reset_cache, _reset_quota_counter


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Reset the module-level response cache and quota counter between tests.

    Both live in ``tools.flights`` as process-lifetime state, which is correct
    for a running server but makes tests order-dependent: without this, a
    cached response from an earlier test is served to a later one and the
    later test's ``respx`` router never sees a request. ``test_flights.py``
    reset them in a module-local fixture; every other module that reaches
    SerpAPI needs the same isolation, so it belongs here.
    """
    _reset_cache()
    _reset_quota_counter()
    yield


@pytest.fixture(autouse=True)
def _no_real_network():
    """Fail rather than let any test reach the network.

    Validation tests call the tools inside ``pytest.raises`` without mocking,
    on the assumption that a guard fires before the HTTP call. That holds
    today, but if a guard ever regresses those tests would quietly issue real
    SerpAPI requests and spend quota. A router wrapping every test turns an
    unmocked request into an error instead of a live call; tests that install
    their own ``respx.mock`` nest inside it unaffected.
    """
    with respx.mock(assert_all_called=False):
        yield
