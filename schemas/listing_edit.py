from pydantic import BaseModel, StrictBool
from typing import Optional, Any
from datetime import datetime
import uuid


class ListingEditResponse(BaseModel):
    id: uuid.UUID
    listing_id: uuid.UUID
    listing_title: str
    listing_location: str
    listing_thumbnail: Optional[str]
    submitted_by_name: Optional[str]
    submitted_by_email: Optional[str]
    submitted_at: datetime
    status: str
    current_data: dict[str, Any]
    proposed_data: dict[str, Any]
    rejection_reason: Optional[str]

    model_config = {"from_attributes": True}


class ListingEditRejectBody(BaseModel):
    reason: str


class ListingEditApproveBody(BaseModel):
    room_photo_tour_reviewed: StrictBool
