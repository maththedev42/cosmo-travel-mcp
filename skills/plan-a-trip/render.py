#!/usr/bin/env python3
"""Render a researched trip into one self-contained HTML page.

    python3 render.py trip.json [-o dossier.html]

Standard library only, so it runs with the system Python — same constraint as
``watch.py``.

Why a renderer instead of asking the model to write HTML
--------------------------------------------------------
The first time this page was built, the model wrote the HTML directly and kept
two hand-typed lists of the same event titles in different places. They drifted
by three characters and the build died with a ``KeyError``. Hand-typed data
duplicated across a document is not a formatting problem, it is a correctness
problem, and it gets worse as the page gets longer.

So this file takes **one** JSON document and derives everything it can:

- **Nights come from dates.** ``check_out - check_in``. Never typed. (Method 6.)
- **Event membership comes from the lodging windows**, not from a field. An
  event belongs to a candidate iff that candidate sleeps in that city that
  night: ``check_in <= date < check_out``. An event on the check-out night is
  watched from the plane; one on the check-in date depends on the arrival time,
  and is labelled ``arrival`` rather than dropped or promoted. (Protocol 5.)
- **Totals are asserted, not trusted.** If a candidate declares a flight total
  that does not equal the sum of its legs, the render fails loudly rather than
  publishing a page whose header disagrees with its own table.
- **Unmeasured things get rendered**, with the reason. A number nobody measured
  must not be able to hide by being absent. (Method 2 and 10.)

The output is a single file with inline CSS and JS: no server, no assets, and it
survives being emailed.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from html import escape
from pathlib import Path

# --------------------------------------------------------------------------- data


def d(iso: str) -> date:
    return date.fromisoformat(iso)


def nights(window: dict) -> int:
    """Derived, never typed — Method rule 6."""
    return (d(window["check_out"]) - d(window["check_in"])).days


def money(value, currency: str = "BRL") -> str:
    if value is None:
        return "—"
    if currency == "BRL":
        return "R$ " + f"{value:,.0f}".replace(",", ".")
    return f"{currency} {value:,.0f}"


_MONTHS_PT = ("jan", "fev", "mar", "abr", "mai", "jun",
              "jul", "ago", "set", "out", "nov", "dez")
_MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def short_date(iso: str, lang: str) -> str:
    dt = d(iso)
    months = _MONTHS_PT if lang == "pt" else _MONTHS_EN
    return f"{dt.day} {months[dt.month - 1]}"


# ------------------------------------------------------------------- derivation


def membership(event_date: date, city: str, lodging: list[dict]) -> dict[int, str]:
    """Which candidates can attend, derived from where each one sleeps.

    Returns ``{candidate_id: "firm" | "arrival"}``. ``arrival`` means the event
    falls on the day that candidate lands — the flight may not make it in time.
    Never silently dropped, never called firm.
    """
    out: dict[int, str] = {}
    for w in lodging:
        if w["city"] != city:
            continue
        ci, co = d(w["check_in"]), d(w["check_out"])
        if ci <= event_date < co:
            for cand in w["candidates"]:
                # A candidate with two windows in one city keeps the better label.
                label = "arrival" if event_date == ci else "firm"
                if out.get(cand) != "firm":
                    out[cand] = label
    return out


def verify(data: dict) -> list[str]:
    """Assert the page cannot contradict itself. Returns human-readable notes."""
    notes: list[str] = []
    legs, lodging = data.get("legs", []), data.get("lodging", [])

    for cand in data["candidates"]:
        cid = cand["id"]

        flown = sum(leg["price"] for leg in legs if cid in leg["candidates"])
        declared = cand.get("flights_total")
        if declared is None:
            cand["flights_total"] = flown
        else:
            assert declared == flown, (
                f"candidate {cid}: flights_total says {declared} but its legs "
                f"sum to {flown}. Fix the data, not this assertion.")

        slept = sum(w["chosen"]["total"] for w in lodging if cid in w["candidates"])
        declared = cand.get("lodging_total")
        if declared is None:
            cand["lodging_total"] = slept
        else:
            assert declared == slept, (
                f"candidate {cid}: lodging_total says {declared} but its windows "
                f"sum to {slept}.")

        cand["total"] = cand["flights_total"] + cand["lodging_total"]
        cand["nights"] = sum(nights(w) for w in lodging if cid in w["candidates"])

    for w in lodging:
        w["nights"] = nights(w)
        per_night = w["chosen"]["total"] / w["nights"] if w["nights"] else 0
        stated = w["chosen"].get("nightly")
        if stated and abs(per_night - stated) > max(2, stated * 0.02):
            notes.append(
                f"{w['city']} {w['check_in']}: nightly rate {money(stated)} × "
                f"{w['nights']} nights = {money(stated * w['nights'])}, but the "
                f"total says {money(w['chosen']['total'])}. Taxes, or a typo?")

    for ev in data.get("events", []):
        derived = membership(d(ev["date"]), ev["city"], lodging)
        typed = ev.get("candidates")
        if typed and {int(k) for k in typed} != set(derived):
            notes.append(
                f"event “{ev['title']}” listed candidates {sorted(typed)} but the "
                f"lodging windows say {sorted(derived)} — using the windows.")
        ev["candidates"] = derived
        if not derived:
            notes.append(
                f"event “{ev['title']}” ({ev['date']}, {ev['city']}) falls in no "
                f"candidate's window and was dropped from the table.")

    return notes


# ------------------------------------------------------------------------ render

CSS = """
:root{
  --bg:#0e1116; --card:#151a21; --ink:#e8eaed; --muted:#9aa4b2; --line:#242c37;
  --ok:#4ade80; --warn:#fbbf24; --bad:#f87171; --accent:#60a5fa;
  --ok-bg:#122019; --warn-bg:#211a0d; --bad-bg:#21100f;
}
@media (prefers-color-scheme: light){
  :root{
    --bg:#fbfbf9; --card:#fff; --ink:#16161a; --muted:#66707d; --line:#e4e4e0;
    --ok:#15803d; --warn:#b45309; --bad:#b91c1c; --accent:#1d4ed8;
    --ok-bg:#f0f7f1; --warn-bg:#fdf6ec; --bad-bg:#fdf1f0;
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:16px/1.62 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:980px;margin:0 auto;padding:48px 24px 80px}
h1{font-size:2rem;letter-spacing:-.02em;margin:0 0 .3rem}
h2{font-size:1.4rem;letter-spacing:-.015em;margin:0 0 .5rem}
h3{font-size:1.05rem;margin:0 0 .4rem}
.eyebrow{font-size:.72rem;letter-spacing:.1em;text-transform:uppercase;
  color:var(--muted);display:block;margin-bottom:.5rem}
.lede{color:var(--muted);margin:0 0 1.4rem}
section{margin:0 0 3rem}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.9em}

