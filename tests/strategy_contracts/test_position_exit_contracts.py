import inspect
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fx_core import CurrencyPair
from swap_bot.models import ApprovedLiquidationIntent, Side
from swap_bot.strategy import (
    POSITION_CLOSE_CANDIDATE_CONTRACT_VERSION,
    PositionCloseCandidate,
    PositionCloseEvidenceLineage,
    PositionExitEvaluationOutcome,
    PositionExitKeepReason,
    PositionExitReason,
    ProductionPositionExitEvaluation,
    ProductionPositionExitEvaluationInput,
)

from tests.strategy_contracts.factories import (
    NOW,
    authorized_pair_signal,
    position_exit_context,
    position_exit_input,
    swap_evidence,
)


def _keep(
    evaluation_input: ProductionPositionExitEvaluationInput,
) -> ProductionPositionExitEvaluation:
    return ProductionPositionExitEvaluation.create_keep(
        evaluation_input,
        reason=PositionExitKeepReason.NO_EXIT_CONDITION,
    )


def _close(
    evaluation_input: ProductionPositionExitEvaluationInput,
    reason: PositionExitReason = PositionExitReason.SIGNAL_REVERSED,
) -> ProductionPositionExitEvaluation:
    return ProductionPositionExitEvaluation.create_close_candidate(
        evaluation_input,
        close_candidate_contract_version=POSITION_CLOSE_CANDIDATE_CONTRACT_VERSION,
        exit_reason=reason,
    )


def _evaluate(
    evaluation_input: ProductionPositionExitEvaluationInput, outcome: str
) -> ProductionPositionExitEvaluation:
    return _keep(evaluation_input) if outcome == "KEEP" else _close(evaluation_input)


def _input_with_change(name: str) -> ProductionPositionExitEvaluationInput:
    if name == "existing_position_side":
        return position_exit_input(existing_position_side=Side.SELL)
    if name == "position_evidence_id":
        return position_exit_input(
            context_changes={"position_evidence_id": "position-evidence-2"}
        )
    if name == "position_opened_at":
        return position_exit_input(
            context_changes={"position_opened_at": NOW - timedelta(days=29)}
        )
    if name == "position_observed_at":
        return position_exit_input(
            context_changes={
                "position_observed_at": NOW + timedelta(milliseconds=500)
            }
        )
    if name == "signal_id":
        return position_exit_input(
            authorized_pair_signal=authorized_pair_signal(signal_id="signal-pair-2")
        )
    if name == "authorization_id":
        return position_exit_input(
            authorized_pair_signal=authorized_pair_signal(
                authorization_id="signal-authorization-2"
            )
        )
    if name == "adoption_decision_id":
        return position_exit_input(
            authorized_pair_signal=authorized_pair_signal(
                adoption_decision_id="adoption-approval-2"
            )
        )
    if name == "swap_evidence_id":
        return position_exit_input(swap_evidence=swap_evidence(source_version="swap-v2"))
    if name == "signal_selection_checkpoint_id":
        return position_exit_input(
            context_changes={
                "signal_selection_checkpoint_id": "signal-selection-checkpoint-2"
            }
        )
    if name == "swap_selection_checkpoint_id":
        return position_exit_input(
            context_changes={
                "swap_selection_checkpoint_id": "swap-selection-checkpoint-2"
            }
        )
    if name == "expected_signal_specification_identity":
        return position_exit_input(
            context_changes={
                "expected_signal_specification_identity": "signal-specification-2"
            }
        )
    if name == "prior_adoption_decision_id":
        return position_exit_input(
            context_changes={"prior_adoption_decision_id": "adoption-approval-prior-2"}
        )
    if name == "adoption_state_evidence_id":
        return position_exit_input(
            context_changes={"adoption_state_evidence_id": "adoption-state-evidence-2"}
        )
    if name == "exit_input_policy_version":
        return position_exit_input(
            context_changes={"exit_input_policy_version": "exit-input-policy-v2"}
        )
    if name == "evaluated_at":
        return position_exit_input(evaluated_at=NOW + timedelta(seconds=3))
    raise AssertionError(f"unknown semantic change: {name}")


@pytest.mark.parametrize("outcome", ["KEEP", "CLOSE_CANDIDATE"])
def test_identical_exit_input_and_result_have_deterministic_identity(outcome: str) -> None:
    first = _evaluate(position_exit_input(), outcome)
    second = _evaluate(position_exit_input(), outcome)

    assert first.evaluation_id == second.evaluation_id
    assert first.identity_payload == second.identity_payload


