from datetime import date
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_serializer, model_validator


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
NeighborhoodTopic = Literal["access", "utilities", "internet", "services", "transport", "lifestyle"]
NeighborhoodText = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=600)]
ListingLocation = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=80)]


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


class NeighborhoodFact(Input):
    topic: NeighborhoodTopic
    en: NeighborhoodText
    es: NeighborhoodText
    checked_on: date
    source_url: Annotated[str, StringConstraints(strict=True, max_length=800)]

    @field_validator("checked_on", mode="before")
    @classmethod
    def calendar_date(cls, value):
        if type(value) is date:
            return value
        if isinstance(value, str):
            try:
                parsed = date.fromisoformat(value)
                if parsed.isoformat() == value:
                    return parsed
            except ValueError:
                pass
        raise ValueError("Use a calendar date in YYYY-MM-DD format")

    @field_validator("source_url")
    @classmethod
    def https_source(cls, value):
        return EditorialSource.https_source(value)


class Neighborhood(Input):
    listing_location: ListingLocation | None
    facts: list[NeighborhoodFact] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def distinct_topics(self):
        if len({fact.topic for fact in self.facts}) != len(self.facts):
            raise ValueError("Use each neighborhood topic once")
        return self


class EditorialContent(Input):
    en: ArticleLocale
    es: ArticleLocale
    neighborhood: Neighborhood | None = None

    @model_serializer(mode="wrap")
    def omit_legacy_empty_neighborhood(self, serialize):
        data = serialize(self)
        if self.neighborhood is None:
            data.pop("neighborhood", None)
        return data


class EditorialDraft(Input):
    content: EditorialContent
    sources: list[EditorialSource] = Field(max_length=8)
    review_note: ReviewNote

    @model_validator(mode="after")
    def fact_sources_are_part_of_draft(self):
        details = self.content.neighborhood
        if details is not None:
            sources = {source.url for source in self.sources}
            if any(fact.source_url not in sources for fact in details.facts):
                raise ValueError("Add each neighborhood fact's HTTPS source to this draft")
        return self


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


class PublicNeighborhoodSummary(BaseModel):
    slug: Slug
    title: Title
    summary: Summary
    published_at: AwareDatetime
    listing_location: ListingLocation | None


class PublicNeighborhoodFact(BaseModel):
    topic: NeighborhoodTopic
    text: NeighborhoodText
    checked_on: date
    source: EditorialSource


class PublicNeighborhoodDetail(PublicNeighborhoodSummary):
    facts: list[PublicNeighborhoodFact] = Field(min_length=1, max_length=6)


class PublicNeighborhoodList(BaseModel):
    items: list[PublicNeighborhoodSummary]
    total: int
