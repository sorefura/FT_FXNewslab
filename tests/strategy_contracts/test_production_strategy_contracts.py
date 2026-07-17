from dataclasses import fields, replace
from datetime import datetime, timedelta

import pytest
from fx_core import PairScore, Probability, Signal
from swap_bot.models import ApprovedLiquidationIntent, PositionId, Side, TradeCandidate
from swap_bot.strategy import (
    POSITION_CLOSE_CANDIDATE_CONTRACT_VERSION,
    PRODUCTION_CANDIDATE_CONTRACT_VERSION,
    EntryEvaluationOutcome,
    EntrySkipReason,
    PositionCloseCandidate,
    PositionExitEvaluationOutcome,
    PositionExitReason,
    ProductionEntryEvaluation,
    ProductionEntryEvaluationInput,
    ProductionPositionExitEvaluation,
    ProductionPositionExitEvaluationInput,
    ProductionTradeCandidate,
)

from tests.strategy_contracts.factories import NOW, PAIR, entry_input, swap_evidence


def candidate_evaluation() -> ProductionEntryEvaluation:
    return ProductionEntryEvaluation.create_candidate(
        entry_input(),
        candidate_contract_version=PRODUCTION_CANDIDATE_CONTRACT_VERSION,
        side=Side.BUY,
    )


def test_entry_input_accepts_authorized_signal_not_raw_signal() -> None:
    valid = entry_input()
    with pytest.raises(TypeError, match="AuthorizedSignal"):
        ProductionEntryEvaluationInput(
            authorized_pair_signal=valid.authorized_pair_signal.signal,  # type: ignore[arg-type]
            approved_strategy_config_identity=valid.approved_strategy_config_identity,
            swap_evidence=valid.swap_evidence,
            evaluated_at=valid.evaluated_at,
        )
    assert not isinstance(valid.authorized_pair_signal, Signal)


def test_entry_input_and_result_require_utc_evaluation_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        entry_input(evaluated_at=datetime(2026, 7, 17, 3, 0))


def test_candidate_result_keeps_exact_signal_authorization_swap_and_config_lineage() -> None:
    evaluation_input = entry_input()
    result = ProductionEntryEvaluation.create_candidate(
        evaluation_input,
        candidate_contract_version=PRODUCTION_CANDIDATE_CONTRACT_VERSION,
        side=Side.BUY,
    )

    assert result.outcome is EntryEvaluationOutcome.CANDIDATE
    assert result.signal_id == evaluation_input.authorized_pair_signal.signal.signal_id
    assert (
        result.authorization_id
        == evaluation_input.authorized_pair_signal.authorization.authorization_id
    )
    assert (
        result.adoption_decision_id
        == evaluation_input.authorized_pair_signal.authorization.adoption_decision_id
    )
    assert result.swap_evidence_id == evaluation_input.swap_evidence.swap_evidence_id
    assert (
        result.strategy_config_identity
        == evaluation_input.approved_strategy_config_identity
    )


def test_candidate_and_skip_evaluation_identity_is_deterministic() -> None:
    first = candidate_evaluation()
    second = candidate_evaluation()
    skip_one = ProductionEntryEvaluation.create_skip(
        entry_input(), reason=EntrySkipReason.CARRY_NOT_POSITIVE
    )
    skip_two = ProductionEntryEvaluation.create_skip(
        entry_input(), reason=EntrySkipReason.CARRY_NOT_POSITIVE
    )

    assert first.evaluation_id == second.evaluation_id
    assert first.candidate is not None and second.candidate is not None
    assert first.candidate.candidate_id == second.candidate.candidate_id
    assert skip_one.evaluation_id == skip_two.evaluation_id


def test_entry_outcome_requires_exactly_candidate_or_structured_skip() -> None:
    candidate = candidate_evaluation()
    skip = ProductionEntryEvaluation.create_skip(
        entry_input(), reason=EntrySkipReason.SIGNAL_STALE
    )

    with pytest.raises(ValueError, match="requires candidate"):
        replace(candidate, candidate=None)
    with pytest.raises(ValueError, match="prohibits skip_reason"):
        replace(candidate, skip_reason=EntrySkipReason.SIGNAL_STALE)
    with pytest.raises(ValueError, match="requires skip_reason"):
        replace(skip, skip_reason=None)
    with pytest.raises(ValueError, match="prohibits candidate"):
        replace(skip, candidate=candidate.candidate)


def test_entry_evaluation_rejects_forged_identity() -> None:
    with pytest.raises(ValueError, match="does not match"):
        replace(candidate_evaluation(), evaluation_id="strategy-entry-evaluation-forged")


