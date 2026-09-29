from __future__ import annotations

import builtins
from decimal import Decimal

import pytest

from app.policy.engine import evaluate_policy
from app.policy.rules import (
    POLICY_VERSION,
    RULE_EXCESSIVE_FAILED_ATTEMPTS,
    RULE_HIGH_RISK_HOLD,
    RULE_LOW_RISK_APPROVAL,
    RULE_MEDIUM_RISK_VERIFY,
    RULE_PRIOR_FRAUD_HOLD,
    RULE_SENDER_ACCOUNT_INACTIVE,
)
from app.policy.schemas import PolicyContext, PolicyEvaluationError


def context(score: str = "0.10", level: str = "LOW", **overrides) -> PolicyContext:
    values = {
        "risk_score": Decimal(score),
        "risk_level": level,
        "risk_factors": (),
        "amount": Decimal("100.00"),
        "sender_balance": Decimal("1000.00"),
        "sender_user_id": "sender-user",
        "receiver_user_id": "receiver-user",
        "sender_user_active": True,
        "sender_account_active": True,
        "receiver_user_active": True,
        "receiver_account_active": True,
        "beneficiary_active": True,
        "payment_status": "PENDING_RISK",
        "failed_attempts_24h": 0,
    }
    values.update(overrides)
    return PolicyContext(**values)


def test_low_medium_high_baseline_decisions() -> None:
    assert evaluate_policy(context("0.10", "LOW")).decision == "APPROVE"
    assert evaluate_policy(context("0.40", "MEDIUM")).decision == "VERIFY"
    high = evaluate_policy(context("0.80", "HIGH"))
    assert high.decision == "HOLD"
    assert high.triggered_rules == (RULE_HIGH_RISK_HOLD,)


@pytest.mark.parametrize(
    ("score", "level", "decision", "rule"),
    [
        ("0.29", "LOW", "APPROVE", RULE_LOW_RISK_APPROVAL),
        ("0.30", "MEDIUM", "VERIFY", RULE_MEDIUM_RISK_VERIFY),
        ("0.69", "MEDIUM", "VERIFY", RULE_MEDIUM_RISK_VERIFY),
        ("0.70", "HIGH", "HOLD", RULE_HIGH_RISK_HOLD),
    ],
)
def test_exact_ml_score_boundaries(score: str, level: str, decision: str, rule: str) -> None:
    result = evaluate_policy(context(score, level))
    assert result.decision == decision
    assert result.triggered_rules == (rule,)


def test_hard_security_or_business_violation_rejects_before_risk_band() -> None:
    result = evaluate_policy(context("0.05", "LOW", sender_account_active=False))
    assert result.decision == "REJECT"
    assert result.triggered_rules == (RULE_SENDER_ACCOUNT_INACTIVE,)

    excessive_attempts = evaluate_policy(context("0.10", "LOW", failed_attempts_24h=5))
    assert excessive_attempts.decision == "REJECT"
    assert RULE_EXCESSIVE_FAILED_ATTEMPTS in excessive_attempts.triggered_rules


def test_high_priority_security_signals_hold_medium_risk() -> None:
    prior_fraud = evaluate_policy(context("0.40", "MEDIUM", risk_factors=("Previous fraud history",)))
    assert prior_fraud.decision == "HOLD"
    assert prior_fraud.triggered_rules == (RULE_PRIOR_FRAUD_HOLD,)

    repeated_failures = evaluate_policy(context("0.40", "MEDIUM", failed_attempts_24h=2))
    assert repeated_failures.decision == "HOLD"


def test_result_has_version_reason_and_deterministic_triggered_rules() -> None:
    input_context = context("0.40", "MEDIUM")
    first = evaluate_policy(input_context)
    second = evaluate_policy(input_context)
    assert first == second
    assert first.policy_version == POLICY_VERSION
    assert first.reason
    assert first.triggered_rules == (RULE_MEDIUM_RISK_VERIFY,)


def test_policy_engine_does_not_modify_balance_or_depend_on_ml_or_drunix(monkeypatch: pytest.MonkeyPatch) -> None:
    input_context = context("0.10", "LOW")
    starting_balance = input_context.sender_balance
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "drunix" or name.startswith("drunix.") or name == "ml" or name.startswith("ml.") or name.startswith("app.risk"):
            raise AssertionError(f"policy engine attempted a prohibited dependency import: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    result = evaluate_policy(input_context)
    assert result.decision == "APPROVE"
    assert input_context.sender_balance == starting_balance


def test_invalid_risk_context_fails_closed() -> None:
    with pytest.raises(PolicyEvaluationError, match="finite and between"):
        evaluate_policy(context("1.5", "HIGH"))
    mismatch = evaluate_policy(context("0.70", "MEDIUM"))
    assert mismatch.decision == "REJECT"
    assert "RISK_ASSESSMENT_LEVEL_MISMATCH" in mismatch.triggered_rules
