"""Itinerary checking and calendar export — pure computation, no API calls.

Neither tool here spends quota. They exist because two jobs in trip planning
are ones a language model does badly and arithmetic does well:

* **`check_itinerary`** — comparing every scheduled stop against its own
  opening hours, its neighbours' times, and the distance between them. A plan
  that reads beautifully and sends someone to a museum on its closing day is
  the characteristic failure of AI-written itineraries.
* **`build_calendar`** — emitting RFC 5545 and correctly-escaped calendar
  URLs, which is mechanical work with real edge cases (escaping, line folding,
  timezones).

Neither returns prose. `check_itinerary` returns findings and `build_calendar`
returns artifacts; how any of it is displayed is the client's business.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from math import asin, cos, radians, sin, sqrt
from typing import Any
from urllib.parse import urlencode

# ---------------------------------------------------------------------------
# Weekday resolution
# ---------------------------------------------------------------------------

# `operating_hours` keys come back in the language of the `hl` used to fetch
# them, so a date cannot be matched to its hours without knowing the language.
# These tables are transcribed, not guessed — and when a key set matches none
# of them the checker says so (an "unchecked" finding) rather than assuming the
# place is open. Silently passing an unverifiable stop is the failure this
# whole tool exists to prevent.
_WEEKDAY_NAMES: dict[str, tuple[str, ...]] = {
    # index 0 = Monday, matching date.weekday()
    "en": ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"),
    "pt": ("segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"),
    "es": ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"),
    "fr": ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"),
    "it": ("lunedi", "martedi", "mercoledi", "giovedi", "venerdi", "sabato", "domenica"),
    "de": ("montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag"),
}

# Values that mean "shut", in the same languages.
_CLOSED_WORDS: frozenset[str] = frozenset(
    {"closed", "fechado", "cerrado", "ferme", "chiuso", "geschlossen"}
)

# Values that mean "open all day".
_ALWAYS_OPEN_WORDS: tuple[str, ...] = (
    "24 hours", "24 horas", "24 heures", "24 ore", "24 stunden", "aberto 24",
)


def _fold(text: str) -> str:
    """Lowercase, strip accents and punctuation — for matching, never display.

    Keeps every alphanumeric character, not just ASCII. Restricting to
    ``[a-z0-9]`` collapsed non-Latin keys ("月曜日") to the empty string, and an
    empty key prefix-matches every weekday name — so a Japanese or Arabic
    hours map resolved as if understood and the stop passed unchecked. Non-ASCII
    keys must stay intact so they fail to match and are reported honestly.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return "".join(c for c in stripped.casefold() if c.isalnum())


def _hours_for_date(operating_hours: dict[str, Any], on: date) -> tuple[str | None, bool]:
    """Return ``(hours_string, resolved)`` for *on* from a localized hours map.

    ``resolved`` is False when the key language is not one this module knows —
    the caller must then report the stop as unchecked rather than assume it.
    """
    if not isinstance(operating_hours, dict) or not operating_hours:
        return None, False

    folded = {_fold(k): v for k, v in operating_hours.items()}
    index = on.weekday()

    for names in _WEEKDAY_NAMES.values():
        wanted = _fold(names[index])
        for key, value in folded.items():
            # Guard the degenerate ends: an empty or 1-2 character key would
            # prefix-match unrelated day names.
            if len(key) < 3 or not wanted:
                continue
            # "segunda" matches "segunda-feira"; "tues" would not match "thursday".
            if key.startswith(wanted) or wanted.startswith(key):
                return (value if isinstance(value, str) else None), True

    return None, False


def _is_closed(hours: str | None) -> bool:
    return bool(hours) and _fold(hours) in {_fold(w) for w in _CLOSED_WORDS}


def _is_always_open(hours: str | None) -> bool:
    if not hours:
        return False
    low = hours.casefold()
    return any(marker in low for marker in _ALWAYS_OPEN_WORDS)


# ---------------------------------------------------------------------------
# Time parsing
# ---------------------------------------------------------------------------

# Matches "9:30 AM", "09:30", "21:00" — including the U+202F narrow no-break
# space the Google engines put before the meridiem.
_TIME_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([APap][Mm])?")


def _parse_clock(value: str) -> int | None:
    """Parse a wall-clock time into minutes past midnight, or None."""
    if not value:
        return None
    match = _TIME_RE.match(value.strip().replace(" ", " "))
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or "").lower()
    if meridiem == "pm" and hour != 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return hour * 60 + minute


