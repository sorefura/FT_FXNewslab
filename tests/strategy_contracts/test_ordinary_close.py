import dataclasses
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
import swap_bot.strategy.ordinary_close as ordinary_close_module
from fx_core import (
    CurrencyPair,
    FeatureId,
    Horizon,
    PairScore,
    PairTarget,
    Probability,
    Signal,
    SignalId,
    VersionMetadata,
)
from swap_bot.adoption import (
    AdoptionMode,
    AuthorizedSignal,
    RuntimeMode,
    SignalAuthorization,
    digest,
)
from swap_bot.execution_authority import ExecutionAuthorityMode
from swap_bot.models import ApprovedLiquidationIntent, PositionId, Side
from swap_bot.operational_swap import (
    OperationalSwapResolution,
    OperationalSwapResolutionOutcome,
)
from swap_bot.strategy import (
    ApprovedCloseIntent,
    NewsFilteredCarryStrategyConfig,
    OperationalPositionExitEvaluationResult,
    OperationalSwapEvidence,
    OrdinaryCloseAllocationPolicy,
    OrdinaryClosePortfolioDecision,
    OrdinaryClosePortfolioDisposition,
    OrdinaryCloseReservationEntry,
    OrdinaryCloseReservationSnapshot,
    OrdinaryCloseRiskDecision,
    OrdinaryCloseRiskOutcome,
    OrdinaryCloseRiskPolicy,
    OrdinaryCloseRiskReason,
    OrdinaryPositionExitEvaluator,
    OrdinaryPositionExitWorkItem,
    PositionCloseCandidate,
    PositionCloseCapacityEvidence,
    PositionExitEvaluationOutcome,
    PositionExitReason,
    SignalAdoptionResolutionOutcome,
    SignalAdoptionTerminalResolution,
    evaluate_ordinary_close_portfolio_and_risk,
)

from tests.strategy_contracts.factories import (
    NOW,
    PAIR,
    position_exit_input,
    strategy_config,
    swap_evidence,
)

MXN_JPY = CurrencyPair.parse("MXN_JPY")


class _Unset:
    pass


_UNSET = _Unset()


def _work(
    *,
    score: float = 1.75,
    authority: ExecutionAuthorityMode = ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED,
) -> OrdinaryPositionExitWorkItem:
    evidence_input = position_exit_input(
        position_changes={"position_opened_at": NOW - timedelta(days=1)}
    )
    signal = evidence_input.authorized_pair_signal
    assert signal is not None
    signal = replace(
        signal, signal=replace(signal.signal, direction=type(signal.signal.direction)(score))
    )
    evidence_input = replace(evidence_input, authorized_pair_signal=signal)
    context = evidence_input.evidence_context
    swap = swap_evidence()
    return OrdinaryPositionExitWorkItem.create(
        evaluation_input=evidence_input,
        capacity=PositionCloseCapacityEvidence.create(
            capacity_contract_version="position-close-capacity-v1",
            position_id=evidence_input.position_id,
            position_evidence_id=context.position_evidence_id,
            pair=evidence_input.pair,
            existing_position_side=evidence_input.existing_position_side,
            position_observed_at=context.position_observed_at,
            open_quantity=Decimal("1000"),
            quantity_unit="BASE_UNITS",
            source="position-snapshot",
            checkpoint_id="position-checkpoint-1",
        ),
        signal_resolution=SignalAdoptionTerminalResolution.create(
            outcome=SignalAdoptionResolutionOutcome.AUTHORIZED,
            signal_selection_checkpoint_id=context.signal_selection_checkpoint_id,
            selection_request_id="pair-request-1",
            selection_claim_id="pair-claim-1",
            selection_snapshot_id="pair-selection-1",
            selection_completion_id="pair-completion-1",
            prior_adoption_decision_id=context.prior_adoption_decision_id,
            adoption_state_evidence_id=context.adoption_state_evidence_id,
            reason_code="AUTHORIZED",
            resolved_at=evidence_input.evaluated_at,
            authorized_signal=signal,
        ),
        swap_resolution=OperationalSwapResolution.create(
            pair=evidence_input.pair,
            source=swap.source,
            source_version=swap.source_version,
            requested_at=evidence_input.evaluated_at,
            outcome=OperationalSwapResolutionOutcome.EVIDENCE,
            reason_code="AVAILABLE",
            evidence=swap,
        ),
        allocation_policy=OrdinaryCloseAllocationPolicy("allocation-v1", Decimal("1")),
        risk_policy=OrdinaryCloseRiskPolicy("risk-v1", timedelta(hours=1)),
        authority=authority,
    )


def test_ordinary_exit_evaluator_returns_additive_keep_root() -> None:
    result = OrdinaryPositionExitEvaluator(strategy_config()).evaluate(_work())

    assert type(result) is OperationalPositionExitEvaluationResult
    assert result.evaluation.outcome is PositionExitEvaluationOutcome.KEEP
    assert result.evaluation.close_candidate is None


def test_ordinary_exit_evaluator_uses_strict_opposite_threshold() -> None:
    result = OrdinaryPositionExitEvaluator(strategy_config()).evaluate(_work(score=-0.5001))

    assert result.evaluation.close_candidate is not None
    assert result.evaluation.close_candidate.exit_reason is PositionExitReason.SIGNAL_REVERSED


def test_ordinary_exit_evaluator_rejects_live_before_evaluation() -> None:
    work = _work(authority=ExecutionAuthorityMode.LIVE)

    with pytest.raises(ValueError, match="LIVE"):
        OrdinaryPositionExitEvaluator(strategy_config()).evaluate(work)


def test_capacity_rejects_non_base_units_and_nonfinite_quantities() -> None:
    work = _work()

    with pytest.raises(ValueError, match="BASE_UNITS"):
        replace(work.capacity, quantity_unit="LOTS")
    with pytest.raises(ValueError, match="positive finite"):
        replace(work.capacity, open_quantity=Decimal("NaN"))


