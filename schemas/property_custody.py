"""The offering and accepting accounts must explicitly acknowledge custody."""
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool, field_validator, model_validator

from schemas.property_network import Input


class PropertyCustodyOffer(Input):
    submission_id: UUID
    property_id: UUID
    expected_property_version: int = Field(ge=1, strict=True)
    recipient_user_code: int = Field(ge=1, le=2147483647, strict=True)
    label: str = Field(min_length=1, max_length=120)
    transfer_acknowledged: StrictBool

    @field_validator("transfer_acknowledged")
    @classmethod
    def require_acknowledgement(cls, value):
        if value is not True:
            raise ValueError("Confirm the exact private record fields being transferred")
        return value


class PropertyCustodyAction(Input):
    submission_id: UUID
    expected_version: int = Field(ge=1, le=2, strict=True)
    operation: Literal["accept", "decline", "cancel"]
    transfer_acknowledged: StrictBool | None = None

    @model_validator(mode="after")
    def require_acceptance_only(self):
        if self.operation == "accept":
            if self.transfer_acknowledged is not True:
                raise ValueError("Explicitly accept custody of the private property")
        elif "transfer_acknowledged" in self.model_fields_set:
            raise ValueError("Only acceptance acknowledges custody")
        return self