def _parse_open_window(hours: str) -> tuple[int, int] | None:
    """Parse "9:30 AM–5 PM" into (open_minutes, close_minutes)."""
    normalized = hours.replace(" ", " ")
    parts = re.split(r"[–—-]|\bto\b|\bas\b", normalized, maxsplit=1)
    if len(parts) != 2:
        return None
    start, end = _parse_clock(parts[0]), _parse_clock(parts[1])
    if start is None or end is None:
        return None
    return start, end


def _haversine_km(a: dict[str, Any], b: dict[str, Any]) -> float | None:
    """Great-circle distance in km between two {lat, lng} points."""
    try:
        lat1, lng1 = float(a["lat"]), float(a["lng"])
        lat2, lng2 = float(b["lat"]), float(b["lng"])
    except (KeyError, TypeError, ValueError):
        return None
    dlat, dlng = radians(lat2 - lat1), radians(lng2 - lng1)
    h = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    return 2 * 6371.0 * asin(sqrt(h))


# Straight-line km/h assumed for city transit. Deliberately low: real routes
# are longer than the great-circle line, so this errs toward flagging a gap
# that is actually fine rather than passing one that is not.
_CITY_TRANSIT_KMH = 18.0


# ---------------------------------------------------------------------------
# check_itinerary
# ---------------------------------------------------------------------------


def _finding(severity: str, day: int, reason: str, detail: str, **extra: Any) -> dict[str, Any]:
    finding = {"severity": severity, "day": day, "reason": reason, "detail": detail}
    finding.update(extra)
    return finding


async def check_itinerary(days: list[dict[str, Any]]) -> dict[str, Any]:
    """Check a drafted itinerary for conflicts. Costs nothing — no API calls.

    Run this **before** presenting a plan to the traveller. It catches the
    mistakes that make an itinerary look right and be wrong: a stop scheduled
    on its closing day, a visit outside opening hours, two stops booked at the
    same time, or a gap too short to cross the distance involved.

    Args:
        days: One entry per day, each ``{"date": "YYYY-MM-DD", "stops": [...]}``.
            Each stop takes ``{"name", "start", "end"}`` (times as ``"HH:MM"``)
            and optionally ``operating_hours`` and ``coordinates`` copied
            **verbatim** from a ``search_things_to_do`` result — do not
            translate or reformat them, the checker reads them as returned.

    Returns:
        ``{"findings": [...], "blockers": int, "warnings": int, "unchecked": int,
        "stops_checked": int}``. Each finding carries ``severity``
        (``blocker`` | ``warning`` | ``unchecked``), ``day``, ``reason`` and a
        human-readable ``detail``. An empty ``findings`` list means every stop
        was verifiable and no conflict was found.
    """
    if not isinstance(days, list):
        raise ValueError("days must be a list of {date, stops} objects")

    findings: list[dict[str, Any]] = []
    stops_checked = 0

    for day_index, day in enumerate(days, start=1):
        if not isinstance(day, dict):
            raise ValueError(f"day {day_index} is not an object")

        raw_date = day.get("date")
        day_date: date | None = None
        if raw_date:
            try:
                day_date = date.fromisoformat(str(raw_date))
            except ValueError:
                findings.append(_finding(
                    "warning", day_index, "bad_date",
                    f"date {raw_date!r} is not YYYY-MM-DD; hours could not be checked",
                ))

        stops = day.get("stops") or []
        if not isinstance(stops, list):
            raise ValueError(f"day {day_index} 'stops' must be a list")

        previous: dict[str, Any] | None = None
        previous_end: int | None = None

        for stop in stops:
            if not isinstance(stop, dict):
                continue
            stops_checked += 1
            name = stop.get("name", "(unnamed)")
            start = _parse_clock(str(stop.get("start", "")))
            end = _parse_clock(str(stop.get("end", "")))

            # ── ordering and overlap ──────────────────────────────────
            if start is not None and end is not None and end <= start:
                findings.append(_finding(
                    "blocker", day_index, "negative_duration",
                    f"{name} ends at or before it starts", stop=name,
                ))

            if previous_end is not None and start is not None and start < previous_end:
                findings.append(_finding(
                    "blocker", day_index, "overlap",
                    f"{name} starts before {previous.get('name', 'the previous stop')} ends",
                    stop=name,
                ))

            # ── transit feasibility ───────────────────────────────────
            if previous is not None and previous_end is not None and start is not None:
                here, there = stop.get("coordinates"), previous.get("coordinates")
                if isinstance(here, dict) and isinstance(there, dict):
                    km = _haversine_km(there, here)
                    if km is not None and km > 1.0:
                        need = (km / _CITY_TRANSIT_KMH) * 60
                        have = start - previous_end
                        # A negative gap is already reported as an overlap
                        # blocker; adding "-30 min allowed" on top is noise
                        # around a problem the traveller has already been told
                        # about.
                        if 0 <= have < need:
                            findings.append(_finding(
                                "warning", day_index, "transit",
                                f"{km:.1f} km from {previous.get('name', 'previous stop')} "
                                f"to {name}: about {need:.0f} min of travel, "
                                f"{have} min allowed",
                                stop=name,
                            ))

            # ── opening hours ─────────────────────────────────────────
            hours_map = stop.get("operating_hours")
            if isinstance(hours_map, dict) and hours_map and day_date is not None:
                hours, resolved = _hours_for_date(hours_map, day_date)
                if not resolved:
                    findings.append(_finding(
                        "unchecked", day_index, "hours_unreadable",
                        f"{name}: could not match {day_date.isoformat()} to a weekday in "
                        f"{sorted(hours_map)!r} — verify opening hours by hand",
                        stop=name,
                    ))
                elif _is_closed(hours):
                    findings.append(_finding(
                        "blocker", day_index, "closed",
                        f"{name} is closed on {day_date.isoformat()} (hours say {hours!r})",
                        stop=name,
                    ))
                elif hours and not _is_always_open(hours):
                    window = _parse_open_window(hours)
                    if window is None:
                        findings.append(_finding(
                            "unchecked", day_index, "hours_unparsed",
                            f"{name}: could not read opening window from {hours!r}",
                            stop=name,
                        ))
                    else:
                        opens, closes = window
                        if start is not None and start < opens:
                            findings.append(_finding(
                                "blocker", day_index, "before_opening",
                                f"{name} scheduled at {stop.get('start')} but opens at "
                                f"{hours}",
                                stop=name,
                            ))
                        if end is not None and closes > opens and end > closes:
                            findings.append(_finding(
                                "blocker", day_index, "after_closing",
                                f"{name} scheduled until {stop.get('end')} but closes at "
                                f"{hours}",
                                stop=name,
                            ))

            if start is not None:
                previous, previous_end = stop, (end if end is not None else start)

    severities = [f["severity"] for f in findings]
    return {
        "findings": findings,
        "blockers": severities.count("blocker"),
        "warnings": severities.count("warning"),
        "unchecked": severities.count("unchecked"),
        "stops_checked": stops_checked,
    }


