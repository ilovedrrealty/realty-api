from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator


RESERVED_SLUGS = frozenset({
    "can-americans-buy", "closing-costs-dr", "punta-cana-airbnb-roi", "can-canadians-buy",
    "property-taxes-ipi", "las-terrenas", "best-places-to-retire", "dr-real-estate-safe",
    "living-punta-cana", "confotur-explained", "dr-vs-miami", "living-cabarete-sosua",
})
Kind = Literal["article", "guide", "neighborhood"]
Locale = Literal["en", "es"]
Slug = Annotated[str, StringConstraints(strict=True, max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
Title = Annotated[str, StringConstraints(strict=True, max_length=160)]
Summary = Annotated[str, StringConstraints(strict=True, max_length=350)]
Body = Annotated[str, StringConstraints(strict=True, max_length=12000)]
ReviewNote = Annotated[str, StringConstraints(strict=True, max_length=2000)]
Version = Annotated[int, Field(strict=True, ge=1, le=2147483647)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def clean_text(cls, value):
        if isinstance(value, str) and any(
            (ord(char) < 32 and char not in "\n\r\t") or 0xD800 <= ord(char) <= 0xDFFF for char in value
        ):
            raise ValueError("Use valid plain text")
        return value


class EditorialSource(Input):
    label: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=120)]
    url: Annotated[str, StringConstraints(strict=True, max_length=800)]

    @field_validator("url")
    @classmethod
    def https_source(cls, value):
        try:
            parts = urlsplit(value)
            _ = parts.port
            if (parts.scheme != "https" or not parts.hostname or parts.username is not None
                    or parts.password is not None or "\\" in value or any(c.isspace() or ord(c) < 33 for c in value)):
                raise ValueError()
        except ValueError:
            raise ValueError("Provide a complete HTTPS source link") from None
        return value


class ArticleLocale(Input):
    title: Title
    summary: Summary
    body: Body


class EditorialContent(Input):
    en: ArticleLocale
    es: ArticleLocale


class EditorialDraft(Input):
    content: EditorialContent
    sources: list[EditorialSource] = Field(max_length=8)
    review_note: ReviewNote


class EditorialCreate(EditorialDraft):
    slug: Slug
    kind: Kind


class EditorialWrite(EditorialDraft):
    expected_version: Version


class EditorialChange(Input):
    expected_version: Version


class EditorialRevisionChange(EditorialChange):
    revision: Version


class DocumentState(BaseModel):
    id: UUID
    slug: Slug
    kind: Kind
    version: Version
    draft_revision: Version
    reviewed_revision: Version | None
    published_revision: Version | None


class EditorialSummary(DocumentState):
    title: Title


class EditorialList(BaseModel):
    items: list[EditorialSummary]


class EditorialDetail(DocumentState, EditorialDraft):
    pass


class RevisionSummary(BaseModel):
    revision: Version
    saved_at: AwareDatetime


class RevisionList(BaseModel):
    items: list[RevisionSummary]


class RevisionPreview(EditorialDraft):
    revision: Version


class PublicEditorialSummary(BaseModel):
    slug: Slug
    kind: Kind
    title: Title
    summary: Summary
    published_at: AwareDatetime


class PublicEditorialDetail(PublicEditorialSummary):
    body: Body
    sources: list[EditorialSource] = Field(max_length=8)


class PublicEditorialList(BaseModel):
    items: list[PublicEditorialSummary]
    total: int
