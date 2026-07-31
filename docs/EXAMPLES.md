# Worked examples

These are sample agent flows showing realistic tool calls and truncated
outputs. Every sample output is illustrative — the real output will vary
with dates, availability, and pricing.

---

## 1. Multi-city itinerary: POA to Orlando and Miami

An agent searching a 3-leg trip from Porto Alegre to NYC, then Orlando,
then back from Miami.

```
Tool: search_multi_city
Params: legs=[
  {origin: "POA", destination: "JFK", date: "2025-12-20"},
  {origin: "JFK", destination: "MCO", date: "2025-12-26"},
  {origin: "MIA", destination: "POA", date: "2026-01-05"}
]
```

Sample output (truncated):

```
flights: [
  {
    source: "best_flights",
    price: 5884,
    currency: "BRL",
    total_duration_minutes: 1020,
    stops: 2,
    legs: [
      {airline: "LATAM", departure_airport: "POA", arrival_airport: "GRU", …},
      {airline: "Delta", departure_airport: "GRU", arrival_airport: "JFK", …},
      …
    ],
    layovers: [{airport: "GRU", duration_minutes: 120}, …]
  },
  …
]
total_best: 4
total_other: 8
```

**Key point:** the `price` field on each phase-1 result (R$5,884 here) is
the **full itinerary total**, not the price of the first leg. See the
[reading multi-city and round-trip prices](../README.md#reading-multi-city-and-round-trip-prices)
note in the README.

---

## 2. Round-trip with departure_token drill-down

Phase 1 — search for a round-trip:

```
Tool: search_flights
Params: origin="POA", destination="JFK",
        outbound_date="2025-12-10", return_date="2026-01-10"
```

Sample output:

```
flights: [{price: 3450, departure_token: "WyJDal...", …}, …]
phase: "outbound options — prices are round-trip totals; pass departure_token
        to fetch return flights for one of them."
```

Phase 2 — pick an outbound and get its return legs:

```
Tool: search_flights
Params: origin="POA", destination="JFK",
        outbound_date="2025-12-10", return_date="2026-01-10",
        departure_token="WyJDal..."
```

Sample output:

```
flights: [
  {price: 3450, legs: [{departure_airport: "JFK", arrival_airport: "POA", …}]},
  …
]
phase: "return options — these are the return-flight options for the selected outbound."
```

---

## 3. Hotels for a date range

```
Tool: search_accommodations
Params: location="Orlando near Universal",
        check_in_date="2025-12-26", check_out_date="2025-12-31",
        adults=2, vacation_rentals=true
```

Sample output (truncated):

```
results: [
  {
    name: "Modern 2BR near Universal — Pool & Parking",
    type: "Vacation rental",
    rate_per_night: {lowest: "R$450"},
    total_rate: {lowest: "R$2,250"},
    rating: 4.8,
    reviews: 312,
    link: "https://www.google.com/travel/hotels/…"
  },
  …
]
vacation_rentals: true
total_results: 37
```

---

## 4. Drive-vs-fly comparison

```
Tool: compare_drive_or_fly
Params: origin="Orlando, FL", destination="Miami, FL",
        fuel_price_per_liter=4.50,
        fuel_efficiency_km_per_liter=12,
        rental_car_cost_total=350,
        flight_price=620,
        flight_duration_minutes=75,
        currency="BRL"
```

Sample output:

```
distance_km: 378.5
driving_duration_minutes: 227
estimated_fuel_cost: 141.94
estimated_total_driving_cost: 491.94
comparison: {
  cost_difference: 128.06,
  currency: "BRL",
  time_difference_minutes: -152
}
```

Driving wins on cost (R$491.94 vs R$620) but loses on time (~3.8h vs ~1.25h
in the air). Airport overhead (check-in, security, boarding) typically adds
2h, making the door-to-door gap smaller than the raw flight duration
suggests — the agent should flag this.