@pytest.mark.parametrize("outcome", ["KEEP", "CLOSE_CANDIDATE"])
@pytest.mark.parametrize(
    "semantic_change",
    [
        "existing_position_side",
        "position_evidence_id",
        "position_opened_at",
        "position_observed_at",
        "signal_id",
        "authorization_id",
        "adoption_decision_id",
        "swap_evidence_id",
        "signal_selection_checkpoint_id",
        "swap_selection_checkpoint_id",
        "expected_signal_specification_identity",
        "prior_adoption_decision_id",
        "adoption_state_evidence_id",
        "exit_input_policy_version",
        "evaluated_at",
    ],
)
def test_every_exit_semantic_input_changes_identity(
    outcome: str, semantic_change: str
) -> None:
    baseline = _evaluate(position_exit_input(), outcome)
    changed = _evaluate(_input_with_change(semantic_change), outcome)

    assert changed.evaluation_id != baseline.evaluation_id


def test_exit_outcome_and_reason_change_identity() -> None:
    evaluation_input = position_exit_input()

    keep = _keep(evaluation_input)
    reversed_signal = _close(evaluation_input, PositionExitReason.SIGNAL_REVERSED)
    holding_age = _close(evaluation_input, PositionExitReason.MAXIMUM_HOLDING_AGE)

    assert len(
        {keep.evaluation_id, reversed_signal.evaluation_id, holding_age.evaluation_id}
    ) == 3


