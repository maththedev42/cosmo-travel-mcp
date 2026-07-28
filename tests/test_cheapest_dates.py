"""Unit tests for cheapest-dates search — zero real network calls."""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest
import respx
from httpx import Response

from cosmo_travel_mcp.tools.cheapest_dates import (
    _extract_cheapest_price,
    _generate_candidate_dates,
    search_cheapest_dates,
)

SERPAPI_URL = "https://serpapi.com/search.json"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_api_key() -> None:
    """Ensure no SERPAPI_API_KEY leaks between tests."""
    os.environ.pop("SERPAPI_API_KEY", None)


def _set_api_key() -> None:
    os.environ["SERPAPI_API_KEY"] = "test-key-123"


def _make_flight_item(price: float, currency: str = "BRL") -> dict:
    """Minimal parsed flight item for _extract_cheapest_price."""
    return {"price": price, "currency": currency, "bucket": "best_flights"}


# ---------------------------------------------------------------------------
# Candidate date generation
# ---------------------------------------------------------------------------


class TestGenerateCandidateDates:
    def test_evenly_spaced(self) -> None:
        """Dates evenly spaced across a range, first and last included."""
        result = _generate_candidate_dates(
            "2026-08-01", "2026-08-15", trip_duration_days=5, max_calls=6
        )
        assert result[0] == "2026-08-01"
        assert result[-1] == "2026-08-10"  # 15 - 5 = 10
        assert len(result) <= 6

    def test_single_day_window(self) -> None:
        """When window == trip_duration_days, only one candidate."""
        result = _generate_candidate_dates(
            "2026-08-01", "2026-08-06", trip_duration_days=5, max_calls=6
        )
        assert result == ["2026-08-01"]

    def test_max_calls_one(self) -> None:
        """max_calls=1 returns only the first date."""
        result = _generate_candidate_dates(
            "2026-08-01", "2026-08-20", trip_duration_days=5, max_calls=1
        )
        assert result == ["2026-08-01"]

    def test_deduplicates_rounding(self) -> None:
        """When rounding produces duplicates, they are removed."""
        result = _generate_candidate_dates(
            "2026-08-01", "2026-08-03", trip_duration_days=1, max_calls=10
        )
        # Window: Aug 1 to Aug 2 (only 2 possible departures)
        assert len(result) <= 2
        assert result[0] == "2026-08-01"
        assert result[-1] == "2026-08-02"

    def test_impossible_window(self) -> None:
        """earliest_departure + trip_duration_days > latest_return."""
        with pytest.raises(ValueError, match="Impossible window"):
            _generate_candidate_dates(
                "2026-08-10", "2026-08-14", trip_duration_days=5, max_calls=6
            )


# ---------------------------------------------------------------------------
# _extract_cheapest_price
# ---------------------------------------------------------------------------


class TestExtractCheapestPrice:
    def test_empty_results(self) -> None:
        assert _extract_cheapest_price({"flights": []}) is None

    def test_single_item(self) -> None:
        parsed = {"flights": [_make_flight_item(1234.0)]}
        assert _extract_cheapest_price(parsed) == (1234.0, "BRL")

    def test_cheapest_across_buckets(self) -> None:
        parsed = {
            "flights": [
                _make_flight_item(1500.0),
                _make_flight_item(900.0),
                _make_flight_item(1200.0),
            ]
        }
        assert _extract_cheapest_price(parsed) == (900.0, "BRL")


# ---------------------------------------------------------------------------
# search_cheapest_dates (mocked HTTP)
# ---------------------------------------------------------------------------


