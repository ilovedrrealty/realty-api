"""Strict public content configuration, separate from platform/billing settings."""

import ipaddress
import json
import re
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import parse_qsl, unquote, urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
from utils.listing_search import normalized_location


MAX_SETTINGS_BODY_BYTES = 256 * 1024
PROTOTYPE_KEYS = {"__proto__", "prototype", "constructor"}
LOCALE_NAMESPACES = {
    "common", "landing", "home_showcase", "buying", "selling", "rentals", "team",
    "contact", "get_started", "lead_capture", "footer", "blog", "search",
}
IMAGE_KEYS = {
    "home_hero", "founders", "founders_secondary", "buying_hero", "selling_hero",
    "rentals_hero", "contact_hero", "resources_hero", "buying_story", "selling_story",
    "contact_story", "category_new", "category_rent", "category_villa", "category_apartment",
    "category_condo", "category_land", "category_commercial", "category_punta_cana", "category_sosua",
}
RouteKey = Literal["landing", "buying", "selling", "team", "contact", "get-started", "blog", "rentals", "search", "calculator"]
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$", min_length=1, max_length=64)]
LocationTerm = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


def _safe_key(value: str) -> str:
    if any(part.lower() in PROTOTYPE_KEYS for part in value.split(".")):
        raise ValueError("Prototype keys are not allowed")
    return value


def _text(value: str, *, max_length: int, empty: bool = False) -> str:
    if len(value) > max_length or (not empty and not value.strip()):
        raise ValueError("Text is empty or too long")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in value) or "\x7f" in value:
        raise ValueError("Control characters are not allowed")
    return value


def validate_public_https_url(value: str) -> str:
    if len(value) > 2048 or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("A public HTTPS URL without whitespace is required")
    if any(ord(char) < 32 or ord(char) == 127 for char in unquote(value)):
        raise ValueError("Control characters are not allowed in URLs")
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid HTTPS URL") from exc
    if parsed.scheme.lower() != "https" or not host or parsed.username is not None or parsed.password is not None:
        raise ValueError("A public HTTPS URL without embedded credentials is required")
    if "\\" in value or port not in (None, 443):
        raise ValueError("A standard public HTTPS URL is required")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".test", ".invalid")):
        raise ValueError("Local addresses are not allowed")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", host):
            raise ValueError("A public domain name is required")
    else:
        if not address.is_global:
            raise ValueError("Private addresses are not allowed")
    sensitive_keys = {
        "password", "passwd", "secret", "token", "access_token", "refresh_token",
        "api_key", "apikey", "key", "authorization", "auth", "signature", "client_secret",
        "session", "sessionid", "jwt", "code",
    }
    if any(key.lower() in sensitive_keys for key, _ in parse_qsl(parsed.query)):
        raise ValueError("Credential-bearing URLs are not allowed")
    return value


class StrictContentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BilingualLabel(StrictContentModel):
    en: str = Field(min_length=1, max_length=160)
    es: str = Field(min_length=1, max_length=160)

    @field_validator("en", "es")
    @classmethod
    def validate_label(cls, value):
        return _text(value, max_length=160)


class BilingualDescription(StrictContentModel):
    en: str = Field(max_length=4000)
    es: str = Field(max_length=4000)

    @field_validator("en", "es")
    @classmethod
    def validate_description(cls, value):
        return _text(value, max_length=4000, empty=True)


class TranslationOverrides(StrictContentModel):
    en: dict[str, dict[str, str]] = Field(default_factory=dict)
    es: dict[str, dict[str, str]] = Field(default_factory=dict)

    @field_validator("en", "es")
    @classmethod
    def validate_namespaces(cls, namespaces):
        if set(namespaces) - LOCALE_NAMESPACES:
            raise ValueError("Unknown public translation namespace")
        for namespace, messages in namespaces.items():
            _safe_key(namespace)
            if len(messages) > 600:
                raise ValueError("Too many translation overrides")
            for key, value in messages.items():
                _safe_key(key)
                if len(key) > 150 or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", key) or ".." in key:
                    raise ValueError("Invalid translation key")
                _text(value, max_length=6000, empty=True)
        return namespaces


class NavigationItem(StrictContentModel):
    route: RouteKey
    label: BilingualLabel
    enabled: bool = True


