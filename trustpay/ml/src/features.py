"""Validated, stable feature preparation shared by training and inference."""

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

FEATURE_NAMES: tuple[str, ...] = (
    "amount",
    "hour",
    "day_of_week",
    "is_new_beneficiary",
    "is_new_device",
    "is_location_change",
    "account_age_days",
    "transaction_frequency_24h",
    "transaction_velocity_1h",
    "average_transaction_amount",
    "amount_deviation_ratio",
    "failed_attempts_24h",
    "previous_fraud_count",
    "user_transaction_count",
    "is_weekend",
)

BINARY_FEATURES: tuple[str, ...] = (
    "is_new_beneficiary",
    "is_new_device",
    "is_location_change",
    "is_weekend",
)

FORBIDDEN_FEATURES = frozenset(
    {
        "is_fraud",
        "transaction_id",
        "transactionid",
        "user_id",
        "userid",
        "beneficiary_id",
        "beneficiaryid",
        "device_id",
        "deviceid",
        "account_number",
        "password",
        "jwt",
        "private_key",
        "blockchain_transaction_id",
    }
)


def prepare_features(data: Mapping[str, Any] | pd.DataFrame) -> pd.DataFrame:
    """Validate model features and return columns in the canonical order.

    Extra operational metadata is ignored, but target/identifier/secret columns
    are rejected to make leakage and accidental sensitive-input use explicit.
    A mapping represents one row; a DataFrame represents one or more rows.
    """
    if isinstance(data, pd.DataFrame):
        frame = data.copy()
    elif isinstance(data, Mapping):
        frame = pd.DataFrame([dict(data)])
    else:
        raise TypeError("features must be a mapping or pandas DataFrame")

    forbidden = FORBIDDEN_FEATURES.intersection(str(column).lower() for column in frame.columns)
    if forbidden:
        raise ValueError(f"forbidden target, identifier, or sensitive input columns: {sorted(forbidden)}")

    missing = [name for name in FEATURE_NAMES if name not in frame.columns]
    if missing:
        raise ValueError(f"missing required features: {missing}")
    if frame.empty:
        raise ValueError("at least one feature row is required")

    ordered = frame.loc[:, FEATURE_NAMES].copy()
    for name in FEATURE_NAMES:
        ordered[name] = pd.to_numeric(ordered[name], errors="coerce")
    values = ordered.to_numpy(dtype=np.float64)
    if not np.isfinite(values).all():
        raise ValueError("features must contain only finite numeric values")

    if (ordered["amount"] <= 0).any():
        raise ValueError("amount must be greater than zero")
    if ((ordered["hour"] < 0) | (ordered["hour"] > 23) | (ordered["hour"] % 1 != 0)).any():
        raise ValueError("hour must be an integer between 0 and 23")
    if ((ordered["day_of_week"] < 0) | (ordered["day_of_week"] > 6) | (ordered["day_of_week"] % 1 != 0)).any():
        raise ValueError("day_of_week must be an integer between 0 and 6")
    if (ordered["account_age_days"] < 0).any():
        raise ValueError("account_age_days cannot be negative")
    for name in (
        "transaction_frequency_24h",
        "transaction_velocity_1h",
        "average_transaction_amount",
        "amount_deviation_ratio",
        "failed_attempts_24h",
        "previous_fraud_count",
        "user_transaction_count",
    ):
        if (ordered[name] < 0).any():
            raise ValueError(f"{name} cannot be negative")
    if (ordered["average_transaction_amount"] <= 0).any():
        raise ValueError("average_transaction_amount must be greater than zero")
    for name in BINARY_FEATURES:
        if not ordered[name].isin((0, 1)).all():
            raise ValueError(f"{name} must contain only 0 or 1")

    return ordered.astype(np.float64)