class TestSearchCheapestDates:
    @pytest.mark.asyncio
    async def test_basic_sampling(self, respx_mock) -> None:
        """3 candidate dates, each returns a different cheapest price."""
        _set_api_key()

        # 3 candidates for Aug 1-9 with 3-day trips, max_calls=3
        # Dates: Aug 1, Aug 4, Aug 6
        respx_mock.get(SERPAPI_URL).mock(
            side_effect=[
                Response(
                    200,
                    json={
                        "search_metadata": {"status": "Success"},
                        "best_flights": [
                            {
                                "price": 800,
                                "currency": "BRL",
                                "total_duration": 300,
                                "flights": [
                                    {
                                        "departure_airport": {
                                            "id": "GRU",
                                            "name": "Sao Paulo",
                                            "time": "2026-08-01 08:00",
                                        },
                                        "arrival_airport": {
                                            "id": "GIG",
                                            "name": "Rio de Janeiro",
                                            "time": "2026-08-01 09:00",
                                        },
                                        "airline": "LA",
                                        "flight_number": "1234",
                                        "duration": 60,
                                    }
                                ],
                                "departure_token": "tok-001",
                            }
                        ],
                    },
                ),
                Response(
                    200,
                    json={
                        "search_metadata": {"status": "Success"},
                        "best_flights": [
                            {
                                "price": 600,
                                "currency": "BRL",
                                "total_duration": 300,
                                "flights": [
                                    {
                                        "departure_airport": {
                                            "id": "GRU",
                                            "name": "Sao Paulo",
                                            "time": "2026-08-04 08:00",
                                        },
                                        "arrival_airport": {
                                            "id": "GIG",
                                            "name": "Rio de Janeiro",
                                            "time": "2026-08-04 09:00",
                                        },
                                        "airline": "G3",
                                        "flight_number": "5678",
                                        "duration": 60,
                                    }
                                ],
                                "departure_token": "tok-002",
                            }
                        ],
                    },
                ),
                Response(
                    200,
                    json={
                        "search_metadata": {"status": "Success"},
                        "best_flights": [
                            {
                                "price": 950,
                                "currency": "BRL",
                                "total_duration": 300,
                                "flights": [
                                    {
                                        "departure_airport": {
                                            "id": "GRU",
                                            "name": "Sao Paulo",
                                            "time": "2026-08-06 08:00",
                                        },
                                        "arrival_airport": {
                                            "id": "GIG",
                                            "name": "Rio de Janeiro",
                                            "time": "2026-08-06 09:00",
                                        },
                                        "airline": "AD",
                                        "flight_number": "9012",
                                        "duration": 60,
                                    }
                                ],
                                "departure_token": "tok-003",
                            }
                        ],
                    },
                ),
            ]
        )

        result = await search_cheapest_dates(
            origin="GRU",
            destination="GIG",
            earliest_departure="2026-08-01",
            latest_return="2026-08-09",
            trip_duration_days=3,
            max_calls=3,
        )

        assert result["candidates_checked"] == 3
        assert result["max_calls_requested"] == 3
        assert "note" in result
        assert "sample" in result["note"].lower()

        results = result["results"]
        assert len(results) == 3

        # Sorted by price ascending
        assert results[0]["cheapest_price"] == 600
        assert results[1]["cheapest_price"] == 800
        assert results[2]["cheapest_price"] == 950

    @pytest.mark.asyncio
    async def test_exactly_one_candidate(self, respx_mock) -> None:
        """Window exactly equals trip_duration_days — only one candidate."""
        _set_api_key()

        respx_mock.get(SERPAPI_URL).mock(
            return_value=Response(
                200,
                json={
                    "search_metadata": {"status": "Success"},
                    "best_flights": [
                        {
                            "price": 500,
                            "currency": "BRL",
                            "total_duration": 200,
                            "flights": [
                                {
                                    "departure_airport": {
                                        "id": "GRU",
                                        "name": "Sao Paulo",
                                        "time": "2026-08-01 10:00",
                                    },
                                    "arrival_airport": {
                                        "id": "GIG",
                                        "name": "Rio de Janeiro",
                                        "time": "2026-08-01 11:00",
                                    },
                                    "airline": "LA",
                                    "flight_number": "100",
                                    "duration": 60,
                                }
                            ],
                            "departure_token": "tok-single",
                        }
                    ],
                },
            )
        )

        result = await search_cheapest_dates(
            origin="GRU",
            destination="GIG",
            earliest_departure="2026-08-01",
            latest_return="2026-08-06",
            trip_duration_days=5,
            max_calls=5,
        )

        assert result["candidates_checked"] == 1
        assert len(result["results"]) == 1
        assert result["results"][0]["cheapest_price"] == 500

    @pytest.mark.asyncio
    async def test_max_calls_exceeds_cap(self) -> None:
        """max_calls > 15 rejected with clear error."""
        _set_api_key()

        with pytest.raises(ValueError, match="hard cap"):
            await search_cheapest_dates(
                origin="GRU",
                destination="GIG",
                earliest_departure="2026-08-01",
                latest_return="2026-08-20",
                trip_duration_days=5,
                max_calls=20,
            )

    @pytest.mark.asyncio
    async def test_max_calls_below_one(self) -> None:
        """max_calls < 1 rejected."""
        _set_api_key()

        with pytest.raises(ValueError, match="at least 1"):
            await search_cheapest_dates(
                origin="GRU",
                destination="GIG",
                earliest_departure="2026-08-01",
                latest_return="2026-08-20",
                trip_duration_days=5,
                max_calls=0,
            )

    @pytest.mark.asyncio
    async def test_impossible_window_rejected(self) -> None:
        """earliest + trip > latest returns clear error, no API calls."""
        _set_api_key()

        with pytest.raises(ValueError, match="Impossible window"):
            await search_cheapest_dates(
                origin="GRU",
                destination="GIG",
                earliest_departure="2026-08-15",
                latest_return="2026-08-18",
                trip_duration_days=5,
                max_calls=3,
            )

    @pytest.mark.asyncio
    async def test_missing_api_key(self) -> None:
        """Missing key → clear error, no HTTP call."""
        with pytest.raises(ValueError, match="SERPAPI_API_KEY is not set"):
            await search_cheapest_dates(
                origin="GRU",
                destination="GIG",
                earliest_departure="2026-08-01",
                latest_return="2026-08-15",
                trip_duration_days=5,
            )

    @pytest.mark.asyncio
    async def test_no_results_sets_none_price(self, respx_mock) -> None:
        """When SerpAPI returns no flights, price is None."""
        _set_api_key()

        respx_mock.get(SERPAPI_URL).mock(
            return_value=Response(
                200,
                json={
                    "search_metadata": {"status": "Success"},
                    "best_flights": [],
                    "other_flights": [],
                },
            )
        )

        result = await search_cheapest_dates(
            origin="GRU",
            destination="GIG",
            earliest_departure="2026-08-01",
            latest_return="2026-08-10",
            trip_duration_days=3,
            max_calls=2,
        )

        for r in result["results"]:
            assert r["cheapest_price"] is None

    @pytest.mark.asyncio
    async def test_passes_cabin_class_and_stops(self, respx_mock) -> None:
        """Cabin class and stops are passed through to SerpAPI params."""
        _set_api_key()

        respx_mock.get(SERPAPI_URL).mock(
            return_value=Response(
                200,
                json={
                    "search_metadata": {"status": "Success"},
                    "best_flights": [
                        {
                            "price": 700,
                            "currency": "BRL",
                            "total_duration": 300,
                            "flights": [
                                {
                                    "departure_airport": {
                                        "id": "GRU",
                                        "name": "Sao Paulo",
                                        "time": "2026-08-01 08:00",
                                    },
                                    "arrival_airport": {
                                        "id": "GIG",
                                        "name": "Rio de Janeiro",
                                        "time": "2026-08-01 09:00",
                                    },
                                    "airline": "LA",
                                    "flight_number": "123",
                                    "duration": 60,
                                }
                            ],
                            "departure_token": "tok-cs",
                        }
                    ],
                },
            )
        )

        result = await search_cheapest_dates(
            origin="GRU",
            destination="GIG",
            earliest_departure="2026-08-01",
            latest_return="2026-08-10",
            trip_duration_days=3,
            max_calls=1,
            cabin_class="business",
            max_stops="nonstop",
            country="BR",
            language="pt",
        )

        assert result["candidates_checked"] == 1

        # Verify the request included the right params
        request = respx_mock.calls.last.request
        assert request.url.params.get("travel_class") == "3"  # business
        assert request.url.params.get("stops") == "1"  # nonstop
        assert request.url.params.get("gl") == "BR"
        assert request.url.params.get("hl") == "pt"

    @pytest.mark.asyncio
    async def test_never_exceeds_max_calls(self, respx_mock) -> None:
        """Even with a large window, never more than max_calls requests."""
        _set_api_key()

        respx_mock.get(SERPAPI_URL).mock(
            return_value=Response(
                200,
                json={
                    "search_metadata": {"status": "Success"},
                    "best_flights": [
                        {
                            "price": 500,
                            "currency": "BRL",
                            "total_duration": 300,
                            "flights": [
                                {
                                    "departure_airport": {
                                        "id": "GRU",
                                        "name": "Sao Paulo",
                                        "time": "2026-08-01 08:00",
                                    },
                                    "arrival_airport": {
                                        "id": "GIG",
                                        "name": "Rio de Janeiro",
                                        "time": "2026-08-01 09:00",
                                    },
                                    "airline": "LA",
                                    "flight_number": "123",
                                    "duration": 60,
                                }
                            ],
                            "departure_token": "tok",
                        }
                    ],
                },
            )
        )

        await search_cheapest_dates(
            origin="GRU",
            destination="GIG",
            earliest_departure="2026-08-01",
            latest_return="2026-09-01",
            trip_duration_days=3,
            max_calls=5,
        )

        assert respx_mock.calls.call_count <= 5

    @pytest.mark.asyncio
    async def test_zero_trip_duration_rejected(self) -> None:
        """trip_duration_days < 1 rejected."""
        _set_api_key()

        with pytest.raises(ValueError, match="at least 1"):
            await search_cheapest_dates(
                origin="GRU",
                destination="GIG",
                earliest_departure="2026-08-01",
                latest_return="2026-08-10",
                trip_duration_days=0,
            )
