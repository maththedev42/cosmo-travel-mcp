#!/usr/bin/env python3
"""The trip ledger — records purchased legs and tracks travel coverage.

Standard library only. Designed to live in `skills/plan-a-trip/` and work
alongside `watch.py` on the same watchlist state files.

Usage:
    python3 ledger.py purchase WATCHLIST --leg N --data FILE [--replace] [--date-changed]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

# Hour threshold (03:00) to determine if a night belongs to a ground gap.
# A traveler sleeping from date d to d+1 is required to have accommodation
# if and only if they are on the ground at 03:00 of d+1:
# arrive <= (d+1)T03:00 < depart
PIVOT_HOUR = 3

ALLOWED_PURCHASE_KEYS = {
    "schema",
    "date",
    "seller",
    "locator",
    "adults",
    "segments",
    "paid",
    "source",
    "open_issues",
    "notes",
    "extra",
    "paid_unmeasured_why",
}

REQUIRED_PURCHASE_KEYS = {
    "date",
    "seller",
    "locator",
    "adults",
    "segments",
    "paid",
    "source",
}

ALLOWED_SEGMENT_KEYS = {"flight", "from", "to", "depart", "arrive", "unmeasured_why"}
REQUIRED_SEGMENT_KEYS = {"flight", "from", "to", "depart", "arrive"}

ALLOWED_STAY_KEYS = {
    "label",
    "check_in",
    "check_out",
    "status",
    "booking",
    "why",
    "notes",
}

REQUIRED_STAY_KEYS = {
    "check_in",
    "check_out",
    "status",
}

ALLOWED_STAY_BOOKING_KEYS = {
    "seller",
    "locator",
    "source",
    "paid",
    "refundable_until",
    "paid_unmeasured_why",
}

REQUIRED_STAY_BOOKING_KEYS = {
    "seller",
    "locator",
    "source",
    "paid",
}

ALLOWED_ADD_LEG_KEYS = {
    "label",
    "origin",
    "destination",
    "outbound_date",
    "adults",
    "watch",
    "watch_off_reason",
}

REQUIRED_ADD_LEG_KEYS = {
    "label",
    "origin",
    "destination",
    "outbound_date",
    "adults",
    "watch",
}


def state_dir() -> Path:
    """Resolve the travel state directory fresh on every call."""
    override = os.environ.get("COSMO_TRAVEL_STATE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".cosmo-travel"


def validate_purchase(
    purchase: dict,
    leg: dict,
    leg_idx: int,
    all_legs: list[dict],
    replace: bool = False,
    date_changed: bool = False,
) -> tuple[list[str], list[str]]:
    """Validate a purchase block against schema 1 and leg constraints.

    Returns (errors, warnings).
    """
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Allowed / required keys
    extra_keys = set(purchase.keys()) - ALLOWED_PURCHASE_KEYS
    if extra_keys:
        errors.append(
            f"unknown key(s) in purchase: {', '.join(sorted(extra_keys))}. "
            f"Allowed keys: {', '.join(sorted(ALLOWED_PURCHASE_KEYS))}"
        )

    missing_keys = REQUIRED_PURCHASE_KEYS - set(purchase.keys())
    if missing_keys:
        errors.append(
            f"missing required key(s) in purchase: {', '.join(sorted(missing_keys))}"
        )

    if "schema" in purchase and purchase["schema"] != 1:
        errors.append(f"schema must be 1, got {purchase['schema']}")

    # 2. date (valid ISO YYYY-MM-DD)
    if "date" in purchase:
        val = purchase["date"]
        if not isinstance(val, str):
            errors.append(f"date must be a valid ISO date string (YYYY-MM-DD), got {type(val).__name__}")
        else:
            try:
                d = datetime.strptime(val, "%Y-%m-%d").date()
                if d.strftime("%Y-%m-%d") != val:
                    errors.append(f"date must be YYYY-MM-DD, got {val}")
            except ValueError:
                errors.append(f"date must be a valid ISO date (YYYY-MM-DD), got {val}")

    # Strings: seller, locator, source
    for field in ("seller", "locator", "source"):
        if field in purchase:
            v = purchase[field]
            if not isinstance(v, str) or not v.strip():
                errors.append(f"{field} must be a non-empty string")

    # 3. adults (int >= 1, matches leg["adults"])
    if "adults" in purchase:
        val = purchase["adults"]
        leg_adults = leg.get("adults")
        if not isinstance(val, int) or isinstance(val, bool) or val < 1:
            errors.append(f"adults must be an integer >= 1, got {val}")
        elif leg_adults is not None and val != leg_adults:
            errors.append(f"adults ({val}) must match leg adults ({leg_adults})")

    # Optional fields types
    if "open_issues" in purchase:
        val = purchase["open_issues"]
        if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
            errors.append("open_issues must be a list of strings")

    if "notes" in purchase:
        val = purchase["notes"]
        if not isinstance(val, str):
            errors.append(f"notes must be a string, got {type(val).__name__}")

    if "extra" in purchase:
        val = purchase["extra"]
        if not isinstance(val, dict):
            errors.append(f"extra must be an object (dict), got {type(val).__name__}")

    # 4. segments
    segments = purchase.get("segments")
    if not isinstance(segments, list) or len(segments) == 0:
        errors.append("segments must be a non-empty list")
    else:
        for idx, seg in enumerate(segments):
            if not isinstance(seg, dict):
                errors.append(f"segment {idx} must be a dict")
                continue

            seg_extra = set(seg.keys()) - ALLOWED_SEGMENT_KEYS
            if seg_extra:
                errors.append(
                    f"segment {idx} has unknown key(s): {', '.join(sorted(seg_extra))}"
                )

            seg_missing = REQUIRED_SEGMENT_KEYS - set(seg.keys())
            if seg_missing:
                errors.append(
                    f"segment {idx} missing required key(s): {', '.join(sorted(seg_missing))}"
                )
                continue

            fl = seg.get("flight")
            if not isinstance(fl, str) or not fl.strip():
                errors.append(f"segment {idx} 'flight' must be a non-empty string")

            for f_code in ("from", "to"):
                code = seg.get(f_code)
                if not isinstance(code, str) or len(code) != 3 or not code.isupper() or not code.isalpha():
                    errors.append(f"segment {idx} '{f_code}' must be 3 uppercase ASCII letters, got {code!r}")

            has_null = False
            for f_time in ("depart", "arrive"):
                time_val = seg.get(f_time)
                if time_val is None:
                    has_null = True
                elif isinstance(time_val, str):
                    try:
                        datetime.strptime(time_val, "%Y-%m-%dT%H:%M")
                    except ValueError:
                        errors.append(
                            f"segment {idx} '{f_time}' must be YYYY-MM-DDTHH:MM without offset, got {time_val}"
                        )
                else:
                    errors.append(
                        f"segment {idx} '{f_time}' must be string or null, got {type(time_val).__name__}"
                    )

            unmeasured_why = seg.get("unmeasured_why")
            if has_null:
                if not isinstance(unmeasured_why, str) or not unmeasured_why.strip():
                    errors.append(
                        f"segment {idx} has null depart/arrive but missing non-empty 'unmeasured_why'"
                    )
            else:
                if unmeasured_why is not None:
                    errors.append(
                        f"segment {idx} has no null depart/arrive, so 'unmeasured_why' is not allowed"
                    )

        # 5. origin/destination match
        if len(segments) > 0 and isinstance(segments[0], dict) and isinstance(segments[0].get("from"), str):
            allowed_origins = [o.strip() for o in leg.get("origin", "").split(",") if o.strip()]
            if allowed_origins and segments[0]["from"] not in allowed_origins:
                errors.append(
                    f"first segment 'from' ({segments[0]['from']}) not in leg origin ({leg.get('origin')})"
                )

        if len(segments) > 0 and isinstance(segments[-1], dict) and isinstance(segments[-1].get("to"), str):
            allowed_dests = [d.strip() for d in leg.get("destination", "").split(",") if d.strip()]
            if allowed_dests and segments[-1]["to"] not in allowed_dests:
                errors.append(
                    f"last segment 'to' ({segments[-1]['to']}) not in leg destination ({leg.get('destination')})"
                )

        # 6. Consecutive segments comparison & warning
        for i in range(len(segments) - 1):
            s1 = segments[i]
            s2 = segments[i + 1]
            if isinstance(s1, dict) and isinstance(s2, dict):
                arr = s1.get("arrive")
                dep = s2.get("depart")
                if arr is not None and dep is not None and isinstance(arr, str) and isinstance(dep, str):
                    try:
                        arr_dt = datetime.strptime(arr, "%Y-%m-%dT%H:%M")
                        dep_dt = datetime.strptime(dep, "%Y-%m-%dT%H:%M")
                        if dep_dt < arr_dt:
                            errors.append(
                                f"segment {i+1} depart ({dep}) is earlier than segment {i} arrive ({arr}) at same airport"
                            )
                    except ValueError:
                        pass

                s1_to = s1.get("to")
                s2_from = s2.get("from")
                if s1_to and s2_from and s1_to != s2_from:
                    warnings.append(
                        f"consecutive segments change airport from {s1_to} to {s2_from}"
                    )

        # 8. Date changed check
        if len(segments) > 0 and isinstance(segments[0], dict):
            dep = segments[0].get("depart")
            if dep and isinstance(dep, str) and "T" in dep:
                dep_date = dep.split("T")[0]
                leg_outbound = leg.get("outbound_date")
                if leg_outbound and dep_date != leg_outbound:
                    if not date_changed:
                        errors.append(
                            f"depart date ({dep_date}) differs from leg outbound_date ({leg_outbound}); "
                            f"pass --date-changed to allow"
                        )

    # 7. paid validation
    paid = purchase.get("paid")
    paid_unmeasured_why = purchase.get("paid_unmeasured_why")

    if paid is None:
        if "paid" in purchase:
            if not isinstance(paid_unmeasured_why, str) or not paid_unmeasured_why.strip():
                errors.append("paid is null: non-empty 'paid_unmeasured_why' is required")
    elif isinstance(paid, dict):
        if "amount" in paid or "currency" in paid:
            paid_keys = set(paid.keys())
            if paid_keys != {"amount", "currency"}:
                extra = paid_keys - {"amount", "currency"}
                errors.append(f"paid dict with amount has unknown keys: {', '.join(sorted(extra))}")
            amt = paid.get("amount")
            curr = paid.get("currency")
            if amt == 0:
                errors.append("amount is 0: valor ausente se registra como null com motivo")
            elif not isinstance(amt, (int, float)) or isinstance(amt, bool) or amt < 0:
                errors.append(f"paid 'amount' must be a positive number, got {amt}")
            if not isinstance(curr, str) or len(curr) != 3 or not curr.isupper() or not curr.isalpha():
                errors.append(f"paid 'currency' must be 3 uppercase ASCII letters, got {curr!r}")
            if paid_unmeasured_why is not None:
                errors.append("paid has amount, so 'paid_unmeasured_why' is not allowed")
        elif "included_in_leg" in paid:
            paid_keys = set(paid.keys())
            if paid_keys != {"included_in_leg"}:
                extra = paid_keys - {"included_in_leg"}
                errors.append(f"paid included_in_leg dict has unknown keys: {', '.join(sorted(extra))}")
            target_n = paid.get("included_in_leg")
            if not isinstance(target_n, int) or isinstance(target_n, bool):
                errors.append(f"included_in_leg must be an integer, got {target_n}")
            elif target_n < 0 or target_n >= len(all_legs):
                errors.append(f"included_in_leg index {target_n} is out of bounds (0..{len(all_legs)-1})")
            elif target_n == leg_idx:
                errors.append(f"included_in_leg cannot point to the same leg ({target_n})")
            else:
                target_leg = all_legs[target_n]
                target_purchase = target_leg.get("purchase")
                if not isinstance(target_purchase, dict) or target_purchase.get("schema") != 1:
                    errors.append(f"target leg {target_n} does not have schema 1 purchase")
                else:
                    target_paid = target_purchase.get("paid")
                    if not isinstance(target_paid, dict) or not isinstance(target_paid.get("amount"), (int, float)) or target_paid.get("amount", 0) <= 0:
                        errors.append(f"target leg {target_n} does not have real paid amount (got {target_paid})")
                    target_locator = target_purchase.get("locator")
                    cur_locator = purchase.get("locator")
                    if target_locator != cur_locator:
                        errors.append(
                            f"locator mismatch with target leg {target_n}: '{cur_locator}' vs '{target_locator}'"
                        )
            if paid_unmeasured_why is not None:
                errors.append("paid is included_in_leg, so 'paid_unmeasured_why' is not allowed")
        else:
            errors.append(
                f"paid dict must have ('amount', 'currency') or 'included_in_leg', got keys: {list(paid.keys())}"
            )
    else:
        errors.append(f"paid must be dict or null, got {type(paid).__name__}")

    # 9. Existing purchase replacement
    if "purchase" in leg:
        if not replace:
            errors.append("leg already has purchase block; pass --replace to overwrite")
        else:
            if "purchase_legacy" in leg:
                errors.append("leg already has purchase_legacy; cannot replace again")

    return errors, warnings


def atomic_write_json(path: Path, data: dict) -> None:
    """Atomically write data as JSON to path via a temp file in the same directory."""
    path = path.resolve()
    temp_path = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        temp_path.write_text(content, encoding="utf-8")
        # Verify parse before replace
        json.loads(temp_path.read_text(encoding="utf-8"))
        os.replace(temp_path, path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


def resolve_watchlist(target: str) -> Path:
    """Resolve a watchlist file path, falling back to state_dir() if bare filename."""
    p = Path(target)
    if not p.exists() and not p.is_absolute() and ("/" not in target and "\\" not in target):
        candidate = state_dir() / target
        if candidate.exists():
            return candidate
    return p


def resolve_watchlists(paths: list[str] | None) -> list[Path]:
    """Resolve a list of watchlist files or scan state_dir() for watchlist-*.json."""
    if paths:
        return [Path(p) for p in paths]
    sdir = state_dir()
    if not sdir.exists():
        return []
    return sorted(
        [p for p in sdir.glob("watchlist-*.json") if ".bak" not in p.name]
    )


def validate_stay(
    stay: dict,
    existing_stays: list[dict] | None = None,
) -> tuple[list[str], list[str]]:
    """Validate a stay block and check for overlap with existing stays.

    Returns (errors, warnings).
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(stay, dict):
        return ["stay must be a JSON object"], []

    # 1. Allowed / required top-level keys
    extra_keys = set(stay.keys()) - ALLOWED_STAY_KEYS
    if extra_keys:
        errors.append(
            f"unknown key(s) in stay: {', '.join(sorted(extra_keys))}. "
            f"Allowed keys: {', '.join(sorted(ALLOWED_STAY_KEYS))}"
        )

    missing_keys = REQUIRED_STAY_KEYS - set(stay.keys())
    if missing_keys:
        errors.append(
            f"missing required key(s) in stay: {', '.join(sorted(missing_keys))}"
        )

    # 2. check_in and check_out
    cin: date | None = None
    cout: date | None = None
    if "check_in" in stay:
        val = stay["check_in"]
        if not isinstance(val, str):
            errors.append(f"check_in must be a valid ISO date string (YYYY-MM-DD), got {type(val).__name__}")
        else:
            try:
                d = datetime.strptime(val, "%Y-%m-%d").date()
                if d.strftime("%Y-%m-%d") != val:
                    errors.append(f"check_in must be YYYY-MM-DD, got {val}")
                else:
                    cin = d
            except ValueError:
                errors.append(f"check_in must be a valid ISO date (YYYY-MM-DD), got {val}")

    if "check_out" in stay:
        val = stay["check_out"]
        if not isinstance(val, str):
            errors.append(f"check_out must be a valid ISO date string (YYYY-MM-DD), got {type(val).__name__}")
        else:
            try:
                d = datetime.strptime(val, "%Y-%m-%d").date()
                if d.strftime("%Y-%m-%d") != val:
                    errors.append(f"check_out must be YYYY-MM-DD, got {val}")
                else:
                    cout = d
            except ValueError:
                errors.append(f"check_out must be a valid ISO date (YYYY-MM-DD), got {val}")

    if cin and cout:
        if cout <= cin:
            errors.append(f"check_out ({stay['check_out']}) must be after check_in ({stay['check_in']})")

    # 3. status
    status = stay.get("status")
    if status not in ("booked", "not_needed"):
        errors.append(f"status must be 'booked' or 'not_needed', got '{status}'")
    else:
        if status == "booked":
            if "why" in stay and stay["why"] is not None:
                errors.append("why is not allowed when status is 'booked'")
            if "booking" not in stay or not isinstance(stay["booking"], dict):
                errors.append("booking object is required when status is 'booked'")
            else:
                b = stay["booking"]
                extra_b_keys = set(b.keys()) - ALLOWED_STAY_BOOKING_KEYS
                if extra_b_keys:
                    errors.append(
                        f"unknown key(s) in booking: {', '.join(sorted(extra_b_keys))}. "
                        f"Allowed keys: {', '.join(sorted(ALLOWED_STAY_BOOKING_KEYS))}"
                    )
                missing_b_keys = REQUIRED_STAY_BOOKING_KEYS - set(b.keys())
                if missing_b_keys:
                    errors.append(
                        f"missing required key(s) in booking: {', '.join(sorted(missing_b_keys))}"
                    )

                for str_field in ("seller", "locator", "source"):
                    if str_field in b:
                        if not isinstance(b[str_field], str) or not b[str_field].strip():
                            errors.append(f"booking.{str_field} must be a non-empty string")

                # paid
                if "paid" in b:
                    paid = b["paid"]
                    if paid is None:
                        why_unmeasured = b.get("paid_unmeasured_why") or stay.get("paid_unmeasured_why")
                        if not why_unmeasured or not isinstance(why_unmeasured, str) or not why_unmeasured.strip():
                            errors.append("when booking.paid is null, paid_unmeasured_why is required and cannot be empty")
                    elif isinstance(paid, dict):
                        if "included_in_leg" in paid:
                            errors.append("included_in_leg is only for flights, not allowed for stays")
                        if "amount" not in paid or "currency" not in paid:
                            errors.append("booking.paid object must contain 'amount' and 'currency'")
                        else:
                            amt = paid["amount"]
                            if not isinstance(amt, (int, float)) or isinstance(amt, bool) or amt <= 0:
                                errors.append(f"booking.paid.amount must be a positive number (> 0), got {amt}")
                            curr = paid["currency"]
                            if not isinstance(curr, str) or len(curr) != 3 or not curr.isupper() or not curr.isalpha():
                                errors.append(f"booking.paid.currency must be a 3-letter uppercase IATA/ISO code, got {curr}")
                    else:
                        errors.append(f"booking.paid must be an object or null, got {type(paid).__name__}")

                # refundable_until
                if "refundable_until" in b and b["refundable_until"] is not None:
                    ref_val = b["refundable_until"]
                    if not isinstance(ref_val, str):
                        errors.append("booking.refundable_until must be an ISO date string (YYYY-MM-DD)")
                    else:
                        try:
                            ref_d = datetime.strptime(ref_val, "%Y-%m-%d").date()
                            if ref_d.strftime("%Y-%m-%d") != ref_val:
                                errors.append(f"booking.refundable_until must be YYYY-MM-DD, got {ref_val}")
                            elif cin and ref_d > cin:
                                errors.append(
                                    f"booking.refundable_until ({ref_val}) must be <= check_in ({stay.get('check_in')})"
                                )
                        except ValueError:
                            errors.append(f"booking.refundable_until must be a valid ISO date (YYYY-MM-DD), got {ref_val}")

        elif status == "not_needed":
            if "booking" in stay and stay["booking"] is not None:
                errors.append("booking is not allowed when status is 'not_needed'")
            if not stay.get("why") or not isinstance(stay["why"], str) or not stay["why"].strip():
                errors.append("why is required and cannot be empty when status is 'not_needed'")

    # Overlap check with existing stays
    if cin and cout and existing_stays:
        for ex in existing_stays:
            ex_in_str = ex.get("check_in")
            ex_out_str = ex.get("check_out")
            if ex_in_str and ex_out_str:
                try:
                    ex_in = datetime.strptime(ex_in_str, "%Y-%m-%d").date()
                    ex_out = datetime.strptime(ex_out_str, "%Y-%m-%d").date()
                    if max(cin, ex_in) < min(cout, ex_out):
                        lbl = ex.get("label", "unlabeled stay")
                        warnings.append(
                            f"stay overlaps with existing stay '{lbl}' ({ex_in_str} to {ex_out_str})"
                        )
                except ValueError:
                    pass

    return errors, warnings


