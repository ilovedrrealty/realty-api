"""Strict private service inputs; clients never select an actor or custodian."""
import re
from datetime import date
from typing import Literal
from uuid import UUID
from pydantic import Field, StrictBool, field_validator, model_validator
from schemas.property_network import Input
from utils.professional_options import CATEGORY_IDS, PROPERTY_CLASS_IDS


class Submission(Input):
    submission_id: UUID


class PreferenceWrite(Submission):
    expected_version: int = Field(ge=0, strict=True)
    service_opt_in: StrictBool
    member_alias: str = Field(max_length=120)
    category_ids: list[str] = Field(max_length=12)
    property_class_ids: list[str] = Field(max_length=10)

    @model_validator(mode="after")
    def known_sets(self):
        for values, known in ((self.category_ids, CATEGORY_IDS), (self.property_class_ids, PROPERTY_CLASS_IDS)):
            if values != sorted(set(values)) or set(values) - known:
                raise ValueError("Choose unique sorted catalog IDs")
        if self.service_opt_in and (not self.member_alias or not self.category_ids or not self.property_class_ids):
            raise ValueError("Offering services needs an alias and service contexts")
        return self


class RequestCreate(Submission):
    property_id: UUID | None = None
    listing_id: UUID | None = None
    category_id: str
    property_class: str
    title: str = Field(min_length=1, max_length=180)
    brief: str = Field(min_length=1, max_length=4000)
    area_label: str = Field(default="", max_length=120)
    requester_alias: str = Field(min_length=1, max_length=120)
    desired_start_on: date | None = None
    recipient_user_codes: list[int] = Field(min_length=1, max_length=5)
    shared_brief_acknowledged: StrictBool

    @field_validator("recipient_user_codes", mode="before")
    @classmethod
    def strict_codes(cls, value):
        if type(value) is not list or any(type(code) is not int for code in value):
            raise ValueError("Use existing integer member codes")
        return value

    @model_validator(mode="after")
    def valid_request(self):
        if (self.property_id is None) == (self.listing_id is None):
            raise ValueError("Choose exactly one property source")
        if self.category_id not in CATEGORY_IDS or self.property_class not in PROPERTY_CLASS_IDS:
            raise ValueError("Choose a known service context")
        if any(type(v) is not int or v <= 0 for v in self.recipient_user_codes) or self.recipient_user_codes != sorted(set(self.recipient_user_codes)):
            raise ValueError("Choose unique sorted member codes")
        if self.shared_brief_acknowledged is not True:
            raise ValueError("Preview and acknowledge shared fields")
        return self


class RecipientAction(Submission):
    expected_recipient_version: int = Field(ge=1, strict=True)


class ManagedRecipientAction(RecipientAction):
    expected_request_version: int = Field(ge=1, strict=True)


class ProposalTerms(Input):
    amount: str
    currency: Literal["USD", "DOP"]
    pricing_basis: Literal["fixed", "estimate"]
    scope_text: str = Field(min_length=1, max_length=4000)
    exclusions: str = Field(default="", max_length=2000)
    estimated_days: int | None = Field(default=None, ge=1, le=365, strict=True)
    valid_until: date

    @field_validator("amount", mode="before")
    @classmethod
    def exact_amount(cls, value):
        if type(value) is not str or not re.fullmatch(r"(?:0|[1-9][0-9]{0,9})(?:\.[0-9]{1,2})?", value) or not any(c in "123456789" for c in value):
            raise ValueError("Use a positive bounded decimal string")
        return value


class QuoteWrite(RecipientAction):
    current_proposal_id: UUID | None = None
    expected_proposal_version: int | None = Field(default=None, ge=1, strict=True)
    terms: ProposalTerms

    @model_validator(mode="after")
    def current_pair(self):
        if (self.current_proposal_id is None) != (self.expected_proposal_version is None):
            raise ValueError("Supply both current proposal and version")
        return self


class CounterWrite(QuoteWrite):
    expected_request_version: int = Field(ge=1, strict=True)

    @model_validator(mode="after")
    def existing_proposal(self):
        if self.current_proposal_id is None:
            raise ValueError("Choose an existing proposal")
        return self


class ProposalAction(RecipientAction):
    expected_proposal_version: int = Field(ge=1, strict=True)


class RequestAction(Submission):
    expected_request_version: int = Field(ge=1, strict=True)


class AwardWrite(ManagedRecipientAction):
    recipient_id: UUID
    proposal_id: UUID
    expected_proposal_version: int = Field(ge=1, strict=True)
    project_title: str = Field(min_length=1, max_length=180)
    project_summary: str = Field(default="", max_length=4000)
    project_scope_acknowledged: StrictBool

    @field_validator("project_scope_acknowledged")
    @classmethod
    def approved_scope(cls, value):
        if value is not True:
            raise ValueError("Preview and approve the shared project scope")
        return value
