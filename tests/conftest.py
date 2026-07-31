"""Shared test configuration."""

from __future__ import annotations

import pytest
import respx


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
