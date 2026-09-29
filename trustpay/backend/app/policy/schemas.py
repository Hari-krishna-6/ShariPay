from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

PolicyDecision = Literal["APPROVE", "HOLD", "VERIFY", "REJECT"]


class PolicyEvaluationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PolicyContext:
    risk_score: Decimal
    risk_level: str
    risk_factors: tuple[str, ...]
    amount: Decimal
    sender_balance: Decimal
    sender_user_id: str
    receiver_user_id: str
    sender_user_active: bool
    sender_account_active: bool
    receiver_user_active: bool
    receiver_account_active: bool
    beneficiary_active: bool
    payment_status: str
    failed_attempts_24h: int = 0


@dataclass(frozen=True, slots=True)
class PolicyResult:
    decision: PolicyDecision
    reason: str
    policy_version: str
    triggered_rules: tuple[str, ...]