table{width:100%;border-collapse:collapse;font-size:.94rem}
th{text-align:left;font-size:.7rem;letter-spacing:.08em;text-transform:uppercase;
  color:var(--muted);font-weight:600;padding:.6rem .7rem;border-bottom:1px solid var(--line)}
td{padding:.7rem;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:none}
.tscroll{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--card)}
.num{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;white-space:nowrap}

.pill{display:inline-block;padding:.1rem .5rem;border-radius:999px;font-size:.72rem;
  font-weight:600;border:1px solid var(--line);color:var(--muted);line-height:1.6}
.pill.ok{color:var(--ok);border-color:var(--ok);background:var(--ok-bg)}
.pill.warn{color:var(--warn);border-color:var(--warn);background:var(--warn-bg)}
.pill.bad{color:var(--bad);border-color:var(--bad);background:var(--bad-bg)}

.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(215px,1fr));gap:14px}
.cand{background:var(--card);border:1px solid var(--line);border-radius:11px;
  padding:15px 16px;cursor:pointer;text-align:left;color:inherit;font:inherit;
  transition:border-color .12s,background .12s}
.cand:hover{border-color:var(--muted)}
.cand[aria-pressed="true"]{border-color:var(--ok);background:var(--ok-bg)}
.cand .top{display:flex;justify-content:space-between;align-items:baseline;
  font-size:.7rem;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.cand .price{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  font-size:1.05rem;color:var(--ink);letter-spacing:-.02em}
.cand .route{font-size:1.02rem;margin:.35rem 0 .45rem;letter-spacing:-.01em}
.cand .meta{font-size:.82rem;color:var(--muted);line-height:1.5}

.callout{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--accent);
  border-radius:9px;padding:15px 18px;margin:1.2rem 0}
