from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fx_core import (
    Currency,
    CurrencyPair,
    CurrencyTarget,
    DirectionScore,
    PairScore,
)
from swap_bot.adoption import AuthorizedSignal, SignalAuthorization
from swap_bot.models import Side
from swap_bot.strategy import (
    EntryEvaluationOutcome,
    EntrySkipReason,
    NewsFilteredCarryStrategy,
    NewsFilteredCarryStrategyConfig,
    OperationalSwapEvidence,
)
from swap_bot.swap import SwapAvailability

from tests.strategy_contracts.factories import (
    NOW,
    PAIR,
    authorized_pair_signal,
    entry_input,
    strategy_config,
    swap_evidence,
)


def _input_with_signal(**signal_changes: object):
    if "signal_created_at" in signal_changes:
        signal_changes["created_at"] = signal_changes.pop("signal_created_at")
    original = authorized_pair_signal()
    signal = replace(original.signal, **signal_changes)
    return entry_input(authorized_pair_signal=AuthorizedSignal(signal, original.authorization))


def _result(**changes: object):
    config = strategy_config(**changes.pop("config", {}))
    evaluation_input = entry_input(**changes)
    return NewsFilteredCarryStrategy(config).evaluate_entry(evaluation_input)


def _authorized_with(
    *,
    signal_changes: dict[str, object] | None = None,
    authorization_changes: dict[str, object] | None = None,
) -> AuthorizedSignal:
    original = authorized_pair_signal()
    authorization_changes = authorization_changes or {}
    authorization = replace(original.authorization, **authorization_changes)
    if "authorization_id" not in authorization_changes:
        authorization = replace(
            authorization,
            authorization_id=authorization.expected_authorization_id,
        )
    return AuthorizedSignal(
        replace(original.signal, **(signal_changes or {})),
        authorization,
    )


def _forged_exact(value: object, **changes: object) -> object:
    forged = object.__new__(type(value))
    for field in value.__dataclass_fields__:  # type: ignore[attr-defined]
        object.__setattr__(
            forged,
            field,
            changes.get(field, getattr(value, field)),
        )
    return forged


