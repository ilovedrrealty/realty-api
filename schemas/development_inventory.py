"""Versioned source-backed development inventory and its public projection."""

from datetime import date
from decimal import Decimal
import re
from typing import Annotated, Literal
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID

from pydantic import AwareDatetime, BeforeValidator, Field, StringConstraints, field_validator, model_validator

from schemas.editorial import Input


def _calendar_date(value):
    if type(value) is date:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return date.fromisoformat(value)
    raise ValueError("Use a calendar date in YYYY-MM-DD format")


def _delivery_target(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}(?:-(?:Q[1-4]|[0-9]{2}(?:-[0-9]{2})?))?", value):
        raise ValueError("Use a supplied year, quarter, month or exact delivery date")
    numeric = value[:4] if "-Q" in value else value
    date.fromisoformat({4: numeric + "-01-01", 7: numeric + "-01", 10: numeric}[len(numeric)])
    return value


DateValue = Annotated[date, BeforeValidator(_calendar_date)]
DeliveryTarget = Annotated[str, BeforeValidator(_delivery_target)]
Name = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, max_length=160)]
Code = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=60)]
Floor = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=60)]
Paragraph = Annotated[str, StringConstraints(strict=True, max_length=1600)]
SourceRef = Annotated[str, StringConstraints(strict=True, max_length=1200)]
PrivateNote = Annotated[str, StringConstraints(strict=True, max_length=2000)]
FloorPlanUrl = Annotated[str, StringConstraints(strict=True, max_length=800)]
Price = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2, allow_inf_nan=False)]
Area = Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=2, allow_inf_nan=False)]
Percent = Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2, allow_inf_nan=False)]
Version = Annotated[int, Field(strict=True, ge=1, le=2147483647)]
Currency = Literal["USD", "DOP"]
UnitStatus = Literal["available", "reserved", "sold", "unconfirmed"]


class Unit(Input):
    code: Code
    floor: Floor | None = None
    area_m2: Area | None = None
    price: Price | None = None
    currency: Currency | None = None
    status: UnitStatus = "unconfirmed"
    floor_plan_url: FloorPlanUrl | None = None

    @model_validator(mode="after")
    def quoted_price_has_currency(self):
        if (self.price is None) != (self.currency is None):
            raise ValueError("Supply unit price and currency together")
        return self

    @field_validator("floor_plan_url")
    @classmethod
    def shareable_floorplan(cls, value):
        if value is None:
            return None
        try:
            parts = urlsplit(value)
            _ = parts.port
            keys = [re.sub(r"[^a-z0-9]", "", key.lower()) for key, _ in parse_qsl(parts.query, max_num_fields=16)]
            private_param = ("token", "secret", "signature", "credential", "password", "auth", "session",
                             "apikey", "accesskey", "resourcekey", "privatekey")
            if (parts.scheme != "https" or not parts.hostname or parts.username is not None
                    or parts.password is not None or parts.fragment or "\\" in value
                    or any(char.isspace() for char in value)
                    or any(key in {"key", "sig", "sas", "jwt"} or key.startswith(("xamz", "xgoog"))
                           or any(marker in key for marker in private_param) for key in keys)):
                raise ValueError()
        except ValueError:
            raise ValueError("Provide a public HTTPS floor plan link without credentials") from None
        return value


class DraftUnit(Unit):
    floor_plan_approved: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def approval_requires_floorplan(self):
        if self.floor_plan_approved and self.floor_plan_url is None:
            raise ValueError("Add a floor plan link before approving it")
        return self


class PhaseInfo(Input):
    code: Code
    name_en: Name = ""
    name_es: Name = ""
    target_delivery: DeliveryTarget | None = None


class Phase(PhaseInfo):
    units: list[Unit] = Field(max_length=500)


class DraftPhase(PhaseInfo):
    units: list[DraftUnit] = Field(max_length=500)


class PaymentStage(Input):
    label_en: Name = ""
    label_es: Name = ""
    due_en: Name = ""
    due_es: Name = ""
    due_date: DateValue | None = None
    percent: Percent | None = None
    amount: Price | None = None
    currency: Currency | None = None

    @model_validator(mode="after")
    def charges_are_unambiguous(self):
        if ((self.amount is None) != (self.currency is None)
                or self.percent is not None and self.amount is not None):
            raise ValueError("Describe a stage, supply a percent, or supply an amount with its currency")
        return self


class ProjectUpdate(Input):
    date: DateValue
    title_en: Name = ""
    title_es: Name = ""
    text_en: Paragraph = ""
    text_es: Paragraph = ""


class ProjectInfo(Input):
    name_en: Name = ""
    name_es: Name = ""
    summary_en: Paragraph = ""
    summary_es: Paragraph = ""
    target_delivery: DeliveryTarget | None = None
    payment_stages: list[PaymentStage] = Field(max_length=24)
    updates: list[ProjectUpdate] = Field(max_length=36)


class Project(ProjectInfo):
    phases: list[Phase] = Field(max_length=20)


class DraftProject(ProjectInfo):
    phases: list[DraftPhase] = Field(max_length=20)

    @model_validator(mode="after")
    def inventory_keys_are_unique_and_bounded(self):
        phases = [phase.code.casefold() for phase in self.phases]
        if len(phases) != len(set(phases)) or sum(len(phase.units) for phase in self.phases) > 500:
            raise ValueError("Use unique phase codes and at most 500 units")
        for phase in self.phases:
            codes = [unit.code.casefold() for unit in phase.units]
            if len(codes) != len(set(codes)):
                raise ValueError("Use unique unit codes within each phase")
        return self


class Evidence(Input):
    source_reference: SourceRef = ""
    verified_on: DateValue | None = None
    availability_valid_through: DateValue | None = None
    private_note: PrivateNote = ""

    @model_validator(mode="after")
    def availability_follows_verification(self):
        if (self.verified_on is not None and self.availability_valid_through is not None
                and self.availability_valid_through < self.verified_on):
            raise ValueError("Availability cannot end before verification")
        return self


class Draft(Input):
    project: DraftProject
    evidence: Evidence


class DraftChange(Draft):
    expected_version: Annotated[int, Field(strict=True, ge=0, le=2147483647)]


class VersionChange(Input):
    expected_version: Version


class RevisionChange(VersionChange):
    revision: Version


class RevisionSnapshot(Draft):
    revision: Version
    saved_at: AwareDatetime


class PublishedInventory(Input):
    listing_id: UUID
    revision: Version
    published_at: AwareDatetime
    verified_on: DateValue
    availability_valid_through: DateValue
    availability_current: bool
    project: Project


class AdminInventory(Input):
    listing_id: UUID
    version: int
    draft_revision: Version | None = None
    reviewed_revision: Version | None = None
    published_revision: Version | None = None
    draft: Draft | None = None
    published: PublishedInventory | None = None
