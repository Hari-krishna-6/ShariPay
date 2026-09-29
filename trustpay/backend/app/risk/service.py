from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import joblib
from sqlalchemy.orm import Session

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ml.src.config import DEFAULT_RISK_FACTOR_THRESHOLDS, RiskThresholds
from ml.src.features import FEATURE_NAMES, prepare_features
from ml.src.predict import risk_factors_for_features, risk_level_for_score

from app.config import settings
from app.models.account import Account
from app.models.audit_log import AuditLog
from app.models.beneficiary import Beneficiary
from app.models.payment import Payment

MODEL_VERSION = "trustpay-fraud-rf-v1"
NEUTRAL_AVERAGE_AMOUNT = Decimal("2200.00")


class RiskAssessmentError(RuntimeError):
    pass


@dataclass(frozen=True)
class RiskAssessmentResult:
    risk_score: Decimal
    risk_level: str
    risk_factors: list[str]
    model_version: str


@lru_cache(maxsize=4)
def _load_model_bundle(model_path: str) -> dict[str, Any]:
    path = Path(model_path)
    if not path.is_file():
        raise RiskAssessmentError(f"Risk model artifact is missing: {path}")
    try:
        bundle = joblib.load(path)
        if not isinstance(bundle, dict) or bundle.get("feature_names") != list(FEATURE_NAMES):
            raise ValueError("artifact feature schema does not match the backend feature contract")
        model = bundle.get("model")
        if model is None or not hasattr(model, "predict_proba"):
            raise ValueError("artifact does not contain a probability-capable model")
        classes = list(model.classes_)
        if 1 not in classes:
            raise ValueError("artifact model does not contain the positive fraud class")
        threshold_values = bundle.get("risk_thresholds", {})
        bundle["_thresholds"] = RiskThresholds(
            low_upper=float(threshold_values.get("low_upper", 0.30)),
            medium_upper=float(threshold_values.get("medium_upper", 0.70)),
        )
        return bundle
    except RiskAssessmentError:
        raise
    except Exception as exc:
        raise RiskAssessmentError(f"Unable to load risk model artifact: {path}") from exc


def build_payment_features(
    db: Session,
    payment: Payment,
    beneficiary: Beneficiary,
    sender_account: Account,
) -> dict[str, float | int]:
    """Build canonical features from payment/account history and explicit safe defaults."""
    now = payment.created_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    day_start = now - timedelta(hours=24)
    hour_start = now - timedelta(hours=1)

    history = (
        db.query(Payment.amount, Payment.created_at, Payment.beneficiary_id)
        .filter(Payment.sender_user_id == payment.sender_user_id, Payment.id != payment.id)
        .all()
    )
    prior_payments = [row for row in history if row.created_at is not None]
    recent_24h = [row for row in prior_payments if row.created_at >= day_start]
    recent_1h = [row for row in prior_payments if row.created_at >= hour_start]
    prior_beneficiary_payments = [row for row in prior_payments if row.beneficiary_id == beneficiary.id]
    if prior_payments:
        average_amount = sum((Decimal(row.amount) for row in prior_payments), Decimal("0")) / len(prior_payments)
    else:
        average_amount = NEUTRAL_AVERAGE_AMOUNT
    if average_amount <= 0:
        average_amount = NEUTRAL_AVERAGE_AMOUNT

    failed_attempts_24h = (
        db.query(AuditLog)
        .filter(
            AuditLog.user_id == payment.sender_user_id,
            AuditLog.event_type.in_(
                ("payment_validation", "payment_insufficient_balance", "payment_idempotency_conflict")
            ),
            AuditLog.event_status == "failed",
            AuditLog.created_at >= day_start,
        )
        .count()
    )
    account_age_days = max(0, (now - sender_account.created_at).days) if sender_account.created_at else 0
    amount = Decimal(payment.amount)
    amount_deviation_ratio = float(abs(amount - average_amount) / average_amount)

    features: dict[str, float | int] = {
        "amount": float(amount),
        "hour": now.hour,
        "day_of_week": now.weekday(),
        "is_new_beneficiary": int(not prior_beneficiary_payments),
        "is_new_device": 0,
        "is_location_change": 0,
        "account_age_days": account_age_days,
        "transaction_frequency_24h": len(recent_24h),
        "transaction_velocity_1h": len(recent_1h),
        "average_transaction_amount": float(average_amount),
        "amount_deviation_ratio": amount_deviation_ratio,
        "failed_attempts_24h": failed_attempts_24h,
        "previous_fraud_count": 0,
        "user_transaction_count": len(prior_payments),
        "is_weekend": int(now.weekday() >= 5),
    }
    if tuple(features) != FEATURE_NAMES:
        raise RiskAssessmentError("Backend feature construction does not match the trained model feature order")
    return features


class RiskAssessmentService:
    def __init__(self, model_path: str | Path | None = None) -> None:
        self.model_path = str(Path(model_path or settings.ML_MODEL_PATH).resolve())

    def assess(self, features: Mapping[str, Any]) -> RiskAssessmentResult:
        bundle = _load_model_bundle(self.model_path)
        try:
            prepared = prepare_features(features)
            model = bundle["model"]
            probabilities = model.predict_proba(prepared)
            positive_index = list(model.classes_).index(1)
            score = float(probabilities[0, positive_index])
            feature_row = prepared.iloc[0].to_dict()
            level = risk_level_for_score(score, bundle["_thresholds"])
            factors = risk_factors_for_features(feature_row, DEFAULT_RISK_FACTOR_THRESHOLDS)
            return RiskAssessmentResult(
                risk_score=Decimal(str(score)),
                risk_level=level,
                risk_factors=factors,
                model_version=MODEL_VERSION,
            )
        except Exception as exc:
            if isinstance(exc, RiskAssessmentError):
                raise
            raise RiskAssessmentError("Risk inference failed for the validated payment feature vector") from exc


def clear_model_cache() -> None:
    _load_model_bundle.cache_clear()