def test_evaluates_both_configured_pairs_with_buy_and_sell_candidates() -> None:
    for pair, score, side, long_amount, short_amount in (
        (CurrencyPair.parse("USD_JPY"), PairScore(0.51), Side.BUY, Decimal("1"), Decimal("-1")),
        (CurrencyPair.parse("MXN_JPY"), PairScore(-0.51), Side.SELL, Decimal("-1"), Decimal("1")),
    ):
        original = authorized_pair_signal(pair=pair)
        signal = replace(original.signal, direction=score)
        evaluation_input = entry_input(
            evaluated_pair=pair,
            authorized_pair_signal=AuthorizedSignal(signal, original.authorization),
            swap_evidence=swap_evidence(
                pair=pair, long_received_amount=long_amount, short_received_amount=short_amount
            ),
        )
        result = NewsFilteredCarryStrategy(strategy_config()).evaluate_entry(evaluation_input)
        assert result.outcome is EntryEvaluationOutcome.CANDIDATE
        assert result.pair == pair
        assert result.candidate is not None and result.candidate.side is side


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (
            {"authorized_pair_signal": authorized_pair_signal(pair=CurrencyPair.parse("MXN_JPY"))},
            EntrySkipReason.SIGNAL_NOT_PAIR_TARGET,
        ),
        ({"evaluated_pair": CurrencyPair.parse("EUR_JPY")}, EntrySkipReason.PAIR_NOT_CONFIGURED),
        (
            {
                "authorized_pair_signal": _input_with_signal(
                    signal_type="other"
                ).authorized_pair_signal
            },
            EntrySkipReason.SIGNAL_TYPE_MISMATCH,
        ),
        (
            {
                "authorized_pair_signal": _input_with_signal(
                    versions=replace(
                        authorized_pair_signal().signal.versions, transformation_version="other"
                    )
                ).authorized_pair_signal
            },
            EntrySkipReason.TRANSFORMATION_VERSION_MISMATCH,
        ),
        (
            {"authorized_pair_signal": authorized_pair_signal(strategy_version="other")},
            EntrySkipReason.SIGNAL_STRATEGY_IDENTITY_MISMATCH,
        ),
        (
            {"approved_strategy_config_identity": "other"},
            EntrySkipReason.SIGNAL_CONFIG_IDENTITY_MISMATCH,
        ),
        (
            {
                "authorized_pair_signal": _input_with_signal(
                    signal_created_at=NOW + timedelta(seconds=2)
                ).authorized_pair_signal
            },
            EntrySkipReason.SIGNAL_IN_FUTURE,
        ),
        (
            {
                "authorized_pair_signal": _input_with_signal(
                    observed_at=NOW - timedelta(hours=5)
                ).authorized_pair_signal
            },
            EntrySkipReason.SIGNAL_STALE,
        ),
        (
            {
                "authorized_pair_signal": _input_with_signal(
                    direction=PairScore(0.5)
                ).authorized_pair_signal
            },
            EntrySkipReason.DIRECTION_NEUTRAL,
        ),
        (
            {"swap_evidence": swap_evidence(pair=CurrencyPair.parse("MXN_JPY"))},
            EntrySkipReason.SWAP_WRONG_PAIR,
        ),
        (
            {
                "swap_evidence": swap_evidence(
                    availability=SwapAvailability.UNKNOWN,
                    long_received_amount=None,
                    short_received_amount=None,
                    unit_basis=None,
                    settlement_currency=None,
                )
            },
            EntrySkipReason.SWAP_UNKNOWN,
        ),
        (
            {
                "swap_evidence": swap_evidence(
                    availability=SwapAvailability.UNAVAILABLE,
                    long_received_amount=None,
                    short_received_amount=None,
                    unit_basis=None,
                    settlement_currency=None,
                )
            },
            EntrySkipReason.SWAP_UNAVAILABLE,
        ),
        (
            {
                "swap_evidence": swap_evidence(
                    availability=SwapAvailability.NOT_APPLICABLE,
                    long_received_amount=None,
                    short_received_amount=None,
                    unit_basis=None,
                    settlement_currency=None,
                )
            },
            EntrySkipReason.SWAP_NOT_APPLICABLE,
        ),
        (
            {"swap_evidence": swap_evidence(availability=SwapAvailability.STALE)},
            EntrySkipReason.SWAP_STALE,
        ),
        (
            {"swap_evidence": swap_evidence(received_at=NOW + timedelta(seconds=2))},
            EntrySkipReason.SWAP_MALFORMED,
        ),
        (
            {"swap_evidence": swap_evidence(effective_from=NOW + timedelta(seconds=2))},
            EntrySkipReason.SWAP_NOT_APPLICABLE,
        ),
        ({"swap_evidence": swap_evidence(effective_until=NOW)}, EntrySkipReason.SWAP_STALE),
        (
            {
                "swap_evidence": swap_evidence(
                    long_received_amount=Decimal("0"), short_received_amount=Decimal("0")
                )
            },
            EntrySkipReason.CARRY_NOT_POSITIVE,
        ),
        (
            {
                "swap_evidence": swap_evidence(
                    long_received_amount=Decimal("0"), short_received_amount=Decimal("1")
                )
            },
            EntrySkipReason.DIRECTION_CARRY_MISMATCH,
        ),
    ],
)
def test_returns_each_structured_skip_reason(
    change: dict[str, object], reason: EntrySkipReason
) -> None:
    result = _result(**change)
    assert result.outcome is EntryEvaluationOutcome.SKIP
    assert result.skip_reason is reason


