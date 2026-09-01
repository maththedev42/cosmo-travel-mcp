"""Shared test configuration."""

from __future__ import annotations

import os
import tempfile

import pytest
import respx

from cosmo_travel_mcp.tools import flights
from cosmo_travel_mcp.tools.flights import _reset_cache, _reset_quota_counter

# A REAL environment mutation, set once for the whole pytest process — not
# via `monkeypatch`, which is function-scoped and undone at each test's
# teardown. `_engine_errors_path()` defaults to
# `~/.cosmo-travel/engine_errors.json`, the live watch's own state directory
# (alongside `watchlist-eua-2026.json`, `alerts.md`), and running the suite
# must never be able to read or write it.
#
# A per-test `monkeypatch.setattr` on `_engine_errors_path` was tried first
# and looked correct on paper — teardown order should keep the patch active
# through `_reset_engine_errors()`'s own teardown call, since fixtures that
# depend on `monkeypatch` tear down before `monkeypatch` itself does. Measured
# instead: instrumenting every fixture boundary with prints across the full
# suite showed the real file vanishing between two tests' fixture teardowns,
# not inside either one — some interaction in that boundary the analysis
# above did not predict. A session-wide env var sidesteps the question of
# fixture teardown ordering entirely: it is simply still set no matter what
# runs when, so it needed no further root-causing to trust.
os.environ["COSMO_TRAVEL_STATE_DIR"] = tempfile.mkdtemp(prefix="cosmo-travel-test-state-")


@pytest.fixture(autouse=True)
def _reset_engine_errors_state():
    """Clear engine-error state between tests.

    The directory itself is fixed for the whole session (see the module-level
    `COSMO_TRAVEL_STATE_DIR` above); this only clears the in-memory cache and
    that session's file between tests, so a failure recorded by one test does
    not leak into the next.
    """
    flights._reset_engine_errors()
    yield
    flights._reset_engine_errors()


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
