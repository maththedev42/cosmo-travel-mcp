"""Tests for the pure trip-window math (no network).

The golden case is the 2026-08-20 measurement that started this feature: a
fixed-date concert (System of a Down at Maracanã, night of Fri 15 Jan 2027)
from POA, 2 passengers, direct. The window with the extra night was R$ 320
cheaper on airfare (R$ 1.595 over 3 nights vs R$ 1.915 over 2 nights), and
whether the trip was cheaper overall depended on what that night cost —
break-even was R$ 320/night.
"""

from __future__ import annotations

from datetime import date

import pytest

from cosmo_travel_mcp.tools.trip_windows import _combine, _generate_windows, _rank


def _window(depart, return_date):
    return {
        "depart": depart,
        "return": return_date,
        "nights": (return_date - depart).days,
    }


# ---------------------------------------------------------------------------
# Anchor rule
# ---------------------------------------------------------------------------


def test_window_returning_on_anchor_date_is_excluded():
    """A window that returns on the anchor date means flying home the day of
    the event — a different trip, not a cheaper option."""
    anchor = date(2027, 1, 15)
    windows = _generate_windows(
        anchor_date=anchor,
        earliest_depart=date(2027, 1, 13),
        latest_return=date(2027, 1, 16),
        min_nights=2,
        max_nights=4,
        max_windows=10,
    )
    # Without the guard, depart 13 -> return 15 (2 nights, return == anchor)
    # would qualify. It must not appear.
    assert windows, "expected at least some qualifying windows"
    for w in windows:
        assert w["return"] > anchor
    assert _window(date(2027, 1, 13), date(2027, 1, 15)) not in windows


def test_window_departing_after_anchor_date_is_excluded():
    """Departing after the anchor date means missing the anchor night."""
    anchor = date(2027, 1, 15)
    windows = _generate_windows(
        anchor_date=anchor,
        earliest_depart=date(2027, 1, 14),
        latest_return=date(2027, 1, 19),
        min_nights=2,
        max_nights=4,
        max_windows=10,
    )
    # Without the guard, depart 16 -> return 18 (2 nights) would qualify even
    # though the traveller leaves after the event night.
    for w in windows:
        assert w["depart"] <= anchor
    assert _window(date(2027, 1, 16), date(2027, 1, 18)) not in windows


def test_anchor_night_is_always_covered():
    """Every qualifying window spends the anchor night in the destination."""
    anchor = date(2027, 1, 15)
    windows = _generate_windows(
        anchor_date=anchor,
        earliest_depart=date(2027, 1, 10),
        latest_return=date(2027, 1, 20),
        min_nights=2,
        max_nights=6,
        max_windows=10,
    )
    assert windows
    for w in windows:
        assert w["depart"] <= anchor < w["return"]


# ---------------------------------------------------------------------------
# Nights are derived, never typed
# ---------------------------------------------------------------------------


def test_nights_derived_from_span():
    """nights always equals return - depart; there is no parameter for it."""
    anchor = date(2027, 1, 15)
    windows = _generate_windows(
        anchor_date=anchor,
        earliest_depart=date(2027, 1, 12),
        latest_return=date(2027, 1, 18),
        min_nights=2,
        max_nights=4,
        max_windows=10,
    )
    assert windows
    for w in windows:
        assert w["nights"] == (w["return"] - w["depart"]).days


def test_nights_respect_min_and_max():
    anchor = date(2027, 1, 15)
    windows = _generate_windows(
        anchor_date=anchor,
        earliest_depart=date(2027, 1, 12),
        latest_return=date(2027, 1, 19),
        min_nights=3,
        max_nights=4,
        max_windows=10,
    )
    assert windows
    for w in windows:
        assert 3 <= w["nights"] <= 4


# ---------------------------------------------------------------------------
# max_windows truncation
# ---------------------------------------------------------------------------


def test_max_windows_keeps_windows_nearest_to_anchor():
    anchor = date(2027, 1, 15)
    windows = _generate_windows(
        anchor_date=anchor,
        earliest_depart=date(2027, 1, 12),
        latest_return=date(2027, 1, 19),
        min_nights=2,
        max_nights=4,
        max_windows=2,
    )
    assert len(windows) == 2
    # Nearest first: the 2-night window departing on the anchor date
    # (15 -> 17), then the 2-night window arriving the night before (14 -> 16).
    assert windows[0] == _window(date(2027, 1, 15), date(2027, 1, 17))
    assert windows[1] == _window(date(2027, 1, 14), date(2027, 1, 16))


