from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.policy.rules import (
    LOW_RISK_UPPER,
    MAX_FAILED_PAYMENT_ATTEMPTS_24H,
    MEDIUM_RISK_UPPER,
    POLICY_VERSION,
    RULE_BENEFICIARY_INACTIVE,
    RULE_EXCESSIVE_FAILED_ATTEMPTS,
    RULE_HIGH_RISK_HOLD,
    RULE_INSUFFICIENT_BALANCE,
    RULE_INVALID_AMOUNT,
    RULE_LOW_RISK_APPROVAL,
    RULE_MEDIUM_RISK_VERIFY,
    RULE_PAYMENT_NOT_PENDING,
    RULE_PRIOR_FRAUD_HOLD,
    RULE_RECEIVER_ACCOUNT_INACTIVE,
    RULE_RECEIVER_INACTIVE,
    RULE_REPEATED_FAILURES_HOLD,
    RULE_RISK_LEVEL_MISMATCH,
    RULE_SELF_PAYMENT,
    RULE_SENDER_ACCOUNT_INACTIVE,
    RULE_SENDER_INACTIVE,
)
from app.policy.schemas import PolicyContext, PolicyEvaluationError, PolicyResult


HARD_REJECTION_REASONS = {
    RULE_INVALID_AMOUNT: "Payment amount must be positive.",
    RULE_SENDER_INACTIVE: "Sender account owner is inactive.",
    RULE_SENDER_ACCOUNT_INACTIVE: "Sender account is inactive.",
    RULE_RECEIVER_INACTIVE: "Recipient account owner is inactive.",
    RULE_RECEIVER_ACCOUNT_INACTIVE: "Recipient account is inactive.",
    RULE_BENEFICIARY_INACTIVE: "Beneficiary is inactive.",
    RULE_SELF_PAYMENT: "Payments between the same user are not permitted.",
    RULE_INSUFFICIENT_BALANCE: "Simulated sender balance is insufficient.",
    RULE_EXCESSIVE_FAILED_ATTEMPTS: "Payment attempt limit was exceeded.",
    RULE_PAYMENT_NOT_PENDING: "Only payments pending risk evaluation can be evaluated.",
    RULE_RISK_LEVEL_MISMATCH: "Risk score and risk level are inconsistent.",
}


def _risk_level_for_score(score: Decimal) -> str:
    if score < LOW_RISK_UPPER:
        return "LOW"
    if score < MEDIUM_RISK_UPPER:
        return "MEDIUM"
    return "HIGH"


def evaluate_policy(context: PolicyContext) -> PolicyResult:
    """Evaluate trusted payment and ML context without I/O or side effects."""
    try:
        score = Decimal(context.risk_score)
        amount = Decimal(context.amount)
        balance = Decimal(context.sender_balance)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PolicyEvaluationError("Policy context contains invalid monetary or risk values") from exc
    if not score.is_finite() or not Decimal("0") <= score <= Decimal("1"):
        raise PolicyEvaluationError("Risk score must be finite and between zero and one")
    if not amount.is_finite() or not balance.is_finite():
        raise PolicyEvaluationError("Payment amount and sender balance must be finite")
    if not isinstance(context.failed_attempts_24h, int) or context.failed_attempts_24h < 0:
        raise PolicyEvaluationError("Failed-attempt count must be a non-negative integer")

    expected_level = _risk_level_for_score(score)
    hard_rules: list[str] = []
    if amount <= 0:
        hard_rules.append(RULE_INVALID_AMOUNT)
    if not context.sender_user_active:
        hard_rules.append(RULE_SENDER_INACTIVE)
    if not context.sender_account_active:
        hard_rules.append(RULE_SENDER_ACCOUNT_INACTIVE)
    if not context.receiver_user_active:
        hard_rules.append(RULE_RECEIVER_INACTIVE)
    if not context.receiver_account_active:
        hard_rules.append(RULE_RECEIVER_ACCOUNT_INACTIVE)
    if not context.beneficiary_active:
        hard_rules.append(RULE_BENEFICIARY_INACTIVE)
    if context.sender_user_id == context.receiver_user_id:
        hard_rules.append(RULE_SELF_PAYMENT)
    if amount > balance:
        hard_rules.append(RULE_INSUFFICIENT_BALANCE)
    if context.failed_attempts_24h >= MAX_FAILED_PAYMENT_ATTEMPTS_24H:
        hard_rules.append(RULE_EXCESSIVE_FAILED_ATTEMPTS)
    if context.payment_status != "PENDING_RISK":
        hard_rules.append(RULE_PAYMENT_NOT_PENDING)
    if context.risk_level != expected_level:
        hard_rules.append(RULE_RISK_LEVEL_MISMATCH)

    if hard_rules:
        first_rule = hard_rules[0]
        return PolicyResult(
            decision="REJECT",
            reason=HARD_REJECTION_REASONS[first_rule],
            policy_version=POLICY_VERSION,
            triggered_rules=tuple(hard_rules),
        )

    if "Previous fraud history" in context.risk_factors:
        return PolicyResult(
            decision="HOLD",
            reason="Previous fraud history requires a temporary hold.",
            policy_version=POLICY_VERSION,
            triggered_rules=(RULE_PRIOR_FRAUD_HOLD,),
        )
    if context.failed_attempts_24h >= 2:
        return PolicyResult(
            decision="HOLD",
            reason="Repeated failed payment attempts require a temporary hold.",
            policy_version=POLICY_VERSION,
            triggered_rules=(RULE_REPEATED_FAILURES_HOLD,),
        )
    if expected_level == "HIGH":
        return PolicyResult(
            decision="HOLD",
            reason="High-risk payment requires a temporary hold.",
            policy_version=POLICY_VERSION,
            triggered_rules=(RULE_HIGH_RISK_HOLD,),
        )
    if expected_level == "MEDIUM":
        return PolicyResult(
            decision="VERIFY",
            reason="Medium-risk payment requires user verification.",
            policy_version=POLICY_VERSION,
            triggered_rules=(RULE_MEDIUM_RISK_VERIFY,),
        )
    return PolicyResult(
        decision="APPROVE",
        reason="Low-risk payment meets the current application policy.",
        policy_version=POLICY_VERSION,
        triggered_rules=(RULE_LOW_RISK_APPROVAL,),
    )
