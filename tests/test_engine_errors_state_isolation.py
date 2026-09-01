"""The test suite must never read or write the live watch's state directory.

Reproduces the incident measured 2026-08-31: `_ENGINE_ERRORS_FILE` was a fixed
`Path.home() / ".cosmo-travel" / "engine_errors.json"` constant — the same
directory `watch.py` uses for `watchlist-eua-2026.json` and `alerts.md`.
Planting a canary there and running `pytest` overwrote it with a mock's
fabricated 400, and the next test's `_reset_engine_errors()` deleted it. A
`check_setup` call afterward reported a healthy engine as broken.

These tests do not rely on the autouse `_isolate_engine_errors_file` fixture
in conftest.py catching a regression silently — they inspect the real home
path directly, so a change that removes or narrows the fixture fails loudly
here instead of only becoming visible when someone's real file disappears.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cosmo_travel_mcp.tools import flights


def _real_production_path() -> Path:
    """The path `_engine_errors_path()` resolves to with no monkeypatch and
    no `COSMO_TRAVEL_STATE_DIR` override — i.e. what a real deployment uses."""
    return Path.home() / ".cosmo-travel" / "engine_errors.json"


def test_engine_errors_path_is_isolated_during_tests():
    """The autouse fixture must actually be active, not merely present."""
    resolved = flights._engine_errors_path()
    assert resolved != _real_production_path(), (
        "_engine_errors_path() resolved to the real home directory during a "
        "test run — the isolation fixture in conftest.py is not taking effect."
    )


def test_suite_does_not_touch_a_real_canary_file(tmp_path, monkeypatch):
    """Direct reproduction of the 2026-08-31 incident, run against the REAL
    production path rather than the monkeypatched one the other tests use."""
    real_path = _real_production_path()
    canary = '{"canario": "nao me sobrescreva"}'

    pre_existed = real_path.exists()
    pre_content = real_path.read_text(encoding="utf-8") if pre_existed else None

    real_path.parent.mkdir(parents=True, exist_ok=True)
    real_path.write_text(canary, encoding="utf-8")

    try:
        # Exercise exactly the operations a running server performs on a
        # failed and then a cleared engine call — through the *isolated*
        # path the conftest fixture installs, not this test's own patch.
        flights._record_engine_error("google", "fabricated by a test mock")
        flights._clear_engine_error("google")
        flights._reset_engine_errors()

        assert real_path.exists(), (
            "the canary file was deleted from the real ~/.cosmo-travel/ "
            "directory by test-suite activity"
        )
        assert real_path.read_text(encoding="utf-8") == canary, (
            "the canary file was overwritten in the real ~/.cosmo-travel/ "
            "directory by test-suite activity"
        )
    finally:
        if pre_existed:
            real_path.write_text(pre_content, encoding="utf-8")
        else:
            real_path.unlink(missing_ok=True)


def test_reset_engine_errors_only_unlinks_the_resolved_path(tmp_path, monkeypatch):
    """`_reset_engine_errors()` must never delete a file it did not resolve to."""
    other_file = tmp_path / "someone_elses_state.json"
    other_file.write_text("not engine_errors.json", encoding="utf-8")

    isolated = tmp_path / "engine_errors.json"
    monkeypatch.setattr(flights, "_engine_errors_path", lambda: isolated)
    isolated.write_text('{"google": ["x", "2026-01-01"]}', encoding="utf-8")

    flights._reset_engine_errors()

    assert not isolated.exists()
    assert other_file.exists()
    assert other_file.read_text(encoding="utf-8") == "not engine_errors.json"
