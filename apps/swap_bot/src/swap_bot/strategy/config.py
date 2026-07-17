from dataclasses import dataclass
from datetime import timedelta

from fx_core import CurrencyPair, PairScore

from ..adoption import digest

NEWS_FILTERED_CARRY_CONFIG_VERSION = "news-filtered-carry-config-v1"
INITIAL_ELIGIBLE_PAIRS = (
    CurrencyPair.parse("USD_JPY"),
    CurrencyPair.parse("MXN_JPY"),
)


@dataclass(frozen=True, slots=True)
class NewsFilteredCarryStrategyConfig:
    config_contract_version: str
    strategy_id: str
    strategy_version: str
    eligible_pairs: tuple[CurrencyPair, ...]
    pair_transformation_version: str
    expected_pair_signal_type: str
    positive_entry_threshold: PairScore
    negative_entry_threshold: PairScore
    signal_max_age: timedelta
    swap_max_age: timedelta
    entry_policy_version: str
    exit_policy_version: str
    candidate_contract_version: str
    close_on_signal_reversal: bool
    close_on_non_positive_carry: bool
    close_on_missing_or_stale_signal: bool
    close_on_missing_or_stale_swap: bool
    maximum_holding_age: timedelta | None

    def __post_init__(self) -> None:
        if self.config_contract_version != NEWS_FILTERED_CARRY_CONFIG_VERSION:
            raise ValueError("unsupported NewsFilteredCarryStrategy config contract")
        for value, label in (
            (self.strategy_id, "strategy_id"),
            (self.strategy_version, "strategy_version"),
            (self.pair_transformation_version, "pair_transformation_version"),
            (self.expected_pair_signal_type, "expected_pair_signal_type"),
            (self.entry_policy_version, "entry_policy_version"),
            (self.exit_policy_version, "exit_policy_version"),
            (self.candidate_contract_version, "candidate_contract_version"),
        ):
            if not value.strip():
                raise ValueError(f"{label} must not be blank")
        if self.eligible_pairs != INITIAL_ELIGIBLE_PAIRS:
            raise ValueError("v1 eligible_pairs must be ordered USD_JPY, MXN_JPY")
        if self.positive_entry_threshold.value <= 0:
            raise ValueError("positive_entry_threshold must be positive")
        if self.negative_entry_threshold.value >= 0:
            raise ValueError("negative_entry_threshold must be negative")
        if self.negative_entry_threshold.value >= self.positive_entry_threshold.value:
            raise ValueError("negative threshold must be below positive threshold")
        if self.signal_max_age <= timedelta(0):
            raise ValueError("signal_max_age must be positive")
        if self.swap_max_age <= timedelta(0):
            raise ValueError("swap_max_age must be positive")
        if self.maximum_holding_age is not None and self.maximum_holding_age <= timedelta(0):
            raise ValueError("maximum_holding_age must be positive when provided")
        for flag, label in (
            (self.close_on_signal_reversal, "close_on_signal_reversal"),
            (self.close_on_non_positive_carry, "close_on_non_positive_carry"),
            (self.close_on_missing_or_stale_signal, "close_on_missing_or_stale_signal"),
            (self.close_on_missing_or_stale_swap, "close_on_missing_or_stale_swap"),
        ):
            if not isinstance(flag, bool):
                raise TypeError(f"{label} must be bool")

    @property
    def identity_payload(self) -> dict[str, object]:
        return {
            "config_contract_version": self.config_contract_version,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "eligible_pairs": [pair.symbol for pair in self.eligible_pairs],
            "pair_transformation_version": self.pair_transformation_version,
            "expected_pair_signal_type": self.expected_pair_signal_type,
            "positive_entry_threshold": self.positive_entry_threshold.value,
            "negative_entry_threshold": self.negative_entry_threshold.value,
            "signal_max_age_microseconds": _timedelta_microseconds(self.signal_max_age),
            "swap_max_age_microseconds": _timedelta_microseconds(self.swap_max_age),
            "entry_policy_version": self.entry_policy_version,
            "exit_policy_version": self.exit_policy_version,
            "candidate_contract_version": self.candidate_contract_version,
            "close_on_signal_reversal": self.close_on_signal_reversal,
            "close_on_non_positive_carry": self.close_on_non_positive_carry,
            "close_on_missing_or_stale_signal": self.close_on_missing_or_stale_signal,
            "close_on_missing_or_stale_swap": self.close_on_missing_or_stale_swap,
            "maximum_holding_age_microseconds": (
                None
                if self.maximum_holding_age is None
                else _timedelta_microseconds(self.maximum_holding_age)
            ),
        }

    @property
    def strategy_config_identity(self) -> str:
        return "strategy-config-" + digest(self.identity_payload)


def _timedelta_microseconds(value: timedelta) -> int:
    return (
        value.days * 86_400_000_000
        + value.seconds * 1_000_000
        + value.microseconds
    )