def validate_add_leg(
    leg_data: dict,
    existing_legs: list[dict] | None = None,
) -> list[str]:
    """Validate leg data for add-leg subcommand.

    Returns list of error messages.
    """
    errors: list[str] = []

    if not isinstance(leg_data, dict):
        return ["leg data must be a JSON object"]

    # 1. Allowed / required keys
    extra_keys = set(leg_data.keys()) - ALLOWED_ADD_LEG_KEYS
    if extra_keys:
        errors.append(
            f"unknown key(s) in leg data: {', '.join(sorted(extra_keys))}. "
            f"Allowed keys: {', '.join(sorted(ALLOWED_ADD_LEG_KEYS))}"
        )

    missing_keys = REQUIRED_ADD_LEG_KEYS - set(leg_data.keys())
    if missing_keys:
        errors.append(
            f"missing required key(s) in leg data: {', '.join(sorted(missing_keys))}"
        )

    # 2. label
    if "label" in leg_data:
        val = leg_data["label"]
        if not isinstance(val, str) or not val.strip():
            errors.append("label must be a non-empty string")

    # 3. origin and destination
    for field in ("origin", "destination"):
        if field in leg_data:
            val = leg_data[field]
            if not isinstance(val, str):
                errors.append(f"{field} must be a comma-separated string of 3-letter IATA codes")
            else:
                codes = [c.strip() for c in val.split(",") if c.strip()]
                if not codes:
                    errors.append(f"{field} must contain at least one 3-letter IATA code")
                else:
                    for c in codes:
                        if len(c) != 3 or not c.isalpha() or not c.isupper():
                            errors.append(f"invalid IATA code '{c}' in {field}: must be 3 uppercase letters")

    # 4. outbound_date
    if "outbound_date" in leg_data:
        val = leg_data["outbound_date"]
        if not isinstance(val, str):
            errors.append("outbound_date must be a valid ISO date string (YYYY-MM-DD)")
        else:
            try:
                d = datetime.strptime(val, "%Y-%m-%d").date()
                if d.strftime("%Y-%m-%d") != val:
                    errors.append(f"outbound_date must be YYYY-MM-DD, got {val}")
            except ValueError:
                errors.append(f"outbound_date must be a valid ISO date (YYYY-MM-DD), got {val}")

    # 5. adults
    if "adults" in leg_data:
        val = leg_data["adults"]
        if not isinstance(val, int) or isinstance(val, bool) or val < 1:
            errors.append(f"adults must be an integer >= 1, got {val}")

    # 6. watch and watch_off_reason
    if "watch" in leg_data:
        watch_val = leg_data["watch"]
        if not isinstance(watch_val, bool):
            errors.append(f"watch must be a boolean (true/false), got {type(watch_val).__name__}")
        elif watch_val is False:
            reason = leg_data.get("watch_off_reason")
            if not reason or not isinstance(reason, str) or not reason.strip():
                errors.append("watch_off_reason is required and cannot be empty when watch is false")

    # 7. Duplicate leg check
    if existing_legs and "origin" in leg_data and "destination" in leg_data and "outbound_date" in leg_data:
        orig = leg_data["origin"]
        dest = leg_data["destination"]
        out_d = leg_data["outbound_date"]
        for idx, ex_leg in enumerate(existing_legs):
            if (
                ex_leg.get("origin") == orig
                and ex_leg.get("destination") == dest
                and ex_leg.get("outbound_date") == out_d
            ):
                errors.append(
                    f"leg with same origin ('{orig}'), destination ('{dest}'), "
                    f"and outbound_date ('{out_d}') already exists at index {idx}"
                )
                break

    return errors