@pytest.mark.parametrize(
    ("context_change", "message"),
    [
        ({"position_opened_at": datetime(2026, 7, 1)}, "timezone-aware"),
        (
            {
                "position_opened_at": datetime(
                    2026, 7, 1, tzinfo=timezone(timedelta(hours=9))
                )
            },
            "must be UTC",
        ),
        (
            {"position_opened_at": NOW + timedelta(seconds=2)},
            "cannot be after position_observed_at",
        ),
    ],
)
def test_position_evidence_time_order_is_fail_closed(
    context_change: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        position_exit_context(**context_change)


def test_position_observation_cannot_postdate_evaluation() -> None:
    with pytest.raises(ValueError, match="position_observed_at"):
        position_exit_input(evaluated_at=NOW)


def test_current_signal_authorization_and_swap_cannot_come_from_the_future() -> None:
    with pytest.raises(ValueError, match="Signal created_at"):
        position_exit_input(
            authorized_pair_signal=authorized_pair_signal(
                signal_created_at=NOW + timedelta(seconds=3)
            )
        )
    with pytest.raises(ValueError, match="Signal authorized_at"):
        position_exit_input(
            authorized_pair_signal=authorized_pair_signal(
                authorized_at=NOW + timedelta(seconds=3)
            )
        )
    with pytest.raises(ValueError, match="swap received_at"):
        position_exit_input(
            swap_evidence=swap_evidence(received_at=NOW + timedelta(seconds=3))
        )


def test_keep_and_close_retain_the_exact_typed_input_lineage() -> None:
    evaluation_input = position_exit_input()
    keep = _keep(evaluation_input)
    close = _close(evaluation_input)
    lineage = evaluation_input.evidence_lineage

    assert keep.evidence_lineage == lineage
    assert close.evidence_lineage == lineage
    assert close.close_candidate is not None
    assert close.close_candidate.evidence_lineage == lineage
    assert lineage.position_evidence_id == "position-evidence-1"
    assert lineage.signal_id == authorized_pair_signal().signal.signal_id
    assert lineage.authorization_id == "signal-authorization-1"
    assert lineage.current_adoption_decision_id == "adoption-approval-1"
    assert lineage.swap_evidence_id == swap_evidence().swap_evidence_id
    assert (
        lineage.context.signal_selection_checkpoint_id
        == "signal-selection-checkpoint-1"
    )
    assert lineage.context.swap_selection_checkpoint_id == "swap-selection-checkpoint-1"


def _candidate_with_lineage(
    candidate: PositionCloseCandidate,
    lineage: PositionCloseEvidenceLineage,
) -> PositionCloseCandidate:
    return PositionCloseCandidate.create(
        close_candidate_contract_version=candidate.close_candidate_contract_version,
        strategy_id=candidate.strategy_id,
        strategy_version=candidate.strategy_version,
        strategy_config_identity=candidate.strategy_config_identity,
        strategy_evaluation_id=candidate.strategy_evaluation_id,
        position_id=candidate.position_id,
        pair=candidate.pair,
        existing_position_side=candidate.existing_position_side,
        exit_reason=candidate.exit_reason,
        evidence_lineage=lineage,
        created_at=candidate.created_at,
    )


@pytest.mark.parametrize("forgery", ["position", "signal", "swap"])
def test_exit_evaluation_rejects_a_close_candidate_from_other_lineage(
    forgery: str,
) -> None:
    evaluation = _close(position_exit_input())
    candidate = evaluation.close_candidate
    assert candidate is not None
    if forgery == "position":
        forged = replace(
            candidate.evidence_lineage,
            context=replace(
                candidate.evidence_lineage.context,
                position_evidence_id="position-evidence-forged",
            ),
        )
    elif forgery == "signal":
        forged = replace(
            candidate.evidence_lineage,
            authorized_pair_signal=authorized_pair_signal(signal_id="signal-forged"),
        )
    else:
        forged = replace(
            candidate.evidence_lineage,
            swap_evidence=swap_evidence(source_version="swap-forged"),
        )
    forged_candidate = _candidate_with_lineage(candidate, forged)

    with pytest.raises(ValueError, match="lineage does not match"):
        replace(evaluation, close_candidate=forged_candidate)


def test_close_candidate_rejects_forged_id_and_caller_controlled_evidence_api_is_gone() -> None:
    evaluation = _close(position_exit_input())
    candidate = evaluation.close_candidate
    assert candidate is not None

    with pytest.raises(ValueError, match="does not match"):
        replace(candidate, close_candidate_id="position-close-candidate-forged")
    assert "evidence_ids" not in inspect.signature(
        ProductionPositionExitEvaluation.create_close_candidate
    ).parameters
    assert "evidence_ids" not in {field.name for field in fields(PositionCloseCandidate)}


def test_signal_reversed_requires_current_authorized_pair_signal() -> None:
    assert _close(position_exit_input()).close_candidate is not None
    with pytest.raises(ValueError, match="requires current AuthorizedSignal"):
        _close(position_exit_input(authorized_pair_signal=None))
    with pytest.raises(ValueError, match="another Pair"):
        position_exit_input(
            authorized_pair_signal=authorized_pair_signal(
                pair=CurrencyPair.parse("EUR_JPY")
            )
        )


def test_carry_close_requires_current_swap_evidence_for_the_position_pair() -> None:
    assert (
        _close(
            position_exit_input(), PositionExitReason.CARRY_NO_LONGER_POSITIVE
        ).close_candidate
        is not None
    )
    with pytest.raises(ValueError, match="requires current OperationalSwapEvidence"):
        _close(
            position_exit_input(swap_evidence=None),
            PositionExitReason.CARRY_NO_LONGER_POSITIVE,
        )
    with pytest.raises(ValueError, match="another Pair"):
        position_exit_input(
            swap_evidence=swap_evidence(pair=CurrencyPair.parse("EUR_JPY"))
        )


def test_holding_age_close_retains_opened_at_and_rejects_future_position_evidence() -> None:
    evaluation = _close(
        position_exit_input(), PositionExitReason.MAXIMUM_HOLDING_AGE
    )
    assert (
        evaluation.evidence_lineage.context.position_opened_at
        == NOW - timedelta(days=30)
    )
    with pytest.raises(ValueError):
        position_exit_input(
            context_changes={
                "position_opened_at": NOW + timedelta(seconds=3),
                "position_observed_at": NOW + timedelta(seconds=3),
            }
        )


def test_adoption_close_can_omit_current_market_authority_but_not_adoption_evidence() -> None:
    evaluation = _close(
        position_exit_input(authorized_pair_signal=None, swap_evidence=None),
        PositionExitReason.ADOPTION_NO_LONGER_ACTIVE,
    )
    assert evaluation.close_candidate is not None
    for field_name in ("adoption_state_evidence_id", "prior_adoption_decision_id"):
        with pytest.raises(ValueError, match="must not be blank"):
            position_exit_input(context_changes={field_name: " "})


def test_missing_signal_close_requires_the_expected_specification_checkpoint() -> None:
    evaluation = _close(
        position_exit_input(authorized_pair_signal=None),
        PositionExitReason.REQUIRED_SIGNAL_MISSING_OR_STALE,
    )
    assert evaluation.close_candidate is not None
    for field_name in (
        "signal_selection_checkpoint_id",
        "expected_signal_specification_identity",
    ):
        with pytest.raises(ValueError, match="must not be blank"):
            position_exit_input(context_changes={field_name: ""})


def test_missing_swap_close_requires_the_swap_selection_checkpoint() -> None:
    evaluation = _close(
        position_exit_input(swap_evidence=None),
        PositionExitReason.REQUIRED_SWAP_MISSING_OR_STALE,
    )
    assert evaluation.close_candidate is not None
    with pytest.raises(ValueError, match="must not be blank"):
        position_exit_input(context_changes={"swap_selection_checkpoint_id": " "})


def test_close_candidate_remains_quantity_free_reduce_only_strategy_output() -> None:
    evaluation = _close(position_exit_input())
    candidate = evaluation.close_candidate
    assert candidate is not None
    names = {field.name for field in fields(PositionCloseCandidate)}

    assert {"quantity", "requested_quantity", "action", "reduce_only"}.isdisjoint(names)
    assert candidate.reduce_only is True
    assert candidate.close_side is Side.SELL
    assert not issubclass(PositionCloseCandidate, ApprovedLiquidationIntent)
    assert evaluation.outcome is PositionExitEvaluationOutcome.CLOSE_CANDIDATE


def test_position_exit_fixture_uses_utc_timestamps() -> None:
    context = position_exit_context()
    assert context.position_opened_at.utcoffset() == UTC.utcoffset(NOW)
