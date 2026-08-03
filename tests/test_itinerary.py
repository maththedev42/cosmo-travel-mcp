"""Tests for check_itinerary and build_calendar (pure logic, no API)."""

from __future__ import annotations

import pytest

from cosmo_travel_mcp.tools.itinerary import build_calendar, check_itinerary

# Real hour maps as the engines return them: English uses U+202F before the
# meridiem, Portuguese localizes the KEYS and says "Fechado" for closed.
EN_HOURS = {
    "monday": "9:30 AM–5 PM",
    "tuesday": "9:30 AM–5 PM",
    "wednesday": "9:30 AM–5 PM",
    "thursday": "9:30 AM–5 PM",
    "friday": "9:30 AM–5 PM",
    "saturday": "9:30 AM–5 PM",
    "sunday": "9:30 AM–5 PM",
}

# MARGS, captured live 2026-08-01: closed Mondays.
PT_HOURS = {
    "sábado": "10:00–19:00",
    "domingo": "10:00–19:00",
    "segunda-feira": "Fechado",
    "terça-feira": "10:00–19:00",
    "quarta-feira": "10:00–19:00",
    "quinta-feira": "10:00–19:00",
    "sexta-feira": "10:00–19:00",
}

# Hours that can never fail a window check, so the ordering and transit tests
# below stay about ordering and transit. They still have to be *present*: a
# scheduled stop with no hours is reported `unchecked`, by design.
ALWAYS = dict.fromkeys(
    ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"],
    "Open 24 hours",
)

# 2026-08-03 is a Monday; 2026-08-04 a Tuesday.
MONDAY = "2026-08-03"
TUESDAY = "2026-08-04"


def _day(date_str, *stops):
    return {"date": date_str, "stops": list(stops)}


def _stop(name, start, end, **extra):
    return {"name": name, "start": start, "end": end, **extra}


# ---------------------------------------------------------------------------
# Closed days — the headline check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_on_a_closed_day_is_a_blocker_with_localized_keys():
    result = await check_itinerary([
        _day(MONDAY, _stop("MARGS", "14:00", "16:00", operating_hours=PT_HOURS))
    ])

    assert result["blockers"] == 1
    finding = result["findings"][0]
    assert finding["reason"] == "closed"
    assert finding["severity"] == "blocker"
    assert "MARGS" in finding["detail"]


@pytest.mark.asyncio
async def test_same_stop_on_an_open_day_passes():
    result = await check_itinerary([
        _day(TUESDAY, _stop("MARGS", "14:00", "16:00", operating_hours=PT_HOURS))
    ])

    assert result["findings"] == []


@pytest.mark.asyncio
async def test_unknown_hours_language_is_reported_not_assumed_open():
    """An unverifiable stop must surface, never silently pass."""
    japanese = {"月曜日": "10:00–19:00", "火曜日": "10:00–19:00"}
    result = await check_itinerary([
        _day(MONDAY, _stop("Somewhere", "14:00", "16:00", operating_hours=japanese))
    ])

    assert result["unchecked"] == 1
    assert result["blockers"] == 0
    assert result["findings"][0]["reason"] == "hours_unreadable"


# ---------------------------------------------------------------------------
# Opening windows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_arriving_before_opening_is_a_blocker():
    result = await check_itinerary([
        _day(TUESDAY, _stop("Jungle Island", "08:00", "10:00", operating_hours=EN_HOURS))
    ])

    assert [f["reason"] for f in result["findings"]] == ["before_opening"]


@pytest.mark.asyncio
async def test_staying_past_closing_is_a_blocker():
    result = await check_itinerary([
        _day(TUESDAY, _stop("Jungle Island", "15:00", "18:30", operating_hours=EN_HOURS))
    ])

    assert [f["reason"] for f in result["findings"]] == ["after_closing"]


@pytest.mark.asyncio
async def test_visit_inside_the_window_passes():
    result = await check_itinerary([
        _day(TUESDAY, _stop("Jungle Island", "10:00", "12:00", operating_hours=EN_HOURS))
    ])

    assert result["findings"] == []


