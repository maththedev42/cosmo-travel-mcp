# Captured fixtures

Real SerpAPI response bodies, captured live and scrubbed of credentials.
They exist because hand-invented mock shapes let three normalizers ship
reading fields the API never returns.

| file | engine | request | captured |
|---|---|---|---|
| `google_events_search.json` | `google_events` | `q=Events in New York`, `hl=en`, `gl=us` | 2026-07-31 |
| `google_hotels_property_details.json` | `google_hotels` | `q=Miami Beach hotels` + `property_token`, `check_in_date=2026-09-10`, `check_out_date=2026-09-12`, `adults=2`, `currency=USD` | 2026-07-31 |
| `google_maps_things_to_do.json` | `google_maps` | `q=things to do in Miami`, `type=search`, `hl=en` | 2026-08-01 |
| `google_maps_restaurants.json` | `google_maps` | `q=restaurants in Miami`, `type=search`, `hl=en` | 2026-08-01 |

The two `google_maps` fixtures are both kept because attractions and food
return **different field sets** from the same engine: food adds `price`,
`extracted_price`, `description`, `reserve_a_table` and `service_options`,
and attractions omit them entirely. One fixture would have made half the
normalizer untestable.

Two details in these bodies are load-bearing and must not be "tidied":

* Times use U+202F (narrow no-break space) before the meridiem —
  `9:30 AM–5 PM`, not a regular space.
* `operating_hours` keys are **localized by `hl`**. These fixtures are `en`;
  an `hl=pt-br` call returns `segunda-feira` / `sábado` with `Fechado` for a
  closed day. See `test_localized_weekday_keys_survive_untranslated`.

Arrays are truncated to keep the files reviewable; shapes are untouched.
`search_metadata`, `search_parameters` and pagination blocks are removed.

**Rule:** a test asserting on the *shape* of an upstream response uses a
captured fixture. Hand-built payloads are for degenerate cases only
(missing field, null value, malformed member) and must say so in a comment.
