from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.config import RiskThresholds
from src.features import FEATURE_NAMES, prepare_features
from src.generate_data import generate_dataset
from src.predict import predict_risk, risk_factors_for_features, risk_level_for_score
from src.train import train_model


def normal_transaction() -> dict[str, float | int]:
    return {
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


def suspicious_transaction() -> dict[str, float | int]:
    return {
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


class RiskEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.model_path = Path(cls.temp_dir.name) / "model.joblib"
        cls.dataset = generate_dataset(rows=6000, seed=42)
        cls.metrics = train_model(cls.dataset, cls.model_path)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_dataset_reproducible_and_schema(self) -> None:
        first = generate_dataset(100, seed=123)
        second = generate_dataset(100, seed=123)
        self.assertTrue(first.equals(second))
        self.assertEqual(list(first.columns), [*FEATURE_NAMES, "is_fraud"])
        self.assertEqual(set(first["is_fraud"].unique()), {0, 1})

    def test_feature_validation_and_order(self) -> None:
        prepared = prepare_features(normal_transaction())
        self.assertEqual(tuple(prepared.columns), FEATURE_NAMES)
        with self.assertRaisesRegex(ValueError, "missing required features"):
            prepare_features({"amount": 100})
        with self.assertRaisesRegex(ValueError, "forbidden"):
            prepare_features({**normal_transaction(), "is_fraud": 0})
        with self.assertRaisesRegex(ValueError, "hour"):
            prepare_features({**normal_transaction(), "hour": 24})

    def test_risk_threshold_boundaries_and_configuration(self) -> None:
        self.assertEqual(risk_level_for_score(0.299999), "LOW")
        self.assertEqual(risk_level_for_score(0.30), "MEDIUM")
        self.assertEqual(risk_level_for_score(0.70), "HIGH")
        self.assertEqual(risk_level_for_score(0.25, RiskThresholds(0.2, 0.8)), "MEDIUM")
        with self.assertRaises(ValueError):
            RiskThresholds(0.8, 0.7)

    def test_rule_factors_are_separate_and_feature_based(self) -> None:
        factors = risk_factors_for_features(suspicious_transaction())
        self.assertIn("New beneficiary", factors)
        self.assertIn("New device", factors)
        self.assertIn("Location change", factors)
        self.assertIn("High transaction velocity in one hour", factors)
        self.assertIn("Multiple failed attempts in 24 hours", factors)
        self.assertIn("Previous fraud history", factors)

    def test_training_metrics_and_model_inference(self) -> None:
        self.assertTrue(self.model_path.exists())
        for name in ("accuracy", "precision", "recall", "f1", "roc_auc", "false_positive_rate", "confusion_matrix"):
            self.assertIn(name, self.metrics)
        self.assertGreater(self.metrics["dataset_class_counts"]["1"], 0)
        for transaction in (normal_transaction(), suspicious_transaction()):
            prediction = predict_risk(transaction, self.model_path)
            self.assertGreaterEqual(prediction["risk_score"], 0.0)
            self.assertLessEqual(prediction["risk_score"], 1.0)
            self.assertIn(prediction["risk_level"], {"LOW", "MEDIUM", "HIGH"})
            self.assertIsInstance(prediction["risk_factors"], list)

    def test_input_numeric_rejection(self) -> None:
        with self.assertRaisesRegex(ValueError, "finite numeric"):
            prepare_features({**normal_transaction(), "amount": float("nan")})
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            prepare_features({**normal_transaction(), "amount": 0})


if __name__ == "__main__":
    unittest.main()