def test_threshold_and_time_boundary_equalities_are_inclusive() -> None:
    config = strategy_config(signal_max_age=timedelta(hours=4), swap_max_age=timedelta(hours=12))
    original = authorized_pair_signal()
    equality_signal = replace(
        original.signal,
        direction=PairScore(0.5),
        observed_at=NOW - timedelta(hours=4),
    )
    neutral = NewsFilteredCarryStrategy(config).evaluate_entry(
        entry_input(
            authorized_pair_signal=AuthorizedSignal(equality_signal, original.authorization),
            approved_strategy_config_identity=config.strategy_config_identity,
            evaluated_at=NOW,
        )
    )
    assert neutral.skip_reason is EntrySkipReason.DIRECTION_NEUTRAL

    negative_equality_signal = replace(
        original.signal,
        direction=PairScore(-0.5),
        observed_at=NOW - timedelta(hours=4),
    )
    negative_neutral = NewsFilteredCarryStrategy(config).evaluate_entry(
        entry_input(
            authorized_pair_signal=AuthorizedSignal(
                negative_equality_signal, original.authorization
            ),
            approved_strategy_config_identity=config.strategy_config_identity,
            evaluated_at=NOW,
        )
    )
    assert negative_neutral.skip_reason is EntrySkipReason.DIRECTION_NEUTRAL

    accepted_signal = replace(
        original.signal, direction=PairScore(0.51), observed_at=NOW - timedelta(hours=4)
    )
    accepted = NewsFilteredCarryStrategy(config).evaluate_entry(
        entry_input(
            authorized_pair_signal=AuthorizedSignal(accepted_signal, original.authorization),
            approved_strategy_config_identity=config.strategy_config_identity,
            swap_evidence=swap_evidence(
                provider_observed_at=NOW - timedelta(hours=12),
                received_at=NOW - timedelta(hours=12),
                effective_from=NOW,
                effective_until=NOW + timedelta(seconds=1),
            ),
            evaluated_at=NOW,
        )
    )
    assert accepted.outcome is EntryEvaluationOutcome.CANDIDATE

    accepted_at_effective_until = NewsFilteredCarryStrategy(config).evaluate_entry(
        entry_input(
            authorized_pair_signal=AuthorizedSignal(
                accepted_signal, original.authorization
            ),
            approved_strategy_config_identity=config.strategy_config_identity,
            swap_evidence=swap_evidence(
                provider_observed_at=NOW - timedelta(hours=12),
                received_at=NOW - timedelta(hours=12),
                effective_from=NOW - timedelta(seconds=1),
                effective_until=NOW,
            ),
            evaluated_at=NOW,
        )
    )
    assert accepted_at_effective_until.outcome is EntryEvaluationOutcome.CANDIDATE


def test_candidate_evaluation_and_candidate_ids_are_deterministic() -> None:
    config = strategy_config()
    strategy = NewsFilteredCarryStrategy(config)
    evaluation_input = entry_input(
        approved_strategy_config_identity=config.strategy_config_identity
    )
    first = strategy.evaluate_entry(evaluation_input)
    second = strategy.evaluate_entry(evaluation_input)
    assert first.outcome is EntryEvaluationOutcome.CANDIDATE
    assert first.evaluation_id == second.evaluation_id
    assert first.candidate is not None and second.candidate is not None
    assert first.candidate.candidate_id == second.candidate.candidate_id


@pytest.mark.parametrize(
    ("score", "long_amount", "short_amount", "reason"),
    [
        (PairScore(0.51), Decimal("0"), Decimal("0"), EntrySkipReason.CARRY_NOT_POSITIVE),
        (
            PairScore(0.51),
            Decimal("0"),
            Decimal("1"),
            EntrySkipReason.DIRECTION_CARRY_MISMATCH,
        ),
        (PairScore(-0.51), Decimal("0"), Decimal("0"), EntrySkipReason.CARRY_NOT_POSITIVE),
        (
            PairScore(-0.51),
            Decimal("1"),
            Decimal("0"),
            EntrySkipReason.DIRECTION_CARRY_MISMATCH,
        ),
    ],
)
def test_buy_and_sell_partition_non_positive_and_misaligned_carry(
    score: PairScore,
    long_amount: Decimal,
    short_amount: Decimal,
    reason: EntrySkipReason,
) -> None:
    result = _result(
        authorized_pair_signal=_authorized_with(signal_changes={"direction": score}),
        swap_evidence=swap_evidence(
            long_received_amount=long_amount,
            short_received_amount=short_amount,
        ),
    )
    assert result.skip_reason is reason


