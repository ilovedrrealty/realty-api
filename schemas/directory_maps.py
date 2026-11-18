"""Read-only directory map projections, separate from stored contact/profile JSON."""

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

CountryCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
LocationStatus = Literal["all", "mapped", "unmapped"]


class MapModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DirectoryMapArea(MapModel):
    area_key: str
    label: str
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    precision: Literal["area_reference"] = "area_reference"
    purpose: Literal["record_area", "service_area"]
    reference_country_code: CountryCode | None = None
    source_kind: Literal["existing_area_catalog"] = "existing_area_catalog"
    source_reference: str
    area_kind: Literal["provincial_capital", "popular_locality"]
    record_count: int = Field(ge=1)


class DirectoryMapCatalog(MapModel):
    id: str
    reference_country_code: CountryCode
    source_reference: str
    purpose: Literal["approximate_area_references"] = "approximate_area_references"


class DirectoryMapSummary(MapModel):
    areas: list[DirectoryMapArea]
    total: int = Field(ge=0)
    mapped_total: int = Field(ge=0)
    unmapped_total: int = Field(ge=0)
    complete: Literal[True] = True
    catalog: DirectoryMapCatalog
