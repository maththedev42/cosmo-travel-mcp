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