def _authorized_signal(
    *, pair: CurrencyPair, score: float, created_at: datetime
) -> AuthorizedSignal:
    # Built directly (rather than via factories.authorized_pair_signal) because that
    # factory hardcodes observed_at near "now", which blocks constructing signals old
    # enough to exercise staleness/equality boundaries far in the past.
    signal = Signal(
        signal_id=SignalId("signal-pair-1"),
        target=PairTarget(pair),
        signal_type="pair_fundamental",
        direction=PairScore(score),
        strength=Probability(0.9),
        confidence=Probability(0.8),
        horizon=Horizon.DAYS_3,
        observed_at=created_at - timedelta(seconds=1),
        created_at=created_at,
        source_feature_ids=(FeatureId("feature-1"), FeatureId("feature-2")),
        versions=VersionMetadata(
            producer_version="producer-v1",
            model_version="model-v1",
            prompt_version="prompt-v1",
            scorer_version="fundamental-scorer-v1",
            transformation_version="currency-pair-v1",
        ),
    )
    authorization = SignalAuthorization(
        authorization_id="pending-authorization-id",
        signal_id=signal.signal_id.value,
        adoption_decision_id="adoption-approval-1",
        evidence_snapshot_id="research-evidence-1",
        adoption_policy_version="adoption-policy-v1",
        strategy_id="news-filtered-carry",
        strategy_version="strategy-v1",
        adoption_mode=AdoptionMode.SHADOW_ONLY,
        runtime_mode=RuntimeMode.SHADOW,
        authorized_at=NOW,
    )
    authorization = replace(
        authorization, authorization_id=authorization.expected_authorization_id
    )
    return AuthorizedSignal(signal, authorization)


def _work_item(
    *,
    side: Side = Side.BUY,
    pair: CurrencyPair = PAIR,
    score: float | None = 0.0,
    resolution_outcome: SignalAdoptionResolutionOutcome | None = None,
    signal_created_at: datetime = NOW - timedelta(minutes=1),
    position_opened_at: datetime = NOW - timedelta(days=1),
    swap: OperationalSwapEvidence | None | _Unset = _UNSET,
    capacity_open_quantity: Decimal = Decimal("1000"),
    authority: ExecutionAuthorityMode = ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED,
    config: NewsFilteredCarryStrategyConfig | None = None,
    signal_field_overrides: dict[str, object] | None = None,
) -> OrdinaryPositionExitWorkItem:
    if resolution_outcome is None:
        resolution_outcome = (
            SignalAdoptionResolutionOutcome.AUTHORIZED
            if score is not None
            else SignalAdoptionResolutionOutcome.NO_SELECTION
        )
    resolved_config = config or strategy_config()

    authorized_signal = None
    if score is not None:
        authorized_signal = _authorized_signal(pair=pair, score=score, created_at=signal_created_at)
        if signal_field_overrides:
            authorized_signal = replace(
                authorized_signal,
                signal=replace(authorized_signal.signal, **signal_field_overrides),
            )

    swap_ev: OperationalSwapEvidence | None = (
        swap_evidence(pair=pair) if isinstance(swap, _Unset) else swap
    )

    evidence_input = position_exit_input(
        position_changes={
            "existing_position_side": side,
            "pair": pair,
            "position_opened_at": position_opened_at,
        },
        authorized_pair_signal=authorized_signal,
        swap_evidence=swap_ev,
        approved_strategy_config_identity=resolved_config.strategy_config_identity,
    )
    context = evidence_input.evidence_context

    swap_outcome = (
        OperationalSwapResolutionOutcome.EVIDENCE
        if swap_ev is not None
        else OperationalSwapResolutionOutcome.MISSING
    )
    swap_resolution = OperationalSwapResolution.create(
        pair=pair,
        source=swap_ev.source if swap_ev is not None else "swap-source",
        source_version=swap_ev.source_version if swap_ev is not None else "swap-source-v1",
        requested_at=evidence_input.evaluated_at,
        outcome=swap_outcome,
        reason_code="AVAILABLE" if swap_ev is not None else "MISSING",
        evidence=swap_ev,
    )

    selection_kwargs: dict[str, object] = (
        {}
        if resolution_outcome is SignalAdoptionResolutionOutcome.ADOPTION_INACTIVE
        else {
            "selection_request_id": "pair-request-1",
            "selection_claim_id": "pair-claim-1",
            "selection_snapshot_id": "pair-selection-1",
            "selection_completion_id": "pair-completion-1",
        }
    )
    signal_resolution = SignalAdoptionTerminalResolution.create(
        outcome=resolution_outcome,
        signal_selection_checkpoint_id=context.signal_selection_checkpoint_id,
        prior_adoption_decision_id=context.prior_adoption_decision_id,
        adoption_state_evidence_id=context.adoption_state_evidence_id,
        reason_code=resolution_outcome.value,
        resolved_at=evidence_input.evaluated_at,
        authorized_signal=authorized_signal,
        **selection_kwargs,
    )

    return OrdinaryPositionExitWorkItem.create(
        evaluation_input=evidence_input,
        capacity=PositionCloseCapacityEvidence.create(
            capacity_contract_version="position-close-capacity-v1",
            position_id=evidence_input.position_id,
            position_evidence_id=context.position_evidence_id,
            pair=evidence_input.pair,
            existing_position_side=evidence_input.existing_position_side,
            position_observed_at=context.position_observed_at,
            open_quantity=capacity_open_quantity,
            quantity_unit="BASE_UNITS",
            source="position-snapshot",
            checkpoint_id="position-checkpoint-1",
        ),
        signal_resolution=signal_resolution,
        swap_resolution=swap_resolution,
        allocation_policy=OrdinaryCloseAllocationPolicy("allocation-v1", Decimal("1")),
        risk_policy=OrdinaryCloseRiskPolicy("risk-v1", timedelta(hours=1)),
        authority=authority,
    )


def _reason_for(
    work: OrdinaryPositionExitWorkItem,
    config: NewsFilteredCarryStrategyConfig | None = None,
) -> PositionExitReason | None:
    result = OrdinaryPositionExitEvaluator(config or strategy_config()).evaluate(work)
    candidate = result.evaluation.close_candidate
    return None if candidate is None else candidate.exit_reason


# ---------------------------------------------------------------------------
# Terminal outcomes
# ---------------------------------------------------------------------------


def test_ordinary_exit_evaluator_sell_side_reverses_above_positive_threshold() -> None:
    work = _work_item(side=Side.SELL, score=0.5001)

    assert _reason_for(work) is PositionExitReason.SIGNAL_REVERSED


def test_ordinary_exit_evaluator_adoption_inactive_closes_regardless_of_disabled_flags() -> None:
    config = strategy_config(
        close_on_signal_reversal=False,
        close_on_non_positive_carry=False,
        close_on_missing_or_stale_signal=False,
        close_on_missing_or_stale_swap=False,
    )
    work = _work_item(
        score=None,
        resolution_outcome=SignalAdoptionResolutionOutcome.ADOPTION_INACTIVE,
        config=config,
    )

    assert _reason_for(work, config) is PositionExitReason.ADOPTION_NO_LONGER_ACTIVE


def test_ordinary_exit_evaluator_missing_signal_closes_when_flag_true() -> None:
    work = _work_item(score=None, resolution_outcome=SignalAdoptionResolutionOutcome.NO_SELECTION)

    assert _reason_for(work) is PositionExitReason.REQUIRED_SIGNAL_MISSING_OR_STALE