@pytest.mark.asyncio
async def test_open_24_hours_never_flags_a_window():
    always = dict.fromkeys(EN_HOURS, "Aberto 24 horas")
    result = await check_itinerary([
        _day(TUESDAY, _stop("Parque Redenção", "06:00", "23:00", operating_hours=always))
    ])

    assert result["findings"] == []


# ---------------------------------------------------------------------------
# Overlaps and ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overlapping_stops_are_a_blocker():
    result = await check_itinerary([
        _day(TUESDAY,
             _stop("A", "10:00", "12:00", operating_hours=ALWAYS),
             _stop("B", "11:00", "13:00", operating_hours=ALWAYS))
    ])

    assert [f["reason"] for f in result["findings"]] == ["overlap"]


@pytest.mark.asyncio
async def test_backwards_stop_is_a_blocker():
    result = await check_itinerary([
        _day(TUESDAY, _stop("A", "15:00", "14:00", operating_hours=ALWAYS))
    ])

    assert [f["reason"] for f in result["findings"]] == ["negative_duration"]


@pytest.mark.asyncio
async def test_sequential_stops_pass():
    result = await check_itinerary([
        _day(TUESDAY,
             _stop("A", "10:00", "12:00", operating_hours=ALWAYS),
             _stop("B", "13:00", "15:00", operating_hours=ALWAYS))
    ])

    assert result["findings"] == []
    assert result["stops_checked"] == 2


# ---------------------------------------------------------------------------
# Transit feasibility
# ---------------------------------------------------------------------------

# Real Miami coordinates: Wynwood Walls to Zoo Miami is ~20 km.
WYNWOOD = {"lat": 25.8010, "lng": -80.1990}
ZOO_MIAMI = {"lat": 25.6110, "lng": -80.3990}


@pytest.mark.asyncio
async def test_impossible_transit_gap_is_flagged():
    result = await check_itinerary([
        _day(TUESDAY,
             _stop("Wynwood Walls", "10:00", "11:00", coordinates=WYNWOOD, operating_hours=ALWAYS),
             _stop("Zoo Miami", "11:10", "13:00", coordinates=ZOO_MIAMI, operating_hours=ALWAYS))
    ])

    assert [f["reason"] for f in result["findings"]] == ["transit"]
    assert result["warnings"] == 1
    assert result["blockers"] == 0


@pytest.mark.asyncio
async def test_generous_transit_gap_passes():
    result = await check_itinerary([
        _day(TUESDAY,
             _stop("Wynwood Walls", "10:00", "11:00", coordinates=WYNWOOD, operating_hours=ALWAYS),
             _stop("Zoo Miami", "13:00", "15:00", coordinates=ZOO_MIAMI, operating_hours=ALWAYS))
    ])

    assert result["findings"] == []


@pytest.mark.asyncio
async def test_overlapping_stops_report_overlap_only_not_negative_transit():
    """An overlap already says it; "-30 min allowed" on top is noise."""
    result = await check_itinerary([
        _day(TUESDAY,
             _stop("Wynwood Walls", "10:00", "12:00", coordinates=WYNWOOD, operating_hours=ALWAYS),
             _stop("Zoo Miami", "11:30", "13:00", coordinates=ZOO_MIAMI, operating_hours=ALWAYS))
    ])

    assert [f["reason"] for f in result["findings"]] == ["overlap"]
    assert result["warnings"] == 0


@pytest.mark.asyncio
async def test_nearby_stops_do_not_trigger_transit_warnings():
    near = {"lat": 25.8015, "lng": -80.1995}
    result = await check_itinerary([
        _day(TUESDAY,
             _stop("Wynwood Walls", "10:00", "11:00", coordinates=WYNWOOD, operating_hours=ALWAYS),
             _stop("Cafe next door", "11:05", "12:00", coordinates=near, operating_hours=ALWAYS))
    ])

    assert result["findings"] == []


# ---------------------------------------------------------------------------
# Shape / robustness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clean_itinerary_returns_no_findings():
    result = await check_itinerary([
        _day(TUESDAY, _stop("MARGS", "10:30", "12:00", operating_hours=PT_HOURS)),
        _day(TUESDAY, _stop("Jungle Island", "10:00", "12:00", operating_hours=EN_HOURS)),
    ])

    assert result == {
        "findings": [], "blockers": 0, "warnings": 0, "unchecked": 0, "stops_checked": 2,
    }


