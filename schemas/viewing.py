from datetime import datetime, timedelta
import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, StringConstraints,
                      field_validator, model_validator)


Mode = Literal["in_person", "virtual"]
Status = Literal["requested", "proposed", "confirmed", "declined", "cancelled", "unavailable"]
Action = Literal["propose", "confirm", "accept", "decline", "cancel"]
Title = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=100)]
Note = Annotated[str, StringConstraints(strict=True, max_length=1000)]
MeetingDetails = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=1000)]
MeetingUrl = Annotated[str, StringConstraints(strict=True, max_length=600)]
Version = Annotated[int, Field(strict=True, ge=1, le=2147483647)]


def dominican_time(value):
    if isinstance(value, str) and re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?-04:00", value
    ):
        return value
    if isinstance(value, datetime) and value.utcoffset() == timedelta(hours=-4):
        return value
    raise ValueError("Use a Dominican Republic time with an explicit -04:00 offset")


DominicanTime = Annotated[AwareDatetime, BeforeValidator(dominican_time)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def plain_text(cls, value):
        if isinstance(value, str) and any(
            (ord(char) < 32 and char not in "\r\n\t") or 0xD800 <= ord(char) <= 0xDFFF for char in value
        ):
            raise ValueError("Use valid plain text")
        return value


class ViewingRequest(Input):
    listing_id: UUID
    mode: Mode
    starts_at: DominicanTime
    note: Note


class ItineraryCreate(Input):
    request_id: UUID
    title: Title
    stops: list[ViewingRequest] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def distinct_listings(self):
        if len({stop.listing_id for stop in self.stops}) != len(self.stops):
            raise ValueError("Choose each property once")
        return self


class ViewingAction(Input):
    expected_version: Version
    action: Action
    starts_at: DominicanTime | None = None
    meeting_details: MeetingDetails | None = None
    meeting_url: MeetingUrl | None = None
    availability_checked: StrictBool | None = None

    @model_validator(mode="after")
    def specific_fields(self):
        if self.action in ("propose", "confirm"):
            if self.availability_checked is not True or self.action == "propose" and self.starts_at is None:
                raise ValueError("Verify availability and include the proposed time")
        elif any(value is not None for value in
                 (self.starts_at, self.meeting_details, self.meeting_url, self.availability_checked)):
            raise ValueError("Only scheduling actions accept meeting fields")
        return self


class ListingView(BaseModel):
    id: UUID
    title: str
    location: str


class ContactView(BaseModel):
    listing: ListingView
    agent_name: str
    scheduling_url: str | None


class ViewingView(ContactView):
    id: UUID
    version: Version
    status: Status
    mode: Mode
    starts_at: AwareDatetime
    timezone: Literal["America/Santo_Domingo"]
    attendee_name: str
    note: str
    meeting_details: str | None
    meeting_url: str | None


class ItineraryView(BaseModel):
    id: UUID
    title: str
    items: list[ViewingView]


class ItineraryPage(BaseModel):
    items: list[ItineraryView]


class AssignedPage(BaseModel):
    items: list[ViewingView]