.callout.warn{border-left-color:var(--warn)}
.callout h4{margin:0 0 .4rem;font-size:1rem}
.callout p{margin:.4rem 0;font-size:.93rem}
.callout p:last-child{margin-bottom:0}

.day{border-left:2px solid var(--line);padding:0 0 1.3rem 20px;margin-left:5px;position:relative}
.day::before{content:"";position:absolute;left:-6px;top:5px;width:10px;height:10px;
  border-radius:50%;background:var(--bg);border:2px solid var(--accent)}
.day.stay::before{background:var(--bg)}
.day.flight::before{background:var(--accent)}
.day .when{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.8rem;
  color:var(--accent);letter-spacing:.02em}
.day h3{margin:.1rem 0 .35rem}
.day ul{margin:.3rem 0 0;padding-left:1.1rem;color:var(--muted);font-size:.92rem}
.day .box{background:var(--card);border:1px solid var(--line);border-radius:8px;
  padding:9px 12px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  font-size:.83rem;line-height:1.65;overflow-x:auto}

a{color:var(--accent)}
a.cal{display:inline-block;padding:.15rem .55rem;border:1px solid var(--accent);
  border-radius:6px;font-size:.76rem;text-decoration:none;white-space:nowrap}
.foot{color:var(--muted);font-size:.83rem;border-top:1px solid var(--line);
  padding-top:1.2rem;margin-top:3rem}
