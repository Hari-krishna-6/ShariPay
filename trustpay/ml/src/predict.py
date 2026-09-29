"""Risk prediction interface; score comes from the trained model, factors from rules."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import joblib

from .config import DEFAULT_RISK_FACTOR_THRESHOLDS, DEFAULT_RISK_THRESHOLDS, RiskFactorThresholds, RiskThresholds
from .features import FEATURE_NAMES, prepare_features
from .train import DEFAULT_MODEL_PATH


def risk_level_for_score(score: float, thresholds: RiskThresholds = DEFAULT_RISK_THRESHOLDS) -> str:
    if score < thresholds.low_upper:
        return "LOW"
    if score < thresholds.medium_upper:
        return "MEDIUM"
    return "HIGH"


def risk_factors_for_features(
    features: dict[str, Any],
    thresholds: RiskFactorThresholds = DEFAULT_RISK_FACTOR_THRESHOLDS,
) -> list[str]:
    factors: list[str] = []
    if features["is_new_beneficiary"] == 1:
        factors.append("New beneficiary")
    if features["is_new_device"] == 1:
        factors.append("New device")
    if features["is_location_change"] == 1:
        factors.append("Location change")
    if features["amount_deviation_ratio"] >= thresholds.unusual_amount_deviation_ratio:
        factors.append("Unusually high transaction amount compared with account history")
    if features["amount"] >= thresholds.high_amount_inr:
        factors.append("High transaction amount")
    if features["transaction_frequency_24h"] >= thresholds.high_transaction_frequency_24h:
        factors.append("High transaction frequency in 24 hours")
    if features["transaction_velocity_1h"] >= thresholds.high_transaction_velocity_1h:
        factors.append("High transaction velocity in one hour")
    if features["failed_attempts_24h"] >= thresholds.multiple_failed_attempts_24h:
        factors.append("Multiple failed attempts in 24 hours")
    if features["previous_fraud_count"] > 0:
        factors.append("Previous fraud history")
    if thresholds.unusual_hour_start <= features["hour"] <= thresholds.unusual_hour_end:
        factors.append("Unusual transaction time")
    return factors


def predict_risk(
    transaction: dict[str, Any],
    model_path: str | Path = DEFAULT_MODEL_PATH,
    thresholds: RiskThresholds = DEFAULT_RISK_THRESHOLDS,
) -> dict[str, Any]:
    prepared = prepare_features(transaction)
    model_bundle = joblib.load(model_path)
    if model_bundle.get("feature_names") != list(FEATURE_NAMES):
        raise ValueError("model feature schema does not match current feature preparation")
    model = model_bundle.get("model")
    probabilities = model.predict_proba(prepared)
    classes = list(model.classes_)
    if 1 not in classes:
        raise ValueError("trained model does not contain the positive fraud class")
    score = float(probabilities[0, classes.index(1)])
    feature_row = prepared.iloc[0].to_dict()
    return {
        "risk_score": score,
        "risk_level": risk_level_for_score(score, thresholds),
        "risk_factors": risk_factors_for_features(feature_row),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--input", type=Path, required=True, help="JSON file containing one transaction feature object")
    args = parser.parse_args()
    transaction = json.loads(args.input.read_text(encoding="utf-8"))
    print(json.dumps(predict_risk(transaction, args.model), indent=2))


if __name__ == "__main__":
    main()