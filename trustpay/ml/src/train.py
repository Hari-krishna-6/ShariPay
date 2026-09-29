"""Train and evaluate the prototype fraud-risk Random Forest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import train_test_split

from .config import DEFAULT_RISK_THRESHOLDS
from .features import FEATURE_NAMES, prepare_features

DEFAULT_MODEL_PATH = Path("models/fraud_model.joblib")
RANDOM_SEED = 42


def train_model(
    dataset: pd.DataFrame,
    model_path: str | Path = DEFAULT_MODEL_PATH,
    test_size: float = 0.25,
    random_state: int = RANDOM_SEED,
) -> dict[str, Any]:
    if "is_fraud" not in dataset.columns:
        raise ValueError("dataset must contain target column is_fraud")
    target = pd.to_numeric(dataset["is_fraud"], errors="coerce")
    if target.isna().any() or not target.isin((0, 1)).all():
        raise ValueError("is_fraud must contain only binary 0/1 labels")
    features = prepare_features(dataset.drop(columns=["is_fraud"]))
    if target.nunique() != 2:
        raise ValueError("training dataset must contain both legitimate and fraud classes")

    x_train, x_test, y_train, y_test = train_test_split(
        features,
        target.astype(int),
        test_size=test_size,
        random_state=random_state,
        stratify=target.astype(int),
    )
    model = RandomForestClassifier(
        n_estimators=600,
        max_depth=10,
        min_samples_leaf=2,
        max_features="sqrt",
        class_weight="balanced",
        random_state=random_state,
        n_jobs=-1,
    )
    model.fit(x_train, y_train)
    probabilities = model.predict_proba(x_test)[:, list(model.classes_).index(1)]
    predicted = (probabilities >= 0.5).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_test, predicted, labels=[0, 1]).ravel()
    metrics = {
        "accuracy": float(accuracy_score(y_test, predicted)),
        "precision": float(precision_score(y_test, predicted, zero_division=0)),
        "recall": float(recall_score(y_test, predicted, zero_division=0)),
        "f1": float(f1_score(y_test, predicted, zero_division=0)),
        "roc_auc": float(roc_auc_score(y_test, probabilities)),
        "false_positive_rate": float(fp / (fp + tn)) if fp + tn else 0.0,
        "confusion_matrix": [[int(tn), int(fp)], [int(fn), int(tp)]],
        "test_rows": int(len(y_test)),
        "test_class_counts": {str(int(label)): int(count) for label, count in y_test.value_counts().sort_index().items()},
        "dataset_class_counts": {str(int(label)): int(count) for label, count in target.value_counts().sort_index().items()},
    }
    destination = Path(model_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model": model,
            "feature_names": list(FEATURE_NAMES),
            "risk_thresholds": {
                "low_upper": DEFAULT_RISK_THRESHOLDS.low_upper,
                "medium_upper": DEFAULT_RISK_THRESHOLDS.medium_upper,
            },
            "random_state": random_state,
            "metrics": metrics,
        },
        destination,
        compress=3,
    )
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("data/transactions.csv"))
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--test-size", type=float, default=0.25)
    args = parser.parse_args()
    dataset = pd.read_csv(args.dataset)
    metrics = train_model(dataset, args.model, args.test_size)
    print(json.dumps(metrics, indent=2, sort_keys=True))
    print(f"Model saved to {args.model}")
    print("Accuracy alone is insufficient for fraud detection: it can hide missed fraud in an imbalanced dataset.")


if __name__ == "__main__":
    main()