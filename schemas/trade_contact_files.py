"""Bounded, Admin-only spreadsheet analysis and explicit column mapping."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from schemas.trade_contacts import TradeContactEntry, TradeContactImportResult

CELL_LIMIT = 4000
IMPORT_FIELDS = frozenset(TradeContactEntry.model_fields) - {"source_details"}
Cell = Annotated[str, StringConstraints(max_length=CELL_LIMIT)]


class FileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContactFileRow(FileInput):
    row_number: int = Field(ge=1, le=1048576)
    values: list[Cell] = Field(max_length=40)


class ContactFileTable(FileInput):
    columns: list[Annotated[str, StringConstraints(max_length=240)]] = Field(min_length=1, max_length=40)
    rows: list[ContactFileRow] = Field(min_length=1, max_length=250)
    file_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    sheets: list[Annotated[str, StringConstraints(max_length=80)]] = Field(default_factory=list, max_length=100)
    sheet: str | None = Field(default=None, max_length=80)
    suggested_mapping: dict[str, int] = Field(default_factory=dict, max_length=25)


class ContactFileMapping(FileInput):
    table: ContactFileTable
    mapping: dict[str, Annotated[int, Field(strict=True, ge=0, le=39)]] = Field(max_length=25)
    defaults: dict[str, Cell] = Field(default_factory=dict, max_length=25)

    @field_validator("mapping", "defaults")
    @classmethod
    def known_fields(cls, value):
        if set(value) - IMPORT_FIELDS:
            raise ValueError("Choose known contact fields")
        return value


class ContactFileIssue(FileInput):
    row: int
    field: str
    code: Literal["required", "invalid"]


class ContactMappingPreview(FileInput):
    entries: list[TradeContactEntry] = Field(default_factory=list, max_length=250)
    issues: list[ContactFileIssue] = Field(default_factory=list, max_length=50)
    issue_count: int = Field(default=0, ge=0)
    result: TradeContactImportResult | None = None


class ContactFilterOptions(FileInput):
    categories: list[str]
    provinces: list[str]
    localities: list[str]
