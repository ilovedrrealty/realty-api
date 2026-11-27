"""Minimal public HTTP projections; no provider/account/contact history fields."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class PublicHTTPModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AssistantSessionResponse(PublicHTTPModel):
    csrf_token: str = Field(pattern=r"^[a-f0-9]{64}$")
    session_expires_at: int
    model_enabled: bool
    contact_enabled: bool


class AssistantCitationResponse(PublicHTTPModel):
    listing_id: str = Field(pattern=r"^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$")
    url: str = Field(max_length=256)
    label: str = Field(max_length=500)


class AssistantTurnResponse(PublicHTTPModel):
    locale: Literal["en", "es"]
    answer: str = Field(min_length=1, max_length=4000)
    citations: list[AssistantCitationResponse] = Field(max_length=3)
    model_invoked: Literal[True]
    grounding_scope: Literal["cited_listing_facts_only"]
    availability_verified: Literal[False]


class AssistantContactReview(PublicHTTPModel):
    intent: Literal["buy", "sell", "rent", "property_inquiry"]
    name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(max_length=254)
    phone: str | None = Field(max_length=50)
    message: str | None = Field(max_length=2000)
    property_id: str | None
    contact_consent: Literal[True]
    locale: Literal["en", "es"]
    consent_version: Literal["contact_request_v1"]
    consent_text: str = Field(max_length=500)


class AssistantReviewResponse(PublicHTTPModel):
    review: AssistantContactReview
    confirmation_token: str = Field(max_length=256)
    expires_at: int


class AssistantConfirmationResponse(PublicHTTPModel):
    reference: str = Field(pattern=r"^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$")
    recorded: Literal[True]
    replayed: bool
    delivery_confirmed: Literal[False]
