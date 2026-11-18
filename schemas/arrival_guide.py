from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


MAX_GUIDE_TEXT = 1500
MAX_GUIDE_VERSION = (1 << 53) - 1
GuideText = Annotated[str, StringConstraints(strict=True, max_length=MAX_GUIDE_TEXT)]
GuideVersion = Annotated[int, Field(strict=True, ge=0, le=MAX_GUIDE_VERSION)]


class GuideLocale(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directions: GuideText
    check_in: GuideText
    check_out: GuideText
    house_notes: GuideText

    @field_validator("directions", "check_in", "check_out", "house_notes")
    @classmethod
    def valid_plain_text(cls, value):
        if any((ord(char) < 32 and char not in "\r\n\t") or 0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError("Use valid plain text")
        return value


class ArrivalGuideContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    en: GuideLocale
    es: GuideLocale

    @model_validator(mode="after")
    def has_instructions(self):
        if not any(text.strip() for locale in (self.en, self.es) for text in locale.model_dump().values()):
            raise ValueError("Enter arrival instructions or clear the guide")
        return self


class ArrivalGuideWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: GuideVersion
    content: ArrivalGuideContent | None


class OwnerArrivalGuide(BaseModel):
    version: GuideVersion
    content: ArrivalGuideContent | None


class GuestArrivalGuide(BaseModel):
    content: ArrivalGuideContent
    available_until: AwareDatetime