@pytest.mark.asyncio
async def test_stops_without_hours_or_coordinates_are_accepted():
    result = await check_itinerary([_day(TUESDAY, {"name": "Free time"})])
    assert result["stops_checked"] == 1


@pytest.mark.asyncio
async def test_malformed_date_is_reported_not_raised():
    result = await check_itinerary([
        _day("15/08/2026", _stop("X", "10:00", "11:00", operating_hours=EN_HOURS))
    ])

    assert any(f["reason"] == "bad_date" for f in result["findings"])


@pytest.mark.asyncio
async def test_days_must_be_a_list():
    with pytest.raises(ValueError, match="days must be a list"):
        await check_itinerary({"date": TUESDAY})


# ---------------------------------------------------------------------------
# build_calendar
# ---------------------------------------------------------------------------

ITEM = {
    "title": "MARGS",
    "start": "2026-08-04T10:30",
    "end": "2026-08-04T12:00",
    "location": "Praça da Alfândega, Porto Alegre",
}


@pytest.mark.asyncio
async def test_ics_has_valid_envelope_and_crlf_endings():
    result = await build_calendar([ITEM])
    ics = result["ics"]

    assert ics.startswith("BEGIN:VCALENDAR\r\n")
    assert ics.rstrip().endswith("END:VCALENDAR")
    assert "VERSION:2.0" in ics
    assert ics.count("BEGIN:VEVENT") == 1
    assert ics.count("END:VEVENT") == 1
    # RFC 5545 requires CRLF, and a bare LF breaks strict parsers.
    assert "\n" in ics and ics.replace("\r\n", "").count("\n") == 0


@pytest.mark.asyncio
async def test_floating_times_carry_no_timezone_marker():
    """No TZID and no trailing Z: the event shows at the stated wall-clock time."""
    result = await build_calendar([ITEM])

    assert "DTSTART:20260804T103000" in result["ics"]
    assert "TZID" not in result["ics"]
    assert "DTSTART:20260804T103000Z" not in result["ics"]


@pytest.mark.asyncio
async def test_timezone_name_is_attached_when_given():
    result = await build_calendar([ITEM], timezone_name="America/Sao_Paulo")

    assert "DTSTART;TZID=America/Sao_Paulo:20260804T103000" in result["ics"]
    assert "ctz=America%2FSao_Paulo" in result["events"][0]["google_calendar_url"]


@pytest.mark.asyncio
async def test_special_characters_are_escaped_per_rfc5545():
    result = await build_calendar([{
        "title": "Dinner, drinks; then a show",
        "start": "2026-08-04T20:00",
        "description": "Line one\nLine two",
    }])
    ics = result["ics"]

    assert "SUMMARY:Dinner\\, drinks\\; then a show" in ics
    assert "\\n" in ics


@pytest.mark.asyncio
async def test_long_summary_is_folded_with_leading_space():
    result = await build_calendar([{
        "title": "A" * 200, "start": "2026-08-04T20:00",
    }])

    for line in result["ics"].split("\r\n"):
        assert len(line.encode("utf-8")) <= 75, f"unfolded line: {line[:40]}…"
    assert "\r\n " in result["ics"]


@pytest.mark.asyncio
async def test_google_calendar_url_is_escaped_and_dated():
    result = await build_calendar([ITEM])
    url = result["events"][0]["google_calendar_url"]

    assert url.startswith("https://calendar.google.com/calendar/render?")
    assert "action=TEMPLATE" in url
    assert "dates=20260804T103000%2F20260804T120000" in url
    assert "text=MARGS" in url
    # Spaces and accents must be percent-encoded, never raw.
    assert " " not in url


@pytest.mark.asyncio
async def test_uid_is_deterministic_so_reimport_does_not_duplicate():
    first = await build_calendar([ITEM])
    second = await build_calendar([ITEM])

    def uid(ics: str) -> str:
        return next(line for line in ics.split("\r\n") if line.startswith("UID:"))

    assert uid(first["ics"]) == uid(second["ics"])