def test_precedence_uses_first_failing_condition_and_result_ids_are_deterministic() -> None:
    config = strategy_config()
    strategy = NewsFilteredCarryStrategy(config)
    evaluation_input = entry_input(
        approved_strategy_config_identity="other",
        swap_evidence=swap_evidence(pair=CurrencyPair.parse("MXN_JPY")),
    )
    first = strategy.evaluate_entry(evaluation_input)
    second = strategy.evaluate_entry(evaluation_input)
    assert first.skip_reason is EntrySkipReason.SIGNAL_CONFIG_IDENTITY_MISMATCH
    assert first.evaluation_id == second.evaluation_id


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        (
            {
                "authorized_pair_signal": authorized_pair_signal(
                    pair=CurrencyPair.parse("MXN_JPY")
                ),
                "approved_strategy_config_identity": "other",
            },
            EntrySkipReason.SIGNAL_NOT_PAIR_TARGET,
        ),
        (
            {
                "authorized_pair_signal": _input_with_signal(
                    observed_at=NOW - timedelta(hours=5)
                ).authorized_pair_signal,
                "swap_evidence": swap_evidence(pair=CurrencyPair.parse("MXN_JPY")),
            },
            EntrySkipReason.SIGNAL_STALE,
        ),
        (
            {
                "authorized_pair_signal": _input_with_signal(
                    direction=PairScore(0.5)
                ).authorized_pair_signal,
                "swap_evidence": swap_evidence(pair=CurrencyPair.parse("MXN_JPY")),
            },
            EntrySkipReason.DIRECTION_NEUTRAL,
        ),
        (
            {
                "swap_evidence": swap_evidence(
                    availability=SwapAvailability.UNKNOWN,
                    long_received_amount=None,
                    short_received_amount=None,
                    unit_basis=None,
                    settlement_currency=None,
                    received_at=NOW + timedelta(seconds=2),
                )
            },
            EntrySkipReason.SWAP_UNKNOWN,
        ),
    ],
)
def test_precedence_collisions_choose_the_earliest_reason(
    changes: dict[str, object], reason: EntrySkipReason
) -> None:
    assert _result(**changes).skip_reason is reason


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        (
            {
                "authorized_pair_signal": _authorized_with(
                    signal_changes={
                        "target": CurrencyTarget(Currency("USD")),
                        "direction": DirectionScore(0.8),
                    }
                ),
                "evaluated_pair": CurrencyPair.parse("EUR_JPY"),
            },
            EntrySkipReason.SIGNAL_NOT_PAIR_TARGET,
        ),
        (
            {
                "evaluated_pair": CurrencyPair.parse("EUR_JPY"),
                "authorized_pair_signal": _authorized_with(
                    signal_changes={"signal_type": "other"}
                ),
            },
            EntrySkipReason.PAIR_NOT_CONFIGURED,
        ),
        (
            {
                "authorized_pair_signal": _authorized_with(
                    signal_changes={
                        "signal_type": "other",
                        "versions": replace(
                            authorized_pair_signal().signal.versions,
                            transformation_version="other",
                        ),
                    }
                )
            },
            EntrySkipReason.SIGNAL_TYPE_MISMATCH,
        ),
        (
            {
                "authorized_pair_signal": _authorized_with(
                    signal_changes={
                        "versions": replace(
                            authorized_pair_signal().signal.versions,
                            transformation_version="other",
                        )
                    },
                    authorization_changes={"strategy_version": "other"},
                )
            },
            EntrySkipReason.TRANSFORMATION_VERSION_MISMATCH,
        ),
        (
            {
                "authorized_pair_signal": _authorized_with(
                    authorization_changes={"strategy_version": "other"}
                ),
                "approved_strategy_config_identity": "other",
            },
            EntrySkipReason.SIGNAL_STRATEGY_IDENTITY_MISMATCH,
        ),
        (
            {
                "approved_strategy_config_identity": "other",
                "authorized_pair_signal": _authorized_with(
                    signal_changes={"created_at": NOW + timedelta(seconds=2)}
                ),
            },
            EntrySkipReason.SIGNAL_CONFIG_IDENTITY_MISMATCH,
        ),
        (
            {
                "authorized_pair_signal": _authorized_with(
                    signal_changes={
                        "observed_at": NOW - timedelta(hours=5),
                        "created_at": NOW + timedelta(seconds=2),
                    }
                )
            },
            EntrySkipReason.SIGNAL_IN_FUTURE,
        ),
        (
            {
                "authorized_pair_signal": _authorized_with(
                    signal_changes={
                        "observed_at": NOW - timedelta(hours=5),
                        "direction": PairScore(0.5),
                    }
                )
            },
            EntrySkipReason.SIGNAL_STALE,
        ),
        (
            {
                "authorized_pair_signal": _authorized_with(
                    signal_changes={"direction": PairScore(0.5)}
                ),
                "swap_evidence": swap_evidence(pair=CurrencyPair.parse("MXN_JPY")),
            },
            EntrySkipReason.DIRECTION_NEUTRAL,
        ),
        (
            {
                "swap_evidence": swap_evidence(
                    pair=CurrencyPair.parse("MXN_JPY"),
                    availability=SwapAvailability.UNKNOWN,
                    long_received_amount=None,
                    short_received_amount=None,
                    unit_basis=None,
                    settlement_currency=None,
                )
            },
            EntrySkipReason.SWAP_WRONG_PAIR,
        ),
        (
            {
                "swap_evidence": swap_evidence(
                    availability=SwapAvailability.UNKNOWN,
                    long_received_amount=None,
                    short_received_amount=None,
                    unit_basis=None,
                    settlement_currency=None,
                    received_at=NOW + timedelta(seconds=2),
                )
            },
            EntrySkipReason.SWAP_UNKNOWN,
        ),
        (
            {
                "swap_evidence": swap_evidence(
                    received_at=NOW + timedelta(seconds=2),
                    effective_from=NOW + timedelta(seconds=3),
                    effective_until=NOW + timedelta(seconds=4),
                    long_received_amount=Decimal("0"),
                    short_received_amount=Decimal("1"),
                )
            },
            EntrySkipReason.SWAP_MALFORMED,
        ),
        (
            {
                "swap_evidence": swap_evidence(
                    provider_observed_at=NOW - timedelta(hours=13),
                    received_at=NOW - timedelta(hours=13),
                    effective_from=NOW + timedelta(seconds=2),
                    effective_until=NOW + timedelta(seconds=3),
                )
            },
            EntrySkipReason.SWAP_NOT_APPLICABLE,
        ),
        (
            {
                "swap_evidence": swap_evidence(
                    provider_observed_at=NOW - timedelta(hours=13),
                    received_at=NOW - timedelta(hours=13),
                    long_received_amount=Decimal("0"),
                    short_received_amount=Decimal("1"),
                )
            },
            EntrySkipReason.SWAP_STALE,
        ),
    ],
)
def test_every_precedence_boundary_wins_over_a_later_failure(
    changes: dict[str, object], reason: EntrySkipReason
) -> None:
    assert _result(**changes).skip_reason is reason


