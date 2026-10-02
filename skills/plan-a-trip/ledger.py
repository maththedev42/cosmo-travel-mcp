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
from datetime import date, datetime
from pathlib import Path

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
        }
        trips.append(trip_summary)

    result = {
        "today": today_iso,
        "trips": trips,
        "unreadable": unreadable,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "purchase":
        return run_purchase(args)
    if args.subcommand == "summary":
        return run_summary(args)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