def test_max_windows_zero_returns_nothing():
    anchor = date(2027, 1, 15)
    windows = _generate_windows(
        anchor_date=anchor,
        earliest_depart=date(2027, 1, 14),
        latest_return=date(2027, 1, 17),
        min_nights=2,
        max_nights=4,
        max_windows=0,
    )
    assert windows == []


# ---------------------------------------------------------------------------
# _combine
# ---------------------------------------------------------------------------


def test_combine_sums_parts():
    window = _window(date(2027, 1, 14), date(2027, 1, 17))
    combined = _combine(window, flights_total=1595, lodging_total=923)
    assert combined["flights_total"] == 1595
    assert combined["lodging_total"] == 923
    assert combined["combined_total"] == 2518
    assert combined["nights"] == 3  # original window fields preserved


def test_combine_raises_when_parts_do_not_sum_to_total():
    """A total that disagrees with its parts fails loudly rather than renders."""
    window = _window(date(2027, 1, 14), date(2027, 1, 17))
    # Already annotated with a total that does not match the incoming parts.
    stale = {**window, "combined_total": 1000}
    with pytest.raises(AssertionError):
        _combine(stale, flights_total=1595, lodging_total=923)


# ---------------------------------------------------------------------------
# _rank
# ---------------------------------------------------------------------------


def test_rank_breaks_even_only_with_more_nights():
    """break_even_nightly is None at equal nights, correct otherwise."""
    winner = _combine(
        _window(date(2027, 1, 14), date(2027, 1, 16)),
        flights_total=2000,
        lodging_total=0,
    )
    # 4 nights, saves 500 on airfare against the winner, but the two extra
    # nights cost 600 -> loses overall; break-even is 500 / 2 = 250.0.
    longer = _combine(
        _window(date(2027, 1, 12), date(2027, 1, 16)),
        flights_total=1500,
        lodging_total=600,
    )
    ranked = _rank([longer, winner])

    assert ranked[0] is winner
    assert winner["delta_vs_best"] == 0
    assert winner["break_even_nightly"] is None
    assert longer["delta_vs_best"] == 100
    assert longer["break_even_nightly"] == 250.0


def test_rank_orders_by_combined_total_not_airfare():
    """The lower combined_total ranks first even when its airfare is higher.

    This is the whole point of the tool: a date window is only cheaper once
    the extra nights are paid for. The 2-night window costs more on airfare
    but wins on combined because the longer window's lodging exceeds the
    saving.
    """
    two_night = _combine(
        _window(date(2027, 1, 15), date(2027, 1, 17)),
        flights_total=1915,
        lodging_total=0,
    )
    three_night = _combine(
        _window(date(2027, 1, 14), date(2027, 1, 17)),
        flights_total=1595,
        lodging_total=500,
    )
    ranked = _rank([three_night, two_night])
    assert ranked[0]["combined_total"] == 1915
    assert ranked[1]["combined_total"] == 2095


# ---------------------------------------------------------------------------
# Golden case from 2026-08-20
# ---------------------------------------------------------------------------


def test_golden_case_soad_concert_break_even():
    """The measurement that started this feature, as a regression.

    System of a Down at Maracanã, anchor night Fri 15 Jan 2027, from POA,
    2 passengers, direct. Three windows were priced: R$ 1.595 over 3 nights,
    R$ 1.915 over 2 nights (x2). The window with the extra night was R$ 320
    cheaper on airfare; the break-even for that night is 320.0 — real
    Botafogo rooms ranged R$ 231–447, so the same trip was cheaper or more
    expensive depending on the hotel, not the date.
    """
    anchor = date(2027, 1, 15)
    windows = _generate_windows(
        anchor_date=anchor,
        earliest_depart=date(2027, 1, 14),
        latest_return=date(2027, 1, 17),
        min_nights=2,
        max_nights=4,
        max_windows=3,
    )
    # The measured windows: two 2-night at 1915, one 3-night at 1595.
    assert len(windows) == 3
    flights = {2: 1915, 3: 1595}
    # Price the 3-night window's extra night at 500 (> break-even), so the
    # shorter window is the winner and the longer one is the interesting row.
    lodging = {2: 0, 3: 500}
    combined = [_combine(w, flights[w["nights"]], lodging[w["nights"]]) for w in windows]
    ranked = _rank(combined)

    assert ranked[0]["combined_total"] == 1915
    assert ranked[0]["delta_vs_best"] == 0
    assert ranked[0]["break_even_nightly"] is None

    longer = next(w for w in ranked if w["nights"] == 3)
    assert longer["combined_total"] == 1595 + 500
    assert longer["flights_total"] == 1595
    assert longer["delta_vs_best"] == 2095 - 1915
    assert longer["break_even_nightly"] == 320.0
