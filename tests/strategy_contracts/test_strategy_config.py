import inspect
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta

import pytest
from fx_core import CurrencyPair, PairScore
from swap_bot.strategy import NewsFilteredCarryStrategyConfig

from tests.strategy_contracts.factories import strategy_config


def test_same_config_content_has_stable_cross_python_identity() -> None:
    first = strategy_config()
    second = strategy_config()

    assert first.strategy_config_identity == second.strategy_config_identity
    assert first.strategy_config_identity == (
        "strategy-config-c930f8385b8ae0a6173382144299e94c"
        "00ae30d8db0cfecc5a8ef75beb0b879b"
    )


def test_each_config_field_is_canonical_and_content_changes_identity() -> None:
    config = strategy_config()
    changed = strategy_config(strategy_version="strategy-v2")

    assert set(config.identity_payload) == {
        field.name
        if field.name not in {"signal_max_age", "swap_max_age", "maximum_holding_age"}
        else f"{field.name}_microseconds"
        for field in config.__dataclass_fields__.values()
    }
    assert changed.strategy_config_identity != config.strategy_config_identity


@pytest.mark.parametrize(
    "pairs",
    [
        (CurrencyPair.parse("MXN_JPY"), CurrencyPair.parse("USD_JPY")),
        (CurrencyPair.parse("USD_JPY"),),
        (CurrencyPair.parse("USD_JPY"), CurrencyPair.parse("USD_JPY")),
        (
            CurrencyPair.parse("USD_JPY"),
            CurrencyPair.parse("MXN_JPY"),
            CurrencyPair.parse("EUR_JPY"),
        ),
        (CurrencyPair.parse("USD_JPY"), CurrencyPair.parse("EUR_JPY")),
    ],
)
def test_v1_rejects_pair_order_removal_duplicate_addition_or_replacement(
    pairs: tuple[CurrencyPair, ...],
) -> None:
    with pytest.raises(ValueError, match="ordered USD_JPY, MXN_JPY"):
        strategy_config(eligible_pairs=pairs)


@pytest.mark.parametrize(
    ("field", "threshold"),
    [
        ("positive_entry_threshold", PairScore(0)),
        ("negative_entry_threshold", PairScore(0)),
        ("positive_entry_threshold", PairScore(-0.1)),
        ("negative_entry_threshold", PairScore(0.1)),
    ],
)
def test_threshold_sign_and_neutral_boundaries_are_explicit(
    field: str, threshold: PairScore
) -> None:
    with pytest.raises(ValueError):
        strategy_config(**{field: threshold})


@pytest.mark.parametrize("value", [-2.000001, 2.000001])
def test_pair_thresholds_cannot_exceed_pair_score_range(value: float) -> None:
    with pytest.raises(ValueError):
        PairScore(value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("signal_max_age", timedelta(0)),
        ("signal_max_age", timedelta(microseconds=-1)),
        ("swap_max_age", timedelta(0)),
        ("maximum_holding_age", timedelta(0)),
    ],
)
def test_freshness_and_holding_durations_must_be_positive(
    field: str, value: timedelta
) -> None:
    with pytest.raises(ValueError):
        strategy_config(**{field: value})


def test_duration_identity_uses_exact_integer_microseconds() -> None:
    config = strategy_config()

    assert config.identity_payload["signal_max_age_microseconds"] == 14_400_000_003
    assert config.identity_payload["swap_max_age_microseconds"] == 43_200_000_007
    assert config.identity_payload["maximum_holding_age_microseconds"] == 2_592_000_000_011


def test_production_config_has_no_hidden_field_defaults_and_is_immutable() -> None:
    parameters = inspect.signature(NewsFilteredCarryStrategyConfig).parameters.values()
    assert all(parameter.default is inspect.Parameter.empty for parameter in parameters)

    with pytest.raises(FrozenInstanceError):
        strategy_config().strategy_version = "changed"  # type: ignore[misc]


def test_config_contract_and_versions_reject_blank_or_forged_values() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        replace(strategy_config(), config_contract_version="config-v2")
    with pytest.raises(ValueError, match="must not be blank"):
        replace(strategy_config(), entry_policy_version=" ")
