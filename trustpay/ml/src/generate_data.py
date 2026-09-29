"""Generate reproducible, fictional transactions for prototype training."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from .features import FEATURE_NAMES

DEFAULT_ROWS = 20_000
DEFAULT_SEED = 42
SCENARIOS = (
    "normal",
    "account_takeover",
    "velocity_attack",
    "unusual_amount",
    "new_beneficiary",
    "device_location_anomaly",
    "failed_attempts_then_large_payment",
)
SCENARIO_PROBABILITIES = (0.80, 0.05, 0.05, 0.03, 0.03, 0.02, 0.02)


def generate_dataset(rows: int = DEFAULT_ROWS, seed: int = DEFAULT_SEED) -> pd.DataFrame:
    """Generate correlated-but-noisy synthetic fraud scenarios.

    Scenario labels shape feature distributions but are not exported as model
    features. Fraud labels are sampled from a probabilistic rule mixture, so no
    individual signal perfectly determines the target.
    """
    if rows < 1:
        raise ValueError("rows must be at least 1")
    rng = np.random.default_rng(seed)
    scenario = rng.choice(SCENARIOS, size=rows, p=SCENARIO_PROBABILITIES)

    account_age = np.clip(rng.gamma(shape=3.2, scale=240.0, size=rows), 0, 3650).astype(int)
    average_amount = np.clip(rng.lognormal(mean=np.log(2200), sigma=0.75, size=rows), 100, 25_000)
    amount = np.clip(rng.lognormal(mean=np.log(1800), sigma=0.85, size=rows), 100, 30_000)
    hour = rng.choice(24, size=rows, p=_hour_probabilities())
    day_of_week = rng.integers(0, 7, size=rows)
    new_beneficiary = rng.binomial(1, 0.07, size=rows)
    new_device = rng.binomial(1, 0.035, size=rows)
    location_change = rng.binomial(1, 0.025, size=rows)
    frequency_24h = rng.poisson(lam=2.2, size=rows)
    velocity_1h = rng.poisson(lam=0.35, size=rows)
    failed_attempts = rng.poisson(lam=0.12, size=rows)
    previous_fraud = rng.binomial(1, 0.012, size=rows)
    user_transactions = np.maximum(1, rng.poisson(lam=95, size=rows))

    _apply_scenario(
        rng,
        scenario,
        amount,
        average_amount,
        account_age,
        hour,
        new_beneficiary,
        new_device,
        location_change,
        frequency_24h,
        velocity_1h,
        failed_attempts,
        previous_fraud,
    )

    average_amount = np.maximum(average_amount, 100.0)
    deviation = np.abs(amount - average_amount) / average_amount
    is_weekend = (day_of_week >= 5).astype(int)
    features = pd.DataFrame(
        {
            "amount": amount.round(2),
            "hour": hour,
            "day_of_week": day_of_week,
            "is_new_beneficiary": new_beneficiary,
            "is_new_device": new_device,
            "is_location_change": location_change,
            "account_age_days": account_age,
            "transaction_frequency_24h": frequency_24h,
            "transaction_velocity_1h": velocity_1h,
            "average_transaction_amount": average_amount.round(2),
            "amount_deviation_ratio": deviation.round(4),
            "failed_attempts_24h": failed_attempts,
            "previous_fraud_count": previous_fraud,
            "user_transaction_count": user_transactions,
            "is_weekend": is_weekend,
        }
    ).loc[:, FEATURE_NAMES]
    labels = _sample_fraud_labels(rng, features, scenario)
    features["is_fraud"] = _ensure_both_classes(features, labels)
    return features


def _hour_probabilities() -> np.ndarray:
    weights = np.array([0.25, 0.12, 0.08, 0.06, 0.05, 0.05, 0.06, 0.08, 0.12, 0.18, 0.24, 0.30,
                        0.32, 0.30, 0.28, 0.27, 0.30, 0.34, 0.38, 0.36, 0.31, 0.28, 0.24, 0.18])
    return weights / weights.sum()


def _apply_scenario(
    rng: np.random.Generator,
    scenario: np.ndarray,
    amount: np.ndarray,
    average_amount: np.ndarray,
    account_age: np.ndarray,
    hour: np.ndarray,
    new_beneficiary: np.ndarray,
    new_device: np.ndarray,
    location_change: np.ndarray,
    frequency_24h: np.ndarray,
    velocity_1h: np.ndarray,
    failed_attempts: np.ndarray,
    previous_fraud: np.ndarray,
) -> None:
    masks = {name: scenario == name for name in SCENARIOS}
    for name in SCENARIOS[1:]:
        mask = masks[name]
        count = int(mask.sum())
        if not count:
            continue
        if name in ("account_takeover", "device_location_anomaly"):
            new_device[mask] = rng.binomial(1, 0.82, count)
            location_change[mask] = rng.binomial(1, 0.68, count)
            account_age[mask] = np.minimum(account_age[mask], rng.integers(0, 180, count))
            hour[mask] = rng.choice([0, 1, 2, 3, 4, 5, 22, 23], count)
        if name in ("account_takeover", "new_beneficiary", "failed_attempts_then_large_payment"):
            new_beneficiary[mask] = rng.binomial(1, 0.84, count)
        if name in ("velocity_attack", "failed_attempts_then_large_payment"):
            frequency_24h[mask] = rng.poisson(10, count) + 5
            velocity_1h[mask] = rng.poisson(5, count) + 3
        if name in ("unusual_amount", "new_beneficiary", "failed_attempts_then_large_payment", "account_takeover"):
            amount[mask] = rng.lognormal(np.log(42_000), 0.65, count)
        if name == "unusual_amount":
            average_amount[mask] = rng.lognormal(np.log(1_800), 0.45, count)
        if name == "failed_attempts_then_large_payment":
            failed_attempts[mask] = rng.poisson(3, count) + 2
        if name in ("account_takeover", "failed_attempts_then_large_payment"):
            previous_fraud[mask] = rng.binomial(1, 0.45, count)


def _sample_fraud_labels(rng: np.random.Generator, frame: pd.DataFrame, scenario: np.ndarray) -> np.ndarray:
    signals = (
        1.10 * frame["is_new_beneficiary"].to_numpy()
        + 1.05 * frame["is_new_device"].to_numpy()
        + 1.05 * frame["is_location_change"].to_numpy()
        + 1.35 * (frame["amount_deviation_ratio"].to_numpy() > 2.0).astype(float)
        + 1.20 * (frame["transaction_frequency_24h"].to_numpy() >= 8).astype(float)
        + 1.35 * (frame["transaction_velocity_1h"].to_numpy() >= 4).astype(float)
        + 1.50 * (frame["failed_attempts_24h"].to_numpy() >= 2).astype(float)
        + 2.10 * frame["previous_fraud_count"].to_numpy()
        + 0.55 * (frame["amount"].to_numpy() >= 25_000).astype(float)
    )
    scenario_bonus = np.zeros_like(signals)
    scenario_bonus[scenario == "account_takeover"] = 0.9
    scenario_bonus[scenario == "velocity_attack"] = 1.1
    scenario_bonus[scenario == "unusual_amount"] = 0.8
    scenario_bonus[scenario == "new_beneficiary"] = 0.8
    scenario_bonus[scenario == "device_location_anomaly"] = 0.7
    scenario_bonus[scenario == "failed_attempts_then_large_payment"] = 1.4
    log_odds = -4.35 + 0.95 * signals + scenario_bonus
    probability = 1.0 / (1.0 + np.exp(-log_odds))
    labels = rng.binomial(1, probability).astype(int)

    if len(labels) >= 2:
        overlap_score = (
            frame["is_new_beneficiary"].to_numpy()
            + frame["is_new_device"].to_numpy()
            + frame["is_location_change"].to_numpy()
            + (frame["amount_deviation_ratio"].to_numpy() > 2.0).astype(int)
            + (frame["transaction_frequency_24h"].to_numpy() >= 8).astype(int)
            + (frame["transaction_velocity_1h"].to_numpy() >= 4).astype(int)
            + (frame["failed_attempts_24h"].to_numpy() >= 2).astype(int)
            + frame["previous_fraud_count"].to_numpy()
        )
        keep_normal = (probability < 0.35) & (overlap_score >= 2)
        labels[keep_normal] = 0
        keep_fraud = (probability > 0.65) & (overlap_score <= 2)
        labels[keep_fraud] = 1

    return labels


def _ensure_both_classes(frame: pd.DataFrame, labels: np.ndarray) -> np.ndarray:
    result = labels.astype(int).copy()
    if len(result) < 2:
        return result

    suspiciousness = (
        frame["is_new_beneficiary"].to_numpy()
        + frame["is_new_device"].to_numpy()
        + frame["is_location_change"].to_numpy()
        + (frame["amount_deviation_ratio"].to_numpy() > 2.0).astype(int)
        + (frame["transaction_frequency_24h"].to_numpy() >= 8).astype(int)
        + (frame["transaction_velocity_1h"].to_numpy() >= 4).astype(int)
        + (frame["failed_attempts_24h"].to_numpy() >= 2).astype(int)
        + frame["previous_fraud_count"].to_numpy()
    )

    if not np.any(result == 0):
        result[np.argmin(suspiciousness)] = 0
    if not np.any(result == 1):
        result[np.argmax(suspiciousness)] = 1
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output", type=Path, default=Path("data/transactions.csv"))
    args = parser.parse_args()
    dataset = generate_dataset(args.rows, args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    dataset.to_csv(args.output, index=False)
    print(f"Wrote {len(dataset)} synthetic rows to {args.output}")
    print(f"Target distribution: {dataset['is_fraud'].value_counts().sort_index().to_dict()}")


if __name__ == "__main__":
    main()