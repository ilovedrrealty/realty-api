"""Optional reviewed Google links shared by drafts and published snapshots."""
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from utils.professional_google import PROFILE_URL_LIMIT, EMBED_URL_LIMIT, google_profile_url, google_maps_embed_url


class ProfessionalGoogleFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    google_profile_url: str = Field(default="", max_length=PROFILE_URL_LIMIT)
    google_maps_embed_url: str = Field(default="", max_length=EMBED_URL_LIMIT)

    @field_validator("google_profile_url")
    @classmethod
    def profile_url(cls, value):
        return google_profile_url(value)

    @field_validator("google_maps_embed_url")
    @classmethod
    def embed_url(cls, value):
        return google_maps_embed_url(value)

    @model_validator(mode="after")
    def embed_needs_profile_link(self):
        if self.google_maps_embed_url and not self.google_profile_url:
            raise ValueError("Add the Google profile link before adding its optional map")
        return self
