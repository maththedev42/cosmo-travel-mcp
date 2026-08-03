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
def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_WATCHLIST
    if not path.exists():
        raise SystemExit(f"watchlist not found: {path}")

    wl = json.loads(path.read_text(encoding="utf-8"))
    key = api_key()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    today_iso = date.today().isoformat()

    pending = [l for l in wl["legs"] if not l.get("purchased")]
    if not pending:
        log(f"{stamp}  nothing to watch — every leg is marked purchased")
        return 0

    reserve = wl.get("quota_reserve", 20)
    left = searches_left(key)
    if left is not None and left - len(pending) < reserve:
        log(f"{stamp}  SKIPPED: {left} searches left, {len(pending)} needed, "
            f"reserve is {reserve}. A watch must never starve an interactive search.")
        return 0

    alerts: list[str] = []
    for leg in pending:
        try:
            res = price_leg(key, leg)
        except Exception as exc:                       # noqa: BLE001 — log and move on
            log(f"{stamp}  {leg.get('label')}: request failed — {exc}")
            continue
        if res is None:
            log(f"{stamp}  {leg.get('label')}: no price returned")
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

    wl["last_run"] = today_iso
    path.write_text(json.dumps(wl, indent=2, ensure_ascii=False), encoding="utf-8")

    if alerts:
        header = f"\n## {stamp} — {len(alerts)} alerta(s)\n\n"
        ALERTS.write_text(
            (ALERTS.read_text(encoding="utf-8") if ALERTS.exists() else
             "# Vigília de preço — cosmo-travel\n")
            + header + "\n\n".join(alerts) + "\n",
            encoding="utf-8",
        )
        print("\n".join(alerts))
    return 0


def log(line: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(line)


if __name__ == "__main__":
    raise SystemExit(main())