# ---------------------------------------------------------------------------
# build_calendar
# ---------------------------------------------------------------------------

_GCAL_BASE = "https://calendar.google.com/calendar/render"


def _ics_escape(value: str) -> str:
    """Escape per RFC 5545 §3.3.11 (backslash, semicolon, comma, newline)."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _ics_fold(line: str) -> str:
    """Fold to 75 octets per RFC 5545 §3.1, continuation lines start with a space."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    chunks: list[str] = []
    current = bytearray()
    for char in line:
        char_bytes = char.encode("utf-8")
        limit = 75 if not chunks else 74
        if len(current) + len(char_bytes) > limit:
            chunks.append(current.decode("utf-8"))
            current = bytearray()
        current += char_bytes
    if current:
        chunks.append(current.decode("utf-8"))
    return "\r\n ".join(chunks)


def _parse_local(value: str) -> datetime:
    """Parse ``YYYY-MM-DDTHH:MM`` (or with seconds) as a floating local time."""
    text = str(value).strip().replace(" ", "T")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(
        f"start/end must look like 'YYYY-MM-DDTHH:MM' (local wall-clock time), got {value!r}"
    )


async def build_calendar(
    items: list[dict[str, Any]],
    calendar_name: str = "Trip",
    timezone_name: str | None = None,
) -> dict[str, Any]:
    """Turn itinerary items into an .ics file and per-item Google Calendar links.

    Costs nothing — no API calls. This tool only *generates* the artifacts; it
    cannot write to anyone's calendar.

    **If a calendar tool is available in this session, offer to create these
    events with it — and ask the traveller before writing anything.** If no
    calendar tool is connected, present the links and the .ics instead. This
    server cannot see or call other MCP servers, so that choice is yours.

    Times are treated as **floating local wall-clock** — "14:00" means 14:00
    where the event happens, which is what a traveller means and what keeps a
    museum visit correct regardless of the device's timezone. Pass
    ``timezone_name`` (IANA, e.g. ``"America/Sao_Paulo"``) only when you know
    it; it is attached to the Google links so they resolve unambiguously.

    For flights, whose departure and arrival are in *different* zones, create
    one item per leg using each airport's local time.

    Args:
        items: Each ``{"title", "start", "end"}`` with times as
            ``"YYYY-MM-DDTHH:MM"``, plus optional ``location`` and
            ``description``.
        calendar_name: Name embedded in the .ics.
        timezone_name: Optional IANA timezone for the Google Calendar links.

    Returns:
        ``{"ics", "filename", "events": [{title, start, end, google_calendar_url}],
        "event_count", "note"}``.
    """
    if not isinstance(items, list) or not items:
        raise ValueError("items must be a non-empty list of calendar entries")

    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//cosmo-travel-mcp//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ics_escape(calendar_name)}",
    ]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    events: list[dict[str, Any]] = []

    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"item {index} is not an object")
        title = str(item.get("title") or "").strip()
        if not title:
            raise ValueError(f"item {index} has no title")
        if not item.get("start"):
            raise ValueError(f"item {index} ({title!r}) has no start time")

        start_dt = _parse_local(item["start"])
        end_dt = _parse_local(item["end"]) if item.get("end") else start_dt + timedelta(hours=1)
        if end_dt <= start_dt:
            raise ValueError(f"item {index} ({title!r}) ends at or before it starts")

        location = str(item.get("location") or "")
        description = str(item.get("description") or "")

        local_fmt = "%Y%m%dT%H%M%S"
        dtstart, dtend = start_dt.strftime(local_fmt), end_dt.strftime(local_fmt)

        # Deterministic UID: re-running the tool must not create duplicates
        # in a calendar that already imported the previous file.
        digest = hashlib.sha1(
            f"{title}|{dtstart}|{location}".encode("utf-8")
        ).hexdigest()[:16]

        lines += ["BEGIN:VEVENT", f"UID:{digest}@cosmo-travel-mcp", f"DTSTAMP:{stamp}"]
        if timezone_name:
            lines += [f"DTSTART;TZID={timezone_name}:{dtstart}",
                      f"DTEND;TZID={timezone_name}:{dtend}"]
        else:
            # No TZID and no Z = floating: renders at this wall-clock time in
            # whatever zone the device is in, which is what a traveller wants.
            lines += [f"DTSTART:{dtstart}", f"DTEND:{dtend}"]
        lines.append(_ics_fold(f"SUMMARY:{_ics_escape(title)}"))
        if location:
            lines.append(_ics_fold(f"LOCATION:{_ics_escape(location)}"))
        if description:
            lines.append(_ics_fold(f"DESCRIPTION:{_ics_escape(description)}"))
        lines.append("END:VEVENT")

        query: dict[str, str] = {
            "action": "TEMPLATE",
            "text": title,
            "dates": f"{dtstart}/{dtend}",
        }
        if location:
            query["location"] = location
        if description:
            query["details"] = description
        if timezone_name:
            query["ctz"] = timezone_name

        events.append({
            "title": title,
            "start": start_dt.isoformat(timespec="minutes"),
            "end": end_dt.isoformat(timespec="minutes"),
            "google_calendar_url": f"{_GCAL_BASE}?{urlencode(query)}",
        })

    lines.append("END:VCALENDAR")

    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "-", calendar_name).strip("-").lower() or "trip"

    note = (
        "Times are floating local wall-clock (no timezone attached), so each "
        "event shows at the stated time. "
        if not timezone_name
        else f"Times are anchored to {timezone_name}. "
    )
    note += (
        "If a calendar tool is connected in this session, offer to add these "
        "events with it and confirm before writing. Otherwise share the .ics "
        "or the per-event links."
    )

    return {
        "ics": "\r\n".join(lines) + "\r\n",
        "filename": f"{safe_name}.ics",
        "events": events,
        "event_count": len(events),
        "note": note,
    }


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(mcp: Any) -> None:
    """Register itinerary tools on a FastMCP instance."""
    mcp.tool()(check_itinerary)
    mcp.tool()(build_calendar)