def test_ordinary_exit_evaluator_ambiguous_signal_closes_when_flag_true() -> None:
    work = _work_item(score=None, resolution_outcome=SignalAdoptionResolutionOutcome.AMBIGUOUS)

    assert _reason_for(work) is PositionExitReason.REQUIRED_SIGNAL_MISSING_OR_STALE


def test_ordinary_exit_evaluator_stale_signal_closes_when_flag_true() -> None:
    evaluated_at = position_exit_input().evaluated_at
    signal_max_age = strategy_config().signal_max_age
    stale_created_at = evaluated_at - signal_max_age - timedelta(microseconds=1)
    work = _work_item(signal_created_at=stale_created_at)

    assert _reason_for(work) is PositionExitReason.REQUIRED_SIGNAL_MISSING_OR_STALE


def test_ordinary_exit_evaluator_missing_swap_closes_when_flag_true() -> None:
    work = _work_item(swap=None)

    assert _reason_for(work) is PositionExitReason.REQUIRED_SWAP_MISSING_OR_STALE


def test_ordinary_exit_evaluator_stale_swap_closes_when_flag_true() -> None:
    swap_max_age = strategy_config().swap_max_age
    received_at = NOW - swap_max_age - timedelta(microseconds=1)
    stale_swap = swap_evidence(
        pair=PAIR,
        received_at=received_at,
        provider_observed_at=received_at - timedelta(seconds=1),
        effective_from=received_at - timedelta(days=1),
        effective_until=NOW + timedelta(days=1),
    )
    work = _work_item(swap=stale_swap)

    assert _reason_for(work) is PositionExitReason.REQUIRED_SWAP_MISSING_OR_STALE


def test_ordinary_exit_evaluator_carry_non_positive_closes_for_sell_side() -> None:
    work = _work_item(side=Side.SELL)

    assert _reason_for(work) is PositionExitReason.CARRY_NO_LONGER_POSITIVE


def test_ordinary_exit_evaluator_maximum_holding_age_closes_when_strictly_exceeded() -> None:
    maximum_holding_age = strategy_config().maximum_holding_age
    assert maximum_holding_age is not None
    evaluated_at = position_exit_input().evaluated_at
    opened_at = evaluated_at - maximum_holding_age - timedelta(days=1)
    work = _work_item(position_opened_at=opened_at)

    assert _reason_for(work) is PositionExitReason.MAXIMUM_HOLDING_AGE


# ---------------------------------------------------------------------------
# Equality boundaries
# ---------------------------------------------------------------------------


def test_ordinary_exit_evaluator_threshold_equality_is_not_reversal_buy() -> None:
    work = _work_item(side=Side.BUY, score=-0.5)

    result = OrdinaryPositionExitEvaluator(strategy_config()).evaluate(work)

    assert result.evaluation.outcome is PositionExitEvaluationOutcome.KEEP


def test_ordinary_exit_evaluator_threshold_equality_is_not_reversal_sell() -> None:
    swap = swap_evidence(pair=PAIR, short_received_amount=Decimal("5.00"))
    work = _work_item(side=Side.SELL, score=0.5, swap=swap)

    result = OrdinaryPositionExitEvaluator(strategy_config()).evaluate(work)

    assert result.evaluation.outcome is PositionExitEvaluationOutcome.KEEP


def test_ordinary_exit_evaluator_signal_max_age_equality_is_still_fresh() -> None:
    evaluated_at = position_exit_input().evaluated_at
    signal_max_age = strategy_config().signal_max_age
    created_at = evaluated_at - signal_max_age
    work = _work_item(signal_created_at=created_at)

    result = OrdinaryPositionExitEvaluator(strategy_config()).evaluate(work)

    assert result.evaluation.outcome is PositionExitEvaluationOutcome.KEEP


def test_ordinary_exit_evaluator_swap_max_age_equality_is_still_fresh() -> None:
    evaluated_at = position_exit_input().evaluated_at
    swap_max_age = strategy_config().swap_max_age
    received_at = evaluated_at - swap_max_age
    swap = swap_evidence(
        pair=PAIR,
        received_at=received_at,
        provider_observed_at=received_at - timedelta(seconds=1),
        effective_from=received_at - timedelta(days=1),
        effective_until=NOW + timedelta(days=2),
    )
    work = _work_item(swap=swap)

    result = OrdinaryPositionExitEvaluator(strategy_config()).evaluate(work)

    assert result.evaluation.outcome is PositionExitEvaluationOutcome.KEEP


def test_ordinary_exit_evaluator_swap_effective_from_equality_is_usable() -> None:
    evaluated_at = position_exit_input().evaluated_at
    swap = swap_evidence(
        pair=PAIR,
        effective_from=evaluated_at,
        effective_until=evaluated_at + timedelta(days=1),
        received_at=NOW - timedelta(seconds=1),
        provider_observed_at=NOW - timedelta(seconds=2),
    )
    work = _work_item(swap=swap)

    result = OrdinaryPositionExitEvaluator(strategy_config()).evaluate(work)

    assert result.evaluation.outcome is PositionExitEvaluationOutcome.KEEP


def test_ordinary_exit_evaluator_swap_effective_until_equality_is_usable() -> None:
    evaluated_at = position_exit_input().evaluated_at
    swap = swap_evidence(
        pair=PAIR,
        effective_from=evaluated_at - timedelta(days=1),
        effective_until=evaluated_at,
        received_at=NOW - timedelta(seconds=1),
        provider_observed_at=NOW - timedelta(seconds=2),
    )
    work = _work_item(swap=swap)

    result = OrdinaryPositionExitEvaluator(strategy_config()).evaluate(work)

    assert result.evaluation.outcome is PositionExitEvaluationOutcome.KEEP


def test_ordinary_exit_evaluator_maximum_holding_age_equality_closes() -> None:
    maximum_holding_age = strategy_config().maximum_holding_age
    assert maximum_holding_age is not None
    evaluated_at = position_exit_input().evaluated_at
    opened_at = evaluated_at - maximum_holding_age
    work = _work_item(position_opened_at=opened_at)

    assert _reason_for(work) is PositionExitReason.MAXIMUM_HOLDING_AGE


# ---------------------------------------------------------------------------
# Precedence collisions and flag fall-through
# ---------------------------------------------------------------------------


def test_ordinary_exit_evaluator_adoption_inactive_precedes_missing_signal() -> None:
    work = _work_item(
        score=None, resolution_outcome=SignalAdoptionResolutionOutcome.ADOPTION_INACTIVE
    )
    config = strategy_config(close_on_missing_or_stale_signal=True)

    assert _reason_for(work, config) is PositionExitReason.ADOPTION_NO_LONGER_ACTIVE


