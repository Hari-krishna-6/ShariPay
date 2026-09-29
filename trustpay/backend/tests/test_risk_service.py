from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ml.src import train as train_module

from app.main import app
from app.risk import service as risk_service_module
from app.risk.service import RiskAssessmentError, RiskAssessmentService
from ml.src.features import FEATURE_NAMES


NORMAL_FEATURES = {
    "amount": 2500,
    "hour": 14,
    "day_of_week": 2,
    "is_new_beneficiary": 0,
    "is_new_device": 0,
    "is_location_change": 0,
    "account_age_days": 900,
    "transaction_frequency_24h": 2,
    "transaction_velocity_1h": 0,
    "average_transaction_amount": 2200,
    "amount_deviation_ratio": 0.1364,
    "failed_attempts_24h": 0,
    "previous_fraud_count": 0,
    "user_transaction_count": 120,
    "is_weekend": 0,
}

SUSPICIOUS_FEATURES = {
    "amount": 85000,
    "hour": 2,
    "day_of_week": 6,
    "is_new_beneficiary": 1,
    "is_new_device": 1,
    "is_location_change": 1,
    "account_age_days": 20,
    "transaction_frequency_24h": 15,
    "transaction_velocity_1h": 9,
    "average_transaction_amount": 1800,
    "amount_deviation_ratio": 46.2222,
    "failed_attempts_24h": 4,
    "previous_fraud_count": 1,
    "user_transaction_count": 25,
    "is_weekend": 1,
}


def test_model_loads_once_and_predicts_from_canonical_feature_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    risk_service_module.clear_model_cache()
    original_load = risk_service_module.joblib.load
    load_calls = 0

    def counted_load(path: str | Path):
        nonlocal load_calls
        load_calls += 1
        return original_load(path)

    monkeypatch.setattr(risk_service_module.joblib, "load", counted_load)
    service = RiskAssessmentService()
    normal = service.assess(NORMAL_FEATURES)
    suspicious = service.assess(SUSPICIOUS_FEATURES)

    assert tuple(NORMAL_FEATURES) == FEATURE_NAMES
    assert load_calls == 1
    assert 0 <= normal.risk_score <= 1
    assert normal.risk_level == "LOW"
    assert suspicious.risk_level == "HIGH"
    assert suspicious.risk_score > normal.risk_score
    assert "New device" in suspicious.risk_factors
    assert "High transaction velocity in one hour" in suspicious.risk_factors
    risk_service_module.clear_model_cache()


def test_missing_model_artifact_has_a_clear_error(tmp_path: Path) -> None:
    risk_service_module.clear_model_cache()
    missing = tmp_path / "not-trained.joblib"
    with pytest.raises(RiskAssessmentError, match="Risk model artifact is missing"):
        RiskAssessmentService(missing).assess(NORMAL_FEATURES)
    risk_service_module.clear_model_cache()


def test_application_startup_does_not_train_model(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden_training(*args, **kwargs):
        raise AssertionError("the backend must not train the risk model")

    monkeypatch.setattr(train_module, "train_model", forbidden_training)
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200


def test_feature_contract_rejects_missing_or_noncanonical_inputs() -> None:
    service = RiskAssessmentService()
    with pytest.raises(RiskAssessmentError, match="inference failed"):
        service.assess({"amount": 2500})
    with pytest.raises(RiskAssessmentError, match="inference failed"):
        service.assess({**NORMAL_FEATURES, "hour": 25})
