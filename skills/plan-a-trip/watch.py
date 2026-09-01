#!/usr/bin/env python3
"""Re-price every unpurchased leg in a watchlist and alert when one enters its low band.

The documented entry point the SKILL refers to. Standard library only, so a
scheduler can run it with the system Python without a virtualenv.

    python3 watch.py [WATCHLIST_JSON]

Reads the SerpAPI key from, in order: $SERPAPI_API_KEY, ~/.cosmo-travel/env,
`claude mcp get cosmo-travel`. Never prints it.

Design notes that are decisions, not details:

- **The query is the state, not the price.** Re-running the stored query is the
  only thing that makes today comparable to the day the baseline was captured.
- **Two baseline strengths, worded differently.** `price_history` is the floor
  this exact date has actually had; `typical_price_range` is the route across
  the year. Reporting them the same way is how "cheap" gets said without a
  reference, which is the failure this whole script exists to replace.
- **A leg with no baseline is reported as unmeasured, never as fine.** Silence
  must not read as "nothing to do".
- **Quota reserve.** A background watch that eats the last search of the month
  has cost more than it saved.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

HOME = Path.home()
STATE = HOME / ".cosmo-travel"
DEFAULT_WATCHLIST = STATE / "watchlist-eua-2026.json"
LOG = STATE / "watch.log"
ALERTS = STATE / "alerts.md"
TIMEOUT = 60


# --------------------------------------------------------------------------- key
def api_key() -> str:
    if os.environ.get("SERPAPI_API_KEY"):
        return os.environ["SERPAPI_API_KEY"]

    envfile = STATE / "env"
    if envfile.exists():
        for line in envfile.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() == "SERPAPI_API_KEY" and v.strip():
                return v.strip().strip('"').strip("'")

    claude = shutil.which("claude")
    if claude:
        try:
            out = subprocess.run(
                [claude, "mcp", "get", "cosmo-travel"],
                capture_output=True, text=True, timeout=30,
            ).stdout
            for line in out.splitlines():
                if "SERPAPI_API_KEY=" in line:
                    return line.split("SERPAPI_API_KEY=", 1)[1].strip()
        except (subprocess.SubprocessError, OSError):
            pass

    raise SystemExit(
        "no SerpAPI key: set $SERPAPI_API_KEY, or write SERPAPI_API_KEY=... "
        f"into {envfile} (chmod 600)"
    )


def get(url: str, params: dict) -> dict:
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    with urllib.request.urlopen(f"{url}?{q}", timeout=TIMEOUT) as r:
        return json.loads(r.read().decode())


# ------------------------------------------------------------------------- quota
def searches_left(key: str) -> int | None:
    """Free — the account endpoint does not count against the search quota."""
    try:
        acct = get("https://serpapi.com/account", {"api_key": key})
    except Exception:
        return None
    for field in ("total_searches_left", "plan_searches_left"):
        if isinstance(acct.get(field), int):
            return acct[field]
    return None


# ------------------------------------------------------------------------ pricing
def price_leg(key: str, leg: dict) -> dict | None:
    """One-way search for the stored query. Returns today's price and insights."""
    data = get("https://serpapi.com/search", {
        "engine": "google_flights",
        "departure_id": leg["origin"],
        "arrival_id": leg["destination"],
        "outbound_date": leg["outbound_date"],
        "type": 2,                                   # one-way
        "adults": leg.get("adults", 1),
        "travel_class": 1,                           # economy
        "currency": "BRL",
        "hl": "en", "gl": "us",
        "api_key": key,
    })
    if data.get("error"):
        return None

    options = (data.get("best_flights") or []) + (data.get("other_flights") or [])
    prices = [o["price"] for o in options if isinstance(o.get("price"), (int, float))]
    insights = data.get("price_insights") or {}
    if not prices and not insights.get("lowest_price"):
        return None

    return {
        "price": min(prices) if prices else insights.get("lowest_price"),
        "price_level": insights.get("price_level"),
        "typical_price_range": insights.get("typical_price_range"),
        "price_history": insights.get("price_history"),
    }


def brl(v) -> str:
    return f"R$ {v:,.0f}".replace(",", ".") if isinstance(v, (int, float)) else "—"


# ------------------------------------------------------------------------ events
# Shows are watched for a different reason than fares. A fare oscillates and the
# question is "is it low"; a show is *published* and then *sells out*, and the
# question is "is there something new in my window". So this half of the watch
# reports arrivals, never prices — and it exists because the first sweep found
# nothing at all on 31 December, the one night of this trip that cannot move.
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def window_tokens(start: str, end: str) -> set[str]:
    """Every "Mon D" the provider could emit for a stay, matching its own format.

    The engine returns ``start_date: "Dec 30"`` with no year, so the window is
    matched on the provider's own tokens rather than on a date parsed out of a
    string that does not carry one.
    """
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    out, cur = set(), d0
    while cur < d1:                       # check-out night is spent in the air
        out.add(f"{_MONTHS[cur.month - 1]} {cur.day}")
        cur = date.fromordinal(cur.toordinal() + 1)
    return out


