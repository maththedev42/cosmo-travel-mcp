---
name: plan-a-trip
description: Plan a multi-city trip end to end with cosmo-travel-mcp — compare candidate itineraries, quote flights and lodging comparably, find what is on while the traveller is there, and set up a price watch on what is not yet bought. Use when someone asks to plan, price, or compare a trip with more than one city or more than one possible order, or asks whether to buy a fare now or wait.
---

# Plan a trip

`plan_trip` (the MCP prompt) tells you *which tool to call*. This tells you
*how to be right*. Every rule below is a mistake that was made in a real
planning session and cost either money or a wrong recommendation.

Read **Method** before spending a single search. Then follow **Protocol**.

---

## Method — ten rules, each one a scar

### 1. Never compare entry doors on a single date
Comparing three arrival cities on one date picked the wrong city. Three doors
× three dates found a fare **R$ 1.300 cheaper** and inverted the ranking: the
one date sampled by accident was the only one where the loser won. Cost: 9
searches. Always worth it on an international trip.

The same applies to exits, and the exit matrix lies in a second way — see
rule 7.

### 2. `price_level` is not `price_history`
`price_insights` may carry three different things, and they answer different
questions:

| field | compares against | supports "wait"? |
|---|---|---|
| `price_level` | the route across the whole year | no |
| `typical_price_range` | the route across the whole year | weakly |
| `price_history` | *this exact query and date*, last 60 days | **yes** |

Only the third one supports telling someone to hold. **It is usually absent**
— measured 2026-08-03: of 14 one-way queries run in one batch, **one** came
back with `price_history`. That is provider-side, not a client bug.

So: pull both, quote both, and label the claim by its strength. "Above the
route's normal band" is a much softer statement than "above what this exact
date has cost". Never present them as the same signal, and never say "wait"
on the label alone.

### 3. Quote every candidate itinerary on the same day
Lodging and fares quoted across different days is not a comparison — it
measures the quote date. In one session a **R$ 2.842** gap between two
itineraries collapsed to **R$ 1.417** once all four itineraries (14 flight
legs, 10 lodging windows) were re-quoted in a single batch. Half the
"difference" was the calendar.

**A partial re-quote is worse than a stale one.** Re-quoting only the cheap
side would have widened a gap that was already an artefact. Re-quote
everything or nothing.

### 4. Pass `adults` explicitly to every tool
`search_flights` defaults to `adults=1`; `search_accommodations` defaults to
`adults=2`. Left alone they silently produce a 2× discrepancy inside one
report. Pass it explicitly to both, and echo the passenger count in the
output so the reader can catch you.

### 5. Run `get_accommodation_details` on finalists
The per-category review sentiment overturned a 4.0-rated hotel that was the
cheapest eligible option: 443 negative bathroom mentions against 50 positive,
496 negative on cleanliness, 959 one-star reviews. The mean rating hid all of
it; the breakdown did not. Budget 2–3 of these.

