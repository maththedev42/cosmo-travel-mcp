"""Tests for the trip ledger in `skills/plan-a-trip/ledger.py`."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

LEDGER_PY = Path(__file__).resolve().parents[1] / "skills" / "plan-a-trip" / "ledger.py"
WATCH_PY = Path(__file__).resolve().parents[1] / "skills" / "plan-a-trip" / "watch.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"could not load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ledger():
    return _load_module("ledger_script", LEDGER_PY)


@pytest.fixture(scope="module")
def watch_mod():
    return _load_module("watch_script", WATCH_PY)


@pytest.fixture(autouse=True)
def isolate_state_dir(monkeypatch, tmp_path):
    """Rule 9: Tests must never touch the real state directory (~/.cosmo-travel)."""
    monkeypatch.setenv("COSMO_TRAVEL_STATE_DIR", str(tmp_path))


def _sample_watchlist(tmp_path: Path) -> Path:
    wl = {
        "trip": "EUA 2026",
        "last_run": "2026-09-20",
        "legs": [
            {
                "label": "GRU → MIA",
                "origin": "GRU,CGH",
                "destination": "MIA,FLL",
                "outbound_date": "2026-11-05",
                "adults": 2,
                "purchased": False,
                "watch": True,
            },
            {
                "label": "MIA → GRU",
                "origin": "MIA,FLL",
                "destination": "GRU",
                "outbound_date": "2026-11-20",
                "adults": 2,
                "purchased": False,
                "watch": True,
            },
        ],
    }
    wl_path = tmp_path / "watchlist-test.json"
    wl_path.write_text(json.dumps(wl, indent=2, ensure_ascii=False), encoding="utf-8")
    return wl_path


def _valid_purchase_data() -> dict:
    return {
        "date": "2026-10-01",
        "seller": "LATAM Airlines",
        "locator": "ABC123XYZ",
        "adults": 2,
        "segments": [
            {
                "flight": "LA 8180",
                "from": "GRU",
                "to": "MIA",
                "depart": "2026-11-05T10:30",
                "arrive": "2026-11-05T17:45",
            }
        ],
        "paid": {"amount": 5432.10, "currency": "BRL"},
        "source": "confirmation email read 2026-10-01",
        "open_issues": [],
        "notes": "",
        "extra": {},
    }


# ---------------------------------------------------------------------------
# Validation Item 1: Allowed and required keys
# ---------------------------------------------------------------------------


def test_validation_unknown_keys_and_missing_required_keys(ledger, tmp_path, capsys):
    wl_path = _sample_watchlist(tmp_path)

    # Unknown key
    bad_data = _valid_purchase_data()
    bad_data["bogus_key"] = "surprise"
    data_file = tmp_path / "data1.json"
    data_file.write_text(json.dumps(bad_data), encoding="utf-8")

    code = ledger.main(["purchase", str(wl_path), "--leg", "0", "--data", str(data_file)])
    assert code == 2
    err = capsys.readouterr().err
    assert "unknown key(s) in purchase: bogus_key" in err
    assert "Allowed keys:" in err

    # Missing required key
    bad_data2 = _valid_purchase_data()
    del bad_data2["seller"]
    data_file2 = tmp_path / "data2.json"
    data_file2.write_text(json.dumps(bad_data2), encoding="utf-8")

    code = ledger.main(["purchase", str(wl_path), "--leg", "0", "--data", str(data_file2)])
    assert code == 2
    err2 = capsys.readouterr().err
    assert "missing required key(s) in purchase: seller" in err2


# ---------------------------------------------------------------------------
# Validation Item 2: Date validation
# ---------------------------------------------------------------------------


def test_validation_date_format(ledger, tmp_path, capsys):
    wl_path = _sample_watchlist(tmp_path)

    bad_data = _valid_purchase_data()
    bad_data["date"] = "01/10/2026"
    df = tmp_path / "bad_date.json"
    df.write_text(json.dumps(bad_data), encoding="utf-8")

    code = ledger.main(["purchase", str(wl_path), "--leg", "0", "--data", str(df)])
    assert code == 2
    err = capsys.readouterr().err
    assert "date must be a valid ISO date" in err


# ---------------------------------------------------------------------------
# Validation Item 3: Adults validation
# ---------------------------------------------------------------------------


def test_validation_adults_mismatch(ledger, tmp_path, capsys):
    wl_path = _sample_watchlist(tmp_path)

    bad_data = _valid_purchase_data()
    bad_data["adults"] = 1  # leg expects 2
    df = tmp_path / "bad_adults.json"
    df.write_text(json.dumps(bad_data), encoding="utf-8")

    code = ledger.main(["purchase", str(wl_path), "--leg", "0", "--data", str(df)])
    assert code == 2
    err = capsys.readouterr().err
    assert "adults (1) must match leg adults (2)" in err


# ---------------------------------------------------------------------------
# Validation Item 4: Segments and null unmeasured_why
# ---------------------------------------------------------------------------


def test_validation_segments_null_handling(ledger, tmp_path, capsys):
    wl_path = _sample_watchlist(tmp_path)

    # Null depart without unmeasured_why is error
    bad_data = _valid_purchase_data()
    bad_data["segments"][0]["depart"] = None
    df = tmp_path / "bad_seg1.json"
    df.write_text(json.dumps(bad_data), encoding="utf-8")

    code = ledger.main(["purchase", str(wl_path), "--leg", "0", "--data", str(df)])
    assert code == 2
    err = capsys.readouterr().err
    assert "missing non-empty 'unmeasured_why'" in err

    # Null depart with unmeasured_why passes
    good_data = _valid_purchase_data()
    good_data["segments"][0]["depart"] = None
    good_data["segments"][0]["unmeasured_why"] = "airline email only provided arrival time"
    df2 = tmp_path / "good_seg1.json"
    df2.write_text(json.dumps(good_data), encoding="utf-8")

    code2 = ledger.main(["purchase", str(wl_path), "--leg", "0", "--data", str(df2)])
    assert code2 == 0

    # Non-null depart/arrive with unmeasured_why is error
    bad_data2 = _valid_purchase_data()
    bad_data2["segments"][0]["unmeasured_why"] = "not needed here"
    df3 = tmp_path / "bad_seg2.json"
    df3.write_text(json.dumps(bad_data2), encoding="utf-8")

    code3 = ledger.main(["purchase", str(wl_path), "--leg", "1", "--data", str(df3)])
    assert code3 == 2
    err3 = capsys.readouterr().err
    assert "'unmeasured_why' is not allowed" in err3


# ---------------------------------------------------------------------------
# Validation Item 5: Origin and destination check
# ---------------------------------------------------------------------------


def test_validation_origin_destination_mismatch(ledger, tmp_path, capsys):
    wl_path = _sample_watchlist(tmp_path)

    bad_data = _valid_purchase_data()
    bad_data["segments"][0]["from"] = "BSB"  # leg origin is GRU,CGH
    df = tmp_path / "bad_origin.json"
    df.write_text(json.dumps(bad_data), encoding="utf-8")

    code = ledger.main(["purchase", str(wl_path), "--leg", "0", "--data", str(df)])
    assert code == 2
    err = capsys.readouterr().err
    assert "first segment 'from' (BSB) not in leg origin (GRU,CGH)" in err

    bad_data2 = _valid_purchase_data()
    bad_data2["segments"][0]["to"] = "JFK"  # leg destination is MIA,FLL
    df2 = tmp_path / "bad_dest.json"
    df2.write_text(json.dumps(bad_data2), encoding="utf-8")

    code2 = ledger.main(["purchase", str(wl_path), "--leg", "0", "--data", str(df2)])
    assert code2 == 2
    err2 = capsys.readouterr().err
    assert "last segment 'to' (JFK) not in leg destination (MIA,FLL)" in err2


# ---------------------------------------------------------------------------
# Validation Item 6: Consecutive segments comparison and airport change warning
# ---------------------------------------------------------------------------


def test_validation_consecutive_segments_order_and_warning(ledger, tmp_path, capsys):
    wl = {
        "legs": [
            {
                "label": "GRU → JFK",
                "origin": "GRU",
                "destination": "JFK",
                "outbound_date": "2026-11-05",
                "adults": 1,
                "purchased": False,
            }
        ]
    }
    wl_path = tmp_path / "wl_multi.json"
    wl_path.write_text(json.dumps(wl), encoding="utf-8")

    # Depart earlier than arrive at connection airport (PTY)
    bad_multi = {
        "date": "2026-10-01",
        "seller": "Copa",
        "locator": "COP123",
        "adults": 1,
        "segments": [
            {
                "flight": "CM 101",
                "from": "GRU",
                "to": "PTY",
                "depart": "2026-11-05T01:30",
                "arrive": "2026-11-05T06:45",
            },
            {
                "flight": "CM 202",
                "from": "PTY",
                "to": "JFK",
                "depart": "2026-11-05T05:00",  # 05:00 earlier than 06:45!
                "arrive": "2026-11-05T11:00",
            },
        ],
        "paid": {"amount": 2500.0, "currency": "BRL"},
        "source": "email",
    }
    df = tmp_path / "bad_multi.json"
    df.write_text(json.dumps(bad_multi), encoding="utf-8")

    code = ledger.main(["purchase", str(wl_path), "--leg", "0", "--data", str(df)])
    assert code == 2
    err = capsys.readouterr().err
    assert "depart (2026-11-05T05:00) is earlier than segment 0 arrive (2026-11-05T06:45)" in err

    # Airport change warning: LGA to JFK
    wl_warning = {
        "legs": [
            {
                "label": "GRU → BOS",
                "origin": "GRU",
                "destination": "BOS",
                "outbound_date": "2026-11-05",
                "adults": 1,
                "purchased": False,
            }
        ]
    }
    wl_path2 = tmp_path / "wl_warn.json"
    wl_path2.write_text(json.dumps(wl_warning), encoding="utf-8")

    warn_data = {
        "date": "2026-10-01",
        "seller": "Delta",
        "locator": "DL123",
        "adults": 1,
        "segments": [
            {
                "flight": "DL 1",
                "from": "GRU",
                "to": "JFK",
                "depart": "2026-11-05T10:00",
                "arrive": "2026-11-05T18:00",
            },
            {
                "flight": "DL 2",
                "from": "LGA",  # airport switch!
                "to": "BOS",
                "depart": "2026-11-05T21:00",
                "arrive": "2026-11-05T22:15",
            },
        ],
        "paid": {"amount": 3000.0, "currency": "BRL"},
        "source": "email",
    }
    df2 = tmp_path / "warn_data.json"
    df2.write_text(json.dumps(warn_data), encoding="utf-8")

    code2 = ledger.main(["purchase", str(wl_path2), "--leg", "0", "--data", str(df2)])
    assert code2 == 0
    out = json.loads(capsys.readouterr().out)
    assert any("consecutive segments change airport from JFK to LGA" in w for w in out["warnings"])


# ---------------------------------------------------------------------------
# Validation Item 7: Paid rules (amount > 0, null with why, included_in_leg)
# ---------------------------------------------------------------------------


def test_validation_paid_amount_zero_is_rejected(ledger, tmp_path, capsys):
    wl_path = _sample_watchlist(tmp_path)

    bad_data = _valid_purchase_data()
    bad_data["paid"] = {"amount": 0, "currency": "BRL"}
    df = tmp_path / "zero.json"
    df.write_text(json.dumps(bad_data), encoding="utf-8")

    code = ledger.main(["purchase", str(wl_path), "--leg", "0", "--data", str(df)])
    assert code == 2
    err = capsys.readouterr().err
    assert "valor ausente se registra como null com motivo" in err


def test_validation_paid_null_with_and_without_why(ledger, tmp_path, capsys):
    wl_path = _sample_watchlist(tmp_path)

    bad_data = _valid_purchase_data()
    bad_data["paid"] = None
    # No paid_unmeasured_why
    df = tmp_path / "null_no_why.json"
    df.write_text(json.dumps(bad_data), encoding="utf-8")

    code = ledger.main(["purchase", str(wl_path), "--leg", "0", "--data", str(df)])
    assert code == 2
    err = capsys.readouterr().err
    assert "paid is null: non-empty 'paid_unmeasured_why' is required" in err

    # With paid_unmeasured_why -> passes
    good_data = _valid_purchase_data()
    good_data["paid"] = None
    good_data["paid_unmeasured_why"] = "company paid directly, receipt not sent to traveler"
    df2 = tmp_path / "null_why.json"
    df2.write_text(json.dumps(good_data), encoding="utf-8")

    code2 = ledger.main(["purchase", str(wl_path), "--leg", "0", "--data", str(df2)])
    assert code2 == 0


def test_validation_included_in_leg_different_locator(ledger, tmp_path, capsys):
    wl_path = _sample_watchlist(tmp_path)

    # First purchase leg 0
    leg0_data = _valid_purchase_data()
    df0 = tmp_path / "leg0.json"
    df0.write_text(json.dumps(leg0_data), encoding="utf-8")
    assert ledger.main(["purchase", str(wl_path), "--leg", "0", "--data", str(df0)]) == 0
    capsys.readouterr()

    # Now attempt leg 1 with included_in_leg: 0, but different locator
    leg1_data = {
        "date": "2026-10-01",
        "seller": "LATAM Airlines",
        "locator": "DIFFERENT_LOC",
        "adults": 2,
        "segments": [
            {
                "flight": "LA 8181",
                "from": "MIA",
                "to": "GRU",
                "depart": "2026-11-20T19:00",
                "arrive": "2026-11-21T06:00",
            }
        ],
        "paid": {"included_in_leg": 0},
        "source": "email",
    }
    df1 = tmp_path / "leg1.json"
    df1.write_text(json.dumps(leg1_data), encoding="utf-8")

    code = ledger.main(["purchase", str(wl_path), "--leg", "1", "--data", str(df1)])
    assert code == 2
    err = capsys.readouterr().err
    assert "locator mismatch with target leg 0" in err


def test_validation_included_in_leg_success(ledger, tmp_path, capsys):
    wl_path = _sample_watchlist(tmp_path)

    # First purchase leg 0
    leg0_data = _valid_purchase_data()
    df0 = tmp_path / "leg0.json"
    df0.write_text(json.dumps(leg0_data), encoding="utf-8")
    assert ledger.main(["purchase", str(wl_path), "--leg", "0", "--data", str(df0)]) == 0
    capsys.readouterr()

    # Leg 1 with matching locator and included_in_leg
    leg1_data = {
        "date": "2026-10-01",
        "seller": "LATAM Airlines",
        "locator": "ABC123XYZ",
        "adults": 2,
        "segments": [
            {
                "flight": "LA 8181",
                "from": "MIA",
                "to": "GRU",
                "depart": "2026-11-20T19:00",
                "arrive": "2026-11-21T06:00",
            }
        ],
        "paid": {"included_in_leg": 0},
        "source": "email",
    }
    df1 = tmp_path / "leg1.json"
    df1.write_text(json.dumps(leg1_data), encoding="utf-8")

    code = ledger.main(["purchase", str(wl_path), "--leg", "1", "--data", str(df1)])
    assert code == 0

    wl = json.loads(wl_path.read_text(encoding="utf-8"))
    assert wl["legs"][1]["purchased"] is True
    assert wl["legs"][1]["purchase"]["paid"] == {"included_in_leg": 0}


# ---------------------------------------------------------------------------
# Validation Item 8: Date changed
# ---------------------------------------------------------------------------


def test_validation_date_changed(ledger, tmp_path, capsys):
    wl_path = _sample_watchlist(tmp_path)

    # Depart date is 2026-11-06 (outbound_date is 2026-11-05)
    data = _valid_purchase_data()
    data["segments"][0]["depart"] = "2026-11-06T10:30"
    df = tmp_path / "date_diff.json"
    df.write_text(json.dumps(data), encoding="utf-8")

    # Without --date-changed -> rejected
    code = ledger.main(["purchase", str(wl_path), "--leg", "0", "--data", str(df)])
    assert code == 2
    err = capsys.readouterr().err
    assert "differs from leg outbound_date (2026-11-05); pass --date-changed to allow" in err

    # With --date-changed -> allowed, quoted_outbound_date recorded
    code2 = ledger.main(
        ["purchase", str(wl_path), "--leg", "0", "--data", str(df), "--date-changed"]
    )
    assert code2 == 0
    wl = json.loads(wl_path.read_text(encoding="utf-8"))
    assert wl["legs"][0]["outbound_date"] == "2026-11-06"
    assert wl["legs"][0]["quoted_outbound_date"] == "2026-11-05"


# ---------------------------------------------------------------------------
# Validation Item 9: Replace existing purchase & Real legacy shape
# ---------------------------------------------------------------------------


def test_replace_existing_purchase_and_legacy_real(ledger, tmp_path, capsys):
    legacy_purchase = {
        "date": "04/08/2026",
        "flight": "CM 123, GRU 01:30 -> PTY 06:45",
        "note": "Comprado com milhas + taxa",
        "order": "ORD-987654",
        "fare_brl": 0,
        "taxa_embarque_brl": 320.50,
        "total_brl": 320.50,
        "payment": "Cartão de crédito 1x",
    }
    wl = {
        "legs": [
            {
                "label": "GRU → PTY -- COMPRADO",
                "origin": "GRU",
                "destination": "PTY",
                "outbound_date": "2026-11-05",
                "adults": 2,
                "purchased": True,
                "purchase": dict(legacy_purchase),
            }
        ]
    }
    wl_path = tmp_path / "legacy_wl.json"
    wl_path.write_text(json.dumps(wl, indent=2), encoding="utf-8")

    new_data = {
        "date": "2026-08-04",
        "seller": "Copa Airlines",
        "locator": "ORD-987654",
        "adults": 2,
        "segments": [
            {
                "flight": "CM 123",
                "from": "GRU",
                "to": "PTY",
                "depart": "2026-11-05T01:30",
                "arrive": "2026-11-05T06:45",
            }
        ],
        "paid": {"amount": 320.50, "currency": "BRL"},
        "source": "migrated from legacy purchase block",
    }
    df = tmp_path / "migrated.json"
    df.write_text(json.dumps(new_data), encoding="utf-8")

    # Without --replace: must reject
    code = ledger.main(["purchase", str(wl_path), "--leg", "0", "--data", str(df)])
    assert code == 2
    err = capsys.readouterr().err
    assert "leg already has purchase block; pass --replace to overwrite" in err

    # With --replace: succeeds and preserves old block in purchase_legacy chave por chave
    code2 = ledger.main(
        ["purchase", str(wl_path), "--leg", "0", "--data", str(df), "--replace"]
    )
    assert code2 == 0

    saved_wl = json.loads(wl_path.read_text(encoding="utf-8"))
    leg0 = saved_wl["legs"][0]
    assert leg0["purchase"]["schema"] == 1
    assert leg0["purchase"]["locator"] == "ORD-987654"
    assert leg0["purchase_legacy"] == legacy_purchase

    # Attempting to replace again when purchase_legacy already exists: must reject
    code3 = ledger.main(
        ["purchase", str(wl_path), "--leg", "0", "--data", str(df), "--replace"]
    )
    assert code3 == 2
    err3 = capsys.readouterr().err
    assert "leg already has purchase_legacy; cannot replace again" in err3


# ---------------------------------------------------------------------------
# Error does not touch the file
# ---------------------------------------------------------------------------


def test_error_does_not_touch_file(ledger, tmp_path, capsys):
    wl_path = _sample_watchlist(tmp_path)
    before_bytes = wl_path.read_bytes()

    bad_data = _valid_purchase_data()
    bad_data["adults"] = 99  # invalid: mismatch
    df = tmp_path / "bad.json"
    df.write_text(json.dumps(bad_data), encoding="utf-8")

    code = ledger.main(["purchase", str(wl_path), "--leg", "0", "--data", str(df)])
    assert code == 2

    after_bytes = wl_path.read_bytes()
    assert before_bytes == after_bytes

    # Ensure no leftover temporary files in directory
    temp_files = list(tmp_path.glob("*.tmp*"))
    assert temp_files == []


# ---------------------------------------------------------------------------
# Junta com watch.py
# ---------------------------------------------------------------------------


def test_junta_com_watch_py(ledger, watch_mod, tmp_path, monkeypatch):
    """A purchase recorded by ledger survives watch.py identical and leg is not re-priced."""
    wl = {
        "trip": "EUA 2026",
        "last_run": "2026-09-20",
        "quota_reserve": 10,
        "legs": [
            {
                "label": "GRU → MIA",
                "origin": "GRU",
                "destination": "MIA",
                "outbound_date": "2026-11-05",
                "adults": 2,
                "purchased": False,
                "watch": True,
            },
            {
                "label": "MIA → MCO",
                "origin": "MIA",
                "destination": "MCO",
                "outbound_date": "2026-11-10",
                "adults": 2,
                "purchased": False,
                "watch": True,
                "query": {"origin": "MIA", "destination": "MCO", "outbound_date": "2026-11-10"},
                "baseline": {"low_band_ceiling": 500},
            },
        ],
    }
    wl_path = tmp_path / "watchlist-eua.json"
    wl_path.write_text(json.dumps(wl, indent=2, ensure_ascii=False), encoding="utf-8")

    # 1. Record purchase for Leg 0 using ledger
    p_data = _valid_purchase_data()
    df = tmp_path / "purchase_leg0.json"
    df.write_text(json.dumps(p_data), encoding="utf-8")

    ret = ledger.main(["purchase", str(wl_path), "--leg", "0", "--data", str(df)])
    assert ret == 0

    after_ledger = json.loads(wl_path.read_text(encoding="utf-8"))
    recorded_purchase = dict(after_ledger["legs"][0]["purchase"])
    assert after_ledger["legs"][0]["purchased"] is True

    # 2. Run watch.py on the same file
    priced_legs = []

    def fake_price_leg(key, leg):
        priced_legs.append(leg["label"])
        return {"price": 450, "price_level": "LOW", "price_history": None}

    monkeypatch.setattr(watch_mod, "api_key", lambda: "fake-key")
    monkeypatch.setattr(watch_mod, "searches_left", lambda key: 50)
    monkeypatch.setattr(watch_mod, "price_leg", fake_price_leg)
    monkeypatch.setattr(watch_mod, "sweep_events", lambda key, watch: [])
    monkeypatch.setattr(watch_mod, "LOG", tmp_path / "watch.log")
    monkeypatch.setattr(watch_mod, "ALERTS", tmp_path / "alerts.md")
    monkeypatch.setattr(sys, "argv", ["watch.py", str(wl_path)])

    watch_exit = watch_mod.main()
    assert watch_exit == 0

    # 3. Leg 0 must NOT have been priced (it was purchased)
    assert priced_legs == ["MIA → MCO"]

    # 4. Check watchlist state: Leg 0 purchase survived identical
    after_watch = json.loads(wl_path.read_text(encoding="utf-8"))
    assert after_watch["legs"][0]["purchase"] == recorded_purchase
    assert after_watch["legs"][0]["purchased"] is True


# ===========================================================================
# SUMMARY TESTS (Prompt 02)
# ===========================================================================


def test_rule_1_state_from_booleans_not_label(ledger, tmp_path, capsys):
    """Rule 1: state is derived from purchased and watch booleans, NEVER from label text."""
    wl = {
        "trip": "Status Test",
        "legs": [
            {
                # Deceptive label: says COMPRADO, but boolean says watching!
                "label": "GRU → MIA -- COMPRADO",
                "purchased": False,
                "watch": True,
            },
            {
                # watch: false -> settled_without_ticket
                "label": "MIA → MCO",
                "purchased": False,
                "watch": False,
                "watch_off_reason": "alugou carro",
            },
            {
                # purchased: true -> purchased
                "label": "MCO → JFK",
                "purchased": True,
            },
            {
                # watch omitted -> default to watching
                "label": "JFK → GRU",
                "purchased": False,
            },
        ],
    }
    wl_path = tmp_path / "watchlist-status.json"
    wl_path.write_text(json.dumps(wl, indent=2), encoding="utf-8")

    code = ledger.main(["summary", str(wl_path), "--today", "2026-10-02"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    legs = out["trips"][0]["legs"]
    assert legs[0]["state"] == "watching"
    assert legs[1]["state"] == "settled_without_ticket"
    assert legs[1]["watch_off_reason"] == "alugou carro"
    assert legs[2]["state"] == "purchased"
    assert legs[3]["state"] == "watching"


def test_rule_2_legacy_block_not_read_and_counted_in_gaps(ledger, tmp_path, capsys):
    """Rule 2: Legacy blocks without schema 1 are not read, values are not extracted, and they count in paid_gaps.legacy."""
    wl = {
        "trip": "Legacy Test",
        "legs": [
            {
                "label": "GRU → MIA",
                "purchased": True,
                "purchase": {
                    "date": "04/08/2026",
                    "flight": "CM 123",
                    "note": "anotação livre",
                    "order": "ORD-123",
                    "total_brl": 1500.0,
                },
            },
            {
                "label": "MIA → GRU",
                "purchased": True,
                # purchased: True without a purchase block
            },
        ],
    }
    wl_path = tmp_path / "watchlist-legacy.json"
    wl_path.write_text(json.dumps(wl, indent=2), encoding="utf-8")

    code = ledger.main(["summary", str(wl_path), "--today", "2026-10-02"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    trip = out["trips"][0]

    assert trip["paid_gaps"]["legacy"] == 2
    assert trip["paid_is_partial"] is True
    assert trip["paid_by_currency"] == {}

    leg0 = trip["legs"][0]
    assert leg0["purchase"]["schema"] is None
    assert leg0["purchase"]["legacy_keys"] == ["date", "flight", "note", "order", "total_brl"]

    leg1 = trip["legs"][1]
    assert leg1["purchase"]["schema"] is None
    assert leg1["purchase"]["legacy_keys"] == []


def test_rule_3_included_in_leg_no_gap_no_double_count(ledger, tmp_path, capsys):
    """Rule 3: included_in_leg is not a gap, not counted again, and gives complete total."""
    wl = {
        "trip": "Round Trip",
        "legs": [
            {
                "label": "GRU → MIA",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "locator": "LOC123",
                    "seller": "LATAM",
                    "paid": {"amount": 5000.0, "currency": "BRL"},
                },
            },
            {
                "label": "MIA → GRU",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "locator": "LOC123",
                    "seller": "LATAM",
                    "paid": {"included_in_leg": 0},
                },
            },
        ],
    }
    wl_path = tmp_path / "watchlist-bundle.json"
    wl_path.write_text(json.dumps(wl, indent=2), encoding="utf-8")

    code = ledger.main(["summary", str(wl_path), "--today", "2026-10-02"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    trip = out["trips"][0]

    assert trip["paid_by_currency"] == {"BRL": 5000.0}
    assert trip["paid_gaps"] == {"legacy": 0, "unmeasured": 0}
    assert trip["paid_is_partial"] is False


def test_rule_4_paid_null_counted_in_unmeasured_with_why(ledger, tmp_path, capsys):
    """Rule 4: paid: null counts in paid_gaps.unmeasured with reason preserved in leg summary."""
    wl = {
        "trip": "Null Paid Test",
        "legs": [
            {
                "label": "GRU → MIA",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "locator": "LOC999",
                    "seller": "Copa",
                    "paid": None,
                    "paid_unmeasured_why": "comprado por terceiros sem recibo",
                },
            }
        ],
    }
    wl_path = tmp_path / "watchlist-null.json"
    wl_path.write_text(json.dumps(wl, indent=2), encoding="utf-8")

    code = ledger.main(["summary", str(wl_path), "--today", "2026-10-02"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    trip = out["trips"][0]

    assert trip["paid_gaps"] == {"legacy": 0, "unmeasured": 1}
    assert trip["paid_is_partial"] is True
    assert trip["legs"][0]["purchase"]["paid"] is None
    assert trip["legs"][0]["purchase"]["paid_unmeasured_why"] == "comprado por terceiros sem recibo"


def test_rule_6_deadline_relative_to_today(ledger, tmp_path, capsys):
    """Rule 6: days_to_deadline relative to --today; negative when past, never zeroed or hidden."""
    wl = {
        "trip": "Deadline Test",
        "legs": [
            {
                "label": "GRU → MIA",
                "purchased": False,
                "watch": True,
                "trigger": {"hard_deadline": "2026-11-16"},
            },
            {
                "label": "MIA → JFK",
                "purchased": False,
                "watch": True,
                "trigger": {"hard_deadline": "2026-09-20"},
            },
            {
                "label": "JFK → GRU",
                "purchased": False,
                "watch": True,
                # No hard_deadline
            },
        ],
    }
    wl_path = tmp_path / "watchlist-deadlines.json"
    wl_path.write_text(json.dumps(wl, indent=2), encoding="utf-8")

    code = ledger.main(["summary", str(wl_path), "--today", "2026-10-02"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    legs = out["trips"][0]["legs"]

    assert legs[0]["hard_deadline"] == "2026-11-16"
    assert legs[0]["days_to_deadline"] == 45

    assert legs[1]["hard_deadline"] == "2026-09-20"
    assert legs[1]["days_to_deadline"] == -12  # past deadline, negative!

    assert "hard_deadline" not in legs[2]


def test_rule_7_days_since_last_run(ledger, tmp_path, capsys):
    """Rule 7: days_since_last_run relative to --today; null when last_run is absent."""
    wl1 = {
        "trip": "Trip 1",
        "last_run": "2026-09-17",
        "legs": [],
    }
    wl2 = {
        "trip": "Trip 2",
        "legs": [],
    }
    p1 = tmp_path / "wl1.json"
    p2 = tmp_path / "wl2.json"
    p1.write_text(json.dumps(wl1), encoding="utf-8")
    p2.write_text(json.dumps(wl2), encoding="utf-8")

    code = ledger.main(["summary", str(p1), str(p2), "--today", "2026-10-02"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    trips = {t["trip"]: t for t in out["trips"]}

    assert trips["Trip 1"]["last_run"] == "2026-09-17"
    assert trips["Trip 1"]["days_since_last_run"] == 15
    assert trips["Trip 2"]["last_run"] is None
    assert trips["Trip 2"]["days_since_last_run"] is None


def test_rule_8_unreadable_file_does_not_abort_summary(ledger, tmp_path, capsys):
    """Rule 8: An unreadable file goes to unreadable with reason; remaining files are summarized normally."""
    good_wl = {"trip": "Valid Trip", "legs": []}
    p_good = tmp_path / "good.json"
    p_good.write_text(json.dumps(good_wl), encoding="utf-8")

    p_broken = tmp_path / "broken.json"
    p_broken.write_text("this is not valid json", encoding="utf-8")

    p_no_legs = tmp_path / "no_legs.json"
    p_no_legs.write_text(json.dumps({"trip": "Missing legs"}), encoding="utf-8")

    code = ledger.main(["summary", str(p_good), str(p_broken), str(p_no_legs), "--today", "2026-10-02"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)

    assert len(out["trips"]) == 1
    assert out["trips"][0]["trip"] == "Valid Trip"

    unreadable_files = {u["file"] for u in out["unreadable"]}
    assert "broken.json" in unreadable_files
    assert "no_legs.json" in unreadable_files


def test_rule_9_last_observation(ledger, tmp_path, capsys):
    """Rule 9: last_observation is the last item of observations; null when absent."""
    wl = {
        "trip": "Obs Test",
        "legs": [
            {
                "label": "GRU → MIA",
                "purchased": False,
                "watch": True,
                "observations": [
                    {"date": "2026-09-10", "price": 1000},
                    {"date": "2026-09-24", "price": 918},
                ],
                "baseline": {"low_band_ceiling": 602},
            },
            {
                "label": "MIA → JFK",
                "purchased": False,
                "watch": True,
                "observations": [],
            },
        ],
    }
    wl_path = tmp_path / "watchlist-obs.json"
    wl_path.write_text(json.dumps(wl, indent=2), encoding="utf-8")

    code = ledger.main(["summary", str(wl_path), "--today", "2026-10-02"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    legs = out["trips"][0]["legs"]

    assert legs[0]["last_observation"] == {"date": "2026-09-24", "price": 918}
    assert legs[0]["low_band_ceiling"] == 602
    assert legs[1]["last_observation"] is None


def test_two_files_two_currencies(ledger, tmp_path, capsys):
    """Totals are per currency and per file, never summed across currencies or files."""
    wl1 = {
        "trip": "Viagem Brasil",
        "legs": [
            {
                "label": "Leg 1",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "locator": "LOC1",
                    "seller": "GOL",
                    "paid": {"amount": 1000.0, "currency": "BRL"},
                },
            },
            {
                "label": "Leg 2",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "locator": "LOC2",
                    "seller": "LATAM",
                    "paid": {"amount": 234.50, "currency": "BRL"},
                },
            },
        ],
    }
    wl2 = {
        "trip": "Viagem EUA",
        "legs": [
            {
                "label": "Leg A",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "locator": "LOC3",
                    "seller": "Copa",
                    "paid": {"amount": 300.0, "currency": "BRL"},
                },
            },
            {
                "label": "Leg B",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "locator": "LOC4",
                    "seller": "Delta",
                    "paid": {"amount": 400.0, "currency": "USD"},
                },
            },
        ],
    }
    p1 = tmp_path / "wl_brl.json"
    p2 = tmp_path / "wl_mixed.json"
    p1.write_text(json.dumps(wl1), encoding="utf-8")
    p2.write_text(json.dumps(wl2), encoding="utf-8")

    code = ledger.main(["summary", str(p1), str(p2), "--today", "2026-10-02"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    trips = {t["trip"]: t for t in out["trips"]}

    assert trips["Viagem Brasil"]["paid_by_currency"] == {"BRL": 1234.50}
    assert trips["Viagem EUA"]["paid_by_currency"] == {"BRL": 300.0, "USD": 400.0}
    # Never cross-currency summed
    assert "total" not in trips["Viagem EUA"]["paid_by_currency"]


def test_mixed_real_case(ledger, tmp_path, capsys):
    """The mixed real case: 1 schema 1, 1 legacy, 1 paid: null, 1 watching, 1 settled_without_ticket."""
    wl = {
        "trip": "EUA Real Case",
        "last_run": "2026-09-17",
        "legs": [
            {
                # Leg 0: schema 1 with open_issues
                "label": "GRU → MIA",
                "outbound_date": "2026-11-05",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "locator": "ABC123XYZ",
                    "seller": "LATAM Airlines",
                    "paid": {"amount": 1234.50, "currency": "BRL"},
                    "open_issues": ["chegada 00:38 exige noite do dia 20"],
                },
            },
            {
                # Leg 1: legacy purchase block with real keys
                "label": "MIA → MCO -- COMPRADO",
                "purchased": True,
                "purchase": {
                    "date": "04/08/2026",
                    "flight": "XX 1234",
                    "note": "comprado à mão",
                    "order": "ORD-555",
                    "total_brl": 500.0,
                },
            },
            {
                # Leg 2: paid null
                "label": "MCO → JFK",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "locator": "XYZ789",
                    "seller": "JetBlue",
                    "paid": None,
                    "paid_unmeasured_why": "comprado com milhas sem taxa",
                },
            },
            {
                # Leg 3: watching
                "label": "JFK → BOS",
                "purchased": False,
                "watch": True,
                "observations": [{"date": "2026-09-24", "price": 918}],
                "baseline": {"low_band_ceiling": 602},
                "trigger": {"hard_deadline": "2026-11-16"},
            },
            {
                # Leg 4: settled_without_ticket
                "label": "BOS → NYC",
                "purchased": False,
                "watch": False,
                "watch_off_reason": "decidiu ir de trem",
            },
        ],
    }
    wl_path = tmp_path / "watchlist-mixed.json"
    wl_path.write_text(json.dumps(wl, indent=2), encoding="utf-8")

    code = ledger.main(["summary", str(wl_path), "--today", "2026-10-02"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    trip = out["trips"][0]

    assert trip["days_since_last_run"] == 15

    legs = trip["legs"]
    assert legs[0]["state"] == "purchased"
    assert legs[0]["purchase"]["schema"] == 1
    assert legs[0]["purchase"]["paid"] == {"amount": 1234.50, "currency": "BRL"}

    assert legs[1]["state"] == "purchased"
    assert legs[1]["purchase"]["schema"] is None
    assert legs[1]["purchase"]["legacy_keys"] == ["date", "flight", "note", "order", "total_brl"]

    assert legs[2]["state"] == "purchased"
    assert legs[2]["purchase"]["paid"] is None
    assert legs[2]["purchase"]["paid_unmeasured_why"] == "comprado com milhas sem taxa"

    assert legs[3]["state"] == "watching"
    assert legs[3]["last_observation"] == {"date": "2026-09-24", "price": 918}
    assert legs[3]["low_band_ceiling"] == 602
    assert legs[3]["days_to_deadline"] == 45

    assert legs[4]["state"] == "settled_without_ticket"
    assert legs[4]["watch_off_reason"] == "decidiu ir de trem"

    # paid_by_currency only sums schema 1 with real amount; ignores legacy total_brl!
    assert trip["paid_by_currency"] == {"BRL": 1234.50}

    # paid_gaps and paid_is_partial
    assert trip["paid_gaps"] == {"legacy": 1, "unmeasured": 1}
    assert trip["paid_is_partial"] is True

    # open_issues aggregated
    assert trip["open_issues"] == [
        {"leg": 0, "issue": "chegada 00:38 exige noite do dia 20"}
    ]


def test_default_state_directory(ledger, tmp_path, capsys):
    """When run without arguments, summary scans state_dir() for watchlist-*.json, ignoring *.bak* and alerts.md."""
    # Write files directly into tmp_path (which isolate_state_dir pointed COSMO_TRAVEL_STATE_DIR to)
    (tmp_path / "watchlist-a.json").write_text(json.dumps({"trip": "Trip A", "legs": []}), encoding="utf-8")
    (tmp_path / "watchlist-b.json").write_text(json.dumps({"trip": "Trip B", "legs": []}), encoding="utf-8")
    (tmp_path / "watchlist-a.json.bak-20260831").write_text(json.dumps({"trip": "Trip Bak", "legs": []}), encoding="utf-8")
    (tmp_path / "alerts.md").write_text("# Alerts", encoding="utf-8")

    code = ledger.main(["summary", "--today", "2026-10-02"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)

    trip_files = [t["file"] for t in out["trips"]]
    assert trip_files == ["watchlist-a.json", "watchlist-b.json"]


# ---------------------------------------------------------------------------
# Prompt 03: home, stay, coverage
# ---------------------------------------------------------------------------


def test_home_command(ledger, tmp_path, capsys):
    wl_path = _sample_watchlist(tmp_path)
    # Valid code
    code = ledger.main(["home", str(wl_path), "POA"])
    assert code == 0
    data = json.loads(wl_path.read_text(encoding="utf-8"))
    assert data["home"] == "POA"

    # Multiple valid codes
    code = ledger.main(["home", str(wl_path), "POA,NVT"])
    assert code == 0
    data = json.loads(wl_path.read_text(encoding="utf-8"))
    assert data["home"] == "POA,NVT"

    # Invalid code
    code = ledger.main(["home", str(wl_path), "poa"])
    assert code != 0
    err = capsys.readouterr().err
    assert "invalid IATA code" in err
    # File not modified with invalid code
    data = json.loads(wl_path.read_text(encoding="utf-8"))
    assert data["home"] == "POA,NVT"


def test_stay_validation_rules(ledger, tmp_path, capsys):
    wl_path = _sample_watchlist(tmp_path)
    initial_content = wl_path.read_text(encoding="utf-8")

    # Helper to test invalid stay
    def assert_invalid_stay(stay_dict, expected_err_substr):
        stay_file = tmp_path / "bad_stay.json"
        stay_file.write_text(json.dumps(stay_dict), encoding="utf-8")
        ret = ledger.main(["stay", str(wl_path), "--data", str(stay_file)])
        assert ret != 0
        err = capsys.readouterr().err
        assert expected_err_substr in err
        # File must not be touched
        assert wl_path.read_text(encoding="utf-8") == initial_content

    # 1. check_out <= check_in
    assert_invalid_stay(
        {"check_in": "2026-05-15", "check_out": "2026-05-10", "status": "not_needed", "why": "test"},
        "check_out (2026-05-10) must be after check_in (2026-05-15)",
    )
    assert_invalid_stay(
        {"check_in": "2026-05-10", "check_out": "2026-05-10", "status": "not_needed", "why": "test"},
        "check_out (2026-05-10) must be after check_in (2026-05-10)",
    )

    # 2. invalid date format
    assert_invalid_stay(
        {"check_in": "2026/05/10", "check_out": "2026-05-15", "status": "not_needed", "why": "test"},
        "check_in must be a valid ISO date",
    )

    # 3. invalid status
    assert_invalid_stay(
        {"check_in": "2026-05-10", "check_out": "2026-05-15", "status": "needed", "why": "test"},
        "status must be 'booked' or 'not_needed'",
    )

    # 4. unknown keys
    assert_invalid_stay(
        {"check_in": "2026-05-10", "check_out": "2026-05-15", "status": "not_needed", "why": "test", "extra_field": 123},
        "unknown key(s) in stay: extra_field",
    )

    # 5. booked missing booking
    assert_invalid_stay(
        {"check_in": "2026-05-10", "check_out": "2026-05-15", "status": "booked"},
        "booking object is required when status is 'booked'",
    )

    # 6. booked with why
    valid_booking = {
        "seller": "Booking.com",
        "locator": "HOTEL123",
        "source": "email: confirmation",
        "paid": {"amount": 500.0, "currency": "BRL"},
    }
    assert_invalid_stay(
        {"check_in": "2026-05-10", "check_out": "2026-05-15", "status": "booked", "why": "friends", "booking": valid_booking},
        "why is not allowed when status is 'booked'",
    )

    # 7. booked with unknown key in booking
    bad_b = dict(valid_booking)
    bad_b["room_number"] = 101
    assert_invalid_stay(
        {"check_in": "2026-05-10", "check_out": "2026-05-15", "status": "booked", "booking": bad_b},
        "unknown key(s) in booking: room_number",
    )

    # 8. booked missing required keys in booking
    assert_invalid_stay(
        {"check_in": "2026-05-10", "check_out": "2026-05-15", "status": "booked", "booking": {"seller": "A"}},
        "missing required key(s) in booking",
    )

    # 9. booked with paid.amount == 0
    bad_paid_zero = dict(valid_booking)
    bad_paid_zero["paid"] = {"amount": 0, "currency": "BRL"}
    assert_invalid_stay(
        {"check_in": "2026-05-10", "check_out": "2026-05-15", "status": "booked", "booking": bad_paid_zero},
        "booking.paid.amount must be a positive number (> 0), got 0",
    )

    # 10. booked with included_in_leg in paid
    bad_paid_inc = dict(valid_booking)
    bad_paid_inc["paid"] = {"included_in_leg": 0}
    assert_invalid_stay(
        {"check_in": "2026-05-10", "check_out": "2026-05-15", "status": "booked", "booking": bad_paid_inc},
        "included_in_leg is only for flights, not allowed for stays",
    )

    # 11. booked with paid=null and no paid_unmeasured_why
    bad_paid_null = dict(valid_booking)
    bad_paid_null["paid"] = None
    assert_invalid_stay(
        {"check_in": "2026-05-10", "check_out": "2026-05-15", "status": "booked", "booking": bad_paid_null},
        "when booking.paid is null, paid_unmeasured_why is required",
    )

    # 12. booked refundable_until > check_in
    bad_ref = dict(valid_booking)
    bad_ref["refundable_until"] = "2026-05-12"
    assert_invalid_stay(
        {"check_in": "2026-05-10", "check_out": "2026-05-15", "status": "booked", "booking": bad_ref},
        "booking.refundable_until (2026-05-12) must be <= check_in (2026-05-10)",
    )

    # 13. not_needed with booking
    assert_invalid_stay(
        {"check_in": "2026-05-10", "check_out": "2026-05-15", "status": "not_needed", "why": "friends", "booking": valid_booking},
        "booking is not allowed when status is 'not_needed'",
    )

    # 14. not_needed missing why or empty why
    assert_invalid_stay(
        {"check_in": "2026-05-10", "check_out": "2026-05-15", "status": "not_needed", "why": "   "},
        "why is required and cannot be empty when status is 'not_needed'",
    )

    # 15. Valid stay appends cleanly
    stay_file = tmp_path / "valid_stay.json"
    stay_file.write_text(json.dumps({"label": "Hotel NYC", "check_in": "2026-05-10", "check_out": "2026-05-15", "status": "booked", "booking": valid_booking}), encoding="utf-8")
    ret = ledger.main(["stay", str(wl_path), "--data", str(stay_file)])
    assert ret == 0
    saved = json.loads(wl_path.read_text(encoding="utf-8"))
    assert len(saved.get("stays", [])) == 1
    assert saved["stays"][0]["label"] == "Hotel NYC"

    # 16. Overlapping stay: warns but still saves
    overlap_stay = {
        "label": "Hotel Overlap",
        "check_in": "2026-05-12",
        "check_out": "2026-05-16",
        "status": "not_needed",
        "why": "friends",
    }
    stay_file2 = tmp_path / "overlap_stay.json"
    stay_file2.write_text(json.dumps(overlap_stay), encoding="utf-8")
    ret = ledger.main(["stay", str(wl_path), "--data", str(stay_file2)])
    assert ret == 0
    err = capsys.readouterr().err
    assert "warning: stay overlaps with existing stay 'Hotel NYC'" in err
    saved = json.loads(wl_path.read_text(encoding="utf-8"))
    assert len(saved.get("stays", [])) == 2


def test_coverage_arrival_1810_depart_1140_five_nights(ledger, tmp_path, capsys):
    """Case 1: arrive 24th 18:10, depart 29th 11:40 -> 5 nights required (24, 25, 26, 27, 28)."""
    wl = {
        "trip": "NY Trip",
        "home": "POA",
        "legs": [
            {
                "outbound_date": "2026-05-24",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "LATAM",
                    "locator": "LOC1",
                    "adults": 1,
                    "paid": {"amount": 1000.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {
                            "flight": "LA 100",
                            "from": "POA",
                            "to": "JFK",
                            "depart": "2026-05-24T06:00",
                            "arrive": "2026-05-24T18:10",
                        }
                    ],
                },
            },
            {
                "outbound_date": "2026-05-29",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "LATAM",
                    "locator": "LOC2",
                    "adults": 1,
                    "paid": {"amount": 1000.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {
                            "flight": "LA 200",
                            "from": "JFK",
                            "to": "POA",
                            "depart": "2026-05-29T11:40",
                            "arrive": "2026-05-29T23:50",
                        }
                    ],
                },
            },
        ],
    }
    wl_path = tmp_path / "watchlist-c1.json"
    wl_path.write_text(json.dumps(wl), encoding="utf-8")

    code = ledger.main(["coverage", str(wl_path)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    trip = out["trips"][0]
    assert trip["verdict"] == "uncovered"
    assert len(trip["gaps"]) == 1
    gap = trip["gaps"][0]
    assert gap["late_arrival"] is False
    assert gap["nights_needed"] == [
        "2026-05-24",
        "2026-05-25",
        "2026-05-26",
        "2026-05-27",
        "2026-05-28",
    ]
    assert gap["uncovered"] == [
        {"check_in": "2026-05-24", "check_out": "2026-05-29", "nights": 5}
    ]


def test_coverage_arrival_0705_overnight_flight(ledger, tmp_path, capsys):
    """Case 2: arrival 07:05 on day 21 -> night of 20th does NOT enter (spent on plane)."""
    wl = {
        "trip": "Overnight Trip",
        "home": "POA",
        "legs": [
            {
                "outbound_date": "2026-05-20",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "LATAM",
                    "locator": "LOC1",
                    "adults": 1,
                    "paid": {"amount": 1000.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {
                            "flight": "LA 100",
                            "from": "POA",
                            "to": "JFK",
                            "depart": "2026-05-20T21:00",
                            "arrive": "2026-05-21T07:05",
                        }
                    ],
                },
            },
            {
                "outbound_date": "2026-05-25",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "LATAM",
                    "locator": "LOC2",
                    "adults": 1,
                    "paid": {"amount": 1000.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {
                            "flight": "LA 200",
                            "from": "JFK",
                            "to": "POA",
                            "depart": "2026-05-25T14:00",
                            "arrive": "2026-05-26T06:00",
                        }
                    ],
                },
            },
        ],
    }
    wl_path = tmp_path / "watchlist-c2.json"
    wl_path.write_text(json.dumps(wl), encoding="utf-8")

    code = ledger.main(["coverage", str(wl_path)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    trip = out["trips"][0]
    gap = trip["gaps"][0]
    assert "2026-05-20" not in gap["nights_needed"]
    assert gap["nights_needed"] == [
        "2026-05-21",
        "2026-05-22",
        "2026-05-23",
        "2026-05-24",
    ]
    assert gap["late_arrival"] is False


def test_coverage_arrival_0038_late_arrival(ledger, tmp_path, capsys):
    """Case 3: arrival 00:38 on day 21 -> night of 20th DOES enter, late_arrival is true."""
    wl = {
        "trip": "Late Arrival Trip",
        "home": "POA",
        "legs": [
            {
                "outbound_date": "2026-05-20",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "Copa",
                    "locator": "LOC1",
                    "adults": 1,
                    "paid": {"amount": 1000.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {
                            "flight": "CM 100",
                            "from": "POA",
                            "to": "MIA",
                            "depart": "2026-05-20T16:00",
                            "arrive": "2026-05-21T00:38",
                        }
                    ],
                },
            },
            {
                "outbound_date": "2026-05-25",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "Copa",
                    "locator": "LOC2",
                    "adults": 1,
                    "paid": {"amount": 1000.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {
                            "flight": "CM 200",
                            "from": "MIA",
                            "to": "POA",
                            "depart": "2026-05-25T14:00",
                            "arrive": "2026-05-25T23:00",
                        }
                    ],
                },
            },
        ],
    }
    wl_path = tmp_path / "watchlist-c3.json"
    wl_path.write_text(json.dumps(wl), encoding="utf-8")

    code = ledger.main(["coverage", str(wl_path)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    trip = out["trips"][0]
    gap = trip["gaps"][0]
    assert "2026-05-20" in gap["nights_needed"]
    assert gap["late_arrival"] is True
    assert gap["nights_needed"][0] == "2026-05-20"


def test_coverage_day_connection_omitted(ledger, tmp_path, capsys):
    """Same day connection (e.g. arrive 12:20, depart 17:05) produces 0 nights and is omitted."""
    wl = {
        "trip": "Connection Trip",
        "home": "POA",
        "legs": [
            {
                "outbound_date": "2026-05-20",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "Copa",
                    "locator": "LOC1",
                    "adults": 1,
                    "paid": {"amount": 1000.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {
                            "flight": "CM 100",
                            "from": "POA",
                            "to": "PTY",
                            "depart": "2026-05-20T06:00",
                            "arrive": "2026-05-20T12:20",
                        },
                        {
                            "flight": "CM 200",
                            "from": "PTY",
                            "to": "MIA",
                            "depart": "2026-05-20T17:05",
                            "arrive": "2026-05-20T21:30",
                        },
                    ],
                },
            },
            {
                "outbound_date": "2026-05-25",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "Copa",
                    "locator": "LOC2",
                    "adults": 1,
                    "paid": {"amount": 1000.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {
                            "flight": "CM 300",
                            "from": "MIA",
                            "to": "POA",
                            "depart": "2026-05-25T14:00",
                            "arrive": "2026-05-25T23:00",
                        }
                    ],
                },
            },
        ],
    }
    wl_path = tmp_path / "watchlist-conn.json"
    wl_path.write_text(json.dumps(wl), encoding="utf-8")

    code = ledger.main(["coverage", str(wl_path)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    trip = out["trips"][0]
    # Exactly 1 gap (MIA stay from May 20 21:30 to May 25 14:00), PTY layover omitted!
    assert len(trip["gaps"]) == 1
    assert trip["gaps"][0]["arrive_at"] == "MIA"
    assert trip["gaps"][0]["depart_from"] == "MIA"


def test_coverage_different_airports_two_stays_covered(ledger, tmp_path, capsys):
    """Arrive CGH, depart GRU, covered by two contiguous stays in two cities -> covered."""
    wl = {
        "trip": "Multi City Stay",
        "home": "POA",
        "legs": [
            {
                "outbound_date": "2026-05-10",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "GOL",
                    "locator": "LOC1",
                    "adults": 1,
                    "paid": {"amount": 500.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {"flight": "G3 1", "from": "POA", "to": "CGH", "depart": "2026-05-10T16:00", "arrive": "2026-05-10T18:10"}
                    ],
                },
            },
            {
                "outbound_date": "2026-05-15",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "GOL",
                    "locator": "LOC2",
                    "adults": 1,
                    "paid": {"amount": 500.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {"flight": "G3 2", "from": "GRU", "to": "POA", "depart": "2026-05-15T11:40", "arrive": "2026-05-15T13:30"}
                    ],
                },
            },
        ],
        "stays": [
            {
                "label": "Santos",
                "check_in": "2026-05-10",
                "check_out": "2026-05-12",
                "status": "booked",
                "booking": {"seller": "A", "locator": "L1", "source": "email", "paid": {"amount": 400.0, "currency": "BRL"}},
            },
            {
                "label": "São Paulo",
                "check_in": "2026-05-12",
                "check_out": "2026-05-15",
                "status": "booked",
                "booking": {"seller": "B", "locator": "L2", "source": "email", "paid": {"amount": 600.0, "currency": "BRL"}},
            },
        ],
    }
    wl_path = tmp_path / "watchlist-covered.json"
    wl_path.write_text(json.dumps(wl), encoding="utf-8")

    code = ledger.main(["coverage", str(wl_path)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    trip = out["trips"][0]
    assert trip["verdict"] == "covered"
    assert trip["gaps"][0]["uncovered"] == []
    assert len(trip["gaps"][0]["covered"]) == 5
    assert trip["nights_outside_any_gap"] == []


def test_coverage_stay_ends_in_middle_checkout_night_uncovered(ledger, tmp_path, capsys):
    """5 nights needed, stay check_out on 3rd day -> check_out night is uncovered."""
    wl = {
        "trip": "Partial Stay Trip",
        "home": "POA",
        "legs": [
            {
                "outbound_date": "2026-05-10",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "GOL",
                    "locator": "LOC1",
                    "adults": 1,
                    "paid": {"amount": 500.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {"flight": "G3 1", "from": "POA", "to": "CGH", "depart": "2026-05-10T16:00", "arrive": "2026-05-10T18:10"}
                    ],
                },
            },
            {
                "outbound_date": "2026-05-15",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "GOL",
                    "locator": "LOC2",
                    "adults": 1,
                    "paid": {"amount": 500.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {"flight": "G3 2", "from": "GRU", "to": "POA", "depart": "2026-05-15T11:40", "arrive": "2026-05-15T13:30"}
                    ],
                },
            },
        ],
        "stays": [
            {
                "label": "Short stay",
                "check_in": "2026-05-10",
                "check_out": "2026-05-12",
                "status": "booked",
                "booking": {"seller": "A", "locator": "L1", "source": "email", "paid": {"amount": 400.0, "currency": "BRL"}},
            }
        ],
    }
    wl_path = tmp_path / "watchlist-middle.json"
    wl_path.write_text(json.dumps(wl), encoding="utf-8")

    code = ledger.main(["coverage", str(wl_path)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    trip = out["trips"][0]
    assert trip["verdict"] == "uncovered"
    # Uncovered range starts at 2026-05-12 (the check_out date of the stay!)
    assert trip["gaps"][0]["uncovered"] == [
        {"check_in": "2026-05-12", "check_out": "2026-05-15", "nights": 3}
    ]


def test_coverage_not_needed_stay_covers_equally(ledger, tmp_path, capsys):
    """Stay with status 'not_needed' covers dates the same as booked."""
    wl = {
        "trip": "Event Stay Trip",
        "home": "POA",
        "legs": [
            {
                "outbound_date": "2026-05-10",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "GOL",
                    "locator": "LOC1",
                    "adults": 1,
                    "paid": {"amount": 500.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {"flight": "G3 1", "from": "POA", "to": "CGH", "depart": "2026-05-10T16:00", "arrive": "2026-05-10T18:10"}
                    ],
                },
            },
            {
                "outbound_date": "2026-05-15",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "GOL",
                    "locator": "LOC2",
                    "adults": 1,
                    "paid": {"amount": 500.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {"flight": "G3 2", "from": "GRU", "to": "POA", "depart": "2026-05-15T11:40", "arrive": "2026-05-15T13:30"}
                    ],
                },
            },
        ],
        "stays": [
            {
                "label": "Casa de amigos",
                "check_in": "2026-05-10",
                "check_out": "2026-05-15",
                "status": "not_needed",
                "why": "hospedagem na casa de amigos",
            }
        ],
    }
    wl_path = tmp_path / "watchlist-not-needed.json"
    wl_path.write_text(json.dumps(wl), encoding="utf-8")

    code = ledger.main(["coverage", str(wl_path)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    trip = out["trips"][0]
    assert trip["verdict"] == "covered"
    assert trip["gaps"][0]["uncovered"] == []


def test_coverage_nights_outside_any_gap(ledger, tmp_path, capsys):
    """Paid stay night outside derived gap is recorded in nights_outside_any_gap."""
    wl = {
        "trip": "Outside Nights Trip",
        "home": "POA",
        "legs": [
            {
                "outbound_date": "2026-05-10",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "GOL",
                    "locator": "LOC1",
                    "adults": 1,
                    "paid": {"amount": 500.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {"flight": "G3 1", "from": "POA", "to": "CGH", "depart": "2026-05-10T16:00", "arrive": "2026-05-10T18:10"}
                    ],
                },
            },
            {
                "outbound_date": "2026-05-15",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "GOL",
                    "locator": "LOC2",
                    "adults": 1,
                    "paid": {"amount": 500.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {"flight": "G3 2", "from": "CGH", "to": "POA", "depart": "2026-05-15T11:40", "arrive": "2026-05-15T13:30"}
                    ],
                },
            },
        ],
        "stays": [
            {
                "label": "Legitimate stay",
                "check_in": "2026-05-10",
                "check_out": "2026-05-15",
                "status": "booked",
                "booking": {"seller": "A", "locator": "L1", "source": "email", "paid": {"amount": 500.0, "currency": "BRL"}},
            },
            {
                "label": "Extra unused stay",
                "check_in": "2026-05-20",
                "check_out": "2026-05-22",
                "status": "booked",
                "booking": {"seller": "B", "locator": "L2", "source": "email", "paid": {"amount": 300.0, "currency": "BRL"}},
            },
        ],
    }
    wl_path = tmp_path / "watchlist-outside.json"
    wl_path.write_text(json.dumps(wl), encoding="utf-8")

    code = ledger.main(["coverage", str(wl_path)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    trip = out["trips"][0]
    assert trip["nights_outside_any_gap"] == ["2026-05-20", "2026-05-21"]


def test_coverage_legacy_leg_in_middle_partial(ledger, tmp_path, capsys):
    """Legacy purchased leg in the middle causes verdict to be 'partial', never 'covered'."""
    wl = {
        "trip": "Legacy In Middle",
        "home": "POA",
        "legs": [
            {
                "outbound_date": "2026-05-10",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "GOL",
                    "locator": "LOC1",
                    "adults": 1,
                    "paid": {"amount": 500.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {"flight": "G3 1", "from": "POA", "to": "CGH", "depart": "2026-05-10T16:00", "arrive": "2026-05-10T18:10"}
                    ],
                },
            },
            {
                "outbound_date": "2026-05-12",
                "purchased": True,
                "purchase": {
                    "date": "12/03/2026",
                    "flight": "G3 1234",
                    "paid_brl": 300.0,
                },
            },
            {
                "outbound_date": "2026-05-15",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "GOL",
                    "locator": "LOC3",
                    "adults": 1,
                    "paid": {"amount": 500.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {"flight": "G3 2", "from": "SDU", "to": "POA", "depart": "2026-05-15T11:40", "arrive": "2026-05-15T13:30"}
                    ],
                },
            },
        ],
        "stays": [
            {
                "label": "Full stay",
                "check_in": "2026-05-10",
                "check_out": "2026-05-15",
                "status": "booked",
                "booking": {"seller": "A", "locator": "L1", "source": "email", "paid": {"amount": 500.0, "currency": "BRL"}},
            }
        ],
    }
    wl_path = tmp_path / "watchlist-legacy-mid.json"
    wl_path.write_text(json.dumps(wl), encoding="utf-8")

    code = ledger.main(["coverage", str(wl_path)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    trip = out["trips"][0]
    assert trip["verdict"] == "partial"
    assert len(trip["not_derivable"]) == 1
    assert trip["not_derivable"][0]["leg"] == 1


def test_coverage_missing_home_not_derivable(ledger, tmp_path, capsys):
    """Missing 'home' results in verdict 'not_derivable' and empty gaps."""
    wl = {
        "trip": "No Home Trip",
        "legs": [
            {
                "outbound_date": "2026-05-10",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "GOL",
                    "locator": "LOC1",
                    "adults": 1,
                    "paid": {"amount": 500.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {"flight": "G3 1", "from": "POA", "to": "CGH", "depart": "2026-05-10T16:00", "arrive": "2026-05-10T18:10"}
                    ],
                },
            }
        ],
    }
    wl_path = tmp_path / "watchlist-no-home.json"
    wl_path.write_text(json.dumps(wl), encoding="utf-8")

    code = ledger.main(["coverage", str(wl_path)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    trip = out["trips"][0]
    assert trip["verdict"] == "not_derivable"
    assert trip["gaps"] == []
    assert any(nd.get("reason") == "missing_home" for nd in trip["not_derivable"])


def test_coverage_one_way_flight_open_end(ledger, tmp_path, capsys):
    """Outbound bought, return not bought -> open_end is set, verdict is partial (never covered)."""
    wl = {
        "trip": "One Way Trip",
        "home": "POA",
        "legs": [
            {
                "outbound_date": "2026-05-10",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "GOL",
                    "locator": "LOC1",
                    "adults": 1,
                    "paid": {"amount": 500.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {"flight": "G3 1", "from": "POA", "to": "MIA", "depart": "2026-05-10T16:00", "arrive": "2026-05-10T23:00"}
                    ],
                },
            }
        ],
    }
    wl_path = tmp_path / "watchlist-open-end.json"
    wl_path.write_text(json.dumps(wl), encoding="utf-8")

    code = ledger.main(["coverage", str(wl_path)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    trip = out["trips"][0]
    assert trip["open_end"] == "2026-05-10"
    assert trip["verdict"] != "covered"
    assert trip["verdict"] == "partial"


def test_coverage_pending_legs_inside(ledger, tmp_path, capsys):
    """Unpurchased leg falling inside a gap is listed in pending_legs_inside."""
    wl = {
        "trip": "Pending Inside Trip",
        "home": "POA",
        "legs": [
            {
                "label": "POA → MIA",
                "outbound_date": "2026-05-10",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "Copa",
                    "locator": "LOC1",
                    "adults": 1,
                    "paid": {"amount": 1000.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {"flight": "CM 1", "from": "POA", "to": "MIA", "depart": "2026-05-10T06:00", "arrive": "2026-05-10T18:00"}
                    ],
                },
            },
            {
                "label": "MIA → MCO",
                "outbound_date": "2026-05-12",
                "purchased": False,
                "watch": True,
            },
            {
                "label": "MCO → POA",
                "outbound_date": "2026-05-15",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "Copa",
                    "locator": "LOC2",
                    "adults": 1,
                    "paid": {"amount": 1000.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {"flight": "CM 2", "from": "MCO", "to": "POA", "depart": "2026-05-15T11:00", "arrive": "2026-05-15T22:00"}
                    ],
                },
            },
        ],
    }
    wl_path = tmp_path / "watchlist-pending.json"
    wl_path.write_text(json.dumps(wl), encoding="utf-8")

    code = ledger.main(["coverage", str(wl_path)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    trip = out["trips"][0]
    gap = trip["gaps"][0]
    assert len(gap["pending_legs_inside"]) == 1
    pending = gap["pending_legs_inside"][0]
    assert pending["leg"] == 1
    assert pending["outbound_date"] == "2026-05-12"


def test_watch_main_preserves_home_and_stays(ledger, watch_mod, tmp_path, monkeypatch):
    """Running watch.py on a watchlist with home and stays preserves both keys untouched."""
    wl = {
        "trip": "EUA 2026",
        "home": "POA",
        "last_run": "2026-09-20",
        "quota_reserve": 10,
        "legs": [
            {
                "label": "POA → MIA",
                "origin": "POA",
                "destination": "MIA",
                "outbound_date": "2026-11-05",
                "adults": 1,
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-09-20",
                    "seller": "Copa",
                    "locator": "XYZ123",
                    "adults": 1,
                    "paid": {"amount": 1200.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {"flight": "CM 1", "from": "POA", "to": "MIA", "depart": "2026-11-05T06:00", "arrive": "2026-11-05T18:00"}
                    ],
                },
            }
        ],
        "stays": [
            {
                "label": "Hotel Miami",
                "check_in": "2026-11-05",
                "check_out": "2026-11-10",
                "status": "booked",
                "booking": {"seller": "Expedia", "locator": "EXP456", "source": "email", "paid": {"amount": 2000.0, "currency": "BRL"}},
            }
        ],
    }
    wl_path = tmp_path / "watchlist-preserve.json"
    wl_path.write_text(json.dumps(wl, indent=2), encoding="utf-8")

    monkeypatch.setattr(watch_mod, "api_key", lambda: "fake-key")
    monkeypatch.setattr(watch_mod, "searches_left", lambda key: 50)
    monkeypatch.setattr(watch_mod, "price_leg", lambda key, leg: {"price": 100})
    monkeypatch.setattr(watch_mod, "sweep_events", lambda key, watch: [])
    monkeypatch.setattr(watch_mod, "LOG", tmp_path / "watch.log")
    monkeypatch.setattr(watch_mod, "ALERTS", tmp_path / "alerts.md")
    monkeypatch.setattr(sys, "argv", ["watch.py", str(wl_path)])

    watch_exit = watch_mod.main()
    assert watch_exit == 0

    after = json.loads(wl_path.read_text(encoding="utf-8"))
    assert after["home"] == "POA"
    assert after["stays"] == wl["stays"]


def test_summary_includes_coverage(ledger, tmp_path, capsys):
    """summary command includes 'coverage' block with verdict and uncovered_nights."""
    wl = {
        "trip": "Summary Coverage Trip",
        "home": "POA",
        "last_run": "2026-09-20",
        "legs": [
            {
                "outbound_date": "2026-05-10",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "GOL",
                    "locator": "LOC1",
                    "adults": 1,
                    "paid": {"amount": 500.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {"flight": "G3 1", "from": "POA", "to": "CGH", "depart": "2026-05-10T16:00", "arrive": "2026-05-10T18:10"}
                    ],
                },
            },
            {
                "outbound_date": "2026-05-15",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "GOL",
                    "locator": "LOC2",
                    "adults": 1,
                    "paid": {"amount": 500.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {"flight": "G3 2", "from": "CGH", "to": "POA", "depart": "2026-05-15T11:40", "arrive": "2026-05-15T13:30"}
                    ],
                },
            },
        ],
        "stays": [
            {
                "label": "Short stay",
                "check_in": "2026-05-10",
                "check_out": "2026-05-12",
                "status": "booked",
                "booking": {"seller": "A", "locator": "L1", "source": "email", "paid": {"amount": 400.0, "currency": "BRL"}},
            }
        ],
    }
    wl_path = tmp_path / "watchlist-summary-cov.json"
    wl_path.write_text(json.dumps(wl), encoding="utf-8")

    code = ledger.main(["summary", str(wl_path), "--today", "2026-10-02"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    trip = out["trips"][0]
    assert "coverage" in trip
    assert trip["coverage"] == {
        "verdict": "uncovered",
        "uncovered_nights": 3,
    }


# ---------------------------------------------------------------------------
# Prompt 04: add-leg
# ---------------------------------------------------------------------------


def test_add_leg_full_flow_five_commands(ledger, tmp_path, capsys):
    """The entire real-world flow in 5 commands without manual JSON edits:

    add-leg --create -> purchase --leg 0 -> second add-leg -> purchase --leg 1 -> coverage & summary
    """
    wl_path = tmp_path / "watchlist-flow.json"

    # Step 1: add-leg --create for outbound leg
    leg0_data = {
        "label": "POA → MIA · 10 mai",
        "origin": "POA",
        "destination": "MIA",
        "outbound_date": "2026-05-10",
        "adults": 2,
        "watch": False,
        "watch_off_reason": "já comprado fora da vigília",
    }
    leg0_file = tmp_path / "leg0.json"
    leg0_file.write_text(json.dumps(leg0_data), encoding="utf-8")

    code = ledger.main([
        "add-leg",
        str(wl_path),
        "--data",
        str(leg0_file),
        "--create",
        "--trip",
        "EUA 2026",
        "--home",
        "POA",
    ])
    assert code == 0
    out = capsys.readouterr().out.strip()
    assert out == "0"
    assert wl_path.exists()

    # Step 2: purchase --leg 0
    purch0_data = {
        "schema": 1,
        "date": "2026-03-01",
        "seller": "Copa",
        "locator": "LOC1",
        "adults": 2,
        "paid": {"amount": 2000.0, "currency": "BRL"},
        "source": "email: confirmation",
        "segments": [
            {
                "flight": "CM 1",
                "from": "POA",
                "to": "MIA",
                "depart": "2026-05-10T06:00",
                "arrive": "2026-05-10T18:00",
            }
        ],
    }
    purch0_file = tmp_path / "purch0.json"
    purch0_file.write_text(json.dumps(purch0_data), encoding="utf-8")

    code = ledger.main(["purchase", str(wl_path), "--leg", "0", "--data", str(purch0_file)])
    assert code == 0
    capsys.readouterr()

    # Step 3: second add-leg for return leg
    leg1_data = {
        "label": "MIA → POA · 15 mai",
        "origin": "MIA",
        "destination": "POA",
        "outbound_date": "2026-05-15",
        "adults": 2,
        "watch": False,
        "watch_off_reason": "já comprado fora da vigília",
    }
    leg1_file = tmp_path / "leg1.json"
    leg1_file.write_text(json.dumps(leg1_data), encoding="utf-8")

    code = ledger.main(["add-leg", str(wl_path), "--data", str(leg1_file)])
    assert code == 0
    out = capsys.readouterr().out.strip()
    assert out == "1"

    # Step 4: purchase --leg 1
    purch1_data = {
        "schema": 1,
        "date": "2026-03-01",
        "seller": "Copa",
        "locator": "LOC2",
        "adults": 2,
        "paid": {"amount": 2000.0, "currency": "BRL"},
        "source": "email: confirmation",
        "segments": [
            {
                "flight": "CM 2",
                "from": "MIA",
                "to": "POA",
                "depart": "2026-05-15T11:00",
                "arrive": "2026-05-15T22:00",
            }
        ],
    }
    purch1_file = tmp_path / "purch1.json"
    purch1_file.write_text(json.dumps(purch1_data), encoding="utf-8")

    code = ledger.main(["purchase", str(wl_path), "--leg", "1", "--data", str(purch1_file)])
    assert code == 0
    capsys.readouterr()

    # Step 5: coverage and summary
    code = ledger.main(["coverage", str(wl_path)])
    assert code == 0
    cov_out = json.loads(capsys.readouterr().out)
    trip_cov = cov_out["trips"][0]
    assert trip_cov["verdict"] == "uncovered"
    assert len(trip_cov["gaps"]) == 1
    assert trip_cov["gaps"][0]["nights_needed"] == [
        "2026-05-10",
        "2026-05-11",
        "2026-05-12",
        "2026-05-13",
        "2026-05-14",
    ]
    assert trip_cov["gaps"][0]["uncovered"] == [
        {"check_in": "2026-05-10", "check_out": "2026-05-15", "nights": 5}
    ]

    code = ledger.main(["summary", str(wl_path), "--today", "2026-05-10"])
    assert code == 0
    sum_out = json.loads(capsys.readouterr().out)
    trip_sum = sum_out["trips"][0]
    assert trip_sum["paid_by_currency"] == {"BRL": 4000.0}
    assert trip_sum["paid_is_partial"] is False
    assert trip_sum["coverage"]["verdict"] == "uncovered"
    assert trip_sum["coverage"]["uncovered_nights"] == 5


def test_add_leg_watch_mandatory(ledger, tmp_path, capsys):
    """Mutation 1: 'watch' is mandatory and explicit. Omitting it or non-boolean must fail."""
    wl_path = _sample_watchlist(tmp_path)
    initial_content = wl_path.read_text(encoding="utf-8")

    # Missing watch
    bad_data = {
        "label": "POA → MIA",
        "origin": "POA",
        "destination": "MIA",
        "outbound_date": "2026-05-10",
        "adults": 1,
    }
    bad_file = tmp_path / "bad_watch.json"
    bad_file.write_text(json.dumps(bad_data), encoding="utf-8")

    code = ledger.main(["add-leg", str(wl_path), "--data", str(bad_file)])
    assert code != 0
    err = capsys.readouterr().err
    assert "missing required key(s) in leg data: watch" in err
    assert wl_path.read_text(encoding="utf-8") == initial_content

    # Non-boolean watch
    bad_data["watch"] = "false"
    bad_file.write_text(json.dumps(bad_data), encoding="utf-8")
    code = ledger.main(["add-leg", str(wl_path), "--data", str(bad_file)])
    assert code != 0
    err = capsys.readouterr().err
    assert "watch must be a boolean" in err
    assert wl_path.read_text(encoding="utf-8") == initial_content


def test_add_leg_create_existing_file_fails(ledger, tmp_path, capsys):
    """Mutation 2: --create fails when watchlist file already exists (never overwrites)."""
    wl_path = tmp_path / "existing.json"
    wl_path.write_text(json.dumps({"trip": "Original"}), encoding="utf-8")

    valid_leg = {
        "label": "POA → MIA",
        "origin": "POA",
        "destination": "MIA",
        "outbound_date": "2026-05-10",
        "adults": 1,
        "watch": True,
    }
    leg_file = tmp_path / "leg.json"
    leg_file.write_text(json.dumps(valid_leg), encoding="utf-8")

    code = ledger.main([
        "add-leg",
        str(wl_path),
        "--data",
        str(leg_file),
        "--create",
        "--trip",
        "New Trip",
    ])
    assert code != 0
    err = capsys.readouterr().err
    assert "already exists" in err
    assert "--create will not overwrite" in err
    # File content preserved
    data = json.loads(wl_path.read_text(encoding="utf-8"))
    assert data == {"trip": "Original"}


def test_add_leg_create_validation_failure_leaves_no_file(ledger, tmp_path, capsys):
    """Mutation 3: If validation fails, --create must NOT leave a created file on disk."""
    wl_path = tmp_path / "not-created.json"
    assert not wl_path.exists()

    # Invalid leg data: invalid IATA code
    invalid_leg = {
        "label": "POA → MIA",
        "origin": "invalid_code",
        "destination": "MIA",
        "outbound_date": "2026-05-10",
        "adults": 1,
        "watch": True,
    }
    bad_file = tmp_path / "invalid_leg.json"
    bad_file.write_text(json.dumps(invalid_leg), encoding="utf-8")

    code = ledger.main([
        "add-leg",
        str(wl_path),
        "--data",
        str(bad_file),
        "--create",
        "--trip",
        "Fail Trip",
    ])
    assert code != 0
    err = capsys.readouterr().err
    assert "validation error" in err
    assert not wl_path.exists()


def test_add_leg_missing_file_without_create_fails(ledger, tmp_path, capsys):
    """Mutation 4: Without --create, a missing file path must fail and never create a file."""
    wl_path = tmp_path / "mistyped-path.json"
    assert not wl_path.exists()

    valid_leg = {
        "label": "POA → MIA",
        "origin": "POA",
        "destination": "MIA",
        "outbound_date": "2026-05-10",
        "adults": 1,
        "watch": True,
    }
    leg_file = tmp_path / "leg.json"
    leg_file.write_text(json.dumps(valid_leg), encoding="utf-8")

    code = ledger.main(["add-leg", str(wl_path), "--data", str(leg_file)])
    assert code != 0
    err = capsys.readouterr().err
    assert "watchlist file not found" in err
    assert not wl_path.exists()


def test_add_leg_validation_rules(ledger, tmp_path, capsys):
    """Validation rules: label, origin/dest IATA codes, outbound_date, adults, watch_off_reason, duplicate check."""
    wl_path = _sample_watchlist(tmp_path)
    initial_content = wl_path.read_text(encoding="utf-8")

    def assert_invalid(leg_dict, expected_err):
        f = tmp_path / "bad.json"
        f.write_text(json.dumps(leg_dict), encoding="utf-8")
        ret = ledger.main(["add-leg", str(wl_path), "--data", str(f)])
        assert ret != 0
        err = capsys.readouterr().err
        assert expected_err in err
        assert wl_path.read_text(encoding="utf-8") == initial_content

    base = {
        "label": "Test Leg",
        "origin": "POA",
        "destination": "MIA",
        "outbound_date": "2026-05-10",
        "adults": 1,
        "watch": True,
    }

    # 1. Empty label
    bad = dict(base)
    bad["label"] = "  "
    assert_invalid(bad, "label must be a non-empty string")

    # 2. Invalid origin
    bad = dict(base)
    bad["origin"] = "poa"
    assert_invalid(bad, "invalid IATA code 'poa' in origin: must be 3 uppercase letters")

    # 3. Invalid destination
    bad = dict(base)
    bad["destination"] = "MIA,123"
    assert_invalid(bad, "invalid IATA code '123' in destination: must be 3 uppercase letters")

    # 4. Invalid outbound_date
    bad = dict(base)
    bad["outbound_date"] = "2026/05/10"
    assert_invalid(bad, "outbound_date must be a valid ISO date")

    # 5. Invalid adults
    bad = dict(base)
    bad["adults"] = 0
    assert_invalid(bad, "adults must be an integer >= 1")
    bad["adults"] = True
    assert_invalid(bad, "adults must be an integer >= 1")

    # 6. watch: false without watch_off_reason
    bad = dict(base)
    bad["watch"] = False
    assert_invalid(bad, "watch_off_reason is required and cannot be empty when watch is false")

    # 7. Unknown key
    bad = dict(base)
    bad["extra_field"] = "foo"
    assert_invalid(bad, "unknown key(s) in leg data: extra_field")

    # 8. Duplicate leg (same origin, destination, outbound_date as leg 0 in sample watchlist: GRU,CGH -> MIA,FLL on 2026-11-05)
    dup = {
        "label": "Duplicate leg",
        "origin": "GRU,CGH",
        "destination": "MIA,FLL",
        "outbound_date": "2026-11-05",
        "adults": 1,
        "watch": True,
    }
    assert_invalid(dup, "already exists at index 0")


def test_add_leg_create_missing_trip_fails(ledger, tmp_path, capsys):
    """--create requires --trip."""
    wl_path = tmp_path / "no-trip.json"
    valid_leg = {
        "label": "POA → MIA",
        "origin": "POA",
        "destination": "MIA",
        "outbound_date": "2026-05-10",
        "adults": 1,
        "watch": True,
    }
    f = tmp_path / "leg.json"
    f.write_text(json.dumps(valid_leg), encoding="utf-8")

    code = ledger.main(["add-leg", str(wl_path), "--data", str(f), "--create"])
    assert code != 0
    err = capsys.readouterr().err
    assert "--trip NAME is required when using --create" in err
    assert not wl_path.exists()


def test_add_leg_create_invalid_home_fails(ledger, tmp_path, capsys):
    """--create with invalid --home fails and leaves no file."""
    wl_path = tmp_path / "bad-home.json"
    valid_leg = {
        "label": "POA → MIA",
        "origin": "POA",
        "destination": "MIA",
        "outbound_date": "2026-05-10",
        "adults": 1,
        "watch": True,
    }
    f = tmp_path / "leg.json"
    f.write_text(json.dumps(valid_leg), encoding="utf-8")

    code = ledger.main(["add-leg", str(wl_path), "--data", str(f), "--create", "--trip", "Trip", "--home", "poa"])
    assert code != 0
    err = capsys.readouterr().err
    assert "invalid IATA code 'poa' in --home" in err
    assert not wl_path.exists()


def test_add_leg_junta_com_watch_py(ledger, watch_mod, tmp_path, monkeypatch):
    """A watchlist created by add-leg --create with watch: false passes through watch.main() returning 0 without calling searches_left or price_leg."""
    wl_path = tmp_path / "watchlist-junta.json"
    leg_data = {
        "label": "POA → MIA",
        "origin": "POA",
        "destination": "MIA",
        "outbound_date": "2026-11-05",
        "adults": 2,
        "watch": False,
        "watch_off_reason": "já comprado",
    }
    leg_file = tmp_path / "leg.json"
    leg_file.write_text(json.dumps(leg_data), encoding="utf-8")

    code = ledger.main([
        "add-leg",
        str(wl_path),
        "--data",
        str(leg_file),
        "--create",
        "--trip",
        "EUA 2026",
        "--home",
        "POA",
    ])
    assert code == 0

    # Ensure searches_left and price_leg raise AssertionError if called
    def fail_searches_left(key):
        raise AssertionError("searches_left was called when nothing to watch")

    def fail_price_leg(key, leg):
        raise AssertionError("price_leg was called when nothing to watch")

    monkeypatch.setattr(watch_mod, "api_key", lambda: "fake-key")
    monkeypatch.setattr(watch_mod, "searches_left", fail_searches_left)
    monkeypatch.setattr(watch_mod, "price_leg", fail_price_leg)
    monkeypatch.setattr(watch_mod, "LOG", tmp_path / "watch.log")
    monkeypatch.setattr(watch_mod, "ALERTS", tmp_path / "alerts.md")
    monkeypatch.setattr(sys, "argv", ["watch.py", str(wl_path)])

    watch_exit = watch_mod.main()
    assert watch_exit == 0


# ===========================================================================
# Prompt 03b: Bounded Connection Intervals with Missing Times
# ===========================================================================


def test_coverage_connection_missing_times_same_day_omitted_can_be_covered(ledger, tmp_path, capsys):
    """Real case: A depart 12:10 / arrive null, B depart null / arrive 20:00 on same day -> 0 nights, connection omitted, verdict covered."""
    wl = {
        "trip": "EUA Conexão Mesmo Dia",
        "home": "POA",
        "legs": [
            {
                "label": "POA → MIA",
                "outbound_date": "2026-05-10",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "Copa",
                    "locator": "LOC123",
                    "adults": 1,
                    "paid": {"amount": 1000.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {
                            "flight": "CM 1",
                            "from": "POA",
                            "to": "PTY",
                            "depart": "2026-05-10T12:10",
                            "arrive": None,
                            "unmeasured_why": "horário de escala ausente no e-mail",
                        },
                        {
                            "flight": "CM 2",
                            "from": "PTY",
                            "to": "MIA",
                            "depart": None,
                            "arrive": "2026-05-10T20:00",
                            "unmeasured_why": "horário de escala ausente no e-mail",
                        },
                    ],
                },
            },
            {
                "label": "MIA → POA",
                "outbound_date": "2026-05-15",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "Copa",
                    "locator": "LOC124",
                    "adults": 1,
                    "paid": {"amount": 1000.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {
                            "flight": "CM 3",
                            "from": "MIA",
                            "to": "POA",
                            "depart": "2026-05-15T09:00",
                            "arrive": "2026-05-15T21:00",
                        }
                    ],
                },
            },
        ],
        "stays": [
            {
                "label": "Miami Hotel",
                "check_in": "2026-05-10",
                "check_out": "2026-05-15",
                "status": "booked",
                "booking": {
                    "seller": "Hotel",
                    "locator": "HTL1",
                    "source": "email",
                    "paid": {"amount": 500.0, "currency": "USD"},
                },
            }
        ],
    }
    wl_path = tmp_path / "watchlist-same-day-conn.json"
    wl_path.write_text(json.dumps(wl), encoding="utf-8")

    code = ledger.main(["coverage", str(wl_path)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    trip = out["trips"][0]

    # No gap emitted for PTY; only the destination gap in MIA
    assert len(trip["gaps"]) == 1
    assert trip["gaps"][0]["arrive_at"] == "MIA"
    assert trip["gaps"][0]["depart_from"] == "MIA"
    assert trip["gaps"][0]["derivable"] is True
    assert trip["verdict"] == "covered"


def test_coverage_connection_missing_times_multi_day_not_derivable(ledger, tmp_path, capsys):
    """A arrive null with depart on day 10, B depart null with arrive on day 12 -> 2 nights possible, derivable: false."""
    wl = {
        "trip": "Multi Day Scale Trip",
        "home": "POA",
        "legs": [
            {
                "label": "POA → MIA",
                "outbound_date": "2026-05-10",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "Copa",
                    "locator": "LOC123",
                    "adults": 1,
                    "paid": {"amount": 1000.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {
                            "flight": "CM 1",
                            "from": "POA",
                            "to": "PTY",
                            "depart": "2026-05-10T12:10",
                            "arrive": None,
                            "unmeasured_why": "horário de escala ausente no e-mail",
                        },
                        {
                            "flight": "CM 2",
                            "from": "PTY",
                            "to": "MIA",
                            "depart": None,
                            "arrive": "2026-05-12T20:00",
                            "unmeasured_why": "horário de escala ausente no e-mail",
                        },
                    ],
                },
            },
            {
                "label": "MIA → POA",
                "outbound_date": "2026-05-15",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "Copa",
                    "locator": "LOC124",
                    "adults": 1,
                    "paid": {"amount": 1000.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {
                            "flight": "CM 3",
                            "from": "MIA",
                            "to": "POA",
                            "depart": "2026-05-15T09:00",
                            "arrive": "2026-05-15T21:00",
                        }
                    ],
                },
            },
        ],
        "stays": [
            {
                "label": "Miami Hotel",
                "check_in": "2026-05-12",
                "check_out": "2026-05-15",
                "status": "booked",
                "booking": {
                    "seller": "Hotel",
                    "locator": "HTL1",
                    "source": "email",
                    "paid": {"amount": 500.0, "currency": "USD"},
                },
            }
        ],
    }
    wl_path = tmp_path / "watchlist-multi-day-scale.json"
    wl_path.write_text(json.dumps(wl), encoding="utf-8")

    code = ledger.main(["coverage", str(wl_path)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    trip = out["trips"][0]

    # Gap in PTY is emitted with derivable: False
    pty_gap = next((g for g in trip["gaps"] if g["arrive_at"] == "PTY"), None)
    assert pty_gap is not None
    assert pty_gap["derivable"] is False
    assert pty_gap["arrive"] is None
    assert pty_gap["depart"] is None
    assert pty_gap["unmeasured_why"] == "horário de escala ausente no e-mail"
    assert trip["verdict"] == "partial"


def test_coverage_connection_missing_both_times_not_derivable(ledger, tmp_path, capsys):
    """Segment A with both depart and arrive null -> derivable: false."""
    wl = {
        "trip": "Missing Both Times Trip",
        "home": "POA",
        "legs": [
            {
                "label": "POA → MIA",
                "outbound_date": "2026-05-10",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "Copa",
                    "locator": "LOC123",
                    "adults": 1,
                    "paid": {"amount": 1000.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {
                            "flight": "CM 1",
                            "from": "POA",
                            "to": "PTY",
                            "depart": None,
                            "arrive": None,
                            "unmeasured_why": "sem dados de voo A",
                        },
                        {
                            "flight": "CM 2",
                            "from": "PTY",
                            "to": "MIA",
                            "depart": "2026-05-10T14:00",
                            "arrive": "2026-05-10T20:00",
                        },
                    ],
                },
            },
            {
                "label": "MIA → POA",
                "outbound_date": "2026-05-15",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "Copa",
                    "locator": "LOC124",
                    "adults": 1,
                    "paid": {"amount": 1000.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {
                            "flight": "CM 3",
                            "from": "MIA",
                            "to": "POA",
                            "depart": "2026-05-15T09:00",
                            "arrive": "2026-05-15T21:00",
                        }
                    ],
                },
            },
        ],
        "stays": [
            {
                "label": "Miami Hotel",
                "check_in": "2026-05-10",
                "check_out": "2026-05-15",
                "status": "booked",
                "booking": {
                    "seller": "Hotel",
                    "locator": "HTL1",
                    "source": "email",
                    "paid": {"amount": 500.0, "currency": "USD"},
                },
            }
        ],
    }
    wl_path = tmp_path / "watchlist-both-null.json"
    wl_path.write_text(json.dumps(wl), encoding="utf-8")

    code = ledger.main(["coverage", str(wl_path)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    trip = out["trips"][0]

    pty_gap = next((g for g in trip["gaps"] if g["arrive_at"] == "PTY"), None)
    assert pty_gap is not None
    assert pty_gap["derivable"] is False
    assert pty_gap["unmeasured_why"] == "sem dados de voo A"
    assert trip["verdict"] == "partial"


def test_coverage_connection_missing_times_overnight_not_derivable(ledger, tmp_path, capsys):
    """A depart 23:30 (day 10) / arrive null, B depart null / arrive 00:40 next day (day 11) -> 1 night possible, derivable: false."""
    wl = {
        "trip": "Overnight Scale Trip",
        "home": "POA",
        "legs": [
            {
                "label": "POA → MIA",
                "outbound_date": "2026-05-10",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "Copa",
                    "locator": "LOC123",
                    "adults": 1,
                    "paid": {"amount": 1000.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {
                            "flight": "CM 1",
                            "from": "POA",
                            "to": "PTY",
                            "depart": "2026-05-10T23:30",
                            "arrive": None,
                            "unmeasured_why": "sem horário de escala",
                        },
                        {
                            "flight": "CM 2",
                            "from": "PTY",
                            "to": "MIA",
                            "depart": None,
                            "arrive": "2026-05-11T00:40",
                            "unmeasured_why": "sem horário de escala",
                        },
                    ],
                },
            },
            {
                "label": "MIA → POA",
                "outbound_date": "2026-05-15",
                "purchased": True,
                "purchase": {
                    "schema": 1,
                    "date": "2026-03-01",
                    "seller": "Copa",
                    "locator": "LOC124",
                    "adults": 1,
                    "paid": {"amount": 1000.0, "currency": "BRL"},
                    "source": "email",
                    "segments": [
                        {
                            "flight": "CM 3",
                            "from": "MIA",
                            "to": "POA",
                            "depart": "2026-05-15T09:00",
                            "arrive": "2026-05-15T21:00",
                        }
                    ],
                },
            },
        ],
        "stays": [
            {
                "label": "Miami Hotel",
                "check_in": "2026-05-10",
                "check_out": "2026-05-15",
                "status": "booked",
                "booking": {
                    "seller": "Hotel",
                    "locator": "HTL1",
                    "source": "email",
                    "paid": {"amount": 500.0, "currency": "USD"},
                },
            }
        ],
    }
    wl_path = tmp_path / "watchlist-overnight-scale.json"
    wl_path.write_text(json.dumps(wl), encoding="utf-8")

    code = ledger.main(["coverage", str(wl_path)])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    trip = out["trips"][0]

    # One night is possible in PTY, so it must not be omitted as a 0-night connection!
    pty_gap = next((g for g in trip["gaps"] if g["arrive_at"] == "PTY"), None)
    assert pty_gap is not None
    assert pty_gap["derivable"] is False
    assert pty_gap["unmeasured_why"] == "sem horário de escala"
    assert trip["verdict"] == "partial"