def _start_date(ev: dict) -> str:
    """Extract start_date string from both dict (google_events) and str (google) schemas."""
    d = ev.get("date")
    if isinstance(d, dict):
        return d.get("start_date", "")
    if isinstance(d, str):
        return d
    return ""


def event_id(ev: dict) -> str:
    title = " ".join(str(ev.get("title", "")).split()).casefold()
    return f"{title}|{_start_date(ev)}"


def sweep_events(key: str, watch: dict) -> list[dict]:
    """One query, one page — deliberately cheap, run every week rather than deep."""
    data = get("https://serpapi.com/search", {
        "engine": "google",
        "q": watch["query"],
        "hl": "en", "gl": "us",
        "api_key": key,
    })
    if data.get("error"):
        raise RuntimeError(data["error"])
    tokens = window_tokens(watch["window"]["from"], watch["window"]["to"])
    hits = []
    for ev in data.get("events_results") or []:
        if _start_date(ev) in tokens:
            hits.append(ev)
    return hits


def judge(leg: dict, today: float) -> tuple[str, str]:
    """Return (severity, message). Severity is strong | soft | unmeasured | quiet."""
    b = leg.get("baseline") or {}
    label = leg.get("label") or f'{leg["origin"]} → {leg["destination"]}'
    src, lo, ceiling, when = (b.get("source"), b.get("min"),
                              b.get("low_band_ceiling"), b.get("captured_on"))

    if ceiling is None:
        return "unmeasured", (
            f"**{label}** — hoje {brl(today)}. Sem linha de base: nem histórico de "
            f"60 dias nem faixa típica foram devolvidos para esta consulta. "
            f"Este trecho continua **não medido**, não 'tranquilo'."
        )

    if today > ceiling:
        return "quiet", ""

    if src == "price_history" and lo is not None:
        forca = ("**abaixo do piso medido**" if today <= lo
                 else "dentro da faixa baixa")
        return "strong", (
            f"**{label}** — hoje {brl(today)}, {forca}. O piso desta consulta "
            f"nesta data foi {brl(lo)}, capturado em {when}; o alerta dispara "
            f"abaixo de {brl(ceiling)}. Sinal forte: a comparação é com a mesma "
            f"busca, não com a rota no ano inteiro."
        )

    return "soft", (
        f"**{label}** — hoje {brl(today)}, abaixo do teto da faixa **típica da "
        f"rota** ({brl(ceiling)}, capturado em {when}). Sinal fraco: isto compara "
        f"com a rota ao longo do ano, não com esta data. Não é 'está no mínimo'."
    )


# --------------------------------------------------------------------------- main
EXIT_SKIPPED_QUOTA = 3
EXIT_PROBE_FAILED = 4
"""Exit codes for non-standard outcomes.

A skip, a failed probe, and a quiet week leave indistinguishable traces —
`alerts.md` untouched or missing measurements — so a scheduler reading only
the exit code cannot tell "nothing moved" from "nothing was measured". That
matters most when a watch sits dead for weeks while the scheduler reports 0 (success).

EXIT_SKIPPED_QUOTA (3): did not run, reserve would have been breached.
EXIT_PROBE_FAILED (4): one or more searches failed upstream (partial run; consumer should use measured alerts).
"""


