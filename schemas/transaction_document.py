"""Permissioned transaction document inputs and private projections."""

from datetime import datetime
from typing import Literal
from unicodedata import category
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from utils.transaction_document_storage import MAX_DOCUMENT_BYTES


MediaType = Literal["application/pdf", "image/jpeg", "image/png"]
DocumentStatus = Literal["reserved", "submitted", "approved", "rejected"]


class ReserveDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_id: UUID
    document_id: UUID | None = None
    title: str = Field(min_length=1, max_length=160)
    file_name: str = Field(min_length=1, max_length=140)
    content_type: MediaType
    byte_count: int = Field(ge=1, le=MAX_DOCUMENT_BYTES, strict=True)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$", min_length=64, max_length=64)

    @field_validator("title", "file_name")
    @classmethod
    def forbid_hidden_characters(cls, value: str) -> str:
        if any(category(character).startswith("C") for character in value):
            raise ValueError("Use text without control characters")
        return value

    @field_validator("file_name")
    @classmethod
    def use_file_basename(cls, value: str) -> str:
        if value in {".", ".."} or any(char in value for char in '/\\:<>"|?*'):
            raise ValueError("Use a file name without a path or reserved characters")
        return value


class DocumentReceipt(BaseModel):
    document_id: UUID
    version_id: UUID
    number: int
    status: DocumentStatus


class DocumentVersion(BaseModel):
    id: UUID
    number: int
    file_name: str
    uploaded_at: datetime
    reviewed_at: datetime | None
    content_type: MediaType
    byte_count: int
    status: Literal["submitted", "approved", "rejected"]
    can_download: bool
    can_review: bool
    share_with_buyer: bool | None
    professional_grant_id: UUID | None


class DocumentSummary(BaseModel):
    id: UUID
    title: str
    can_add_version: bool
    versions: list[DocumentVersion]


class DocumentList(BaseModel):
    items: list[DocumentSummary]
    can_upload: bool


class ReviewDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1, strict=True)
    status: Literal["approved", "rejected"]
    share_with_buyer: bool = Field(default=False, strict=True)
    professional_grant_id: UUID | None = None

    @model_validator(mode="after")
    def require_approval_to_share(self):
        if self.status == "rejected" and (self.share_with_buyer or self.professional_grant_id is not None):
            raise ValueError("Approve a version to share it")
        return self
