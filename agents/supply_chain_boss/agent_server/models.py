"""Strict data contracts at the Boss Agent's external-action boundary.

Pydantic rejects unexpected fields, and monetary values use ``Decimal`` so
penalty amounts are not silently rounded through binary floating point.
"""

from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Citation(BaseModel):
    """A source and verbatim quote supplied as evidence for a proposal."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class PenaltyEvidence(BaseModel):
    """Penalty facts extracted from the Specialist answer, not inferred data."""

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
        """Accept decimal-like JSON values but require a finite positive amount."""
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
    """Authoritative vendor attributes returned by the governed SQL lookup."""

    model_config = ConfigDict(extra="forbid")

    vendor_id: str
    vendor_name: str | None = None
    support_tier: str
    approval_required: bool


class PenaltyProposal(BaseModel):
    """Complete, user-reviewable penalty details before any Jira action."""

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
    """Minimal stable reference to a Jira issue created by the Boss."""

    model_config = ConfigDict(extra="forbid")

    key: str
    url: str


class SpecialistResult(BaseModel):
    """Normalized answer and structured outputs returned by the Specialist App."""

    model_config = ConfigDict(extra="forbid")

    answer: str
    custom_outputs: dict[str, Any] = Field(default_factory=dict)