def legs_to_watch(legs: list[dict]) -> list[dict]:
    """The legs a run should re-price.

    A leg leaves the rotation two ways. `purchased` is the obvious one. The
    other is `watch: false`, for a leg that is settled without a ticket — the
    Miami → Orlando hop decided by renting a car, say. Its flight stays in the
    file because it is still the fallback, but re-pricing it every week buys
    nothing and costs a search each time.

    Absent `watch` means watch it: a leg must never fall out of the rotation
    by omission.
    """
    return [l for l in legs if not l.get("purchased") and l.get("watch", True)]


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_WATCHLIST
    if not path.exists():
        raise SystemExit(f"watchlist not found: {path}")

    wl = json.loads(path.read_text(encoding="utf-8"))
    key = api_key()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    today_iso = date.today().isoformat()

    pending = legs_to_watch(wl["legs"])
    sweeps = [w for w in wl.get("event_watches", []) if not w.get("done")]
    cost = len(pending) + len(sweeps)     # one search each: 1 leg, 1 event query
    if not cost:
        log(f"{stamp}  nothing to watch — every leg is settled and every sweep is done")
        return 0

    reserve = wl.get("quota_reserve", 20)
    left = searches_left(key)
    if left is not None and left - cost < reserve:
        log(f"{stamp}  SKIPPED: {left} searches left, {cost} needed, "
            f"reserve is {reserve}. A watch must never starve an interactive search. "
            f"If this repeats, the reserve is too close to the quota that is left.")
        return EXIT_SKIPPED_QUOTA

    alerts: list[str] = []
    failed_probes: list[str] = []

    for leg in pending:
        try:
            res = price_leg(key, leg)
        except Exception as exc:                       # noqa: BLE001 — log and move on
            msg = f"{leg.get('label')}: request failed — {exc}"
            log(f"{stamp}  {msg}")
            failed_probes.append(msg)
            continue
        if res is None:
            msg = f"{leg.get('label')}: no price returned"
            log(f"{stamp}  {msg}")
            failed_probes.append(msg)
            continue

        leg.setdefault("observations", []).append({
            "date": today_iso,
            "price": res["price"],
            "price_level": res["price_level"],
        })

        # A first-ever price_history upgrades a weak baseline to a strong one.
        b = leg.setdefault("baseline", {})
        hist = res.get("price_history")
        if hist and b.get("source") != "price_history":
            pts = [p[1] for p in hist if isinstance(p, list) and len(p) == 2]
            if pts:
                b.update(min=min(pts), max=max(pts), source="price_history",
                         low_band_ceiling=round(min(pts) * 1.10),
                         captured_on=today_iso)
                log(f"{stamp}  {leg.get('label')}: baseline upgraded to "
                    f"price_history (floor {brl(min(pts))})")

        severity, msg = judge(leg, res["price"])
        log(f"{stamp}  {leg.get('label')}: {brl(res['price'])} [{severity}]")
        if msg:
            alerts.append(msg)

    # ── shows and events ──────────────────────────────────────────────
    for watch in wl.get("event_watches", []):
        if watch.get("done"):
            continue
        try:
            hits = sweep_events(key, watch)
        except Exception as exc:                       # noqa: BLE001
            msg = f"eventos {watch['city']}: request failed — {exc}"
            log(f"{stamp}  {msg}")
            failed_probes.append(msg)
            continue

        seen = set(watch.setdefault("seen", []))
        fresh = [e for e in hits if event_id(e) not in seen]
        watch["seen"] = sorted(seen | {event_id(e) for e in hits})
        log(f"{stamp}  eventos {watch['city']}: {len(hits)} na janela, "
            f"{len(fresh)} novo(s)")
        if not fresh:
            continue

        linhas = []
        for e in fresh:
            date_val = e.get("date")
            if isinstance(date_val, dict):
                when_str = date_val.get("start_date", "?")
                if date_val.get("when"):
                    when_str += f", {date_val['when']}"
            elif isinstance(date_val, str) and date_val:
                when_str = date_val
                if e.get("time"):
                    when_str += f" {e['time']}"
            else:
                when_str = "?"

            venue = e.get("venue")
            if isinstance(venue, dict):
                v_name = venue.get("name")
            else:
                v_name = e.get("address")
                if isinstance(v_name, list) and v_name:
                    v_name = v_name[0]
                elif isinstance(v_name, str):
                    v_name = v_name
                else:
                    v_name = ""

            linhas.append(f"- **{e.get('title')}** — {when_str}"
                          f"{' · ' + str(v_name) if v_name else ''}")
        alerts.append(
            f"**{watch['city']} — {len(fresh)} evento(s) novo(s) na sua janela**\n"
            + "\n".join(linhas)
            + "\n\nPublicado desde a última varredura. Ingressos de fim de ano "
              "esgotam antes do resto da temporada."
        )

    wl["last_run"] = today_iso
    path.write_text(json.dumps(wl, indent=2, ensure_ascii=False), encoding="utf-8")

    if alerts or failed_probes:
        out_blocks = []
        if failed_probes:
            out_blocks.append("### Sondagens com falha nesta rodada:\n" + "\n".join(f"- {f}" for f in failed_probes))
        if alerts:
            out_blocks.append("\n\n".join(alerts))

        header = f"\n## {stamp} — {len(alerts)} alerta(s)" + (f", {len(failed_probes)} falha(s)" if failed_probes else "") + "\n\n"
        ALERTS.write_text(
            (ALERTS.read_text(encoding="utf-8") if ALERTS.exists() else
             "# Vigília de preço — cosmo-travel\n")
            + header + "\n\n".join(out_blocks) + "\n",
            encoding="utf-8",
        )
        print("\n".join(alerts))

    if failed_probes:
        return EXIT_PROBE_FAILED

    return 0


def log(line: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(line)


if __name__ == "__main__":
    raise SystemExit(main())