[hidden]{display:none !important}
"""

JS = """
const buttons = [...document.querySelectorAll('.cand')];
function pick(id){
  buttons.forEach(b => b.setAttribute('aria-pressed', String(b.dataset.cand === id)));
  document.querySelectorAll('[data-for]').forEach(el => {
    const owners = el.dataset.for.split(' ');
    el.hidden = !owners.includes(id);
  });
  document.querySelectorAll('[data-dim]').forEach(el => {
    const owners = el.dataset.dim.split(' ');
    el.style.opacity = owners.includes(id) ? '1' : '.32';
  });
}
buttons.forEach(b => b.addEventListener('click', () => pick(b.dataset.cand)));
pick(document.body.dataset.chosen);
"""


def signal_pill(leg: dict) -> str:
    """Method rule 2: `price_level` is not `price_history`, and neither is silence."""
    lo_hi = leg.get("typical_range")
    if not lo_hi:
        return '<span class="pill">unmeasured</span>'
    if leg.get("level") in ("high", "caro", "expensive"):
        return '<span class="pill bad">expensive</span>'
    if leg.get("level") in ("low", "barato", "cheap"):
        return '<span class="pill ok">low</span>'
    return '<span class="pill warn">typical</span>'


def render(data: dict, notes: list[str]) -> str:
    trip = data["trip"]
    cur = trip.get("currency", "BRL")
    lang = trip.get("lang", "en")
    chosen = str(trip.get("chosen") or data["candidates"][0]["id"])
    m = lambda v: money(v, cur)                                    # noqa: E731
    sd = lambda iso: short_date(iso, lang)                         # noqa: E731

    out: list[str] = []
    a = out.append

    # ── candidates ────────────────────────────────────────────────
    a('<section id="candidates">')
    a(f'<span class="eyebrow">Quoted {escape(trip["quoted_on"])} · '
      f'{len(data["candidates"])} candidates, one run</span>')
    a(f'<h1>{escape(trip["title"])}</h1>')
    if trip.get("fixed_point"):
        a(f'<p class="lede">The one thing that does not move: '
          f'<strong>{escape(trip["fixed_point"])}</strong>. Everything else was '
          f'optimised around it. Pick a candidate — the whole page follows.</p>')
    a('<div class="cards">')
    for c in sorted(data["candidates"], key=lambda x: x["total"]):
        a(f'<button class="cand" data-cand="{c["id"]}" aria-pressed="false">'
          f'<span class="top"><span>candidate {c["id"]}</span>'
          f'<span class="price">{m(c["total"])}</span></span>'
          f'<div class="route">{escape(c["label"])}</div>'
          f'<div class="meta">Out {sd(c["depart"])} · back {sd(c["return"])}<br>'
          f'{c["nights"]} nights · flights {m(c["flights_total"])} + '
          f'lodging {m(c["lodging_total"])}'
          + (f'<br>{escape(c["note"])}' if c.get("note") else '')
          + '</div></button>')
    a('</div></section>')

    # ── legs ──────────────────────────────────────────────────────
    if data.get("legs"):
        a('<section id="legs"><span class="eyebrow">Every leg, with its reference</span>')
        a('<h2>Flights</h2>')
        a('<p class="lede">“Expensive” compares against the typical range for the '
          'route across the year. Where a 60-day history for this exact date came '
          'back, it is on the row — that is the only signal that justifies waiting.</p>')
        a('<div class="tscroll"><table><thead><tr><th>Leg</th><th>Today</th>'
          '<th>Typical for the route</th><th>Signal</th><th>What to do</th>'
          '</tr></thead><tbody>')
        for leg in data["legs"]:
            owners = " ".join(str(c) for c in leg["candidates"])
            rng = leg.get("typical_range")
            rng_txt = (f'{m(rng[0])} — {m(rng[1])}' if rng
                       else '<span class="mono" style="color:var(--muted)">not returned</span>')
            hist = (f'<br><span class="mono" style="font-size:.78rem;color:var(--ok)">'
                    f'60-day low {m(leg["history_low"])}</span>'
                    if leg.get("history_low") else '')
            bought = ' <span class="pill ok">bought</span>' if leg.get("purchased") else ''
            # A leg's price is only actionable next to the place you buy it.
            # `links` is a list of {label, url}; absent means no link, never a
            # guessed one — a fabricated booking URL is worse than none.
            buy = "".join(
                f' · <a href="{escape(lk["url"])}">{escape(lk["label"])}</a>'
                for lk in leg.get("links", []))
            buy = (f'<br><span style="font-size:.8rem">{buy[3:]}</span>') if buy else ''
            a(f'<tr data-dim="{owners}"><td><strong>{escape(leg["label"])}</strong>{bought}'
              f'<br><span class="mono" style="font-size:.78rem;color:var(--muted)">'
              f'{sd(leg["date"])} · candidate{"s" if len(leg["candidates"]) > 1 else ""} '
              f'{", ".join(str(c) for c in leg["candidates"])}</span>{buy}</td>'
              f'<td class="num">{m(leg["price"])}</td>'
              f'<td class="num">{rng_txt}{hist}</td>'
              f'<td>{signal_pill(leg)}</td>'
              f'<td style="font-size:.9rem">{escape(leg.get("action", ""))}</td></tr>')
        a('</tbody></table></div></section>')

    # ── lodging ───────────────────────────────────────────────────
    if data.get("lodging"):
        a('<section id="lodging"><span class="eyebrow">'
          'Every window, quoted the same day</span><h2>Lodging</h2>')
        a('<p class="lede">Quoting one candidate today against another yesterday '
          'measures the search date, not the trip. All of these ran in one batch.</p>')
        a('<div class="tscroll"><table><thead><tr><th>City</th><th>Window</th>'
          '<th>Chosen</th><th>Nightly</th><th>Total</th></tr></thead><tbody>')
        for w in data["lodging"]:
            owners = " ".join(str(c) for c in w["candidates"])
            ch = w["chosen"]
            name = (f'<a href="{escape(ch["link"])}">{escape(ch["name"])}</a>'
                    if ch.get("link") else escape(ch["name"]))
            stars = (f'<br><span style="font-size:.78rem;color:var(--muted)">'
                     f'{ch["rating"]} ★ · {ch["reviews"]} reviews</span>'
                     if ch.get("rating") else '')
            a(f'<tr data-dim="{owners}"><td><strong>{escape(w["city"])}</strong></td>'
              f'<td class="mono" style="font-size:.85rem">{sd(w["check_in"])} → '
              f'{sd(w["check_out"])}<br><span style="color:var(--muted)">'
              f'{w["nights"]} nights</span></td>'
              f'<td>{name}{stars}</td>'
              f'<td class="num">{m(ch.get("nightly"))}</td>'
              f'<td class="num">{m(ch["total"])}</td></tr>')
        a('</tbody></table></div></section>')

    # ── ground ────────────────────────────────────────────────────
    if data.get("ground"):
        a('<section id="ground"><span class="eyebrow">Measured road distance and time'
          '</span><h2>Drive or fly</h2>')
        a('<div class="tscroll"><table><thead><tr><th>Leg</th><th>Distance</th>'
          '<th>Driving</th><th>Flying</th><th>Worth driving?</th></tr></thead><tbody>')
        for g in data["ground"]:
            a(f'<tr><td><strong>{escape(g["label"])}</strong></td>'
              f'<td class="num">{g["km"]:.1f} km</td>'
              f'<td class="num">{g["drive_minutes"] // 60}h{g["drive_minutes"] % 60:02d}</td>'
              f'<td class="num">{g["fly_minutes"] // 60}h{g["fly_minutes"] % 60:02d}</td>'
              f'<td>{escape(g["verdict"])}'
              + (f'<br><span style="font-size:.82rem;color:var(--muted)">'
                 f'{escape(g["note"])}</span>' if g.get("note") else '')
              + '</td></tr>')
        a('</tbody></table></div></section>')

    # ── events ────────────────────────────────────────────────────
    live = [e for e in data.get("events", []) if e["candidates"]]
    if live:
        a('<section id="events"><span class="eyebrow">'
          'Cross-checked against the lodging windows, not by eye</span>')
        a('<h2>What is on while you are there</h2>')
        a('<p class="lede">'
          '<span class="pill ok">firm</span> you sleep in the city the night before · '
          '<span class="pill">arrival</span> it is the day you land, so it depends on '
          'the flight. An event on the check-out night is watched from the plane and '
          'is not listed.</p>')
        a('<div class="tscroll"><table><thead><tr><th>Date</th><th>Event</th>'
          '<th>Venue</th><th>Who</th>'
          + ('<th>Calendar</th>' if any(e.get("calendar_url") for e in live) else '')
          + '</tr></thead><tbody>')
        for ev in sorted(live, key=lambda e: e["date"]):
            owners = " ".join(str(c) for c in ev["candidates"])
            chips = " ".join(
                f'<span class="pill {"ok" if kind == "firm" else ""}">{cid}</span>'
                for cid, kind in sorted(ev["candidates"].items()))
            title = (f'<a href="{escape(ev["link"])}">{escape(ev["title"])}</a>'
                     if ev.get("link") else escape(ev["title"]))
            note = (f'<br><span style="font-size:.8rem;color:var(--muted)">'
                    f'{escape(ev["note"])}</span>' if ev.get("note") else '')
            cal = (f'<td><a class="cal" href="{escape(ev["calendar_url"])}" '
                   f'target="_blank" rel="noopener">+ calendar</a></td>'
                   if ev.get("calendar_url") else
                   ('<td></td>' if any(e.get("calendar_url") for e in live) else ''))
            a(f'<tr data-dim="{owners}"><td class="mono" style="font-size:.85rem">'
              f'{sd(ev["date"])}'
              + (f'<br><span style="color:var(--muted)">{escape(ev["time"])}</span>'
                 if ev.get("time") else '')
              + f'</td><td><strong>{title}</strong>{note}</td>'
              f'<td style="font-size:.9rem">{escape(ev.get("venue", ""))}</td>'
              f'<td>{chips}</td>{cal}</tr>')
        a('</tbody></table></div></section>')

    # ── day by day, per candidate ─────────────────────────────────
    if data.get("days"):
        a('<section id="days"><span class="eyebrow">Day by day</span>'
          '<h2>The plan</h2>')
        for c in data["candidates"]:
            mine = [x for x in data["days"] if x.get("candidate") == c["id"]]
            if not mine:
                continue
            a(f'<div data-for="{c["id"]}" hidden>')
            a(f'<h3 style="color:var(--muted);font-weight:400;margin-bottom:1rem">'
              f'{escape(c["label"])}</h3>')
            for day in sorted(mine, key=lambda x: x["date"]):
                kind = day.get("kind", "stay")
                a(f'<div class="day {escape(kind)}">')
                a(f'<span class="when">{sd(day["date"])}</span>')
                a(f'<h3>{escape(day["title"])}</h3>')
                if day.get("box"):
                    a(f'<div class="box">{escape(day["box"])}</div>')
                if day.get("items"):
                    a('<ul>' + "".join(f'<li>{escape(i)}</li>'
                                       for i in day["items"]) + '</ul>')
                a('</div>')
            a('</div>')
        a('</section>')

    # ── what nobody measured ──────────────────────────────────────
    if data.get("unmeasured"):
        a('<section id="unmeasured"><h2>Not measured, and not estimated</h2>')
        a('<div class="callout warn"><h4>These are missing from every total on '
          'this page</h4>')
        for item in data["unmeasured"]:
            if isinstance(item, str):
                a(f'<p>· {escape(item)}</p>')
            else:
                a(f'<p>· <strong>{escape(item["what"])}</strong> — '
                  f'{escape(item["why"])}</p>')
        a('<p style="color:var(--muted)">No tool on this page returns them. A number '
          'nobody measured is worth less than a stated gap.</p></div></section>')

    # ── provenance ────────────────────────────────────────────────
    if data.get("provenance"):
        a('<section id="provenance"><span class="eyebrow">Where each number came from'
          '</span><h2>Provenance</h2>')
        a('<div class="tscroll"><table><thead><tr><th>Tool</th><th>Source</th>'
          '<th>Produced</th><th>Searches</th></tr></thead><tbody>')
        for p in data["provenance"]:
            a(f'<tr><td class="mono">{escape(p["tool"])}</td>'
              f'<td style="font-size:.9rem">{escape(p.get("source", ""))}</td>'
              f'<td style="font-size:.9rem">{escape(p.get("produced", ""))}</td>'
              f'<td class="num">{p.get("searches", "—")}</td></tr>')
        a('</tbody></table></div></section>')

    if notes:
        a('<div class="callout warn"><h4>The renderer disagreed with the data</h4>')
        for n in notes:
            a(f'<p>· {escape(n)}</p>')
        a('</div>')

    a(f'<p class="foot">Quoted {escape(trip["quoted_on"])} · fares and rates move '
      f'daily, and every figure here is only as fresh as that date. Generated by '
      f'<code>plan-a-trip/render.py</code> from a single JSON document — nights, '
      f'candidate membership and totals are derived, not typed.</p>')

    return (
        f'<!doctype html><html lang="{escape(lang)}"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{escape(trip["title"])}</title><style>{CSS}</style></head>'
        f'<body data-chosen="{escape(chosen)}"><div class="wrap">'
        + "\n".join(out)
        + f'</div><script>{JS}</script></body></html>'
    )


# --------------------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("trip", type=Path, help="JSON document produced by the protocol")
    ap.add_argument("-o", "--out", type=Path, help="output .html (default: alongside)")
    args = ap.parse_args()

    data = json.loads(args.trip.read_text(encoding="utf-8"))
    try:
        notes = verify(data)
    except AssertionError as exc:
        print(f"the data contradicts itself:\n  {exc}", file=sys.stderr)
        return 1

    out = args.out or args.trip.with_suffix(".html")
    out.write_text(render(data, notes), encoding="utf-8")

    print(f"{out}  ·  {len(data['candidates'])} candidates, "
          f"{len(data.get('legs', []))} legs, "
          f"{len([e for e in data.get('events', []) if e['candidates']])} events in window")
    for n in notes:
        print(f"  ! {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
