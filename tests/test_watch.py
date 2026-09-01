"""Tests for the watchlist runner in `skills/plan-a-trip/watch.py`.

The script is not part of the installed package — it is standard-library-only
so a scheduler can run it with the system Python — so it is loaded here by
path rather than imported.
"""

from __future__ import annotations

import importlib.util
import json
import sys
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


def test_probe_failed_has_its_own_exit_code(watch):
    """Failed probes must produce non-zero exit code (4) instead of quiet week (0)."""
    assert watch.EXIT_PROBE_FAILED == 4


def test_start_date_supports_dict_and_string_date_fields(watch):
    assert watch._start_date({"date": {"start_date": "Dec 31"}}) == "Dec 31"
    assert watch._start_date({"date": "Dec 31"}) == "Dec 31"
    assert watch._start_date({"date": None}) == ""


def test_event_id_supports_dict_and_string_date_fields(watch):
    legacy = {"title": "Concert", "date": {"start_date": "Dec 31"}}
    new_shape = {"title": "Concert", "date": "Dec 31"}

    assert watch.event_id(legacy) == "concert|Dec 31"
    assert watch.event_id(new_shape) == "concert|Dec 31"


def test_sweep_events_raises_on_api_error(watch, monkeypatch):
    """sweep_events must raise RuntimeError on API error rather than returning [] silently."""
    def fake_get(url, params):
        return {"error": "Unsupported search engine."}

    monkeypatch.setattr(watch, "get", fake_get)
    with pytest.raises(RuntimeError, match="Unsupported search engine"):
        watch.sweep_events("fake_key", {
            "query": "New York events",
            "window": {"from": "2026-12-30", "to": "2027-01-02"},
        })


# ---------------------------------------------------------------------------
# Integration tests for watch.main() exit codes and alert generation
# ---------------------------------------------------------------------------


def _setup_watchlist(tmp_path: Path, legs: list[dict] | None = None, event_watches: list[dict] | None = None) -> Path:
    wl = {
        "quota_reserve": 10,
        "legs": legs or [
            {
                "origin": "POA", "destination": "MIA",
                "outbound_date": "2026-12-20",
                "label": "POA → MIA",
                "baseline": {
                    "min": 5000, "max": 6000, "source": "price_history",
                    "low_band_ceiling": 5500, "captured_on": "2026-08-01",
                },
            }
        ],
        "event_watches": event_watches or [
            {
                "city": "New York", "query": "New York events",
                "window": {"from": "2026-12-30", "to": "2027-01-02"},
                "seen": [],
            }
        ],
    }
    wl_file = tmp_path / "watchlist.json"
    wl_file.write_text(json.dumps(wl, indent=2), encoding="utf-8")
    return wl_file


def test_main_all_probes_failed(watch, monkeypatch, tmp_path):
    """Scenario 1: All probes fail, no flight alerts -> exit code 4, failures recorded in alerts.md."""
    wl_file = _setup_watchlist(tmp_path)
    alerts_file = tmp_path / "alerts.md"
    log_file = tmp_path / "watch.log"

    monkeypatch.setattr(sys, "argv", ["watch.py", str(wl_file)])
    monkeypatch.setenv("SERPAPI_API_KEY", "fake-key")
    monkeypatch.setattr(watch, "searches_left", lambda key: 100)
    monkeypatch.setattr(watch, "ALERTS", alerts_file)
    monkeypatch.setattr(watch, "LOG", log_file)

    def failing_price_leg(key, leg):
        raise RuntimeError("Flight pricing failed upstream")

    def failing_sweep_events(key, watch_item):
        raise RuntimeError("Event sweep failed upstream")

    monkeypatch.setattr(watch, "price_leg", failing_price_leg)
    monkeypatch.setattr(watch, "sweep_events", failing_sweep_events)

    rc = watch.main()
    assert rc == 4, "All probes failing must return exit code 4 (EXIT_PROBE_FAILED)"

    assert alerts_file.exists(), "alerts.md must be written on failure"
    alerts_content = alerts_file.read_text(encoding="utf-8")
    assert "Sondagens com falha nesta rodada:" in alerts_content
    assert "Flight pricing failed upstream" in alerts_content
    assert "Event sweep failed upstream" in alerts_content


def test_main_clean_round(watch, monkeypatch, tmp_path):
    """Scenario 2: Clean round, no failures, price above ceiling -> exit code 0."""
    wl_file = _setup_watchlist(tmp_path)
    alerts_file = tmp_path / "alerts.md"
    log_file = tmp_path / "watch.log"

    monkeypatch.setattr(sys, "argv", ["watch.py", str(wl_file)])
    monkeypatch.setenv("SERPAPI_API_KEY", "fake-key")
    monkeypatch.setattr(watch, "searches_left", lambda key: 100)
    monkeypatch.setattr(watch, "ALERTS", alerts_file)
    monkeypatch.setattr(watch, "LOG", log_file)

    def quiet_price_leg(key, leg):
        return {
            "price": 5800,  # Above ceiling 5500 -> quiet
            "price_level": "NORMAL",
            "typical_price_range": [5000, 6000],
            "price_history": None,
        }

    def quiet_sweep_events(key, watch_item):
        return []  # No events found

    monkeypatch.setattr(watch, "price_leg", quiet_price_leg)
    monkeypatch.setattr(watch, "sweep_events", quiet_sweep_events)

    rc = watch.main()
    assert rc == 0, "Clean round with no alerts must return exit code 0"
    assert not alerts_file.exists(), "Clean round with no alerts must leave alerts.md untouched"


def test_main_partial_failure(watch, monkeypatch, tmp_path):
    """Scenario 3: Partial failure — one flight leg triggers strong alert, event sweep fails -> exit code 4 AND alert recorded."""
    wl_file = _setup_watchlist(tmp_path)
    alerts_file = tmp_path / "alerts.md"
    log_file = tmp_path / "watch.log"

    monkeypatch.setattr(sys, "argv", ["watch.py", str(wl_file)])
    monkeypatch.setenv("SERPAPI_API_KEY", "fake-key")
    monkeypatch.setattr(watch, "searches_left", lambda key: 100)
    monkeypatch.setattr(watch, "ALERTS", alerts_file)
    monkeypatch.setattr(watch, "LOG", log_file)

    def cheap_price_leg(key, leg):
        return {
            "price": 4800,  # Below min 5000 -> strong alert!
            "price_level": "LOW",
            "typical_price_range": [5000, 6000],
            "price_history": [[0, 5000]],
        }

    def failing_sweep_events(key, watch_item):
        raise RuntimeError("Event sweep 500 server error")

    monkeypatch.setattr(watch, "price_leg", cheap_price_leg)
    monkeypatch.setattr(watch, "sweep_events", failing_sweep_events)

    rc = watch.main()
    assert rc == 4, "Partial failure must return exit code 4"
    assert alerts_file.exists()

    content = alerts_file.read_text(encoding="utf-8")
    assert "Sondagens com falha nesta rodada:" in content
    assert "Event sweep 500 server error" in content
    assert "abaixo do piso medido" in content, "Purchase alert MUST be present in alerts.md even on partial failure"