Also fix a selection rule *once* and apply it to every window unchanged
(e.g. "cheapest with rating ≥ 4.0, excluding hostels and anything under 100
reviews"). State it, apply it everywhere, and name any deviation. A rule
chosen per-window is a preference wearing a lab coat.

### 6. Nights are derived from flights, never typed
An overnight flight means the first night is on the plane. A session asked
for **six** nights in New York for itineraries that land at 07:05 the next
morning — one phantom night at R$ 709.

Assert it: `sum(nights) == (departure_date - arrival_date).days`, and
`first_window.check_in == arrival_date`. Both, every time.

### 7. A later return is not cheaper until lodging is counted
Airfare fell R$ 1.200; the seven added nights cost R$ 3.285. And check the
span: "20 Dec to 11 Jan" is 22 days, not the 15 the traveller asked for. A
cheaper column in a fare matrix can describe a trip that was never on offer.

`compare_trip_windows` automates the arithmetic — it prices the flights *and*
the nights for the windows closest to a fixed date and reports the per-night
break-even. It does not remove the obligation to decide whether a window is a
trip the traveller actually wants.

### 8. Bags are not in the headline fare
`bags` in `search_flights` is **carry-on only**. Hold luggage lives in the
booking phase (`baggage_prices`) — pass a `booking_token` back to get it.
Measured miss on one leg: R$ 172. A low-cost carrier can lose to a legacy
one once the checked bag is priced.

### 9. An undated event query returns what is *near*, not what *exists*
`search_events` with a bare `"New York"` returned 40 results, none past 14
November, and the session reported that December was not indexed yet.
**Wrong.** A control run of the same bare query returned the same August
events; adding `"Christmas shows New York December 2026"` and
`"New York January 2027 events"` surfaced the whole Broadway season through
February — including three shows on the night the traveller lands.

**Every `also_search` angle must name the month and year** for a trip more
than ~6 weeks out. And: *absence of results is a claim*. Before writing "there
is nothing on", run the control and say what it was.

### 10. Some things no tool returns — say so, don't estimate
Verified 2026-08-03: no free API returns Disney or Universal admission
pricing (themeparks.wiki exposes only Lightning Lane, 31-day horizon). Car
rental one-way drop fees: same. Outlet discount depth: same.

Name the official channel, offer a labelled web search, and **never write a
number that looks measured when it is not**. A plan whose uncertainty is
visible is worth more than one whose confidence is decorative.

---

## Protocol

### 0. Budget before you spend — `check_setup`
Free, and it reports `total_searches_left`. Then count the plan:

| stage | searches |
|---|---|
| entry doors × dates (rule 1) | 9 |
| exit doors × dates | 9 |
| internal legs | 1 per unique leg per candidate |
| lodging | 1 per city-window (all candidates, rule 3) |
| hotel details | 2–3 |
| things to do | 1 per city |
| events sweep | `(1 + len(also_search)) × pages` per city |

**Show that arithmetic against the remaining quota before calling anything.**
If it does not fit, say what you are dropping and why — never silently
sample less.

### 1. Pin the fixed point
Ask for, or extract, the one thing that cannot move (a wedding, New Year's
Eve in a named city, a return-to-work date). Everything else is optimised
around it. Then enumerate **all** orderings that respect it — do not assume
the obvious city is first. In one session, freeing New York from being the
first city is what opened the whole search space.

Write the candidate set down explicitly before measuring. Four candidates is
normal; more than six means a constraint is missing.

### 2. Fares — doors, then exits, then internals
Per rules 1, 2, 7, 8. One-way per leg (`type=2`), `adults` explicit. Record
for every leg: price, `price_level`, `typical_price_range`, and
`price_history` **if it came**.

### 3. Lodging — every window, one batch
Per rules 3, 5, 6. `vacation_rentals=False` for hotels,
`sort_by="lowest_price"`, `adults` explicit. Same day for all candidates or
the comparison is void.

### 4. Short hops — `compare_drive_or_fly`
Under ~400 km, driving often wins once airport overhead is counted. It cannot
price the rental or the one-way drop fee, so express the answer as a
**threshold**: "driving wins if the drop fee is under X" is a usable answer;
a total that silently omits the fee is not.

### 5. What's on — `search_events`
Per rule 9. Then cross each event against the lodging windows
programmatically, not by eye: in window iff `check_in <= date < check_out`.
An event on the check-out night is watched from the plane; one on the
check-in date depends on the arrival time. Label those two cases
differently — never drop them silently, never call them firm.

### 6. Assemble, then verify — `check_itinerary` (free)
Blockers are cheap to find and expensive to ship. Pass `operating_hours` and
`coordinates` **verbatim** from `search_things_to_do`. Fix every `blocker`
and re-run. Watch holiday closures specifically: a trip built around New
Year's is a trip where half the plan may be shut on 1 January, and normal
opening hours will not say so.

### 7. Offer the calendar — `build_calendar` (free)
One item per flight leg in each airport's own local time, plus check-ins and
booked shows. Do not dump forty events on anyone.

### 8. Render the dossier — `render.py` (free)
Chat is a bad container for a decision worth thousands. Write everything
gathered so far into one JSON document and render it:

```bash
python3 skills/plan-a-trip/render.py trip.json -o dossier.html
```

Out comes a single self-contained HTML file — inline CSS and JS, no assets, no
server — with the candidates as buttons that re-filter the whole page.
`example-trip.json` in the same directory is a filled-in schema from a real
session; copy its shape.

**Do not write the HTML yourself.** The first time this page was built by hand
it carried two hand-typed copies of the same event titles, they drifted by three
characters, and the build died on a `KeyError`. Duplicated hand-typed data is a
correctness problem, not a formatting one. Put the data in the JSON once and let
the renderer derive the rest:

| derived, never typed | asserted, never trusted |
|---|---|
| nights, from `check_out - check_in` | `flights_total` against the sum of that candidate's legs |
| each candidate's night count | `lodging_total` against the sum of its windows |
| which candidates can attend each event | nightly rate × nights against the stated total |

A total that disagrees with its parts **fails the render** — better a missing
page than a page whose header contradicts its own table. Softer disagreements
(a nightly rate that does not multiply out, an event whose typed candidates
differ from what the windows say) render *and* print on the page, because a
correction the reader cannot see is not a correction.

Two things the schema forces you to fill in, and should:

- `unmeasured` — everything no tool returned, each with **why**. Per rule 10, a
  stated gap is worth more than a decorative estimate.
- `provenance` — tool, source, what it produced, searches spent. If a figure
  cannot name the tool that returned it, it does not belong on the page.

### 9. Offer the price watch — see below

---

## The price watch

For everything **not yet purchased** — flights above all. The point: fares
move on a scale of weeks, nobody re-checks by hand for six weeks, and in one
session `price_history` showed **R$ 2.180** in play across two legs.

### What to persist
The *query*, not the result — the query is what makes tomorrow comparable to
today:

```json
{ "kind": "flight", "origin": "", "destination": "", "outbound_date": "",
  "adults": 2, "travel_class": "economy", "stops": null,
  "purchased": false,
  "baseline": { "min": null, "max": null, "source": "price_history|typical_range",
                "low_band_ceiling": null, "captured_on": "" },
  "observations": [{ "date": "", "price": 0, "price_level": "" }] }
```

- `baseline.min`/`max` come from `price_history` on the **first** run.
- When the series is absent (usual — rule 2), fall back to
  `typical_price_range[1]` and set `source` accordingly. **Alerts built on
  the fallback must be worded differently.**
- No series and no range → no baseline. Record it and surface the leg as
  *unmeasured*, never as *fine*.
- `low_band_ceiling` defaults to `min × 1.10`. It is a decision about how
  much upside to leave on the table, so keep it visible and configurable.
- `observations` is append-only. The series is the whole value.

### Alert rule
Fire when `today <= low_band_ceiling`, and say **which**:
- `today <= min` → *below the measured floor* — the strong signal.
- `today <= low_band_ceiling` → *inside the low band*.

Every alert carries the number, the floor it is compared to, and the date
that floor was captured. "Cheap" with no reference is the failure mode this
replaces. Fire a **weak** alert for any watched leg still without a baseline —
silence must never read as "nothing to do".

### Cadence is the traveller's call
The free tier is 100 searches/month. Show this table and take an explicit
choice. **Never pick daily for them, and never install a schedule nobody asked
for.**

| cadence | 4 legs | 8 legs | fits 100/month? |
|---|---|---|---|
| daily | 120 | 240 | no |
| every 3 days | 40 | 80 | yes, 4 legs comfortably |
| **weekly** | **~18** | **~35** | **yes, both** |
| off | 0 | 0 | — |

Weekly is the right default to offer, and not only for quota: the one 60-day
series measured in that session moved R$ 1.051 → R$ 1.925 → R$ 1.459 over two
months. A weekly sample draws that curve completely; a daily one spends six
times the quota to redraw it.

Required at install time:
1. Compute `(unpurchased_legs + event_sweeps) × runs_per_month` and show it
   against `total_searches_left` **before** creating anything. Worked example:
   4 legs + 4 sweeps weekly = 8 per run ≈ 35/month, which fits the free tier
   and shrinks as legs are marked purchased.
2. Take an explicit cadence choice.
3. Stop watching a leg the moment it is marked `purchased` — that is what
   makes an 8-leg watch shrink instead of grow.
4. Skip a run, with a logged reason, when remaining quota is under a reserve
   (default 20). A watch must never starve an interactive search.
5. The same command that installs it must be able to show, re-pace and remove
   it.

### Also watched: shows and events
Different question, different mechanism. A fare oscillates and you ask *is it
low*; a show is **published** and then **sells out**, and you ask *is there
something new in my window*. So event watches report **arrivals, never
prices**, and they never say "buy now" — they say "this did not exist last
week".

An `event_watches` entry is one query, one page, per city window:

```json
{ "city": "", "query": "... <Month> <Year> events",
  "window": { "from": "check-in", "to": "check-out" },
  "seen": ["title|start_date", ...], "done": false }
```

Two things to be honest about:

- **A one-query weekly probe is a change detector, not a discovery sweep.**
  It costs 1 search where the initial sweep costs 6+, and it will miss what a
  different phrasing would have found (rule 9 still holds). Say that in the
  output rather than letting a quiet week read as "nothing is on".
- **Match the window on the provider's own tokens.** `google_events` returns
  `start_date: "Dec 30"` with **no year**, so build the set of `"Mon D"`
  strings for the stay and match those. Do not parse a year out of a string
  that does not carry one.

Point one probe at whatever date came back empty in the initial sweep — that
is where new listings matter most, and an empty result there is a hole, not an
answer.

### Not in scope: hotels
Hotel rates move daily and mean-revert — measured: the same room, same query,
R$ 583 → R$ 493 → R$ 768 across three days. A "minimum" is not a meaningful
target. The right advice is *book refundable and re-check*, not *watch and
pounce*. Say that instead of offering a watch that would fire constantly.

### Where the scheduler lives
**Not in the MCP server.** A tool server that installs background jobs is a
surprise, and the quota arithmetic above makes it a decision that has to be
visible. Use the host's scheduler, pointed at `watch.py` in this directory,
with the watchlist JSON as its state.

**Pick a scheduler that outlives the conversation.** Measured 2026-08-03:
Claude Code's own `CronCreate` is *session-only* — the job is gone when the
session ends — and recurring jobs auto-expire after 7 days. For a trip five
months out that is not a watch, it is the appearance of one. Use `launchd`
(macOS) or `cron` (Linux) instead. `CronCreate` is fine only for a watch that
is meant to last the afternoon.

`watch.py` is standard library only, so the scheduler can run it with the
system Python without a virtualenv:

```
python3 watch.py ~/.cosmo-travel/watchlist-<trip>.json
```

It reads the key from `$SERPAPI_API_KEY`, then `~/.cosmo-travel/env`
(chmod 600), then `claude mcp get cosmo-travel`, and never prints it. It
appends to `watch.log`, writes human-readable alerts to `alerts.md`, upgrades
a weak baseline in place the first time `price_history` appears, and skips the
whole run when the quota reserve would be breached.