@pytest.mark.asyncio
async def test_missing_end_defaults_to_one_hour():
    result = await build_calendar([{"title": "Show", "start": "2026-08-04T20:00"}])

    assert result["events"][0]["end"] == "2026-08-04T21:00"


@pytest.mark.asyncio
async def test_note_tells_the_client_to_confirm_before_writing():
    result = await build_calendar([ITEM])

    assert "confirm" in result["note"].lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad,match", [
    ([], "non-empty list"),
    ([{"start": "2026-08-04T10:00"}], "no title"),
    ([{"title": "X"}], "no start time"),
    ([{"title": "X", "start": "04/08/2026"}], "YYYY-MM-DDTHH:MM"),
    ([{"title": "X", "start": "2026-08-04T10:00", "end": "2026-08-04T09:00"}], "before it starts"),
])
async def test_malformed_items_are_rejected(bad, match):
    with pytest.raises(ValueError, match=match):
        await build_calendar(bad)


@pytest.mark.asyncio
async def test_scheduled_stop_without_hours_is_unchecked_not_clean():
    """A concert has a venue and a time but no operating_hours.

    Found by running the plan-a-trip skill end to end: three date-bound stops
    (the New Year's Eve street party, a concert, a theatre show) came back with
    `unchecked: 0` and an empty findings list, which reads as "verified" for
    stops nothing had looked at.
    """
    result = await check_itinerary([
        _day(TUESDAY, _stop("Joshua Bell at Lincoln Center", "14:00", "17:00"))
    ])

    assert result["unchecked"] == 1
    assert result["blockers"] == 0
    assert result["findings"][0]["reason"] == "hours_missing"


@pytest.mark.asyncio
async def test_unscheduled_note_without_hours_stays_quiet():
    """An entry with no start time is not a claim about opening, so it is not noise."""
    result = await check_itinerary([_day(TUESDAY, {"name": "Free time"})])

    assert result["findings"] == []
    assert result["stops_checked"] == 1


# ---------------------------------------------------------------------------
# Per-item timezones — a trip crosses zones, a calendar file does not
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_each_item_can_carry_its_own_timezone():
    """A Porto Alegre departure and a New York show in one file, each correct.

    Anchoring the whole calendar to one zone showed the 01:35 POA departure as
    01:35 in New York — four hours off, on the item a traveller most needs right.
    """
    result = await build_calendar([
        {"title": "POA → Panamá", "start": "2026-12-20T01:35", "end": "2026-12-20T06:45",
         "timezone_name": "America/Sao_Paulo"},
        {"title": "Réveillon na Times Square", "start": "2026-12-31T18:00",
         "end": "2027-01-01T00:30", "timezone_name": "America/New_York"},
    ])

    assert "DTSTART;TZID=America/Sao_Paulo:20261220T013500" in result["ics"]
    assert "DTSTART;TZID=America/New_York:20261231T180000" in result["ics"]
    assert "ctz=America%2FSao_Paulo" in result["events"][0]["google_calendar_url"]
    assert "ctz=America%2FNew_York" in result["events"][1]["google_calendar_url"]
    assert "2 timezones" in result["note"]


@pytest.mark.asyncio
async def test_item_timezone_overrides_the_call_level_default():
    result = await build_calendar(
        [
            {"title": "Voo de casa", "start": "2026-12-20T01:35",
             "timezone_name": "America/Sao_Paulo"},
            {"title": "Show", "start": "2026-12-31T18:00"},
        ],
        timezone_name="America/New_York",
    )

    assert result["events"][0]["timezone_name"] == "America/Sao_Paulo"
    assert result["events"][1]["timezone_name"] == "America/New_York"


@pytest.mark.asyncio
async def test_no_timezone_anywhere_stays_floating():
    result = await build_calendar([{"title": "Almoço", "start": "2026-12-20T12:00"}])

    assert "TZID" not in result["ics"]
    assert "DTSTART:20261220T120000" in result["ics"]
    assert result["events"][0]["timezone_name"] is None
    assert "floating" in result["note"]