def test_ordinary_exit_evaluator_signal_reversal_precedes_missing_swap() -> None:
    work = _work_item(side=Side.BUY, score=-0.5001, swap=None)
    config = strategy_config(
        close_on_signal_reversal=True,
        close_on_missing_or_stale_swap=True,
    )

    assert _reason_for(work, config) is PositionExitReason.SIGNAL_REVERSED


def test_ordinary_exit_evaluator_disabled_signal_reversal_falls_through_to_swap_check() -> None:
    config = strategy_config(close_on_signal_reversal=False)
    work = _work_item(side=Side.BUY, score=-0.5001, swap=None, config=config)

    assert _reason_for(work, config) is PositionExitReason.REQUIRED_SWAP_MISSING_OR_STALE


def test_ordinary_exit_evaluator_stale_signal_does_not_reverse_when_missing_flag_disabled() -> None:
    evaluated_at = position_exit_input().evaluated_at
    signal_max_age = strategy_config().signal_max_age
    stale_created_at = evaluated_at - signal_max_age - timedelta(microseconds=1)
    config = strategy_config(close_on_missing_or_stale_signal=False)
    work = _work_item(
        side=Side.BUY, score=-0.5001, signal_created_at=stale_created_at, config=config
    )

    reason = _reason_for(work, config)
    assert reason is not PositionExitReason.SIGNAL_REVERSED
    assert reason is None


def test_ordinary_exit_evaluator_stale_signal_with_unsupported_lineage_raises() -> None:
    evaluated_at = position_exit_input().evaluated_at
    signal_max_age = strategy_config().signal_max_age
    stale_created_at = evaluated_at - signal_max_age - timedelta(microseconds=1)
    work = _work_item(
        signal_created_at=stale_created_at,
        signal_field_overrides={"signal_type": "unsupported_signal_type"},
    )

    with pytest.raises(ValueError, match="unsupported Signal lineage"):
        OrdinaryPositionExitEvaluator(strategy_config()).evaluate(work)


# ---------------------------------------------------------------------------
# Both Pairs, both Sides, and deterministic content-addressed IDs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pair", "side"),
    [
        (PAIR, Side.BUY),
        (PAIR, Side.SELL),
        (MXN_JPY, Side.BUY),
        (MXN_JPY, Side.SELL),
    ],
)
def test_ordinary_exit_evaluator_keeps_for_every_pair_and_side_with_deterministic_ids(
    pair: CurrencyPair, side: Side
) -> None:
    swap = swap_evidence(
        pair=pair,
        short_received_amount=Decimal("5.00") if side is Side.SELL else Decimal("-15.25"),
    )

    def build() -> OrdinaryPositionExitWorkItem:
        return _work_item(pair=pair, side=side, swap=swap)

    work_a = build()
    work_b = build()
    assert work_a.work_item_id == work_b.work_item_id

    evaluator = OrdinaryPositionExitEvaluator(strategy_config())
    result = evaluator.evaluate(work_a)
    result_again = evaluator.evaluate(work_b)

    assert result.evaluation.outcome is PositionExitEvaluationOutcome.KEEP
    assert result.operational_evaluation_id == result_again.operational_evaluation_id


@pytest.mark.parametrize(
    ("pair", "side", "score"),
    [
        (PAIR, Side.BUY, -0.5001),
        (PAIR, Side.SELL, 0.5001),
        (MXN_JPY, Side.BUY, -0.5001),
        (MXN_JPY, Side.SELL, 0.5001),
    ],
)
def test_ordinary_exit_evaluator_signal_reversed_for_every_pair_and_side(
    pair: CurrencyPair, side: Side, score: float
) -> None:
    work = _work_item(pair=pair, side=side, score=score)

    assert _reason_for(work) is PositionExitReason.SIGNAL_REVERSED


def test_capacity_evidence_id_is_deterministic_and_content_addressed() -> None:
    kwargs: dict[str, object] = {
        "capacity_contract_version": "position-close-capacity-v1",
        "position_id": PositionId("position-determinism"),
        "position_evidence_id": "position-evidence-determinism",
        "pair": PAIR,
        "existing_position_side": Side.BUY,
        "position_observed_at": NOW,
        "open_quantity": Decimal("1000"),
        "quantity_unit": "BASE_UNITS",
        "source": "position-snapshot",
        "checkpoint_id": "position-checkpoint-1",
    }
    first = PositionCloseCapacityEvidence.create(**kwargs)  # type: ignore[arg-type]
    second = PositionCloseCapacityEvidence.create(**kwargs)  # type: ignore[arg-type]
    assert first.capacity_evidence_id == second.capacity_evidence_id

    changed = PositionCloseCapacityEvidence.create(
        **{**kwargs, "open_quantity": Decimal("500")}  # type: ignore[arg-type]
    )
    assert changed.capacity_evidence_id != first.capacity_evidence_id


def test_work_item_id_is_deterministic_and_content_addressed() -> None:
    first = _work_item()
    second = _work_item()
    assert first.work_item_id == second.work_item_id

    changed = _work_item(capacity_open_quantity=Decimal("500"))
    assert changed.work_item_id != first.work_item_id


# ---------------------------------------------------------------------------
# Exact-type adversarial and cross-lineage inputs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "override",
    [
        {"position_id": PositionId("other-position")},
        {"pair": MXN_JPY},
        {"existing_position_side": Side.SELL},
        {"position_observed_at": NOW + timedelta(seconds=5)},
    ],
)
def test_work_item_rejects_capacity_mismatched_with_position_evidence(
    override: dict[str, object]
) -> None:
    work = _work_item()
    base_kwargs: dict[str, object] = {
        "capacity_contract_version": work.capacity.capacity_contract_version,
        "position_id": work.capacity.position_id,
        "position_evidence_id": work.capacity.position_evidence_id,
        "pair": work.capacity.pair,
        "existing_position_side": work.capacity.existing_position_side,
        "position_observed_at": work.capacity.position_observed_at,
        "open_quantity": work.capacity.open_quantity,
        "quantity_unit": work.capacity.quantity_unit,
        "source": work.capacity.source,
        "checkpoint_id": work.capacity.checkpoint_id,
    }
    base_kwargs.update(override)
    other_capacity = PositionCloseCapacityEvidence.create(**base_kwargs)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="does not match Position evidence"):
        replace(work, capacity=other_capacity)


