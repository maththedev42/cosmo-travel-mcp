"""Tests for the plan_email_search tool and email sender registry."""

from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from cosmo_travel_mcp.onboarding import KEYLESS_TOOLS
from cosmo_travel_mcp.tools import email_search
from cosmo_travel_mcp.tools.email_search import (
    EMAIL_SENDERS,
    READING_INSTRUCTIONS,
    VALID_KINDS,
    plan_email_search,
)

LEDGER_PY = Path(__file__).resolve().parents[1] / "skills" / "plan-a-trip" / "ledger.py"


@pytest.fixture(scope="module")
def ledger():
    spec = importlib.util.spec_from_file_location("ledger_script", LEDGER_PY)
    assert spec and spec.loader, f"could not load {LEDGER_PY}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# 1. includeTrash in every query and no inbox exclusion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_query_has_include_trash_and_no_inbox_exclusion():
    """Every generated query across all passes and inputs must set includeTrash: True
    and never exclude the trash or restrict to inbox alone.
    """
    plans = [
        await plan_email_search(kinds=["flight", "lodging"]),
        await plan_email_search(
            kinds=["flight", "car", "ticket"],
            destination="Miami",
            window_start="2026-09-01",
            window_end="2026-10-04",
            known_locators=["LOC123", "CONF456"],
            include_unconfirmed_senders=True,
        ),
        await plan_email_search(
            kinds=["insurance"],
            destination="Buenos Aires",
            include_unconfirmed_senders=False,
        ),
    ]

    for plan in plans:
        assert len(plan["queries"]) > 0
        for q in plan["queries"]:
            assert q["connector_args"] == {"includeTrash": True}, (
                f"query missing includeTrash: {q}"
            )
            gq = q["gmail_query"]
            assert "-in:trash" not in gq, f"query excludes trash: {gq}"
            assert "in:inbox" not in gq, f"query restricts to inbox: {gq}"


# ---------------------------------------------------------------------------
# 2. Passes distinguish confirmed from unconfirmed senders
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_passes_distinguish_confirmed_from_unconfirmed_and_flag_disables_pass2():
    """Pass 1 must contain ONLY confirmed senders; Pass 2 must contain ONLY unconfirmed.
    Setting include_unconfirmed_senders=False must completely omit pass 2.
    """
    plan = await plan_email_search(kinds=["flight", "lodging"])

    confirmed_senders = {
        s["sender"] for s in EMAIL_SENDERS if s["confirmed"] is True
    }
    unconfirmed_senders = {
        s["sender"] for s in EMAIL_SENDERS if s["confirmed"] is False
    }

    pass1_queries = [q for q in plan["queries"] if q.get("pass") == 1 and q["kind"] in ("flight", "lodging")]
    pass2_queries = [q for q in plan["queries"] if q.get("pass") == 2 and q["kind"] in ("flight", "lodging")]

    assert len(pass1_queries) > 0
    for q in pass1_queries:
        assert q["confirmed_senders"] is True
        # Verify no unconfirmed sender leaked into pass 1 query
        for unconf in unconfirmed_senders:
            assert unconf not in q["gmail_query"], (
                f"unconfirmed sender {unconf} leaked into pass 1 query: {q['gmail_query']}"
            )

    assert len(pass2_queries) > 0
    for q in pass2_queries:
        assert q["confirmed_senders"] is False
        assert q["why"] == "hipótese: remetente nunca visto numa caixa real"
        # Verify no confirmed sender leaked into pass 2 query
        for conf in confirmed_senders:
            assert conf not in q["gmail_query"], (
                f"confirmed sender {conf} leaked into pass 2 query: {q['gmail_query']}"
            )

    # When include_unconfirmed_senders=False, pass 2 is completely removed
    plan_no_unconf = await plan_email_search(
        kinds=["flight", "lodging"],
        include_unconfirmed_senders=False,
    )
    pass2_disabled = [q for q in plan_no_unconf["queries"] if q.get("pass") == 2]
    assert pass2_disabled == [], "pass 2 queries must be removed when include_unconfirmed_senders=False"


# ---------------------------------------------------------------------------
# 3. Date window formatting and validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_date_window_formatting_and_validation():
    """Dates in YYYY-MM-DD become after:YYYY/MM/DD and before:YYYY/MM/DD in queries.
    Invalid dates raise ValueError. Omitted window generates no after/before and warns in limits.
    """
    plan = await plan_email_search(
        kinds=["flight"],
        window_start="2026-09-01",
        window_end="2026-10-04",
    )
    for q in plan["queries"]:
        assert "after:2026/09/01" in q["gmail_query"]
        assert "before:2026/10/04" in q["gmail_query"]

    # Invalid dates raise ValueError
    with pytest.raises(ValueError, match="window_start must be in YYYY-MM-DD format"):
        await plan_email_search(kinds=["flight"], window_start="invalid-date")

    with pytest.raises(ValueError, match="window_end must be in YYYY-MM-DD format"):
        await plan_email_search(kinds=["flight"], window_end="2026/09/01")

    with pytest.raises(ValueError, match="cannot be before window_start"):
        await plan_email_search(
            kinds=["flight"],
            window_start="2026-10-04",
            window_end="2026-09-01",
        )

    # Unrestricted search: no after/before, and limits warns
    plan_unrestricted = await plan_email_search(kinds=["flight"])
    for q in plan_unrestricted["queries"]:
        assert "after:" not in q["gmail_query"]
        assert "before:" not in q["gmail_query"]

    assert any("irrestrita" in limit.lower() or "unrestricted" in limit.lower() for limit in plan_unrestricted["limits"])