def group_consecutive_nights(nights: list[str]) -> list[dict]:
    """Group sorted ISO date strings of nights into check_in / check_out ranges.

    Each range has:
        - check_in: first night
        - check_out: day after last night
        - nights: count of nights
    """
    if not nights:
        return []

    dates = [datetime.strptime(n, "%Y-%m-%d").date() for n in nights]
    ranges: list[dict] = []

    start_d = dates[0]
    prev_d = dates[0]
    count = 1

    for d in dates[1:]:
        if d == prev_d + timedelta(days=1):
            prev_d = d
            count += 1
        else:
            ranges.append({
                "check_in": start_d.strftime("%Y-%m-%d"),
                "check_out": (prev_d + timedelta(days=1)).strftime("%Y-%m-%d"),
                "nights": count,
            })
            start_d = d
            prev_d = d
            count = 1

    ranges.append({
        "check_in": start_d.strftime("%Y-%m-%d"),
        "check_out": (prev_d + timedelta(days=1)).strftime("%Y-%m-%d"),
        "nights": count,
    })
    return ranges


def calculate_coverage(wl: dict, filename: str) -> dict:
    """Derive flight gaps and compare against stays.

    Follows the 03:00 pivot rule (PIVOT_HOUR = 3):
    arrive <= (d+1)T03:00 < depart
    """
    home_val = wl.get("home")
    if not home_val or not isinstance(home_val, str) or not home_val.strip():
        return {
            "file": filename,
            "home": None,
            "verdict": "not_derivable",
            "gaps": [],
            "nights_outside_any_gap": [],
            "not_derivable": [{"reason": "missing_home"}],
            "open_start": None,
            "open_end": None,
        }

    home = home_val.strip()
    home_codes = {c.strip() for c in home.split(",") if c.strip()}

    not_derivable: list[dict] = []
    schema1_legs: list[tuple[int, dict]] = []

    for idx, leg in enumerate(wl.get("legs", [])):
        if leg.get("purchased") is True:
            purchase = leg.get("purchase")
            if isinstance(purchase, dict) and purchase.get("schema") == 1:
                schema1_legs.append((idx, leg))
            else:
                not_derivable.append({
                    "leg": idx,
                    "reason": "legacy_or_missing_purchase_block",
                })

    if not schema1_legs:
        return {
            "file": filename,
            "home": home,
            "verdict": "not_derivable",
            "gaps": [],
            "nights_outside_any_gap": [],
            "not_derivable": not_derivable or [{"reason": "no_schema_1_purchased_legs"}],
            "open_start": None,
            "open_end": None,
        }

    # Sort legs by outbound_date, preserving original order on ties
    sorted_legs = sorted(schema1_legs, key=lambda x: (x[1].get("outbound_date") or "", x[0]))

    # Flatten segments in order
    all_segments: list[dict] = []
    for _leg_idx, leg in sorted_legs:
        for seg in leg.get("purchase", {}).get("segments", []):
            all_segments.append(seg)

    if not all_segments:
        return {
            "file": filename,
            "home": home,
            "verdict": "not_derivable",
            "gaps": [],
            "nights_outside_any_gap": [],
            "not_derivable": not_derivable or [{"reason": "no_segments_in_purchased_legs"}],
            "open_start": None,
            "open_end": None,
        }

    # Open start / Open end
    first_seg = all_segments[0]
    last_seg = all_segments[-1]

    open_start = None
    if first_seg.get("from") not in home_codes:
        open_start = first_seg.get("depart", "")[:10] if first_seg.get("depart") else sorted_legs[0][1].get("outbound_date")

    open_end = None
    if last_seg.get("to") not in home_codes:
        open_end = last_seg.get("arrive", "")[:10] if last_seg.get("arrive") else sorted_legs[-1][1].get("outbound_date")

    # Unpurchased legs to check for pending_legs_inside
    unpurchased_legs = [
        (idx, leg)
        for idx, leg in enumerate(wl.get("legs", []))
        if leg.get("purchased") is not True
    ]

    stays = wl.get("stays", [])
    gaps: list[dict] = []
    all_needed_nights: set[str] = set()

    for i in range(len(all_segments) - 1):
        seg_a = all_segments[i]
        seg_b = all_segments[i + 1]

        arrive_at = seg_a.get("to")
        arrive_str = seg_a.get("arrive")
        depart_from = seg_b.get("from")
        depart_str = seg_b.get("depart")

        if not arrive_str or not depart_str:
            gap: dict = {
                "arrive_at": arrive_at,
                "arrive": arrive_str,
                "depart_from": depart_from,
                "depart": depart_str,
                "derivable": False,
                "late_arrival": False,
                "nights_needed": [],
                "covered": [],
                "uncovered": [],
                "pending_legs_inside": [],
            }
            unmeasured_why = seg_a.get("unmeasured_why") or seg_b.get("unmeasured_why")
            if unmeasured_why:
                gap["unmeasured_why"] = unmeasured_why
            gaps.append(gap)
            continue

        try:
            arrive_dt = datetime.fromisoformat(arrive_str)
            depart_dt = datetime.fromisoformat(depart_str)
        except ValueError:
            gap = {
                "arrive_at": arrive_at,
                "arrive": arrive_str,
                "depart_from": depart_from,
                "depart": depart_str,
                "derivable": False,
                "late_arrival": False,
                "nights_needed": [],
                "covered": [],
                "uncovered": [],
                "pending_legs_inside": [],
            }
            gaps.append(gap)
            continue

        # Derive nights needed using PIVOT_HOUR (03:00)
        # arrive <= (d+1)T03:00 < depart
        nights_needed: list[str] = []
        cur_d = arrive_dt.date() - timedelta(days=1)
        end_d = depart_dt.date()
        while cur_d <= end_d:
            pivot_dt = datetime.combine(cur_d + timedelta(days=1), time(PIVOT_HOUR, 0))
            if arrive_dt.tzinfo is not None:
                pivot_dt = pivot_dt.replace(tzinfo=arrive_dt.tzinfo)
            if arrive_dt <= pivot_dt < depart_dt:
                nights_needed.append(cur_d.strftime("%Y-%m-%d"))
            cur_d += timedelta(days=1)

        # Connection on same day with 0 nights is omitted completely
        if not nights_needed:
            continue

        late_arrival = any(
            datetime.strptime(n, "%Y-%m-%d").date() < arrive_dt.date()
            for n in nights_needed
        )

        covered: list[str] = []
        uncovered_nights: list[str] = []
        for n in nights_needed:
            # Check if covered by any stay in stays (booked or not_needed)
            is_cov = False
            for s in stays:
                s_in = s.get("check_in")
                s_out = s.get("check_out")
                if s_in and s_out and s_in <= n < s_out:
                    is_cov = True
                    break
            if is_cov:
                covered.append(n)
            else:
                uncovered_nights.append(n)

        uncovered = group_consecutive_nights(uncovered_nights)

        # Pending legs inside this gap
        pending_legs_inside: list[dict] = []
        arr_date_str = arrive_dt.date().strftime("%Y-%m-%d")
        dep_date_str = depart_dt.date().strftime("%Y-%m-%d")
        for u_idx, u_leg in unpurchased_legs:
            u_out = u_leg.get("outbound_date")
            if u_out and arr_date_str <= u_out <= dep_date_str:
                pending_legs_inside.append({
                    "leg": u_idx,
                    "label": u_leg.get("label", ""),
                    "outbound_date": u_out,
                })

        gaps.append({
            "arrive_at": arrive_at,
            "arrive": arrive_str,
            "depart_from": depart_from,
            "depart": depart_str,
            "derivable": True,
            "late_arrival": late_arrival,
            "nights_needed": nights_needed,
            "covered": covered,
            "uncovered": uncovered,
            "pending_legs_inside": pending_legs_inside,
        })
        all_needed_nights.update(nights_needed)

    # Nights outside any gap for booked stays
    outside_nights: set[str] = set()
    for s in stays:
        if s.get("status") == "booked":
            s_in_str = s.get("check_in")
            s_out_str = s.get("check_out")
            if s_in_str and s_out_str:
                try:
                    s_in_d = datetime.strptime(s_in_str, "%Y-%m-%d").date()
                    s_out_d = datetime.strptime(s_out_str, "%Y-%m-%d").date()
                    cur = s_in_d
                    while cur < s_out_d:
                        n_str = cur.strftime("%Y-%m-%d")
                        if n_str not in all_needed_nights:
                            outside_nights.add(n_str)
                        cur += timedelta(days=1)
                except ValueError:
                    pass

    nights_outside_any_gap = sorted(list(outside_nights))

    # Verdict determination:
    # 1. not_derivable: missing home, or 0 schema-1 legs (handled above)
    # 2. uncovered: any gap has uncovered nights > 0
    # 3. partial: no uncovered nights, but not_derivable non-empty, any gap derivable: false, or open_start/open_end not None
    # 4. covered: everything derivable and covered
    total_uncovered = sum(sum(u["nights"] for u in g.get("uncovered", [])) for g in gaps)
    if total_uncovered > 0:
        verdict = "uncovered"
    else:
        has_non_derivable_leg = len(not_derivable) > 0
        has_non_derivable_gap = any(not g.get("derivable", True) for g in gaps)
        has_open_ends = (open_start is not None) or (open_end is not None)
        if has_non_derivable_leg or has_non_derivable_gap or has_open_ends:
            verdict = "partial"
        else:
            verdict = "covered"

    return {
        "file": filename,
        "home": home,
        "verdict": verdict,
        "gaps": gaps,
        "nights_outside_any_gap": nights_outside_any_gap,
        "not_derivable": not_derivable,
        "open_start": open_start,
        "open_end": open_end,
    }