def test_evaluated_pair_controls_skip_identity_and_candidate_requires_matching_pair_signal() -> (
    None
):
    input_for_mxn = entry_input(
        evaluated_pair=CurrencyPair.parse("MXN_JPY"),
        swap_evidence=swap_evidence(pair=CurrencyPair.parse("USD_JPY")),
    )
    result = NewsFilteredCarryStrategy(strategy_config()).evaluate_entry(input_for_mxn)
    assert result.pair == CurrencyPair.parse("MXN_JPY")
    assert result.skip_reason is EntrySkipReason.SIGNAL_NOT_PAIR_TARGET

    with pytest.raises(ValueError, match="evaluated_pair"):
        from swap_bot.strategy import ProductionEntryEvaluation

        ProductionEntryEvaluation.create_candidate(
            input_for_mxn,
            candidate_contract_version=strategy_config().candidate_contract_version,
            side=Side.BUY,
        )


def test_entry_input_rejects_subclass_that_overrides_swap_validation() -> None:
    class BypassingSwapEvidence(OperationalSwapEvidence):
        def validate_intrinsic_integrity(self) -> None:
            pass

    valid = swap_evidence()
    bypass = BypassingSwapEvidence(
        **{field: getattr(valid, field) for field in valid.__dataclass_fields__}
    )
    with pytest.raises(TypeError, match="exact OperationalSwapEvidence"):
        entry_input(swap_evidence=bypass)


