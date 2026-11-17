"""Validated public-business records for the private Admin directory."""

from datetime import date
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator


Name = Annotated[str, StringConstraints(min_length=1, max_length=240)]
Label = Annotated[str, StringConstraints(min_length=1, max_length=160)]
Url = Annotated[str, StringConstraints(min_length=1, max_length=1400)]
Verification = Literal["owner_source", "public_source", "official_registry"]


class TradeContactEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: Name
    category: Label
    subtype: Label | None = None
    province: Label | None = None
    locality: Label | None = None
    contact_name: Name | None = None
    contact_title: Label | None = None
    phone: Label | None = None
    email: EmailStr | None = None
    website: Url | None = None
    address: Annotated[str, StringConstraints(max_length=500)] | None = None
    postal_code: Annotated[str, StringConstraints(max_length=24)] | None = None
    service_area: Annotated[str, StringConstraints(max_length=320)] | None = None
    external_id: Label | None = None
    source_authority: Label
    source_url: Url
    verified_on: date
    verification_status: Verification
    source_details: Annotated[dict[Label, Annotated[str, StringConstraints(max_length=1000)]],
                              Field(max_length=20)] | None = None

    @field_validator("source_url", "website")
    @classmethod
    def public_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            parsed = urlsplit(value)
            valid = parsed.scheme in {"http", "https"} and parsed.hostname and "." in parsed.hostname
            if valid and not parsed.username and not parsed.password and not any(char.isspace() for char in value):
                return value
        except ValueError:
            pass
        raise ValueError("Enter a valid public website URL")

    @field_validator("verified_on")
    @classmethod
    def no_future_observations(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("Verification date cannot be in the future")
        return value


class TradeContactRow(TradeContactEntry):
    id: UUID
    source_count: int


class TradeContactList(BaseModel):
    items: list[TradeContactRow]
    total: int


class TradeContactImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dry_run: bool
    entries: list[TradeContactEntry] = Field(min_length=1, max_length=250)


class TradeContactImportResult(BaseModel):
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
