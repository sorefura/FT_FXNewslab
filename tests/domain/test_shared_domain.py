from dataclasses import FrozenInstanceError
from datetime import datetime
from typing import Any, cast

import pytest
from fx_core import (
    Currency,
    CurrencyPair,
    DirectionScore,
    Probability,
)

from tests.factories import signal


def test_currency_requires_uppercase_three_letter_code() -> None:
    with pytest.raises(ValueError):
        Currency("usd")


def test_currency_pair_requires_explicit_separator_and_distinct_currencies() -> None:
    with pytest.raises(ValueError):
        CurrencyPair.parse("USDJPY")
    with pytest.raises(ValueError):
        CurrencyPair.parse("USD_USD")


@pytest.mark.parametrize("value", [-1.01, 1.01, float("nan")])
def test_direction_score_rejects_values_outside_contract(value: float) -> None:
    with pytest.raises(ValueError):
        DirectionScore(value)


@pytest.mark.parametrize("value", [-0.01, 1.01, float("inf")])
def test_probability_rejects_values_outside_contract(value: float) -> None:
    with pytest.raises(ValueError):
        Probability(value)


def test_signal_is_immutable_after_creation() -> None:
    created = signal()
    mutable_view = cast(Any, created)
    with pytest.raises(FrozenInstanceError):
        mutable_view.signal_type = "changed"


def test_signal_rejects_naive_datetime() -> None:
    created = signal()
    with pytest.raises(ValueError):
        type(created)(
            signal_id=created.signal_id,
            target=created.target,
            signal_type=created.signal_type,
            direction=created.direction,
            strength=created.strength,
            confidence=created.confidence,
            horizon=created.horizon,
            observed_at=datetime(2026, 7, 13),
            created_at=created.created_at,
            source_feature_ids=created.source_feature_ids,
            versions=created.versions,
        )