def test_entry_input_rejects_currency_pair_subclasses() -> None:
    class DerivedCurrencyPair(CurrencyPair):
        pass

    derived = DerivedCurrencyPair(PAIR.base, PAIR.quote)
    with pytest.raises(TypeError, match="exact CurrencyPair"):
        entry_input(evaluated_pair=derived)


def test_entry_input_revalidates_forged_exact_currency_pair() -> None:
    forged = _forged_exact(PAIR, quote=PAIR.base)
    assert type(forged) is CurrencyPair
    with pytest.raises(ValueError, match="base and quote must differ"):
        entry_input(evaluated_pair=forged)


def test_entry_input_rejects_nested_currency_subclasses() -> None:
    class DerivedCurrency(Currency):
        pass

    pair = CurrencyPair(DerivedCurrency("USD"), Currency("JPY"))
    with pytest.raises(TypeError, match="evaluated_pair base must be an exact Currency"):
        entry_input(evaluated_pair=pair)


def test_entry_input_rejects_string_and_datetime_subclasses() -> None:
    class EqualToEverything(str):
        def __eq__(self, other: object) -> bool:
            return True

        __hash__ = str.__hash__

    class DerivedDatetime(datetime):
        pass

    with pytest.raises(TypeError, match="config_identity must be exact str"):
        entry_input(
            approved_strategy_config_identity=EqualToEverything("forged")
        )

    derived_at = DerivedDatetime.fromtimestamp(NOW.timestamp(), tz=NOW.tzinfo)
    with pytest.raises(TypeError, match="evaluated_at must be exact datetime"):
        entry_input(evaluated_at=derived_at)


def test_strategy_revalidates_a_forged_exact_config() -> None:
    forged = _forged_exact(strategy_config(), strategy_id="")
    assert type(forged) is NewsFilteredCarryStrategyConfig
    with pytest.raises(ValueError, match="strategy_id must not be blank"):
        NewsFilteredCarryStrategy(forged)  # type: ignore[arg-type]


def test_entry_input_revalidates_forged_exact_authorized_signal_content() -> None:
    original = authorized_pair_signal()
    forged_signal = _forged_exact(original.signal, signal_type="")
    forged_authorized = _forged_exact(original, signal=forged_signal)
    assert type(forged_authorized) is AuthorizedSignal
    with pytest.raises(ValueError, match="signal_type must not be blank"):
        entry_input(authorized_pair_signal=forged_authorized)


def test_strategy_rejects_forged_exact_authorization_identity() -> None:
    valid_input = entry_input()
    authorization = valid_input.authorized_pair_signal.authorization
    forged_authorization = _forged_exact(
        authorization,
        authorization_id="signal-authorization-forged",
    )
    forged_authorized = _forged_exact(
        valid_input.authorized_pair_signal,
        authorization=forged_authorization,
    )
    forged_input = _forged_exact(
        valid_input,
        authorized_pair_signal=forged_authorized,
    )

    with pytest.raises(ValueError, match="ID does not match intrinsic authority"):
        NewsFilteredCarryStrategy(strategy_config()).evaluate_entry(forged_input)  # type: ignore[arg-type]


def test_entry_input_rejects_nested_authorized_signal_subclasses() -> None:
    class DerivedPairScore(PairScore):
        pass

    class DerivedSignalAuthorization(SignalAuthorization):
        pass

    original = authorized_pair_signal()
    derived_direction_signal = _forged_exact(
        original.signal,
        direction=DerivedPairScore(original.signal.direction.value),
    )
    with pytest.raises(TypeError, match="exact PairScore"):
        entry_input(
            authorized_pair_signal=AuthorizedSignal(
                derived_direction_signal, original.authorization
            )
        )

    derived_authorization = DerivedSignalAuthorization(
        **{
            field: getattr(original.authorization, field)
            for field in original.authorization.__dataclass_fields__
        }
    )
    with pytest.raises(TypeError, match="exact SignalAuthorization"):
        entry_input(
            authorized_pair_signal=AuthorizedSignal(
                original.signal, derived_authorization
            )
        )
