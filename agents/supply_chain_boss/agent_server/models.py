"""Validated inputs and outputs used at the Boss Agent action boundary."""

from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class PenaltyEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    penalty_applicable: bool | None
    vendor_id: str | None = None
    vendor_name: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    contract_basis: str | None = None
    amount_quote: str | None = None
    citations: list[Citation] = Field(default_factory=list)

    @field_validator("amount", mode="before")
    @classmethod
    def parse_amount(cls, value: Any) -> Decimal | None:
        if value is None:
            return None
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError) as error:
            raise ValueError("Penalty amount must be a decimal number.") from error
        if not amount.is_finite() or amount <= 0:
            raise ValueError("Penalty amount must be a positive finite number.")
        return amount


class VendorPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vendor_id: str
    vendor_name: str | None = None
    support_tier: str

    @property
    def approval_required(self) -> bool:
        return self.support_tier.casefold() == "platinum"


class PenaltyProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vendor_id: str
    vendor_name: str
    amount: Decimal
    currency: str
    contract_basis: str
    citations: list[Citation]
    support_tier: str
    approval_required: bool


class JiraIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    url: str


class SpecialistResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str
    custom_outputs: dict[str, Any] = Field(default_factory=dict)
