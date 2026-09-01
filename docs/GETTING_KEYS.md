# Getting the API keys

`cosmo-travel-mcp` talks to three paid-but-free-tier providers. You can set up
any of them on its own — the server reports each tool as ready or not ready
independently.

| Key | Env var | Unlocks | Cost |
|---|---|---|---|
| SerpAPI | `SERPAPI_API_KEY` | `search_flights`, `search_multi_city`, `search_accommodations`, `search_cheapest_dates` | Free: 100 searches/month |
| Google Maps | `GOOGLE_MAPS_API_KEY` | `compare_drive_or_fly` | Free monthly credit (~$200); billing account required |
| Ticketmaster | `TICKETMASTER_API_KEY` | `search_ticketmaster_events` | Free: 5,000 requests/day |

**In a hurry?** Run this and it will walk you through all three, check the keys, and
register the server:

```bash
uv tool install git+https://github.com/maththedev42/cosmo-travel-mcp
cosmo-travel-mcp setup --register
```

---

## 1. SerpAPI key

SerpAPI is a licensed Google-results provider. It is what makes the flight and
accommodation tools work without scraping.

1. Go to **<https://serpapi.com/users/sign_up>** and create an account. No
   credit card is required for the free plan.
2. Confirm the email SerpAPI sends you.
3. Open **<https://serpapi.com/manage-api-key>** and copy your **private API
   key**.

### What counts as a search

The free plan includes **100 searches per month**. One search is spent per:

- `search_flights` call (one-way or one phase of a round trip)
- each leg lookup inside `search_multi_city`
- `search_accommodations` call
- **each date sampled** by `search_cheapest_dates`

That last one is the trap. `search_cheapest_dates` defaults to sampling 6 dates
and allows up to 15, so a single call can spend 6–15% of your monthly budget.
Ask for a narrow window, and check your remaining quota with `check_setup` —
that check is free and does not count as a search.

### Verifying

```bash
curl -s "https://serpapi.com/account.json?api_key=YOUR_KEY" | head
```

A valid key returns your plan and `plan_searches_left`. An invalid one returns
`{"error": "Invalid API key..."}`.

---

## 2. Google Maps key

Only needed for `compare_drive_or_fly`, which answers "is driving
Orlando→Miami actually cheaper than flying?" using real road distance and
traffic-aware duration.

