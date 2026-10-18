"""Explicit business profile contracts, separate from private auth/user schemas."""
import re
import unicodedata
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StrictBool, field_validator, model_validator
from schemas.website_settings import validate_public_https_url
from utils.professional_options import CATEGORY_IDS, AREA_IDS, LANGUAGE_IDS

ReviewStatus = Literal["draft", "pending", "approved", "rejected"]
ListingStatus = Literal["hidden", "published", "suspended"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Descriptions(StrictModel):
    en: str = Field(default="", max_length=4000)
    es: str = Field(default="", max_length=4000)

    @field_validator("en", "es")
    @classmethod
    def clean_text(cls, value):
        if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
            raise ValueError("Control characters are not allowed")
        return value.strip()


class ProfessionalDraft(StrictModel):
    business_name: str = Field(default="", max_length=120)
    contact_name: str = Field(default="", max_length=100)
    category_ids: list[str] = Field(default_factory=list, max_length=8)
    custom_services: list[str] = Field(default_factory=list, max_length=8)
    service_area_ids: list[str] = Field(default_factory=list, max_length=20)
    descriptions: Descriptions = Field(default_factory=Descriptions)
    languages: list[str] = Field(default_factory=list, max_length=8)
    public_phone: str = Field(default="", max_length=40)
    public_email: str = Field(default="", max_length=254)
    public_whatsapp: str = Field(default="", max_length=40)
    website_url: str = Field(default="", max_length=2048)
    photo_url: str = Field(default="", max_length=2048)
    public_contact_consent: StrictBool = False

    @field_validator("business_name", "contact_name")
    @classmethod
    def clean_name(cls, value):
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Control characters are not allowed")
        return value.strip()

    @field_validator("category_ids", "service_area_ids", "languages")
    @classmethod
    def known_options(cls, values, info):
        allowed = {"category_ids": CATEGORY_IDS, "service_area_ids": AREA_IDS, "languages": LANGUAGE_IDS}[info.field_name]
        if set(values) - allowed or len(set(values)) != len(values):
            raise ValueError("Select unique known options")
        return values

    @field_validator("custom_services")
    @classmethod
    def clean_services(cls, values):
        cleaned, seen = [], set()
        for value in values:
            if any(ord(char) < 32 or ord(char) == 127 for char in value):
                raise ValueError("Control characters are not allowed")
            value = " ".join(value.split())
            if not 2 <= len(value) <= 80:
                raise ValueError("Each custom service must be 2 to 80 characters")
            normalized = "".join(char for char in unicodedata.normalize("NFKD", value).casefold() if not unicodedata.combining(char))
            if normalized in seen:
                raise ValueError("Custom services must be unique")
            seen.add(normalized)
            cleaned.append(value)
        return cleaned

    @model_validator(mode="after")
    def custom_services_need_other(self):
        if self.custom_services and "other_services" not in self.category_ids:
            raise ValueError("Select Other property services before adding custom services")
        return self

    @field_validator("public_phone", "public_whatsapp")
    @classmethod
    def public_number(cls, value):
        value = value.strip()
        if not value:
            return ""
        if not re.fullmatch(r"\+[0-9 ()\-.]+", value):
            raise ValueError("Use an international phone number starting with +")
        value = "+" + re.sub(r"\D", "", value)
        if not re.fullmatch(r"\+[1-9]\d{6,14}", value):
            raise ValueError("Use a valid international phone number")
        return value

    @field_validator("public_email")
    @classmethod
    def public_mail(cls, value):
        value = value.strip()
        if value:
            # Use Pydantic's declared email validator without accepting arbitrary strings.
            from pydantic import TypeAdapter
            return str(TypeAdapter(EmailStr).validate_python(value))
        return ""

    @field_validator("website_url", "photo_url")
    @classmethod
    def public_url(cls, value):
        return validate_public_https_url(value.strip()) if value.strip() else ""


class SaveProfessionalBody(StrictModel):
    expected_version: int = Field(ge=0)
    draft: ProfessionalDraft


class CreateManagedProfessionalBody(StrictModel):
    draft: ProfessionalDraft


class VersionBody(StrictModel):
    expected_version: int = Field(ge=0)


class ReviewBody(VersionBody):
    note: str = Field(default="", max_length=2000)


class PublicProfessional(StrictModel):
    id: UUID
    business_name: str
    contact_name: str
    category_ids: list[str]
    custom_services: list[str] = Field(default_factory=list)
    service_area_ids: list[str]
    descriptions: Descriptions
    languages: list[str]
    public_phone: str
    public_email: str
    public_whatsapp: str
    website_url: str
    photo_url: str
    approved_at: datetime


class OwnedProfessional(StrictModel):
    id: UUID
    version: int
    review_status: ReviewStatus
    listing_status: ListingStatus
    draft: ProfessionalDraft
    published_profile: PublicProfessional | None
    review_note: str | None
    submitted_at: datetime | None
    reviewed_at: datetime | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PublicProfessionalPage(StrictModel):
    items: list[PublicProfessional]
    total: int
    limit: int
    offset: int


class AdminProfessionalOwner(StrictModel):
    id: UUID
    display_name: str
    email: str
    role: str
    status: str
    email_verified: bool


class ProfessionalHistoryItem(StrictModel):
    version: int
    action: str
    note: str | None
    actor_id: UUID | None
    created_at: datetime


class AdminProfessionalDetail(OwnedProfessional):
    management_type: Literal["admin_managed", "account_owned"]
    owner: AdminProfessionalOwner | None
    history: list[ProfessionalHistoryItem]


class AdminProfessionalSummary(StrictModel):
    id: UUID
    version: int
    review_status: ReviewStatus
    listing_status: ListingStatus
    business_name: str
    contact_name: str
    category_ids: list[str]
    custom_services: list[str] = Field(default_factory=list)
    service_area_ids: list[str]
    management_type: Literal["admin_managed", "account_owned"]
    owner: AdminProfessionalOwner | None
    submitted_at: datetime | None
    reviewed_at: datetime | None
    published_at: datetime | None
    updated_at: datetime


class AdminProfessionalPage(StrictModel):
    items: list[AdminProfessionalSummary]
    total: int
    limit: int
    offset: int