# ---------------------------------------------------------------------------
# 4. Known locators
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_known_locators_queries_and_validation():
    """Each valid known locator generates a dedicated query with quotes and trash inclusion.
    Invalid locators raise ValueError.
    """
    plan = await plan_email_search(
        kinds=["flight"],
        known_locators=["LOC123", "ABCDEFGH"],
    )
    loc_queries = [q for q in plan["queries"] if q["kind"] == "locator"]
    assert len(loc_queries) == 2
    assert loc_queries[0]["gmail_query"] == '"LOC123"'
    assert loc_queries[0]["connector_args"] == {"includeTrash": True}
    assert loc_queries[1]["gmail_query"] == '"ABCDEFGH"'
    assert loc_queries[1]["connector_args"] == {"includeTrash": True}

    # Invalid locators
    with pytest.raises(ValueError, match="known locator must be 5-20 alphanumeric characters"):
        await plan_email_search(kinds=["flight"], known_locators=["ab"])

    with pytest.raises(ValueError, match="known locator must be 5-20 alphanumeric characters"):
        await plan_email_search(kinds=["flight"], known_locators=["a b c"])

    with pytest.raises(ValueError, match="known locator must be 5-20 alphanumeric characters"):
        await plan_email_search(kinds=["flight"], known_locators=["A" * 25])


# ---------------------------------------------------------------------------
# 5. Kinds validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kinds_empty_and_unknown_validation():
    """Empty kinds or unknown kind values raise ValueError with valid kinds list."""
    with pytest.raises(ValueError, match="kinds must be a non-empty list"):
        await plan_email_search(kinds=[])

    with pytest.raises(ValueError, match="unknown kind 'hotel'"):
        await plan_email_search(kinds=["hotel"])


# ---------------------------------------------------------------------------
# 6. extract["lodging"] matches ledger stay booking keys
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_lodging_matches_ledger_stay_booking_keys(ledger):
    """extract['lodging'] must contain check_in, check_out, and exactly the keys
    from ledger.py ALLOWED_STAY_BOOKING_KEYS prefixed with booking.
    """
    plan = await plan_email_search(kinds=["lodging"])
    lodging_extract = plan["extract"]["lodging"]

    assert "check_in" in lodging_extract
    assert "check_out" in lodging_extract

    booking_keys = {
        k.removeprefix("booking.")
        for k in lodging_extract
        if k.startswith("booking.")
    }

    assert booking_keys == ledger.ALLOWED_STAY_BOOKING_KEYS, (
        f"extract['lodging'] booking keys {booking_keys} do not match "
        f"ledger.ALLOWED_STAY_BOOKING_KEYS {ledger.ALLOWED_STAY_BOOKING_KEYS}"
    )


# ---------------------------------------------------------------------------
# 7. reading instructions cover required scenarios
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reading_instructions_cover_required_scenarios():
    """reading must explicitly mention get_message, HTML, anexo PDF,
    conferir datas e destino, and vazio é afirmação.
    """
    plan = await plan_email_search(kinds=["flight"])
    all_reading = " ".join(plan["reading"])

    assert "get_message" in all_reading, "reading must mention get_message"
    assert "HTML" in all_reading, "reading must mention HTML"
    assert "anexo PDF" in all_reading or "PDF" in all_reading, "reading must mention anexo PDF"
    assert "datas" in all_reading and "destino" in all_reading, "reading must mention checking datas and destino"
    assert "vazio é afirmação" in all_reading.lower(), "reading must mention vazio é afirmação"


# ---------------------------------------------------------------------------
# 8. Sender registry integrity and parity with 00-contexto.md
# ---------------------------------------------------------------------------


def test_sender_registry_integrity_and_confirmed_table_parity():
    """All confirmed senders must match 00-contexto.md exactly, carry valid ISO seen dates,
    and contain no personal email addresses.
    """
    expected_confirmed = {
        "info@info.latam.com",
        "notifications@cns.copaair.com",
        "no-reply@info.email.aa.com",
        "contato@123milhas.com",
        "automated@airbnb.com",
        "express@airbnb.com",
        "reply@email-support.airbnb.com",
        "seucheckin@magikey.com.br",
        "sender@notifications.onfly.com.br",
    }

    confirmed_in_registry = {
        s["sender"] for s in EMAIL_SENDERS if s["confirmed"] is True
    }

    assert confirmed_in_registry == expected_confirmed, (
        f"confirmed senders in registry disagree with 00-contexto.md: "
        f"unexpected={confirmed_in_registry - expected_confirmed}, "
        f"missing={expected_confirmed - confirmed_in_registry}"
    )

    for s in EMAIL_SENDERS:
        if s["confirmed"]:
            assert s["seen"], f"confirmed sender {s['sender']} missing seen date"
            # Verify seen is a valid ISO date
            datetime.fromisoformat(s["seen"])
            # Ensure no personal email addresses (e.g. personal gmail/hotmail/yahoo)
            domain = s["sender"].split("@")[-1]
            assert domain not in ("gmail.com", "hotmail.com", "yahoo.com", "outlook.com")


# ---------------------------------------------------------------------------
# 9. Drift check: plan_email_search in KEYLESS_TOOLS
# ---------------------------------------------------------------------------


def test_drift_new_tool_in_keyless_tools():
    """plan_email_search must be listed in KEYLESS_TOOLS in onboarding.py."""
    assert "plan_email_search" in KEYLESS_TOOLS