def test_production_candidate_preserves_pair_score_and_confidence_without_clamp() -> None:
    candidate = candidate_evaluation().candidate
    assert candidate is not None

    assert candidate.pair_score == PairScore(1.75)
    assert candidate.confidence == Probability(0.8)
    assert candidate.pair_score.value > 1.0
    assert isinstance(candidate.pair_score, PairScore)
    assert isinstance(candidate.confidence, Probability)


def test_production_candidate_id_rejects_forgery() -> None:
    candidate = candidate_evaluation().candidate
    assert candidate is not None
    with pytest.raises(ValueError, match="does not match"):
        replace(candidate, candidate_id="production-candidate-forged")


def test_production_candidate_is_separate_and_has_no_allocation_or_broker_fields() -> None:
    names = {field.name for field in fields(ProductionTradeCandidate)}

    assert ProductionTradeCandidate is not TradeCandidate
    assert {
        "quantity",
        "leverage",
        "reference_price",
        "margin",
        "broker_parameters",
        "score",
    }.isdisjoint(names)
    assert {"pair_score", "confidence"}.issubset(names)


def exit_input_without_current_authority() -> ProductionPositionExitEvaluationInput:
    return ProductionPositionExitEvaluationInput(
        strategy_id="news-filtered-carry",
        strategy_version="strategy-v1",
        approved_strategy_config_identity="strategy-config-1",
        position_id=PositionId("position-1"),
        pair=PAIR,
        existing_position_side=Side.BUY,
        authorized_pair_signal=None,
        swap_evidence=None,
        evaluated_at=NOW + timedelta(seconds=2),
    )


def test_safety_close_can_exist_without_current_signal_authorization_or_swap() -> None:
    result = ProductionPositionExitEvaluation.create_close_candidate(
        exit_input_without_current_authority(),
        close_candidate_contract_version=POSITION_CLOSE_CANDIDATE_CONTRACT_VERSION,
        exit_reason=PositionExitReason.ADOPTION_NO_LONGER_ACTIVE,
        evidence_ids=("adoption-approval-1",),
    )

    assert result.outcome is PositionExitEvaluationOutcome.CLOSE_CANDIDATE
    assert result.close_candidate is not None
    assert result.close_candidate.exit_reason is PositionExitReason.ADOPTION_NO_LONGER_ACTIVE


def test_close_candidate_is_deterministic_reduce_only_and_derives_close_side() -> None:
    kwargs = {
        "close_candidate_contract_version": POSITION_CLOSE_CANDIDATE_CONTRACT_VERSION,
        "strategy_id": "news-filtered-carry",
        "strategy_version": "strategy-v1",
        "strategy_config_identity": "strategy-config-1",
        "strategy_evaluation_id": "exit-evaluation-1",
        "position_id": PositionId("position-1"),
        "pair": PAIR,
        "existing_position_side": Side.BUY,
        "exit_reason": PositionExitReason.SIGNAL_REVERSED,
        "evidence_ids": ("signal-1", "authorization-1", swap_evidence().swap_evidence_id),
        "created_at": NOW,
    }
    first = PositionCloseCandidate.create(**kwargs)  # type: ignore[arg-type]
    second = PositionCloseCandidate.create(**kwargs)  # type: ignore[arg-type]

    assert first.close_candidate_id == second.close_candidate_id
    assert first.reduce_only is True
    assert first.close_side is Side.SELL


def test_close_candidate_has_no_quantity_or_action_and_is_not_risk_liquidation() -> None:
    names = {field.name for field in fields(PositionCloseCandidate)}

    assert {"quantity", "requested_quantity", "action", "reduce_only"}.isdisjoint(names)
    assert not issubclass(PositionCloseCandidate, ApprovedLiquidationIntent)


def test_close_candidate_requires_typed_reason_and_rejects_forged_id() -> None:
    result = ProductionPositionExitEvaluation.create_close_candidate(
        exit_input_without_current_authority(),
        close_candidate_contract_version=POSITION_CLOSE_CANDIDATE_CONTRACT_VERSION,
        exit_reason=PositionExitReason.REQUIRED_SIGNAL_MISSING_OR_STALE,
        evidence_ids=(),
    )
    candidate = result.close_candidate
    assert candidate is not None

    with pytest.raises(TypeError, match="PositionExitReason"):
        replace(candidate, exit_reason="CLOSE")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="does not match"):
        replace(candidate, close_candidate_id="position-close-candidate-forged")