def test_work_item_rejects_signal_resolution_lineage_mismatch() -> None:
    work = _work_item()
    resolution = work.signal_resolution
    mismatched = SignalAdoptionTerminalResolution.create(
        outcome=resolution.outcome,
        signal_selection_checkpoint_id="mismatched-checkpoint",
        selection_request_id=resolution.selection_request_id,
        selection_claim_id=resolution.selection_claim_id,
        selection_snapshot_id=resolution.selection_snapshot_id,
        selection_completion_id=resolution.selection_completion_id,
        prior_adoption_decision_id=resolution.prior_adoption_decision_id,
        adoption_state_evidence_id=resolution.adoption_state_evidence_id,
        reason_code=resolution.reason_code,
        resolved_at=resolution.resolved_at,
        authorized_signal=resolution.authorized_signal,
    )

    with pytest.raises(ValueError, match="Signal resolution does not match input context"):
        replace(work, signal_resolution=mismatched)


def test_work_item_rejects_swap_resolution_evidence_mismatch() -> None:
    work = _work_item()
    other_swap = swap_evidence(
        pair=work.evaluation_input.pair,
        source="different-swap-source",
        source_version="different-swap-source-v1",
    )
    mismatched = OperationalSwapResolution.create(
        pair=work.evaluation_input.pair,
        source=other_swap.source,
        source_version=other_swap.source_version,
        requested_at=work.swap_resolution.requested_at,
        outcome=OperationalSwapResolutionOutcome.EVIDENCE,
        reason_code="AVAILABLE",
        evidence=other_swap,
    )

    with pytest.raises(ValueError, match="Swap resolution does not match accepted input"):
        replace(work, swap_resolution=mismatched)


def test_work_item_rejects_future_swap_resolution_requested_at() -> None:
    work = _work_item()
    resolution = work.swap_resolution
    future_resolution = OperationalSwapResolution.create(
        pair=resolution.pair,
        source=resolution.source,
        source_version=resolution.source_version,
        requested_at=work.evaluation_input.evaluated_at + timedelta(seconds=1),
        outcome=resolution.outcome,
        reason_code=resolution.reason_code,
        evidence=resolution.evidence,
    )

    with pytest.raises(ValueError, match="operational evidence is future or cross-lineage"):
        replace(work, swap_resolution=future_resolution)


def test_work_item_rejects_future_signal_resolution_resolved_at() -> None:
    work = _work_item()
    resolution = work.signal_resolution
    future_resolution = SignalAdoptionTerminalResolution.create(
        outcome=resolution.outcome,
        signal_selection_checkpoint_id=resolution.signal_selection_checkpoint_id,
        selection_request_id=resolution.selection_request_id,
        selection_claim_id=resolution.selection_claim_id,
        selection_snapshot_id=resolution.selection_snapshot_id,
        selection_completion_id=resolution.selection_completion_id,
        prior_adoption_decision_id=resolution.prior_adoption_decision_id,
        adoption_state_evidence_id=resolution.adoption_state_evidence_id,
        reason_code=resolution.reason_code,
        resolved_at=work.evaluation_input.evaluated_at + timedelta(seconds=1),
        authorized_signal=resolution.authorized_signal,
    )

    with pytest.raises(ValueError, match="operational evidence is future or cross-lineage"):
        replace(work, signal_resolution=future_resolution)


def test_work_item_rejects_future_nested_swap_evidence_received_at() -> None:
    evaluated_at = position_exit_input().evaluated_at
    future_received_at = evaluated_at + timedelta(seconds=5)
    future_swap = swap_evidence(
        pair=PAIR,
        received_at=future_received_at,
        provider_observed_at=future_received_at - timedelta(seconds=1),
    )
    config = strategy_config(close_on_missing_or_stale_swap=False)

    with pytest.raises(ValueError, match="cannot be after evaluated_at"):
        _work_item(swap=future_swap, config=config)


def test_work_item_rejects_future_nested_signal_created_at() -> None:
    evaluated_at = position_exit_input().evaluated_at
    future_created_at = evaluated_at + timedelta(seconds=5)

    with pytest.raises(ValueError, match="cannot be after evaluated_at"):
        _work_item(signal_created_at=future_created_at)


# ---------------------------------------------------------------------------
# Unsupported Strategy/config lineage
# ---------------------------------------------------------------------------


def test_ordinary_exit_evaluator_rejects_unsupported_strategy_id() -> None:
    work = _work_item()
    evaluator = OrdinaryPositionExitEvaluator(strategy_config(strategy_id="another-strategy"))

    with pytest.raises(ValueError, match="unsupported Strategy/config lineage"):
        evaluator.evaluate(work)


def test_ordinary_exit_evaluator_rejects_unsupported_strategy_version() -> None:
    work = _work_item()
    evaluator = OrdinaryPositionExitEvaluator(strategy_config(strategy_version="strategy-v2"))

    with pytest.raises(ValueError, match="unsupported Strategy/config lineage"):
        evaluator.evaluate(work)


def test_ordinary_exit_evaluator_rejects_unsupported_config_identity() -> None:
    work = _work_item()
    evaluator = OrdinaryPositionExitEvaluator(
        strategy_config(exit_policy_version="another-exit-policy")
    )

    with pytest.raises(ValueError, match="unsupported Strategy/config lineage"):
        evaluator.evaluate(work)


def test_ordinary_exit_evaluator_rejects_pair_not_eligible() -> None:
    work = _work_item(pair=CurrencyPair.parse("EUR_USD"))
    evaluator = OrdinaryPositionExitEvaluator(strategy_config())

    with pytest.raises(ValueError, match="unsupported Strategy/config lineage"):
        evaluator.evaluate(work)


# ---------------------------------------------------------------------------
# Subclass and comparison-override adversarial inputs
# ---------------------------------------------------------------------------


class _ForgedPositionId(PositionId):
    pass


class _AlwaysEqualStr(str):
    def __eq__(self, other: object) -> bool:
        return True

    def __hash__(self) -> int:
        return hash(str(self))


def _capacity_kwargs(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "capacity_contract_version": "position-close-capacity-v1",
        "position_id": PositionId("position-1"),
        "position_evidence_id": "position-evidence-1",
        "pair": PAIR,
        "existing_position_side": Side.BUY,
        "position_observed_at": NOW,
        "open_quantity": Decimal("1000"),
        "quantity_unit": "BASE_UNITS",
        "source": "position-snapshot",
        "checkpoint_id": "position-checkpoint-1",
    }
    kwargs.update(overrides)
    return kwargs


def test_capacity_rejects_forged_position_id_subclass() -> None:
    with pytest.raises(TypeError, match="exact types"):
        PositionCloseCapacityEvidence.create(
            **_capacity_kwargs(position_id=_ForgedPositionId("position-1"))  # type: ignore[arg-type]
        )


