"""Tests for the accommodation search tool (SerpAPI Google Hotels)."""

from __future__ import annotations

import os

import pytest
import respx

from cosmo_travel_mcp.tools.flights import SERPAPI_BASE
from cosmo_travel_mcp.tools.hotels import search_accommodations

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
