"""Aggregate-only Admin analytics contract. No personal records or money values."""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class AnalyticsWindow(BaseModel):
    days: Literal[7, 30, 90]
    start_at: datetime
    end_at: datetime
    timezone: Literal["UTC"] = "UTC"


class AnalyticsSnapshot(BaseModel):
    accounts: int = Field(ge=0)
    active_accounts: int = Field(ge=0)
    accounts_by_role: dict[str, int]
    listing_records: int = Field(ge=0)
    active_listings: int = Field(ge=0)
    listings_by_status: dict[str, int]
    active_listings_by_purpose: dict[str, int]
    active_listings_by_type: dict[str, int]


class AnalyticsInquiries(BaseModel):
    received: int = Field(ge=0)
    excluded_internal_tests: int = Field(ge=0)
    unknown_tracking: int = Field(ge=0)
    contact_recorded: int = Field(ge=0)
    crm_linked: int = Field(ge=0)
    crm_errors: int = Field(ge=0)
    by_type: dict[str, int]
    current_status: dict[str, int]


class AnalyticsPeriod(BaseModel):
    accounts_created: int = Field(ge=0)
    listings_created: int = Field(ge=0)
    inquiries: AnalyticsInquiries
    booking_records_created: int = Field(ge=0)
    booking_current_status: dict[str, int]


class AnalyticsDay(BaseModel):
    date: date
    accounts_created: int = Field(ge=0)
    inquiries_received: int = Field(ge=0)
    booking_records_created: int = Field(ge=0)


class AdminAnalyticsResponse(BaseModel):
    generated_at: datetime
    window: AnalyticsWindow
    snapshot: AnalyticsSnapshot
    period: AnalyticsPeriod
    daily: list[AnalyticsDay]
    financial_values_reported: Literal[False] = False
    definitions_version: Literal[1] = 1