def test_capacity_rejects_comparison_overriding_checkpoint_id_subclass() -> None:
    with pytest.raises(TypeError, match="exact str"):
        PositionCloseCapacityEvidence.create(
            **_capacity_kwargs(  # type: ignore[arg-type]
                checkpoint_id=_AlwaysEqualStr("position-checkpoint-1")
            )
        )


# ---------------------------------------------------------------------------
# B2 - close-specific Portfolio and Risk contracts
# ---------------------------------------------------------------------------


def _close_result(
    *, side: Side = Side.BUY
) -> tuple[OperationalPositionExitEvaluationResult, OrdinaryPositionExitWorkItem]:
    score = -0.5001 if side is Side.BUY else 0.5001
    work = _work_item(side=side, score=score)
    result = OrdinaryPositionExitEvaluator(strategy_config()).evaluate(work)
    assert result.evaluation.outcome is PositionExitEvaluationOutcome.CLOSE_CANDIDATE
    return result, work


def _capacity_for(
    candidate: PositionCloseCandidate,
    *,
    open_quantity: Decimal,
    position_observed_at: datetime | None = None,
) -> PositionCloseCapacityEvidence:
    lineage = candidate.evidence_lineage
    return PositionCloseCapacityEvidence.create(
        capacity_contract_version="position-close-capacity-v1",
        position_id=candidate.position_id,
        position_evidence_id=lineage.position_evidence_id,
        pair=candidate.pair,
        existing_position_side=candidate.existing_position_side,
        position_observed_at=(
            position_observed_at
            if position_observed_at is not None
            else lineage.position.position_observed_at
        ),
        open_quantity=open_quantity,
        quantity_unit="BASE_UNITS",
        source="position-snapshot",
        checkpoint_id="position-checkpoint-2",
    )


def _snapshot(
    candidate: PositionCloseCandidate, *entries: tuple[str, Decimal]
) -> OrdinaryCloseReservationSnapshot:
    return OrdinaryCloseReservationSnapshot(
        position_id=candidate.position_id,
        entries=tuple(
            OrdinaryCloseReservationEntry(intent_id=intent_id, quantity=quantity)
            for intent_id, quantity in entries
        ),
    )


_HALF_ALLOCATION = OrdinaryCloseAllocationPolicy("allocation-v1", Decimal("0.5"))
_ONE_HOUR_RISK_POLICY = OrdinaryCloseRiskPolicy("risk-v1", timedelta(hours=1))


def test_portfolio_accepts_full_target_when_capacity_exceeds_it() -> None:
    result, _ = _close_result()
    candidate = result.evaluation.close_candidate
    assert candidate is not None
    capacity = _capacity_for(candidate, open_quantity=Decimal("1000"))

    decision = OrdinaryClosePortfolioDecision.create(
        operational_evaluation_id=result.operational_evaluation_id,
        candidate=candidate,
        capacity=capacity,
        allocation_policy=_HALF_ALLOCATION,
        reservation_snapshot=_snapshot(candidate),
    )

    assert decision.disposition is OrdinaryClosePortfolioDisposition.ACCEPT
    assert decision.target_quantity == Decimal("500")
    assert decision.available_before == Decimal("1000")
    assert decision.allocated_quantity == Decimal("500")


def test_portfolio_reduces_to_remaining_capacity() -> None:
    result, _ = _close_result()
    candidate = result.evaluation.close_candidate
    assert candidate is not None
    capacity = _capacity_for(candidate, open_quantity=Decimal("1000"))
    snapshot = _snapshot(candidate, ("prior-intent-1", Decimal("700")))

    decision = OrdinaryClosePortfolioDecision.create(
        operational_evaluation_id=result.operational_evaluation_id,
        candidate=candidate,
        capacity=capacity,
        allocation_policy=_HALF_ALLOCATION,
        reservation_snapshot=snapshot,
    )

    assert decision.disposition is OrdinaryClosePortfolioDisposition.REDUCE
    assert decision.available_before == Decimal("300")
    assert decision.allocated_quantity == Decimal("300")


def test_portfolio_rejects_at_exact_zero_capacity_equality() -> None:
    result, _ = _close_result()
    candidate = result.evaluation.close_candidate
    assert candidate is not None
    capacity = _capacity_for(candidate, open_quantity=Decimal("1000"))
    snapshot = _snapshot(candidate, ("prior-intent-1", Decimal("1000")))

    decision = OrdinaryClosePortfolioDecision.create(
        operational_evaluation_id=result.operational_evaluation_id,
        candidate=candidate,
        capacity=capacity,
        allocation_policy=_HALF_ALLOCATION,
        reservation_snapshot=snapshot,
    )

    assert decision.disposition is OrdinaryClosePortfolioDisposition.REJECT
    assert decision.available_before == Decimal("0")
    assert decision.allocated_quantity is None


def test_portfolio_raises_integrity_error_when_reservations_exceed_capacity() -> None:
    result, _ = _close_result()
    candidate = result.evaluation.close_candidate
    assert candidate is not None
    capacity = _capacity_for(candidate, open_quantity=Decimal("1000"))
    snapshot = _snapshot(candidate, ("prior-intent-1", Decimal("1000.01")))

    with pytest.raises(ValueError, match="already exceed"):
        OrdinaryClosePortfolioDecision.create(
            operational_evaluation_id=result.operational_evaluation_id,
            candidate=candidate,
            capacity=capacity,
            allocation_policy=_HALF_ALLOCATION,
            reservation_snapshot=snapshot,
        )


def test_portfolio_accepts_when_available_before_exactly_equals_target_quantity() -> None:
    result, _ = _close_result()
    candidate = result.evaluation.close_candidate
    assert candidate is not None
    capacity = _capacity_for(candidate, open_quantity=Decimal("1000"))
    snapshot = _snapshot(candidate, ("prior-intent-1", Decimal("500")))

    decision = OrdinaryClosePortfolioDecision.create(
        operational_evaluation_id=result.operational_evaluation_id,
        candidate=candidate,
        capacity=capacity,
        allocation_policy=_HALF_ALLOCATION,
        reservation_snapshot=snapshot,
    )

    assert decision.disposition is OrdinaryClosePortfolioDisposition.ACCEPT
    assert decision.available_before == Decimal("500")
    assert decision.target_quantity == Decimal("500")
    assert decision.allocated_quantity == Decimal("500")