1. Open the **[Google Cloud Console](https://console.cloud.google.com/)** and
   create a project (or select an existing one).
2. **Enable billing** on that project. Google requires a billing account on
   file even for free-tier usage. The free monthly credit (~$200) is far more
   than this server will ever consume — each route lookup costs a fraction of
   a cent.
3. Enable the **Routes API**:
   **<https://console.cloud.google.com/apis/library/routes.googleapis.com>**

   > It must be the **Routes API**. The older Distance Matrix API is legacy and
   > this server does not call it — enabling only that one gives you a key that
   > fails with `PERMISSION_DENIED`.
4. Go to **APIs & Services → Credentials → Create credentials → API key** and
   copy the key.
5. Recommended: click **Restrict key** and limit it to the Routes API, so a
   leaked key cannot be spent against your other Google services.

### Verifying

`check_setup` validates this key by making one real `computeRouteMatrix` call
(San Francisco → Los Angeles). Unlike the SerpAPI check, this one is not free —
it costs a fraction of a cent.

---

## 3. Ticketmaster key

Only needed for `search_ticketmaster_events` — the tool that answers "have
tickets gone on sale yet," which `search_events` structurally cannot: the
Google-backed tool has no `sales` field at all.

1. Sign up at **<https://developer.ticketmaster.com/>** — free, no approval
   wait, no credit card.
2. Create an app in your dashboard and copy its **Consumer Key**; that is
   what this server calls the API key. You do not need the Consumer Secret —
   the Discovery API's `GET` endpoints only take `apikey` on the query
   string.
3. Free tier: **5,000 requests/day**, 5 requests/second. It does not share
   quota with SerpAPI — a Ticketmaster call never touches your 100/month.

### Coverage — verified live, not assumed

Live-tested 2026-09-01 with `countryCode` alone (no city filter): **US, CA,
MX, GB, PE, CL, and BR** all returned events. **Argentina returned zero** —
confirmed, not a filter artifact. Ticketmaster's own docs list more countries
than this; treat any country not in that list as unverified rather than
covered.

**`city` is an exact match against Ticketmaster's own registry — it does not
fuzzy-match and does not tolerate diacritics.** Live-tested: `city=São Paulo`
and `city=Sao Paulo` (unaccented) both returned zero for a country
(`countryCode=BR`) that has 141 real events, while `city=Rio de Janeiro`
worked. If a call with `city` set returns zero, retry with `city` omitted and
`country_code`/`keyword` instead before concluding there's no coverage.

### Verifying

```bash
curl -s "https://app.ticketmaster.com/discovery/v2/events.json?apikey=YOUR_KEY&size=1"
```

A valid key returns a `page` object. An invalid one returns HTTP 401 with a
body shaped `{"fault": {"faultstring": "Invalid ApiKey", ...}}` — a different
error envelope from SerpAPI's `{"error": "..."}`, so don't reuse that parsing
logic here.

---

## 4. Attaching the keys

Environment variables are fixed at **registration** time, not read from your
shell when a tool runs. That means adding a key later requires re-registering
the server.

### Interactive (recommended)

```bash
cosmo-travel-mcp setup --register
```

It prompts for each key with hidden input, validates them against the real
APIs before writing anything, shows you the exact command it is about to run
(with the keys masked), removes an existing registration if there is one, and
registers the server.

### By hand

```bash
claude mcp add cosmo-travel --scope user \
  -e SERPAPI_API_KEY=<your-serpapi-key> \
  -e GOOGLE_MAPS_API_KEY=<your-google-maps-key> \
  -e TICKETMASTER_API_KEY=<your-ticketmaster-key> \
  -- "$(which cosmo-travel-mcp)"
```

Register the **installed binary**, by absolute path. Registering
`uvx --from git+…` makes the server re-resolve its git dependency on every
launch, which takes far longer than the 30 seconds a client waits for a stdio
server to come up — the client reports *Failed to connect*.

If the server is already registered, replace it:

```bash
claude mcp remove cosmo-travel --scope user
# …then run the add command above
```

Then restart your MCP client and ask it to call `check_setup`.

---

## Troubleshooting

**The client shows `✘ Failed to connect` / "connection timed out after 30000ms".**
Almost always the launch command is `uvx --from git+…`. uvx re-resolves the git
dependency on every start — over two minutes on a cold cache — and the client
gives a stdio server 30 seconds. Install the tool once and register the binary:

```bash
uv tool install --force git+https://github.com/maththedev42/cosmo-travel-mcp
claude mcp remove cosmo-travel --scope user
claude mcp add cosmo-travel --scope user -- "$(which cosmo-travel-mcp)"
```

Startup is then ~3 seconds. Verify with `claude mcp get cosmo-travel`.

**`check_setup` says a key is "not set" after I registered it.**
The key was not attached to the registration. Env vars come from the `-e` flags
on `claude mcp add`, not from your shell — re-register with the flags, or run
`cosmo-travel-mcp setup --register`.

**Maps key returns HTTP 403 / `PERMISSION_DENIED`.**
Either the Routes API is not enabled on the project, billing is not enabled, or
a key restriction excludes the Routes API. Check all three at
<https://console.cloud.google.com/>.

**Ticketmaster returns HTTP 401 / `Invalid ApiKey`.**
Use the **Consumer Key** from your app's dashboard, not the Consumer Secret —
the Discovery API's `GET` endpoints only accept the key. A freshly created app
can take a minute or two to activate; retry once before assuming the key is
wrong.

**`search_ticketmaster_events` returns zero results for a city I know is
covered.** `city` is an exact match against Ticketmaster's own registry, not a
fuzzy search — verified live: `São Paulo` and `Sao Paulo` both returned zero
for a country with 141 real events. Drop `city` and search with
`country_code` and/or `keyword` instead.

**SerpAPI says "Invalid API key".**
You may have copied a *secret* key from a different service, or the account's
free searches have reset to a new plan. Re-copy from
<https://serpapi.com/manage-api-key>.

**Flights come back empty for a route that obviously exists.**
Check the date format (`YYYY-MM-DD`) and that the airport codes are IATA codes.
Also confirm you have quota left — `check_setup` shows `plan_searches_left`.

**I ran out of searches mid-plan.**
The free tier resets monthly. `check_setup` is free, so use it to confirm the
reset before retrying. Prefer `search_flights` on specific dates over
`search_cheapest_dates` when you already know roughly when you are flying.
