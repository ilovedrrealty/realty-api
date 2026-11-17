"""Bounded operational inputs. These records never execute payments or sign leases."""
from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, EmailStr, field_validator, model_validator

Money = Decimal
Currency = Literal["USD", "DOP"]
Kind = Literal["lease", "maintenance", "inspection", "ledger", "task"]
STATUSES = {
    "lease": ("draft", "active", "ended", "terminated"),
    "maintenance": ("open", "awaiting_approval", "approved", "in_progress", "completed", "cancelled"),
    "inspection": ("scheduled", "in_progress", "completed", "cancelled"),
    "ledger": ("planned", "recorded", "void"),
    "task": ("open", "in_progress", "completed", "cancelled"),
}
CHECKS = {"entrance", "plumbing", "electrical", "cooling", "appliances", "safety", "meters", "condition"}


class StrictData(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    notes: str = Field(default="", max_length=4000)


class LeaseData(StrictData):
    tenant_name: str = Field(min_length=1, max_length=160)
    tenant_email: EmailStr | None = None
    tenant_phone: str = Field(default="", max_length=50)
    unit_reference: str = Field(default="", max_length=80)
    start_date: date
    end_date: date | None = None
    rent: Money = Field(gt=0, le=9999999999, max_digits=12, decimal_places=2)
    deposit: Money = Field(default=Decimal("0"), ge=0, le=9999999999, max_digits=12, decimal_places=2)
    currency: Currency = "USD"
    due_day: int = Field(default=1, ge=1, le=31)
    first_due_date: date | None = None
    first_rent: Money | None = Field(default=None, ge=0, le=9999999999, max_digits=12, decimal_places=2)
    document_url: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def dates(self):
        if self.end_date and self.end_date < self.start_date:
            raise ValueError("End date must follow start date")
        if self.first_due_date and (self.first_due_date < self.start_date or (self.end_date and self.first_due_date > self.end_date)):
            raise ValueError("First rent date must be within the lease")
        return self

    @field_validator("document_url")
    @classmethod
    def document(cls, value):
        return safe_https(value)


class MaintenanceData(StrictData):
    progress_notes: str = Field(default="", max_length=4000)
    category: Literal["plumbing", "electrical", "cooling", "pool", "generator", "water", "roof", "security", "other"] = "other"
    priority: Literal["routine", "urgent", "emergency"] = "routine"
    due_date: date | None = None
    vendor_name: str = Field(default="", max_length=160)
    vendor_phone: str = Field(default="", max_length=50)
    estimated_cost: Money = Field(default=Decimal("0"), ge=0, le=9999999999, max_digits=12, decimal_places=2)
    actual_cost: Money | None = Field(default=None, ge=0, le=9999999999, max_digits=12, decimal_places=2)
    currency: Currency = "USD"
    requires_approval: bool = True


class InspectionData(StrictData):
    inspection_type: Literal["move_in", "move_out", "routine", "turnover", "storm_preparation"] = "routine"
    due_date: date
    inspector: str = Field(default="", max_length=160)
    checks: dict[str, Literal["not_checked", "pass", "issue", "not_applicable"]] = Field(default_factory=dict, max_length=8)
    item_notes: dict[str, str] = Field(default_factory=dict, max_length=8)
    evidence_links: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("checks", "item_notes")
    @classmethod
    def known_checks(cls, value):
        if set(value) - CHECKS or any(len(note) > 1000 for note in value.values()):
            raise ValueError("Check the inspection items")
        return value

    @field_validator("evidence_links")
    @classmethod
    def links(cls, value):
        if any(len(url) > 1000 for url in value):
            raise ValueError("Evidence link too long")
        return [safe_https(url) for url in value]


class LedgerData(StrictData):
    direction: Literal["income", "expense"]
    category: Literal["rent", "deposit", "maintenance", "hoa", "utilities", "management", "insurance", "tax", "other"] = "other"
    amount: Money = Field(gt=0, le=9999999999, max_digits=12, decimal_places=2)
    currency: Currency = "USD"
    transaction_date: date
    reference: str = Field(default="", max_length=120)
    lease_id: UUID | None = None
    rent_period: date | None = None


class TaskData(StrictData):
    category: Literal["cleaning", "maintenance", "renewal", "administration"] = "administration"
    due_date: date
    assigned_to: str = Field(default="", max_length=160)
    recurrence: Literal["none", "weekly", "monthly", "quarterly", "yearly"] = "none"


DATA_TYPES = {"lease": LeaseData, "maintenance": MaintenanceData, "inspection": InspectionData, "ledger": LedgerData, "task": TaskData}


def safe_https(value):
    if not value:
        return value
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or any(ord(c) < 33 for c in value):
            raise ValueError()
        _ = parsed.port
    except ValueError:
        raise ValueError("Use a complete HTTPS reference without embedded credentials")
    return value


class ManagementWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    listing_id: UUID
    kind: Kind
    title: str = Field(min_length=1, max_length=180)
    status: str = Field(min_length=1, max_length=30)
    data: dict = Field(max_length=25)
    origin_record_id: UUID | None = None

    @model_validator(mode="after")
    def validate_payload(self):
        if self.status not in STATUSES[self.kind]:
            raise ValueError("Invalid operational status")
        parsed = DATA_TYPES[self.kind].model_validate(self.data)
        if self.kind == "inspection" and self.status == "completed":
            if set(parsed.checks) != CHECKS or "not_checked" in parsed.checks.values():
                raise ValueError("Finish each inspection item first")
            if any(not parsed.item_notes.get(k) for k, v in parsed.checks.items() if v == "issue"):
                raise ValueError("Describe each inspection issue")
        self.data = parsed.model_dump(mode="json")
        return self


class ManagementUpdate(ManagementWrite):
    expected_version: int = Field(ge=1)


class VersionAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)


class ApprovalAction(VersionAction):
    decision: Literal["approved", "declined"]
    limit: Money | None = Field(default=None, ge=0, le=9999999999, max_digits=12, decimal_places=2)
    comment: str = Field(default="", max_length=1000)