def test_risk_rejects_reservation_snapshot_mismatched_with_portfolio_decision() -> None:
    result, _ = _close_result()
    candidate = result.evaluation.close_candidate
    assert candidate is not None
    capacity = _capacity_for(candidate, open_quantity=Decimal("1000"))
    snapshot = _snapshot(candidate)

    portfolio_decision = OrdinaryClosePortfolioDecision.create(
        operational_evaluation_id=result.operational_evaluation_id,
        candidate=candidate,
        capacity=capacity,
        allocation_policy=_HALF_ALLOCATION,
        reservation_snapshot=snapshot,
    )
    different_snapshot = _snapshot(candidate, ("prior-intent-1", Decimal("50")))

    with pytest.raises(
        ValueError, match="Risk reservation snapshot does not match Portfolio decision"
    ):
        OrdinaryCloseRiskDecision.create(
            portfolio_decision=portfolio_decision,
            candidate=candidate,
            capacity=capacity,
            reservation_snapshot=different_snapshot,
            risk_policy=_ONE_HOUR_RISK_POLICY,
            evaluated_at=result.evaluation.evaluated_at,
        )


def test_risk_reject_is_linked_to_portfolio_reject_with_no_intent() -> None:
    result, _ = _close_result()
    candidate = result.evaluation.close_candidate
    assert candidate is not None
    capacity = _capacity_for(candidate, open_quantity=Decimal("1000"))
    snapshot = _snapshot(candidate, ("prior-intent-1", Decimal("1000")))

    portfolio_decision, risk_decision, intent = evaluate_ordinary_close_portfolio_and_risk(
        result,
        capacity=capacity,
        reservation_snapshot=snapshot,
        allocation_policy=_HALF_ALLOCATION,
        risk_policy=_ONE_HOUR_RISK_POLICY,
        authority=ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED,
    )

    assert portfolio_decision.disposition is OrdinaryClosePortfolioDisposition.REJECT
    assert risk_decision.outcome is OrdinaryCloseRiskOutcome.REJECT
    assert risk_decision.reason is OrdinaryCloseRiskReason.PORTFOLIO_REJECTED
    assert risk_decision.portfolio_decision_id == portfolio_decision.portfolio_decision_id
    assert intent is None


def test_risk_rejects_stale_capacity_strictly_over_maximum_age() -> None:
    result, _ = _close_result()
    candidate = result.evaluation.close_candidate
    assert candidate is not None
    evaluated_at = result.evaluation.evaluated_at
    stale_observed_at = evaluated_at - timedelta(hours=1) - timedelta(microseconds=1)
    capacity = _capacity_for(
        candidate, open_quantity=Decimal("1000"), position_observed_at=stale_observed_at
    )

    _, risk_decision, intent = evaluate_ordinary_close_portfolio_and_risk(
        result,
        capacity=capacity,
        reservation_snapshot=_snapshot(candidate),
        allocation_policy=_HALF_ALLOCATION,
        risk_policy=_ONE_HOUR_RISK_POLICY,
        authority=ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED,
    )

    assert risk_decision.outcome is OrdinaryCloseRiskOutcome.REJECT
    assert risk_decision.reason is OrdinaryCloseRiskReason.CAPACITY_STALE
    assert intent is None


def test_risk_capacity_age_equal_to_maximum_is_still_eligible() -> None:
    result, _ = _close_result()
    candidate = result.evaluation.close_candidate
    assert candidate is not None
    evaluated_at = result.evaluation.evaluated_at
    equal_age_observed_at = evaluated_at - timedelta(hours=1)
    capacity = _capacity_for(
        candidate, open_quantity=Decimal("1000"), position_observed_at=equal_age_observed_at
    )

    _, risk_decision, intent = evaluate_ordinary_close_portfolio_and_risk(
        result,
        capacity=capacity,
        reservation_snapshot=_snapshot(candidate),
        allocation_policy=_HALF_ALLOCATION,
        risk_policy=_ONE_HOUR_RISK_POLICY,
        authority=ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED,
    )

    assert risk_decision.outcome is OrdinaryCloseRiskOutcome.APPROVE
    assert intent is not None


def test_risk_rejects_capacity_observed_in_the_future() -> None:
    result, _ = _close_result()
    candidate = result.evaluation.close_candidate
    assert candidate is not None
    evaluated_at = result.evaluation.evaluated_at
    future_observed_at = evaluated_at + timedelta(seconds=1)
    capacity = _capacity_for(
        candidate, open_quantity=Decimal("1000"), position_observed_at=future_observed_at
    )

    _, risk_decision, intent = evaluate_ordinary_close_portfolio_and_risk(
        result,
        capacity=capacity,
        reservation_snapshot=_snapshot(candidate),
        allocation_policy=_HALF_ALLOCATION,
        risk_policy=_ONE_HOUR_RISK_POLICY,
        authority=ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED,
    )

    assert risk_decision.outcome is OrdinaryCloseRiskOutcome.REJECT
    assert risk_decision.reason is OrdinaryCloseRiskReason.CAPACITY_IN_FUTURE
    assert intent is None


def test_risk_approve_produces_exactly_one_intent_with_opposite_side_and_allocated_quantity() -> (
    None
):
    result, _ = _close_result(side=Side.BUY)
    candidate = result.evaluation.close_candidate
    assert candidate is not None
    capacity = _capacity_for(candidate, open_quantity=Decimal("1000"))

    portfolio_decision, risk_decision, intent = evaluate_ordinary_close_portfolio_and_risk(
        result,
        capacity=capacity,
        reservation_snapshot=_snapshot(candidate),
        allocation_policy=_HALF_ALLOCATION,
        risk_policy=_ONE_HOUR_RISK_POLICY,
        authority=ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED,
    )

    assert risk_decision.outcome is OrdinaryCloseRiskOutcome.APPROVE
    assert intent is not None
    assert intent.side is Side.SELL
    assert candidate.existing_position_side is Side.BUY
    assert intent.quantity == portfolio_decision.allocated_quantity
    assert intent.portfolio_decision_id == portfolio_decision.portfolio_decision_id
    assert intent.risk_decision_id == risk_decision.risk_decision_id


def test_approved_close_intent_rejects_live_authority_independent_of_caller_checks() -> None:
    result, _ = _close_result()
    candidate = result.evaluation.close_candidate
    assert candidate is not None
    capacity = _capacity_for(candidate, open_quantity=Decimal("1000"))
    portfolio_decision, risk_decision, intent = evaluate_ordinary_close_portfolio_and_risk(
        result,
        capacity=capacity,
        reservation_snapshot=_snapshot(candidate),
        allocation_policy=_HALF_ALLOCATION,
        risk_policy=_ONE_HOUR_RISK_POLICY,
        authority=ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED,
    )
    assert intent is not None

    with pytest.raises(ValueError, match="SHADOW_NOT_SUBMITTED or PAPER"):
        ApprovedCloseIntent.create(
            candidate=candidate,
            portfolio_decision=portfolio_decision,
            risk_decision=risk_decision,
            capacity=capacity,
            authority=ExecutionAuthorityMode.LIVE,
            created_at=result.evaluation.evaluated_at,
        )

    with pytest.raises(ValueError, match="SHADOW_NOT_SUBMITTED or PAPER"):
        replace(intent, authority=ExecutionAuthorityMode.LIVE)


