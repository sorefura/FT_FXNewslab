from dataclasses import fields, replace
from datetime import datetime

import pytest
from fx_core import PairScore, Probability, Signal
from swap_bot.models import Side, TradeCandidate
from swap_bot.strategy import (
    PRODUCTION_CANDIDATE_CONTRACT_VERSION,
    EntryEvaluationOutcome,
    EntrySkipReason,
    ProductionEntryEvaluation,
    ProductionEntryEvaluationInput,
    ProductionTradeCandidate,
)

from tests.strategy_contracts.factories import entry_input


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