class GallerySettings(StrictContentModel):
    mode: Literal["auto", "featured"] = "auto"
    ids: list[UUID] = Field(default_factory=list, max_length=5)
    photos: dict[UUID, str] = Field(default_factory=dict, max_length=50)

    @field_validator("ids")
    @classmethod
    def unique_ids(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("Gallery properties must be unique")
        return values

    @field_validator("photos")
    @classmethod
    def validate_photos(cls, values):
        for value in values.values():
            validate_public_https_url(value)
        return values


class SearchArea(StrictContentModel):
    key: Slug
    label: BilingualLabel
    locations: list[LocationTerm] = Field(min_length=1, max_length=60)


class ResourceItem(StrictContentModel):
    id: Slug
    title: BilingualLabel
    description: BilingualDescription
    category: BilingualLabel
    url: str
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def validate_url(cls, value):
        return validate_public_https_url(value)


class RealtorTool(ResourceItem):
    access: Literal["public", "realtor"] = "realtor"
    mode: Literal["link"] = "link"


class TeamMember(StrictContentModel):
    id: Slug
    name: str = Field(min_length=1, max_length=120)
    role: BilingualLabel
    description: BilingualDescription
    photo: str
    section: Literal["leadership", "realtor"]
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, value):
        return _text(value, max_length=120)

    @field_validator("photo")
    @classmethod
    def validate_photo(cls, value):
        return validate_public_https_url(value)

    @field_validator("description")
    @classmethod
    def validate_biography(cls, value):
        if not value.en.strip() or not value.es.strip():
            raise ValueError("A public biography is required in both languages")
        return value


class WebsiteContentSettings(StrictContentModel):
    translations: TranslationOverrides = Field(default_factory=TranslationOverrides)
    navigation: list[NavigationItem] = Field(default_factory=list, max_length=12)
    gallery: GallerySettings = Field(default_factory=GallerySettings)
    images: dict[str, str] = Field(default_factory=dict)
    searchAreas: list[SearchArea] = Field(default_factory=list, max_length=12)
    resources: list[ResourceItem] = Field(default_factory=list, max_length=100)
    realtorTools: list[RealtorTool] = Field(default_factory=list, max_length=100)
    teamMembers: list[TeamMember] = Field(default_factory=list, max_length=100)

    @field_validator("images")
    @classmethod
    def validate_images(cls, images):
        if set(images) - IMAGE_KEYS:
            raise ValueError("Unknown website image key")
        for key, value in images.items():
            _safe_key(key)
            validate_public_https_url(value)
        return images

    @model_validator(mode="after")
    def validate_collections(self):
        for values, attr in (
            (self.navigation, "route"), (self.searchAreas, "key"),
            (self.resources, "id"), (self.realtorTools, "id"), (self.teamMembers, "id"),
        ):
            identifiers = [getattr(value, attr) for value in values]
            for identifier in identifiers:
                _safe_key(identifier)
            if len(identifiers) != len(set(identifiers)):
                raise ValueError("Content identifiers must be unique within each section")
        selected_locations = set()
        for area in self.searchAreas:
            for location in area.locations:
                normalized = normalized_location(location)
                if normalized in selected_locations:
                    raise ValueError("Each search location must belong to only one area")
                selected_locations.add(normalized)
        if len(json.dumps(self.model_dump(mode="json"), ensure_ascii=False).encode("utf-8")) > MAX_SETTINGS_BODY_BYTES:
            raise ValueError("Website settings exceed the size limit")
        return self


class WebsiteSettingsWrite(StrictContentModel):
    expected_version: int = Field(ge=0, le=2147483646)
    settings: WebsiteContentSettings


class WebsiteSettingsRestore(StrictContentModel):
    expected_version: int = Field(ge=0, le=2147483646)


class WebsiteSettingsPublic(StrictContentModel):
    version: int
    settings: WebsiteContentSettings


class WebsiteSettingsAdmin(WebsiteSettingsPublic):
    updated_at: datetime | None = None


class WebsiteSettingsHistoryItem(StrictContentModel):
    version: int
    created_at: datetime
    updated_by_name: str | None = None


class WebsiteSettingsHistory(StrictContentModel):
    items: list[WebsiteSettingsHistoryItem]