def test_intent_idempotency_key_is_deterministic_and_changes_with_quantity() -> None:
    result, _ = _close_result()
    candidate = result.evaluation.close_candidate
    assert candidate is not None
    capacity = _capacity_for(candidate, open_quantity=Decimal("1000"))
    snapshot = _snapshot(candidate)

    _, _, intent_a = evaluate_ordinary_close_portfolio_and_risk(
        result,
        capacity=capacity,
        reservation_snapshot=snapshot,
        allocation_policy=_HALF_ALLOCATION,
        risk_policy=_ONE_HOUR_RISK_POLICY,
        authority=ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED,
    )
    _, _, intent_b = evaluate_ordinary_close_portfolio_and_risk(
        result,
        capacity=capacity,
        reservation_snapshot=snapshot,
        allocation_policy=_HALF_ALLOCATION,
        risk_policy=_ONE_HOUR_RISK_POLICY,
        authority=ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED,
    )
    assert intent_a is not None and intent_b is not None
    assert intent_a.idempotency_key == intent_b.idempotency_key

    _, _, intent_c = evaluate_ordinary_close_portfolio_and_risk(
        result,
        capacity=capacity,
        reservation_snapshot=snapshot,
        allocation_policy=OrdinaryCloseAllocationPolicy("allocation-v1", Decimal("1")),
        risk_policy=_ONE_HOUR_RISK_POLICY,
        authority=ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED,
    )
    assert intent_c is not None
    assert intent_c.idempotency_key != intent_a.idempotency_key


def test_service_rejects_keep_evaluation() -> None:
    work = _work_item()
    result = OrdinaryPositionExitEvaluator(strategy_config()).evaluate(work)
    assert result.evaluation.outcome is PositionExitEvaluationOutcome.KEEP

    with pytest.raises(ValueError, match="CLOSE_CANDIDATE"):
        evaluate_ordinary_close_portfolio_and_risk(
            result,
            capacity=work.capacity,
            reservation_snapshot=OrdinaryCloseReservationSnapshot(
                position_id=work.capacity.position_id, entries=()
            ),
            allocation_policy=OrdinaryCloseAllocationPolicy("allocation-v1", Decimal("1")),
            risk_policy=_ONE_HOUR_RISK_POLICY,
            authority=ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED,
        )


def test_reservation_entry_rejects_comparison_overriding_intent_id_subclass() -> None:
    with pytest.raises(TypeError, match="exact str"):
        OrdinaryCloseReservationEntry(intent_id=_AlwaysEqualStr("intent-1"), quantity=Decimal("1"))


def test_approved_close_intent_is_structurally_distinct_from_liquidation_intent() -> None:
    assert not issubclass(ApprovedCloseIntent, ApprovedLiquidationIntent)
    assert not issubclass(ApprovedLiquidationIntent, ApprovedCloseIntent)
    close_fields = {field.name for field in dataclasses.fields(ApprovedCloseIntent)}
    liquidation_fields = {field.name for field in dataclasses.fields(ApprovedLiquidationIntent)}
    assert close_fields != liquidation_fields
    assert "close_candidate_id" not in liquidation_fields


# ---------------------------------------------------------------------------
# No-overclose defense: Risk independently re-derives available capacity rather
# than trusting a Portfolio decision's stored quantity
# ---------------------------------------------------------------------------


def test_risk_rejects_when_portfolio_decision_overcloses_true_available_capacity() -> None:
    result, _ = _close_result()
    candidate = result.evaluation.close_candidate
    assert candidate is not None
    capacity = _capacity_for(candidate, open_quantity=Decimal("1000"))
    # True available capacity is only 100 (1000 open - 900 already reserved).
    snapshot = _snapshot(candidate, ("prior-intent-1", Decimal("900")))

    # Internally self-consistent per OrdinaryClosePortfolioDecision's own ACCEPT
    # rules (allocated_quantity == target_quantity == available_before), but its
    # stored available_before/allocated_quantity were never actually re-derived
    # from this capacity/snapshot pair - standing in for a stale or forged
    # decision reused with a genuine, currently-matching capacity and snapshot.
    forged_payload = ordinary_close_module._portfolio_decision_payload(
        close_candidate_id=candidate.close_candidate_id,
        operational_evaluation_id=result.operational_evaluation_id,
        capacity_evidence_id=capacity.capacity_evidence_id,
        allocation_policy=_HALF_ALLOCATION,
        reservation_snapshot=snapshot,
        target_quantity=Decimal("1000"),
        available_before=Decimal("1000"),
        disposition=OrdinaryClosePortfolioDisposition.ACCEPT,
        allocated_quantity=Decimal("1000"),
    )
    forged_portfolio_decision = OrdinaryClosePortfolioDecision(
        "ordinary-close-portfolio-decision-" + digest(forged_payload),
        candidate.close_candidate_id,
        result.operational_evaluation_id,
        capacity.capacity_evidence_id,
        _HALF_ALLOCATION,
        snapshot,
        Decimal("1000"),
        Decimal("1000"),
        OrdinaryClosePortfolioDisposition.ACCEPT,
        Decimal("1000"),
    )

    risk_decision = OrdinaryCloseRiskDecision.create(
        portfolio_decision=forged_portfolio_decision,
        candidate=candidate,
        capacity=capacity,
        reservation_snapshot=snapshot,
        risk_policy=_ONE_HOUR_RISK_POLICY,
        evaluated_at=result.evaluation.evaluated_at,
    )

    assert risk_decision.outcome is OrdinaryCloseRiskOutcome.REJECT
    assert risk_decision.reason is OrdinaryCloseRiskReason.OVERCLOSE_QUANTITY


# ---------------------------------------------------------------------------
# Unusable Swap flag fall-through
# ---------------------------------------------------------------------------


def test_ordinary_exit_evaluator_disabled_swap_check_falls_through_to_holding_age() -> None:
    # swap=None makes the swap "unusable"; with the flag disabled the evaluator
    # must not close on that alone, and must skip the carry check entirely
    # (carry cannot be read from a swap that was never usable) rather than
    # raising or closing for the wrong reason.
    config = strategy_config(close_on_missing_or_stale_swap=False)
    work = _work_item(side=Side.BUY, score=0.0, swap=None, config=config)

    assert _reason_for(work, config) is None
