"""Tests for the accommodation search tool (SerpAPI Google Hotels)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import respx

from cosmo_travel_mcp.tools.flights import SERPAPI_BASE
from cosmo_travel_mcp.tools.hotels import get_accommodation_details, search_accommodations

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _set_fake_api_key(monkeypatch):
    """Set a fake SERPAPI_API_KEY so tests never hit the real guard."""
    monkeypatch.setenv("SERPAPI_API_KEY", "fake-test-key")


# ---------------------------------------------------------------------------
# Sample responses
# ---------------------------------------------------------------------------

_HOTEL_RESPONSE = {
    "search_metadata": {"status": "Success"},
    "search_parameters": {"engine": "google_hotels", "q": "Miami, FL"},
    "properties": [
        {
            "name": "Grand Beach Hotel",
            "type": "hotel",
            "rate_per_night": {
                "lowest": "$150",
                "extracted_lowest": 150,
                "before_taxes_fees": "$120",
                "extracted_before_taxes_fees": 120,
            },
            "total_rate": {"lowest": "$450", "extracted_lowest": 450},
            "rating": 4.5,
            "reviews": 230,
            "link": "https://www.google.com/hotels/grandbeach",
            "property_token": "abc123",
        }
    ],
}

_VACATION_RENTAL_RESPONSE = {
    "search_metadata": {"status": "Success"},
    "search_parameters": {"engine": "google_hotels", "q": "Orlando, FL"},
    "properties": [
        {
            "name": "Cozy Villa near Disney",
            "type": "vacation rental",
            "rate_per_night": {
                "lowest": "$200",
                "extracted_lowest": 200,
            },
            "total_rate": {"lowest": "$600"},
            "rating": 4.8,
            "reviews": 95,
            "link": "https://www.google.com/hotels/cozyvilla",
            "property_token": "xyz789",
        }
    ],
}

_ERROR_RESPONSE = {"error": "Invalid API key"}


# ---------------------------------------------------------------------------
# Parsing tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_accommodations_hotel_type():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=_HOTEL_RESPONSE)
        result = await search_accommodations(
            location="Miami, FL",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
        )

    assert result["total_results"] == 1
    prop = result["results"][0]
    assert prop["name"] == "Grand Beach Hotel"
    assert prop["type"] == "hotel"
    assert prop["rate_per_night"]["lowest"] == "$150"
    assert prop["rate_per_night"]["extracted_lowest"] == 150
    assert prop["total_rate"]["lowest"] == "$450"
    assert prop["total_rate"]["extracted_lowest"] == 450
    assert prop["rating"] == 4.5
    assert prop["reviews"] == 230
    assert prop["link"] == "https://www.google.com/hotels/grandbeach"
    assert prop["property_token"] == "abc123"


@pytest.mark.asyncio
async def test_search_accommodations_vacation_rental_type():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=_VACATION_RENTAL_RESPONSE)
        result = await search_accommodations(
            location="Orlando, FL",
            check_in_date="2026-09-01",
            check_out_date="2026-09-07",
        )

    assert result["total_results"] == 1
    prop = result["results"][0]
    assert prop["type"] == "vacation rental"
    assert prop["rate_per_night"]["extracted_lowest"] == 200


@pytest.mark.asyncio
async def test_search_accommodations_vacation_rentals_param():
    """vacation_rentals=True should be passed in the request."""
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=_VACATION_RENTAL_RESPONSE)
        await search_accommodations(
            location="Orlando, FL",
            check_in_date="2026-09-01",
            check_out_date="2026-09-07",
            vacation_rentals=True,
        )
        req = mock.calls.last.request
        assert req.url.params["engine"] == "google_hotels"
        assert "vacation_rentals=true" in str(req.url)


@pytest.mark.asyncio
async def test_search_accommodations_hotels_mode():
    """vacation_rentals=False should pass vacation_rentals=false."""
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=_HOTEL_RESPONSE)
        await search_accommodations(
            location="Miami, FL",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
            vacation_rentals=False,
        )
        req = mock.calls.last.request
        assert "vacation_rentals=false" in str(req.url)


@pytest.mark.asyncio
async def test_search_accommodations_price_filters():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=_HOTEL_RESPONSE)
        await search_accommodations(
            location="Miami, FL",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
            min_price=100,
            max_price=300,
        )
        req = mock.calls.last.request
        assert "min_price=100" in str(req.url)
        assert "max_price=300" in str(req.url)


@pytest.mark.asyncio
async def test_search_accommodations_children_ages():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=_HOTEL_RESPONSE)
        await search_accommodations(
            location="Miami, FL",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
            children=2,
            children_ages=[3, 7],
        )
        req = mock.calls.last.request
        assert "children_ages=3%2C7" in str(req.url)
        assert "children=2" in str(req.url)


@pytest.mark.asyncio
async def test_search_accommodations_missing_api_key(monkeypatch):
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="SERPAPI_API_KEY is not set"):
        await search_accommodations(
            location="Miami, FL",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
        )


@pytest.mark.asyncio
async def test_search_accommodations_serpapi_error():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=_ERROR_RESPONSE)
        with pytest.raises(ValueError, match="Invalid API key"):
            await search_accommodations(
                location="Miami, FL",
                check_in_date="2026-08-01",
                check_out_date="2026-08-05",
            )


@pytest.mark.asyncio
async def test_search_accommodations_empty_results():
    empty_response = {
        "search_metadata": {"status": "Success"},
        "properties": [],
    }
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=empty_response)
        result = await search_accommodations(
            location="Nowhere",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
        )

    assert result["total_results"] == 0
    assert result["results"] == []


# ---------------------------------------------------------------------------
# Filter tests — B-04
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sort_by_lowest_price_maps_to_code_3():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=_HOTEL_RESPONSE)
        await search_accommodations(
            location="Miami, FL",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
            sort_by="lowest_price",
            vacation_rentals=False,
        )
        req = mock.calls.last.request
        assert req.url.params["sort_by"] == "3"


@pytest.mark.asyncio
async def test_sort_by_highest_rating_maps_to_code_8():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=_HOTEL_RESPONSE)
        await search_accommodations(
            location="Miami, FL",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
            sort_by="highest_rating",
            vacation_rentals=False,
        )
        req = mock.calls.last.request
        assert req.url.params["sort_by"] == "8"


@pytest.mark.asyncio
async def test_sort_by_most_reviewed_maps_to_code_13():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=_HOTEL_RESPONSE)
        await search_accommodations(
            location="Miami, FL",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
            sort_by="most_reviewed",
            vacation_rentals=False,
        )
        req = mock.calls.last.request
        assert req.url.params["sort_by"] == "13"


@pytest.mark.asyncio
async def test_sort_by_unknown_raises_value_error():
    with pytest.raises(ValueError, match="sort_by must be one of"):
        await search_accommodations(
            location="Miami, FL",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
            sort_by="closest_to_beach",
            vacation_rentals=False,
        )


@pytest.mark.asyncio
async def test_sort_by_absent_from_request():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=_HOTEL_RESPONSE)
        await search_accommodations(
            location="Miami, FL",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
            vacation_rentals=False,
        )
        req = mock.calls.last.request
        assert "sort_by" not in req.url.params


@pytest.mark.asyncio
async def test_min_rating_3_5_maps_to_code_7():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=_HOTEL_RESPONSE)
        await search_accommodations(
            location="Miami, FL",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
            min_rating=3.5,
            vacation_rentals=False,
        )
        req = mock.calls.last.request
        assert req.url.params["rating"] == "7"


@pytest.mark.asyncio
async def test_min_rating_4_0_maps_to_code_8():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=_HOTEL_RESPONSE)
        await search_accommodations(
            location="Miami, FL",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
            min_rating=4.0,
            vacation_rentals=False,
        )
        req = mock.calls.last.request
        assert req.url.params["rating"] == "8"


@pytest.mark.asyncio
async def test_min_rating_4_5_maps_to_code_9():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=_HOTEL_RESPONSE)
        await search_accommodations(
            location="Miami, FL",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
            min_rating=4.5,
            vacation_rentals=False,
        )
        req = mock.calls.last.request
        assert req.url.params["rating"] == "9"


@pytest.mark.asyncio
async def test_min_rating_4_2_raises_value_error():
    with pytest.raises(ValueError, match="min_rating must be one of"):
        await search_accommodations(
            location="Miami, FL",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
            min_rating=4.2,
            vacation_rentals=False,
        )


@pytest.mark.asyncio
async def test_min_rating_absent_from_request():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=_HOTEL_RESPONSE)
        await search_accommodations(
            location="Miami, FL",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
            vacation_rentals=False,
        )
        req = mock.calls.last.request
        assert "rating" not in req.url.params


@pytest.mark.asyncio
async def test_hotel_class_passthrough():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=_HOTEL_RESPONSE)
        await search_accommodations(
            location="Miami, FL",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
            hotel_class="3,4,5",
            vacation_rentals=False,
        )
        req = mock.calls.last.request
        # SerpAPI expects comma-separated (same as input after validation).
        assert req.url.params["hotel_class"] == "3,4,5"


@pytest.mark.asyncio
async def test_hotel_class_with_vacation_rentals_raises_value_error():
    with pytest.raises(
        ValueError, match="hotel_class cannot be used with vacation_rentals=True"
    ):
        await search_accommodations(
            location="Miami, FL",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
            hotel_class="3,4,5",
            vacation_rentals=True,
        )


@pytest.mark.asyncio
async def test_hotel_class_bad_value_raises_value_error():
    with pytest.raises(ValueError, match="hotel_class values must be integers"):
        await search_accommodations(
            location="Miami, FL",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
            hotel_class="3,seven,5",
            vacation_rentals=False,
        )


@pytest.mark.asyncio
async def test_hotel_class_out_of_range_raises_value_error():
    with pytest.raises(ValueError, match=r"hotel_class values must be 2\S+5"):
        await search_accommodations(
            location="Miami, FL",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
            hotel_class="1,3",
            vacation_rentals=False,
        )


@pytest.mark.asyncio
async def test_hotel_class_absent_from_request():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=_HOTEL_RESPONSE)
        await search_accommodations(
            location="Miami, FL",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
            vacation_rentals=False,
        )
        req = mock.calls.last.request
        assert "hotel_class" not in req.url.params


@pytest.mark.asyncio
async def test_free_cancellation_when_true():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=_HOTEL_RESPONSE)
        await search_accommodations(
            location="Miami, FL",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
            free_cancellation=True,
            vacation_rentals=False,
        )
        req = mock.calls.last.request
        assert req.url.params["free_cancellation"] == "true"


@pytest.mark.asyncio
async def test_free_cancellation_absent_when_false():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=_HOTEL_RESPONSE)
        await search_accommodations(
            location="Miami, FL",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
            free_cancellation=False,
            vacation_rentals=False,
        )
        req = mock.calls.last.request
        assert "free_cancellation" not in req.url.params


@pytest.mark.asyncio
async def test_all_new_filters_pass_through_together():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=_HOTEL_RESPONSE)
        await search_accommodations(
            location="Miami, FL",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
            sort_by="lowest_price",
            min_rating=4.0,
            hotel_class="4,5",
            free_cancellation=True,
            vacation_rentals=False,
        )
        req = mock.calls.last.request
        assert req.url.params["sort_by"] == "3"
        assert req.url.params["rating"] == "8"
        assert req.url.params["hotel_class"] == "4,5"
        assert req.url.params["free_cancellation"] == "true"


@pytest.mark.asyncio
async def test_default_absent_all_filters():
    """Without any new filter args, none of the new keys appear."""
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=_HOTEL_RESPONSE)
        await search_accommodations(
            location="Miami, FL",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
        )
        req = mock.calls.last.request
        for key in ("sort_by", "rating", "hotel_class", "free_cancellation"):
            assert key not in req.url.params, (
                f"{key} should be absent when not requested"
            )

# ---------------------------------------------------------------------------
# Sample response for get_accommodation_details
# ---------------------------------------------------------------------------

_FIXTURES = Path(__file__).parent / "fixtures"

# Real google_hotels property-details body, captured 2026-07-31.
# See tests/fixtures/README.md — the previous hand-written fixture wrapped
# everything in a "property" key and invented `rating_breakdown`, neither of
# which the engine returns, so the parser was a no-op against production.
_FULL_DETAIL_RESPONSE = json.loads(
    (_FIXTURES / "google_hotels_property_details.json").read_text()
)

# Degenerate case, deliberately synthetic: only the handful of fields a
# minimal property carries.
_SPARSE_DETAIL_RESPONSE = {
    "name": "Budget Inn",
    "overall_rating": 3.0,
    "reviews": 12,
    "link": "https://www.google.com/hotels/budgetinn",
}

_NO_PROPERTY_RESPONSE: dict = {}

# ---------------------------------------------------------------------------
# get_accommodation_details tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_accommodation_details_full_response():
    """Parses the real captured response, not a hand-invented shape."""
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=_FULL_DETAIL_RESPONSE)
        result = await get_accommodation_details(
            property_token="abc123",
            location="Miami Beach hotels",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
        )

    assert result["name"] == _FULL_DETAIL_RESPONSE["name"]
    assert result["overall_rating"] == _FULL_DETAIL_RESPONSE["overall_rating"]
    assert result["reviews"] == _FULL_DETAIL_RESPONSE["reviews"]

    # `ratings` is a star distribution: [{stars, count}, ...]
    assert result["rating_distribution"][0]["stars"] == 5
    assert result["rating_distribution"][0]["count"] > 0

    # `reviews_breakdown` is per-category sentiment, not a 0-5 score.
    first = result["reviews_breakdown"][0]
    assert first["category"]
    assert "positive" in first and "negative" in first
    assert "score" not in first

    assert all(isinstance(a, str) for a in result["amenities"])
    assert all(isinstance(i, str) and i for i in result["images"])
    assert len(result["images"]) <= 10

    # rate_per_night arrives as {lowest, extracted_lowest} and is flattened.
    price = result["prices"][0]
    assert isinstance(price["rate_per_night"], str)
    assert isinstance(price["rate_per_night_value"], (int, float))


@pytest.mark.asyncio
async def test_get_accommodation_details_sparse_response():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=_SPARSE_DETAIL_RESPONSE)
        result = await get_accommodation_details(
            property_token="xyz",
            location="Anywhere",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
        )

    assert result["name"] == "Budget Inn"
    assert "description" not in result
    assert "reviews_breakdown" not in result
    assert "rating_distribution" not in result
    assert "amenities" not in result
    assert result["link"] == "https://www.google.com/hotels/budgetinn"


@pytest.mark.asyncio
async def test_get_accommodation_details_requires_q_on_the_wire():
    """The engine 400s without `q` even when a property_token is supplied.

    Verified against the live API on 2026-07-31, which is why `location` is a
    required parameter rather than an optional nicety.
    """
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=_SPARSE_DETAIL_RESPONSE)
        await get_accommodation_details(
            property_token="abc123",
            location="Miami Beach hotels",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
        )
        req = mock.calls.last.request
        assert req.url.params["q"] == "Miami Beach hotels"
        assert req.url.params["property_token"] == "abc123"
        assert req.url.params["engine"] == "google_hotels"
        assert req.url.params["check_in_date"] == "2026-08-01"
        assert req.url.params["check_out_date"] == "2026-08-05"


@pytest.mark.asyncio
async def test_get_accommodation_details_no_property_field():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=_NO_PROPERTY_RESPONSE)
        result = await get_accommodation_details(
            property_token="abc123",
            location="Anywhere",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
        )
    assert result == {}


@pytest.mark.asyncio
async def test_get_accommodation_details_locale_params():
    with respx.mock as mock:
        mock.get(SERPAPI_BASE).respond(json=_SPARSE_DETAIL_RESPONSE)
        await get_accommodation_details(
            property_token="abc123",
            location="Anywhere",
            check_in_date="2026-08-01",
            check_out_date="2026-08-05",
            country="US",
            language="en",
        )
        req = mock.calls.last.request
        assert req.url.params["gl"] == "US"
        assert req.url.params["hl"] == "en"


@pytest.mark.asyncio
async def test_free_cancellation_rejected_for_vacation_rentals():
    """vacation_rentals defaults to True and the engine ignores the filter there."""
    with respx.mock as mock:
        route = mock.get(SERPAPI_BASE).respond(json={"properties": []})
        with pytest.raises(ValueError, match="free_cancellation"):
            await search_accommodations(
                location="Miami",
                check_in_date="2026-08-01",
                check_out_date="2026-08-05",
                free_cancellation=True,
            )
        assert route.call_count == 0
