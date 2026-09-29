from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class AccountResponse(BaseModel):
    id: UUID
    account_type: Literal["SIMULATED"] = "SIMULATED"
    currency: str
    balance: Decimal
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BeneficiaryCreateRequest(BaseModel):
    email: EmailStr
    display_name: str | None = Field(default=None, min_length=1, max_length=255)

    model_config = ConfigDict(extra="forbid")

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("display_name")
    @classmethod
    def clean_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("display_name cannot be blank")
        return cleaned


class BeneficiaryResponse(BaseModel):
    id: UUID
    beneficiary_reference: str
    display_name: str
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaymentRiskAssessmentResponse(BaseModel):
    risk_score: Decimal
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    risk_factors: list[str]
    model_version: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaymentPolicyResultResponse(BaseModel):
    decision: Literal["APPROVE", "HOLD", "VERIFY", "REJECT"]
    reason: str
    policy_version: str
    triggered_rules: list[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaymentCreateRequest(BaseModel):
    beneficiary_id: UUID
    amount: Decimal = Field(gt=Decimal("0"), max_digits=18, decimal_places=2)
    currency: str = Field(min_length=3, max_length=8)
    idempotency_key: str = Field(min_length=1, max_length=128)

    model_config = ConfigDict(extra="forbid")

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("idempotency_key")
    @classmethod
    def normalize_idempotency_key(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("idempotency_key cannot be blank")
        return cleaned


class PaymentResponse(BaseModel):
    transaction_id: str
    sender_user_id: UUID
    receiver_user_id: UUID
    amount: Decimal
    currency: str
    status: Literal["CREATED", "PENDING_RISK", "FAILED", "CANCELLED"]
    risk_assessment: PaymentRiskAssessmentResponse | None = None
    policy_result: PaymentPolicyResultResponse | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