def run_purchase(args: argparse.Namespace) -> int:
    watchlist_path = Path(args.watchlist)
    if not watchlist_path.exists():
        print(f"error: watchlist file not found: {watchlist_path}", file=sys.stderr)
        return 2

    try:
        wl = json.loads(watchlist_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"error reading watchlist JSON: {exc}", file=sys.stderr)
        return 2

    legs = wl.get("legs")
    if not isinstance(legs, list):
        print("error: watchlist has no 'legs' list", file=sys.stderr)
        return 2

    leg_idx = args.leg
    if leg_idx < 0 or leg_idx >= len(legs):
        print(f"error: leg index {leg_idx} out of bounds (0..{len(legs)-1})", file=sys.stderr)
        return 2

    leg = legs[leg_idx]

    # Read purchase data
    if args.data == "-":
        raw_data = sys.stdin.read()
    else:
        data_path = Path(args.data)
        if not data_path.exists():
            print(f"error: data file not found: {data_path}", file=sys.stderr)
            return 2
        try:
            raw_data = data_path.read_text(encoding="utf-8")
        except Exception as exc:
            print(f"error reading data file: {exc}", file=sys.stderr)
            return 2

    try:
        purchase_data = json.loads(raw_data)
    except Exception as exc:
        print(f"error parsing data JSON: {exc}", file=sys.stderr)
        return 2

    if not isinstance(purchase_data, dict):
        print("error: purchase data must be a JSON object", file=sys.stderr)
        return 2

    # Validate before modifying anything
    errors, warnings = validate_purchase(
        purchase=purchase_data,
        leg=leg,
        leg_idx=leg_idx,
        all_legs=legs,
        replace=args.replace,
        date_changed=args.date_changed,
    )

    if errors:
        for err in errors:
            print(f"validation error: {err}", file=sys.stderr)
        return 2

    # Apply changes
    if "purchase" in leg and args.replace:
        leg["purchase_legacy"] = leg["purchase"]

    new_purchase = dict(purchase_data)
    new_purchase["schema"] = 1
    new_purchase.setdefault("open_issues", [])
    new_purchase.setdefault("notes", "")
    new_purchase.setdefault("extra", {})

    leg["purchase"] = new_purchase
    leg["purchased"] = True

    if args.date_changed:
        dep = new_purchase["segments"][0]["depart"]
        if dep and "T" in dep:
            new_date = dep.split("T")[0]
            if leg.get("outbound_date") != new_date:
                leg["quoted_outbound_date"] = leg.get("outbound_date")
                leg["outbound_date"] = new_date

    try:
        atomic_write_json(watchlist_path, wl)
    except Exception as exc:
        print(f"error saving watchlist: {exc}", file=sys.stderr)
        return 2

    result = {
        "leg": leg_idx,
        "label": leg.get("label"),
        "purchase": new_purchase,
        "warnings": warnings,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def run_summary(args: argparse.Namespace) -> int:
    if args.today:
        try:
            today_date = datetime.strptime(args.today, "%Y-%m-%d").date()
        except ValueError:
            print(f"error: --today must be YYYY-MM-DD, got {args.today}", file=sys.stderr)
            return 2
    else:
        today_date = date.today()
    today_iso = today_date.isoformat()

    if args.watchlist:
        candidate_paths = [Path(p) for p in args.watchlist]
    else:
        sdir = state_dir()
        if not sdir.exists():
            candidate_paths = []
        else:
            candidate_paths = sorted(
                [p for p in sdir.glob("watchlist-*.json") if ".bak" not in p.name]
            )

    trips: list[dict] = []
    unreadable: list[dict] = []

    for path in candidate_paths:
        if not path.is_file():
            unreadable.append({"file": path.name, "why": f"file not found: {path}"})
            continue

        try:
            raw_text = path.read_text(encoding="utf-8")
            wl = json.loads(raw_text)
        except Exception as exc:
            unreadable.append({"file": path.name, "why": f"JSON parse error: {exc}"})
            continue

        if not isinstance(wl, dict) or "legs" not in wl or not isinstance(wl["legs"], list):
            unreadable.append({"file": path.name, "why": "missing or invalid 'legs' list in watchlist"})
            continue

        trip_name = wl.get("trip", "")
        last_run = wl.get("last_run")
        days_since_last_run = None
        if last_run and isinstance(last_run, str):
            try:
                lr_date = datetime.strptime(last_run, "%Y-%m-%d").date()
                days_since_last_run = (today_date - lr_date).days
            except ValueError:
                pass

        paid_by_currency: dict[str, float] = {}
        legacy_gaps = 0
        unmeasured_gaps = 0
        open_issues: list[dict] = []
        legs_summary: list[dict] = []

        for idx, leg in enumerate(wl["legs"]):
            if leg.get("purchased") is True:
                state = "purchased"
            elif leg.get("watch", True):
                state = "watching"
            else:
                state = "settled_without_ticket"

            entry: dict = {
                "index": idx,
                "label": leg.get("label", ""),
                "state": state,
            }

            if state == "purchased":
                if "outbound_date" in leg:
                    entry["outbound_date"] = leg["outbound_date"]

                purchase = leg.get("purchase")
                if isinstance(purchase, dict) and purchase.get("schema") == 1:
                    p_info: dict = {
                        "schema": 1,
                        "locator": purchase.get("locator"),
                        "seller": purchase.get("seller"),
                        "paid": purchase.get("paid"),
                        "open_issues": purchase.get("open_issues", []),
                    }
                    paid_val = purchase.get("paid")
                    if paid_val is None:
                        p_info["paid_unmeasured_why"] = purchase.get("paid_unmeasured_why")
                        unmeasured_gaps += 1
                    elif isinstance(paid_val, dict):
                        if "amount" in paid_val and "currency" in paid_val:
                            amt = paid_val["amount"]
                            curr = paid_val["currency"]
                            paid_by_currency[curr] = round(paid_by_currency.get(curr, 0.0) + amt, 2)
                        elif "included_in_leg" in paid_val:
                            pass
                    entry["purchase"] = p_info

                    for issue in purchase.get("open_issues", []):
                        open_issues.append({"leg": idx, "issue": issue})
                else:
                    legacy_gaps += 1
                    legacy_keys = sorted(purchase.keys()) if isinstance(purchase, dict) else []
                    entry["purchase"] = {
                        "schema": None,
                        "legacy_keys": legacy_keys,
                    }

            elif state == "watching":
                obs_list = leg.get("observations")
                if isinstance(obs_list, list) and len(obs_list) > 0:
                    last_obs = obs_list[-1]
                    if isinstance(last_obs, dict):
                        entry["last_observation"] = {
                            "date": last_obs.get("date"),
                            "price": last_obs.get("price"),
                        }
                    else:
                        entry["last_observation"] = None
                else:
                    entry["last_observation"] = None

                baseline = leg.get("baseline")
                if isinstance(baseline, dict) and "low_band_ceiling" in baseline:
                    entry["low_band_ceiling"] = baseline["low_band_ceiling"]

                trigger = leg.get("trigger")
                if isinstance(trigger, dict) and trigger.get("hard_deadline"):
                    hd_str = trigger["hard_deadline"]
                    entry["hard_deadline"] = hd_str
                    try:
                        hd_date = datetime.strptime(hd_str, "%Y-%m-%d").date()
                        entry["days_to_deadline"] = (hd_date - today_date).days
                    except ValueError:
                        entry["days_to_deadline"] = None

            elif state == "settled_without_ticket":
                if "watch_off_reason" in leg:
                    entry["watch_off_reason"] = leg["watch_off_reason"]

            legs_summary.append(entry)

        paid_gaps = {
            "legacy": legacy_gaps,
            "unmeasured": unmeasured_gaps,
        }
        paid_is_partial = (legacy_gaps > 0 or unmeasured_gaps > 0)

        # Coverage derivation
        cov = calculate_coverage(wl, path.name)
        uncovered_nights_count = sum(
            sum(u["nights"] for u in g.get("uncovered", []))
            for g in cov.get("gaps", [])
        )

        trip_summary = {
            "file": path.name,
            "trip": trip_name,
            "last_run": last_run,
            "days_since_last_run": days_since_last_run,
            "legs": legs_summary,
            "paid_by_currency": paid_by_currency,
            "paid_is_partial": paid_is_partial,
            "paid_gaps": paid_gaps,
            "open_issues": open_issues,
            "coverage": {
                "verdict": cov["verdict"],
                "uncovered_nights": uncovered_nights_count,
            },
        }
        trips.append(trip_summary)

    result = {
        "today": today_iso,
        "trips": trips,
        "unreadable": unreadable,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def run_home(args: argparse.Namespace) -> int:
    watchlist_path = resolve_watchlist(args.watchlist)
    if not watchlist_path.exists():
        print(f"error: watchlist file not found: {watchlist_path}", file=sys.stderr)
        return 2

    try:
        wl = json.loads(watchlist_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"error reading watchlist JSON: {exc}", file=sys.stderr)
        return 2

    if not isinstance(wl, dict):
        print("error: watchlist must be a JSON object", file=sys.stderr)
        return 2

    raw_codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    if not raw_codes:
        print("error: at least one IATA code must be provided", file=sys.stderr)
        return 2

    for c in raw_codes:
        if len(c) != 3 or not c.isalpha() or not c.isupper():
            print(f"validation error: invalid IATA code '{c}': must be 3 uppercase letters", file=sys.stderr)
            return 2

    wl["home"] = ",".join(raw_codes)
    try:
        atomic_write_json(watchlist_path, wl)
    except Exception as exc:
        print(f"error saving watchlist: {exc}", file=sys.stderr)
        return 2

    print(json.dumps({"home": wl["home"]}, indent=2, ensure_ascii=False))
    return 0


def run_stay(args: argparse.Namespace) -> int:
    watchlist_path = resolve_watchlist(args.watchlist)
    if not watchlist_path.exists():
        print(f"error: watchlist file not found: {watchlist_path}", file=sys.stderr)
        return 2

    try:
        wl = json.loads(watchlist_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"error reading watchlist JSON: {exc}", file=sys.stderr)
        return 2

    if not isinstance(wl, dict):
        print("error: watchlist must be a JSON object", file=sys.stderr)
        return 2

    # Read stay data
    if args.data == "-":
        raw_data = sys.stdin.read()
    else:
        data_path = Path(args.data)
        if not data_path.exists():
            print(f"error: data file not found: {data_path}", file=sys.stderr)
            return 2
        try:
            raw_data = data_path.read_text(encoding="utf-8")
        except Exception as exc:
            print(f"error reading data file: {exc}", file=sys.stderr)
            return 2

    try:
        stay_data = json.loads(raw_data)
    except Exception as exc:
        print(f"error parsing data JSON: {exc}", file=sys.stderr)
        return 2

    if not isinstance(stay_data, dict):
        print("error: stay data must be a JSON object", file=sys.stderr)
        return 2

    errors, warnings = validate_stay(stay_data, wl.get("stays", []))
    if errors:
        for err in errors:
            print(f"validation error: {err}", file=sys.stderr)
        return 2

    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)

    wl.setdefault("stays", []).append(stay_data)
    try:
        atomic_write_json(watchlist_path, wl)
    except Exception as exc:
        print(f"error saving watchlist: {exc}", file=sys.stderr)
        return 2

    result = {
        "stay": stay_data,
        "warnings": warnings,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def run_coverage(args: argparse.Namespace) -> int:
    candidate_paths = resolve_watchlists(args.watchlist)
    trips: list[dict] = []
    unreadable: list[dict] = []

    for path in candidate_paths:
        if not path.is_file():
            unreadable.append({"file": path.name, "why": f"file not found: {path}"})
            continue

        try:
            wl = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            unreadable.append({"file": path.name, "why": f"JSON parse error: {exc}"})
            continue

        if not isinstance(wl, dict) or "legs" not in wl or not isinstance(wl["legs"], list):
            unreadable.append({"file": path.name, "why": "missing or invalid 'legs' list in watchlist"})
            continue

        trips.append(calculate_coverage(wl, path.name))

    result: dict = {"trips": trips}
    if unreadable:
        result["unreadable"] = unreadable
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def resolve_watchlist_for_create(target: str) -> Path:
    """Resolve a watchlist path for creation, placing in state_dir() if bare filename."""
    p = Path(target)
    if p.is_absolute() or "/" in target or "\\" in target:
        return p
    return state_dir() / target


def run_add_leg(args: argparse.Namespace) -> int:
    # 1. Resolve path
    if args.create:
        watchlist_path = resolve_watchlist_for_create(args.watchlist)
    else:
        watchlist_path = resolve_watchlist(args.watchlist)

    # 2. Check --create requirements
    if args.create:
        if not args.trip or not args.trip.strip():
            print("error: --trip NAME is required when using --create", file=sys.stderr)
            return 2

    validated_home: str | None = None
    if args.home:
        raw_codes = [c.strip() for c in args.home.split(",") if c.strip()]
        if not raw_codes:
            print("error: at least one IATA code must be provided for --home", file=sys.stderr)
            return 2
        for c in raw_codes:
            if len(c) != 3 or not c.isalpha() or not c.isupper():
                print(f"validation error: invalid IATA code '{c}' in --home: must be 3 uppercase letters", file=sys.stderr)
                return 2
        validated_home = ",".join(raw_codes)

    # 3. Read data
    if args.data == "-":
        raw_data = sys.stdin.read()
    else:
        data_path = Path(args.data)
        if not data_path.exists():
            print(f"error: data file not found: {data_path}", file=sys.stderr)
            return 2
        try:
            raw_data = data_path.read_text(encoding="utf-8")
        except Exception as exc:
            print(f"error reading data file: {exc}", file=sys.stderr)
            return 2

    try:
        leg_data = json.loads(raw_data)
    except Exception as exc:
        print(f"error parsing data JSON: {exc}", file=sys.stderr)
        return 2

    if not isinstance(leg_data, dict):
        print("error: leg data must be a JSON object", file=sys.stderr)
        return 2

    # 4. Check file existence rule
    if args.create:
        if watchlist_path.exists():
            print(f"error: watchlist file already exists: {watchlist_path} (--create will not overwrite)", file=sys.stderr)
            return 2
        existing_legs: list[dict] = []
        wl: dict | None = None
    else:
        if not watchlist_path.exists():
            print(f"error: watchlist file not found: {watchlist_path}", file=sys.stderr)
            return 2
        try:
            wl = json.loads(watchlist_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"error reading watchlist JSON: {exc}", file=sys.stderr)
            return 2
        if not isinstance(wl, dict) or "legs" not in wl or not isinstance(wl["legs"], list):
            print("error: missing or invalid 'legs' list in watchlist", file=sys.stderr)
            return 2
        existing_legs = wl["legs"]

    # 5. Validate leg data (including duplicate check against existing_legs)
    # CRITICAL: If validation fails, no file is created or touched!
    errors = validate_add_leg(leg_data, existing_legs)
    if errors:
        for err in errors:
            print(f"validation error: {err}", file=sys.stderr)
        return 2

    # 6. Apply change
    if args.create:
        today_iso = date.today().isoformat()
        wl = {
            "trip": args.trip,
            "created": today_iso,
            "cadence": "off",
            "legs": [],
            "event_watches": [],
        }
        if validated_home:
            wl["home"] = validated_home

    assert wl is not None
    new_leg = dict(leg_data)
    new_leg["purchased"] = False
    new_idx = len(wl["legs"])
    wl["legs"].append(new_leg)

    try:
        atomic_write_json(watchlist_path, wl)
    except Exception as exc:
        print(f"error saving watchlist: {exc}", file=sys.stderr)
        return 2

    print(new_idx)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ledger.py",
        description="The trip ledger — records purchased legs and tracks travel coverage",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # purchase sub-command
    p_purchase = subparsers.add_parser(
        "purchase", help="Record a purchase for a watchlist leg"
    )
    p_purchase.add_argument("watchlist", help="Path to watchlist JSON file")
    p_purchase.add_argument("--leg", type=int, required=True, help="Leg index (0-based)")
    p_purchase.add_argument(
        "--data", required=True, help="Path to JSON file with purchase block, or - for stdin"
    )
    p_purchase.add_argument(
        "--replace",
        action="store_true",
        help="Replace existing purchase block, saving old block to purchase_legacy",
    )
    p_purchase.add_argument(
        "--date-changed",
        action="store_true",
        help="Allow depart date to differ from leg outbound_date",
    )

    # summary sub-command
    p_summary = subparsers.add_parser(
        "summary", help="Summarize travel watchlists and purchases"
    )
    p_summary.add_argument(
        "watchlist",
        nargs="*",
        help="Path(s) to watchlist JSON file(s). If omitted, scans state_dir() for watchlist-*.json",
    )
    p_summary.add_argument(
        "--today",
        help="Reference date for calculations (YYYY-MM-DD), defaults to today",
    )

    # home sub-command
    p_home = subparsers.add_parser(
        "home", help="Record home airport codes for a watchlist"
    )
    p_home.add_argument("watchlist", help="Path to watchlist JSON file")
    p_home.add_argument("codes", help="Comma-separated 3-letter IATA airport codes (e.g., 'POA' or 'POA,NVT')")

    # stay sub-command
    p_stay = subparsers.add_parser(
        "stay", help="Record a stay for a watchlist"
    )
    p_stay.add_argument("watchlist", help="Path to watchlist JSON file")
    p_stay.add_argument(
        "--data", required=True, help="Path to JSON file with stay block, or - for stdin"
    )

    # coverage sub-command
    p_coverage = subparsers.add_parser(
        "coverage", help="Derive trip night coverage against stays"
    )
    p_coverage.add_argument(
        "watchlist",
        nargs="*",
        help="Path(s) to watchlist JSON file(s). If omitted, scans state_dir() for watchlist-*.json",
    )

    # add-leg sub-command
    p_add_leg = subparsers.add_parser(
        "add-leg", help="Add a leg to a watchlist, optionally creating the watchlist"
    )
    p_add_leg.add_argument("watchlist", help="Path to watchlist JSON file")
    p_add_leg.add_argument(
        "--data", required=True, help="Path to JSON file with leg block, or - for stdin"
    )
    p_add_leg.add_argument(
        "--create", action="store_true", help="Create the watchlist file if it does not exist"
    )
    p_add_leg.add_argument(
        "--trip", help="Trip name (required when using --create)"
    )
    p_add_leg.add_argument(
        "--home", help="Comma-separated 3-letter IATA airport codes (optional with --create)"
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "purchase":
        return run_purchase(args)
    if args.subcommand == "summary":
        return run_summary(args)
    if args.subcommand == "home":
        return run_home(args)
    if args.subcommand == "stay":
        return run_stay(args)
    if args.subcommand == "coverage":
        return run_coverage(args)
    if args.subcommand == "add-leg":
        return run_add_leg(args)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
