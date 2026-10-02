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

