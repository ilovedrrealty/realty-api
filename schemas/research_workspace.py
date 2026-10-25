from datetime import datetime
from typing import Literal
from uuid import UUID
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, field_validator

ChecklistItem = Literal["reference", "certificate", "review", "survey", "permits", "balances", "rules", "followup"]


class ResearchLink(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1, max_length=800)

    @field_validator("url")
    @classmethod
    def safe_link(cls, value):
        try:
            parsed = urlsplit(value)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or any(ord(c) < 33 for c in value):
                raise ValueError()
            # Reject invalid bracketed hosts and ports as well as script URLs.
            _ = parsed.port
        except ValueError:
            raise ValueError("Use a complete HTTPS document or resource link without embedded credentials")
        return value


class ResearchData(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=160)
    reference: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=3000)
    checks: dict[ChecklistItem, bool] = Field(default_factory=dict, max_length=8)
    item_notes: dict[ChecklistItem, str] = Field(default_factory=dict, max_length=8)
    links: list[ResearchLink] = Field(default_factory=list, max_length=10)

    @field_validator("item_notes")
    @classmethod
    def bounded_notes(cls, value):
        if any(len(note) > 1000 for note in value.values()):
            raise ValueError("Each checklist note must be at most 1000 characters")
        return value


class SaveResearchData(ResearchData):
    expected_version: int = Field(ge=1)


class ResearchSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    version: int
    updated_at: datetime


class ResearchDetail(ResearchSummary):
    data: ResearchData
