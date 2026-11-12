"""Exact bilingual room metadata for photos already in a public listing gallery."""
import re
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictStr, model_serializer, model_validator


GROUP_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
CONTROLS = re.compile(r"[\x00-\x1f\x7f]")
TRIM = " \t\n\v\f\r\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"


def bounded_text(value, limit):
    if (not isinstance(value, str) or not value or value.strip(TRIM) != value or CONTROLS.search(value)
            or len(value.encode("utf-16-le")) // 2 > limit):
        raise ValueError("Use short plain text without surrounding spaces")
    return value


def group_id(value):
    value = bounded_text(value, 64)
    if GROUP_ID.fullmatch(value) is None:
        raise ValueError("Use a lower-case room identifier")
    return value


def room_label(value):
    return bounded_text(value, 80)


def photo_caption(value):
    return bounded_text(value, 240)


class RoomPhoto(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: StrictStr
    caption_en: Annotated[str, BeforeValidator(photo_caption)] | None = None
    caption_es: Annotated[str, BeforeValidator(photo_caption)] | None = None

    @model_validator(mode="after")
    def bilingual_captions(self):
        if self.model_fields_set & {"caption_en", "caption_es"} and (self.caption_en is None or self.caption_es is None):
            raise ValueError("Supply both photo captions or omit them")
        return self

    @model_serializer(mode="wrap")
    def omit_absent_captions(self, serialize):
        value = serialize(self)
        if self.caption_en is None:
            value.pop("caption_en", None)
            value.pop("caption_es", None)
        return value
        return value


class RoomGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, BeforeValidator(group_id)]
    label_en: Annotated[str, BeforeValidator(room_label)]
    label_es: Annotated[str, BeforeValidator(room_label)]
    photos: list[RoomPhoto] = Field(min_length=1, max_length=100)


class RoomPhotoTour(BaseModel):
    model_config = ConfigDict(extra="forbid")

    groups: list[RoomGroup] = Field(min_length=1, max_length=24)

    @model_validator(mode="after")
    def unique_groups_and_photos(self):
        groups = [group.id for group in self.groups]
        urls = [photo.url for group in self.groups for photo in group.photos]
        if len(set(groups)) != len(groups) or len(urls) > 100 or len(set(urls)) != len(urls):
            raise ValueError("Use unique room identifiers and up to 100 unique photos")
        return self
