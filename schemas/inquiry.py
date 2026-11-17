from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime
from uuid import UUID


class InquiryCreate(BaseModel):
    listing_id: Optional[UUID] = None
    name: str = Field(max_length=200)
    email: EmailStr
    phone: Optional[str] = Field(default=None, max_length=50)
    message: str = Field(max_length=5000)


class InquiryResponse(BaseModel):
    id: str
    listing_id: Optional[str] = None
    listing_title: Optional[str] = None
    name: str
    email: str
    phone: Optional[str] = None
    message: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}
