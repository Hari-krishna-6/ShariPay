"""Single source of prototype risk and rule-factor thresholds."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskThresholds:
    low_upper: float = 0.30
    medium_upper: float = 0.70

    def __post_init__(self) -> None:
        if not 0.0 < self.low_upper < self.medium_upper <= 1.0:
            raise ValueError("thresholds must satisfy 0 < low_upper < medium_upper <= 1")


@dataclass(frozen=True)
class RiskFactorThresholds:
    unusual_amount_deviation_ratio: float = 2.0
    high_amount_inr: float = 50_000.0
    high_transaction_frequency_24h: float = 8.0
    high_transaction_velocity_1h: float = 4.0
    multiple_failed_attempts_24h: float = 2.0
    unusual_hour_start: int = 0
    unusual_hour_end: int = 4


DEFAULT_RISK_THRESHOLDS = RiskThresholds()
DEFAULT_RISK_FACTOR_THRESHOLDS = RiskFactorThresholds()