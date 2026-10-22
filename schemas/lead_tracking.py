"""Small attribution whitelist; never accept form answers as analytics fields."""
import re
from typing import Literal
from urllib.parse import unquote

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class LeadAttribution(BaseModel):
    model_config = ConfigDict(extra="ignore")
    gclid: str | None = None
    gbraid: str | None = None
    wbraid: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    utm_term: str | None = None
    utm_content: str | None = None

    @field_validator("*", mode="before")
    @classmethod
    def safe_parameter(cls, value, info):
        if not isinstance(value, str):
            return None
        value = re.sub(r"[\x00-\x1f\x7f]", "", value).strip()[:200]
        decoded = unquote(value)
        # Drop obvious personal/contact data without rejecting the inquiry.
        if "@" in decoded or re.search(r"(?:\+\d[\d ()-]{8,}\d)", decoded):
            return None
        if info.field_name in {"gclid", "gbraid", "wbraid"} and not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value):
            return None
        return value or None


class LeadTracking(BaseModel):
    model_config = ConfigDict(extra="ignore")
    form: Literal["get_started", "contact", "spanish_landing", "lead_popup", "property_inquiry"]
    page_path: str = Field(default="/other/", max_length=200)
    locale: Literal["en", "es"] = "en"
    intent: Literal["buy", "sell", "rent", "general"] = "general"
    contact_consent: bool
    measurement_consent: Literal["granted", "denied", "unset"] = "unset"
    internal_test: bool = False
    attribution: LeadAttribution | None = None

    @field_validator("page_path", mode="before")
    @classmethod
    def known_page_only(cls, value):
        if isinstance(value, str):
            path = value.split("?", 1)[0].split("#", 1)[0]
            if re.fullmatch(r"/(?:get-started|contact|es|buying|selling|rentals|search|professionals|listing/[a-fA-F0-9-]{36})/?", path):
                return path
            if path == "/":
                return path
        return "/other/"

    @model_validator(mode="after")
    def require_contact_permission_and_honor_measurement(self):
        if not self.contact_consent:
            raise ValueError("Please agree to be contacted about this request")
        if self.measurement_consent != "granted":
            self.attribution = None
        return self
