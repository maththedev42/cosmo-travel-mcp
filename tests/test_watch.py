"""Tests for the watchlist runner in `skills/plan-a-trip/watch.py`.

The script is not part of the installed package — it is standard-library-only
so a scheduler can run it with the system Python — so it is loaded here by
path rather than imported.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

WATCH_PY = Path(__file__).resolve().parents[1] / "skills" / "plan-a-trip" / "watch.py"


def _load():
    spec = importlib.util.spec_from_file_location("watch_script", WATCH_PY)
    assert spec and spec.loader, f"could not load {WATCH_PY}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def watch():
    return _load()


# ---------------------------------------------------------------------------
# legs_to_watch
# ---------------------------------------------------------------------------


def test_a_leg_with_no_flags_is_watched(watch):
    """Omission must never drop a leg — the default has to be "watch it"."""
    assert watch.legs_to_watch([{"label": "POA → MIA"}]) == [{"label": "POA → MIA"}]


def test_purchased_legs_are_dropped(watch):
    legs = [{"label": "a", "purchased": True}, {"label": "b", "purchased": False}]

    assert [l["label"] for l in watch.legs_to_watch(legs)] == ["b"]


def test_watch_false_drops_a_leg_settled_without_a_ticket(watch):
    """The Miami → Orlando case: decided by renting a car, flight kept as fallback.

    Before this existed the only exit was `purchased`, so a leg nobody intended
    to fly was still re-priced every week and still cost a search.
    """
    legs = [
        {"label": "MIA → MCO", "purchased": False, "watch": False},
        {"label": "MCO → JFK", "purchased": False},
    ]

    assert [l["label"] for l in watch.legs_to_watch(legs)] == ["MCO → JFK"]


def test_watch_true_is_redundant_but_honoured(watch):
    legs = [{"label": "a", "watch": True}]

    assert watch.legs_to_watch(legs) == legs


def test_purchased_wins_over_watch_true(watch):
    """A bought leg is done, whatever the watch flag says."""
    legs = [{"label": "a", "purchased": True, "watch": True}]

    assert watch.legs_to_watch(legs) == []


# ---------------------------------------------------------------------------
# The skip has to be visible
# ---------------------------------------------------------------------------


def test_quota_skip_has_its_own_exit_code(watch):
    """A skip and a quiet week both leave alerts.md untouched.

    Returning 0 for both is what let the watch sit dead for weeks while the
    scheduler reported success: `semanal.sh` only checked the exit code and the
    file size, and a skip moves neither.
    """
    assert watch.EXIT_SKIPPED_QUOTA == 3
    assert watch.EXIT_SKIPPED_QUOTA != 0
