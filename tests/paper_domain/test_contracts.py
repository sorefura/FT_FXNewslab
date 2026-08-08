import dataclasses
import decimal
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock

import pytest
from fx_core import CurrencyPair
from swap_bot.execution_authority import ExecutionAuthorityMode
from swap_bot.models import (
    ApprovedExecutionIntent,
    ApprovedLiquidationIntent,
    CandidateId,
    ExecutionIntentId,
    PositionId,
    RiskDecisionId,
    Side,
)
from swap_bot.paper import (
    NO_MARKET_TERMINAL_REASON_CODE,
    PAPER_ATTEMPT_DISPOSITION_PENDING_NO_ELIGIBLE_MARKET,
    PAPER_EXACT_ARITHMETIC_V1,
    PAPER_QUOTIENT_ARITHMETIC_V1,
    FillEvaluationAttempt,
    FillEvaluationPlan,
    FillEvaluationStep,
    PaperAttemptDiagnosticCode,
    PaperFill,
    PaperFillPolicy,
    PaperIntentKind,
    PaperMarketObservation,
    PaperMarketObservationSelection,
    PaperNoMarketOutcome,
    PaperOrder,
    PaperOrderEvent,
    PaperOrderIntentLineage,
    PaperOrderState,
    PaperPartialFillMode,
    PaperStepResolutionVariant,
    emergency_liquidation_source_intent_payload,
    entry_source_intent_payload,
    opposite_side,
    ordinary_close_source_intent_payload,
    project_paper_order_state,
    require_legal_transition,
)
from swap_bot.strategy import ApprovedCloseIntent, evaluate_ordinary_close_portfolio_and_risk

from tests.strategy_contracts.factories import NOW, PAIR
from tests.strategy_contracts.test_ordinary_close import (
    _HALF_ALLOCATION,
    _ONE_HOUR_RISK_POLICY,
    _capacity_for,
    _close_result,
    _snapshot,
)


class _AlwaysEqualStr(str):
    def __eq__(self, other: object) -> bool:
        return True

    def __hash__(self) -> int:
        return hash(str(self))


class _ForgedExecutionIntentId(ExecutionIntentId):
    pass


class _ForgedCurrencyPair(CurrencyPair):
    pass


class _ForgedPaperOrderIntentLineage(PaperOrderIntentLineage):
    pass


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


def _execution_intent(**overrides: object) -> ApprovedExecutionIntent:
    values: dict[str, object] = {
        "intent_id": ExecutionIntentId("execution-intent-1"),
        "candidate_id": CandidateId("candidate-1"),
        "risk_decision_id": RiskDecisionId("risk-decision-1"),
        "pair": PAIR,
        "side": Side.BUY,
        "quantity": Decimal("1000"),
        "idempotency_key": "execution-idem-1",
        "created_at": NOW,
    }
    values.update(overrides)
    return ApprovedExecutionIntent(**values)  # type: ignore[arg-type]


def _liquidation_intent(**overrides: object) -> ApprovedLiquidationIntent:
    values: dict[str, object] = {
        "intent_id": ExecutionIntentId("liquidation-intent-1"),
        "risk_decision_id": RiskDecisionId("risk-decision-2"),
        "position_id": PositionId("position-1"),
        "pair": PAIR,
        "quantity": Decimal("500"),
        "idempotency_key": "liquidation-idem-1",
        "created_at": NOW,
    }
    values.update(overrides)
    return ApprovedLiquidationIntent(**values)  # type: ignore[arg-type]


def _close_intent(
    *,
    authority: ExecutionAuthorityMode = ExecutionAuthorityMode.PAPER,
    open_quantity: Decimal = Decimal("1000"),
) -> ApprovedCloseIntent:
    result, _ = _close_result(side=Side.BUY)
    candidate = result.evaluation.close_candidate
    assert candidate is not None
    capacity = _capacity_for(candidate, open_quantity=open_quantity)
    _, _, intent = evaluate_ordinary_close_portfolio_and_risk(
        result,
        capacity=capacity,
        reservation_snapshot=_snapshot(candidate),
        allocation_policy=_HALF_ALLOCATION,
        risk_policy=_ONE_HOUR_RISK_POLICY,
        authority=authority,
    )
    assert intent is not None
    return intent


def _market_observation(**overrides: object) -> PaperMarketObservation:
    values: dict[str, object] = {
        "pair": PAIR,
        "bid": Decimal("150.000"),
        "ask": Decimal("150.005"),
        "provider_observed_at": NOW - timedelta(seconds=1),
        "received_at": NOW,
        "source": "recorded-provider",
        "source_version": "recorded-provider-v1",
    }
    values.update(overrides)
    return PaperMarketObservation.create(**values)  # type: ignore[arg-type]


def _fill_policy(**overrides: object) -> PaperFillPolicy:
    values: dict[str, object] = {
        "policy_version": "paper-fill-policy-test-v1",
        "market_selection_policy_version": "market-selection-v1",
        "fill_model_version": "fill-model-v1",
        "step_schedule_policy_version": "step-schedule-v1",
        "maximum_market_age": timedelta(minutes=5),
        "step_window_duration": timedelta(minutes=1),
        "step_gap": timedelta(seconds=1),
        "maximum_steps": 3,
        "partial_fill_mode": PaperPartialFillMode.FULL_REMAINING,
        "partial_fill_fraction": None,
        "slippage_basis_points": Decimal("5"),
        "no_fill_terminal_order_state": PaperOrderState.REJECTED,
        "incomplete_terminal_order_state": PaperOrderState.CANCELLED,
    }
    values.update(overrides)
    return PaperFillPolicy.create(**values)  # type: ignore[arg-type]


def _order(**overrides: object) -> PaperOrder:
    lineage = overrides.pop("intent_lineage", None) or PaperOrderIntentLineage.for_entry(
        _execution_intent()
    )
    policy = overrides.pop("fill_policy_id", None) or _fill_policy().paper_fill_policy_id
    values: dict[str, object] = {
        "paper_account_id": "paper-account-1",
        "intent_lineage": lineage,
        "pair": PAIR,
        "side": Side.BUY,
        "original_quantity": Decimal("1000"),
        "authority": ExecutionAuthorityMode.PAPER,
        "fill_policy_id": policy,
        "intent_created_at": NOW,
        "created_at": NOW,
    }
    values.update(overrides)
    return PaperOrder.create(**values)  # type: ignore[arg-type]


def _order_event(
    ordinal: int, state: PaperOrderState, *, paper_order_id: str = "paper-order-1"
) -> PaperOrderEvent:
    return PaperOrderEvent.create(
        paper_order_id=paper_order_id,
        event_ordinal=ordinal,
        state=state,
        source_evidence_kind="test-evidence",
        source_evidence_id=None,
        appended_at=NOW + timedelta(seconds=ordinal),
    )


def _plan(**overrides: object) -> FillEvaluationPlan:
    lineage = overrides.pop("intent_lineage", None) or PaperOrderIntentLineage.for_entry(
        _execution_intent()
    )
    values: dict[str, object] = {
        "paper_order_id": "paper-order-1",
        "intent_lineage": lineage,
        "pair": PAIR,
        "side": Side.BUY,
        "original_quantity": Decimal("1000"),
        "fill_policy_id": "paper-fill-policy-1",
        "intent_created_at": NOW,
        "maximum_steps": 3,
        "plan_expiry_at": NOW + timedelta(minutes=10),
        "created_at": NOW,
    }
    values.update(overrides)
    return FillEvaluationPlan.create(**values)  # type: ignore[arg-type]


def _step(**overrides: object) -> FillEvaluationStep:
    values: dict[str, object] = {
        "fill_evaluation_plan_id": "fill-evaluation-plan-1",
        "ordinal": 0,
        "evaluation_window_start_at": NOW,
        "evaluation_due_at": NOW + timedelta(minutes=1),
        "remaining_quantity_before": Decimal("1000"),
        "fill_policy_id": "paper-fill-policy-1",
        "created_at": NOW,
    }
    values.update(overrides)
    return FillEvaluationStep.create(**values)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# PaperIntentKind
# ---------------------------------------------------------------------------


def test_paper_intent_kind_has_exactly_three_values() -> None:
    assert {member.value for member in PaperIntentKind} == {
        "ENTRY",
        "ORDINARY_CLOSE",
        "EMERGENCY_LIQUIDATION",
    }


# ---------------------------------------------------------------------------
# Source-intent payload builders
# ---------------------------------------------------------------------------


def test_entry_payload_has_frozen_field_set_and_transforms() -> None:
    intent = _execution_intent()
    payload = entry_source_intent_payload(intent)
    assert payload == {
        "intent_id": "execution-intent-1",
        "candidate_id": "candidate-1",
        "risk_decision_id": "risk-decision-1",
        "pair": "USD_JPY",
        "side": "BUY",
        "quantity": "1000",
        "idempotency_key": "execution-idem-1",
        "created_at": NOW.isoformat(),
    }


@pytest.mark.parametrize(
    "builder",
    [entry_source_intent_payload, ordinary_close_source_intent_payload,
     emergency_liquidation_source_intent_payload],
)
def test_payload_builders_reject_non_intent_objects(builder: object) -> None:
    with pytest.raises(TypeError):
        builder(Mock())  # type: ignore[operator]
    with pytest.raises(TypeError):
        builder("not-an-intent")  # type: ignore[operator]


def test_entry_payload_rejects_ordinary_close_and_liquidation_intents() -> None:
    with pytest.raises(TypeError, match="ApprovedExecutionIntent"):
        entry_source_intent_payload(_close_intent())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ApprovedExecutionIntent"):
        entry_source_intent_payload(_liquidation_intent())  # type: ignore[arg-type]


def test_ordinary_close_payload_rejects_entry_and_liquidation_intents() -> None:
    with pytest.raises(TypeError, match="ApprovedCloseIntent"):
        ordinary_close_source_intent_payload(_execution_intent())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ApprovedCloseIntent"):
        ordinary_close_source_intent_payload(_liquidation_intent())  # type: ignore[arg-type]


def test_emergency_liquidation_payload_rejects_entry_and_close_intents() -> None:
    with pytest.raises(TypeError, match="ApprovedLiquidationIntent"):
        emergency_liquidation_source_intent_payload(_execution_intent())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ApprovedLiquidationIntent"):
        emergency_liquidation_source_intent_payload(_close_intent())  # type: ignore[arg-type]


def test_entry_payload_rejects_forged_execution_intent_id_subclass() -> None:
    forged = dataclasses.replace(
        _execution_intent(), intent_id=_ForgedExecutionIntentId("execution-intent-1")
    )
    with pytest.raises(TypeError, match="exact ExecutionIntentId"):
        entry_source_intent_payload(forged)


def test_entry_payload_rejects_comparison_overriding_idempotency_key_subclass() -> None:
    forged = dataclasses.replace(
        _execution_intent(), idempotency_key=_AlwaysEqualStr("execution-idem-1")
    )
    with pytest.raises(TypeError, match="exact str"):
        entry_source_intent_payload(forged)


def test_ordinary_close_payload_matches_intent_fields() -> None:
    intent = _close_intent()
    payload = ordinary_close_source_intent_payload(intent)
    assert payload["position_id"] == intent.position_id.value
    assert payload["pair"] == intent.pair.symbol
    assert payload["side"] == intent.side.value
    assert payload["authority"] == intent.authority.value
    assert payload["idempotency_key"] == intent.idempotency_key


def test_emergency_liquidation_payload_matches_intent_fields() -> None:
    intent = _liquidation_intent()
    payload = emergency_liquidation_source_intent_payload(intent)
    assert payload == {
        "intent_id": "liquidation-intent-1",
        "risk_decision_id": "risk-decision-2",
        "position_id": "position-1",
        "pair": "USD_JPY",
        "quantity": "500",
        "idempotency_key": "liquidation-idem-1",
        "created_at": NOW.isoformat(),
    }


def test_emergency_liquidation_payload_rejects_forged_position_id_subclass() -> None:
    class _ForgedPositionId(PositionId):
        pass

    forged = dataclasses.replace(
        _liquidation_intent(), position_id=_ForgedPositionId("position-1")
    )
    with pytest.raises(TypeError, match="exact PositionId"):
        emergency_liquidation_source_intent_payload(forged)


def test_opposite_side_flips_and_rejects_non_side() -> None:
    assert opposite_side(Side.BUY) is Side.SELL
    assert opposite_side(Side.SELL) is Side.BUY
    with pytest.raises(TypeError, match="exact Side"):
        opposite_side("BUY")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# PaperOrderIntentLineage
# ---------------------------------------------------------------------------


def test_entry_lineage_derives_deterministic_paper_position_id() -> None:
    intent = _execution_intent()
    lineage_a = PaperOrderIntentLineage.for_entry(intent)
    lineage_b = PaperOrderIntentLineage.for_entry(_execution_intent())
    assert lineage_a == lineage_b
    assert lineage_a.intent_kind is PaperIntentKind.ENTRY
    assert lineage_a.source_intent_id == "execution-intent-1"
    assert lineage_a.paper_position_id == "paper-position-" + lineage_a.source_intent_content_digest


def test_different_entry_intents_produce_different_paper_position_ids() -> None:
    lineage_a = PaperOrderIntentLineage.for_entry(_execution_intent())
    lineage_b = PaperOrderIntentLineage.for_entry(_execution_intent(quantity=Decimal("2000")))
    assert lineage_a.paper_position_id != lineage_b.paper_position_id
    assert lineage_a.source_intent_content_digest != lineage_b.source_intent_content_digest


def test_ordinary_close_lineage_uses_idempotency_key_as_source_intent_id() -> None:
    intent = _close_intent()
    lineage = PaperOrderIntentLineage.for_ordinary_close(intent)
    assert lineage.intent_kind is PaperIntentKind.ORDINARY_CLOSE
    assert lineage.source_intent_id == intent.idempotency_key
    assert lineage.source_intent_idempotency_key == intent.idempotency_key
    assert lineage.paper_position_id == intent.position_id.value


def test_emergency_liquidation_lineage_requires_existing_position_side() -> None:
    intent = _liquidation_intent()
    with pytest.raises(TypeError):
        PaperOrderIntentLineage.for_emergency_liquidation(intent)  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="exact Side"):
        PaperOrderIntentLineage.for_emergency_liquidation(
            intent, existing_position_side="BUY"  # type: ignore[arg-type]
        )
    lineage = PaperOrderIntentLineage.for_emergency_liquidation(
        intent, existing_position_side=Side.BUY
    )
    assert lineage.intent_kind is PaperIntentKind.EMERGENCY_LIQUIDATION
    assert lineage.paper_position_id == intent.position_id.value
    assert lineage.source_intent_id == intent.intent_id.value


def test_lineage_rejects_mismatched_entry_paper_position_id() -> None:
    lineage = PaperOrderIntentLineage.for_entry(_execution_intent())
    with pytest.raises(ValueError, match="derive from its content digest"):
        dataclasses.replace(lineage, paper_position_id="paper-position-wrong")


def test_lineage_rejects_ordinary_close_source_intent_id_mismatch() -> None:
    lineage = PaperOrderIntentLineage.for_ordinary_close(_close_intent())
    with pytest.raises(ValueError, match="must be its idempotency_key"):
        dataclasses.replace(lineage, source_intent_id="some-other-id")


def test_lineage_rejects_comparison_overriding_str_subclass_field() -> None:
    lineage = PaperOrderIntentLineage.for_entry(_execution_intent())
    with pytest.raises(TypeError, match="exact str"):
        dataclasses.replace(lineage, source_intent_id=_AlwaysEqualStr(lineage.source_intent_id))


# ---------------------------------------------------------------------------
# PaperMarketObservation
# ---------------------------------------------------------------------------


def test_market_observation_round_trips_and_is_content_addressed() -> None:
    observation = _market_observation()
    assert observation.market_observation_id.startswith("paper-market-")
    rebuilt = _market_observation()
    assert observation.market_observation_id == rebuilt.market_observation_id
    different = _market_observation(bid=Decimal("149.999"))
    assert observation.market_observation_id != different.market_observation_id


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("bid", Decimal("0"), "positive finite"),
        ("bid", Decimal("-1"), "positive finite"),
        ("ask", Decimal("NaN"), "positive finite"),
        ("bid", 150.0, "positive finite"),
    ],
)
def test_market_observation_rejects_invalid_prices(field: str, value: object, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _market_observation(**{field: value})


def test_market_observation_rejects_bid_greater_than_ask() -> None:
    with pytest.raises(ValueError, match="bid must not exceed ask"):
        _market_observation(bid=Decimal("150.010"), ask=Decimal("150.005"))


def test_market_observation_rejects_naive_and_non_utc_timestamps() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _market_observation(received_at=datetime(2026, 7, 17, 3, 0))
    with pytest.raises(ValueError, match="UTC"):
        _market_observation(
            provider_observed_at=NOW.replace(tzinfo=timezone(timedelta(hours=9)))
        )


def test_market_observation_rejects_provider_after_received() -> None:
    with pytest.raises(ValueError, match="must not be after received_at"):
        _market_observation(
            provider_observed_at=NOW + timedelta(seconds=1), received_at=NOW
        )


def test_market_observation_rejects_blank_source_and_version() -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        _market_observation(source="   ")
    with pytest.raises(ValueError, match="must not be blank"):
        _market_observation(source_version="")


def test_market_observation_rejects_forged_pair_subclass() -> None:
    with pytest.raises(TypeError, match="exact CurrencyPair"):
        _market_observation(pair=_ForgedCurrencyPair(PAIR.base, PAIR.quote))


def test_market_observation_rejects_tampered_id() -> None:
    observation = _market_observation()
    with pytest.raises(ValueError, match="does not match content"):
        dataclasses.replace(observation, market_observation_id="paper-market-forged")


# ---------------------------------------------------------------------------
# PaperFillPolicy
# ---------------------------------------------------------------------------


def test_fill_policy_round_trips_and_is_content_addressed() -> None:
    policy = _fill_policy()
    assert policy.paper_fill_policy_id.startswith("paper-fill-policy-")
    same = _fill_policy()
    assert policy.paper_fill_policy_id == same.paper_fill_policy_id
    different = _fill_policy(maximum_steps=5)
    assert policy.paper_fill_policy_id != different.paper_fill_policy_id


def test_fill_policy_full_remaining_rejects_fraction() -> None:
    with pytest.raises(ValueError, match="must not carry a partial_fill_fraction"):
        _fill_policy(
            partial_fill_mode=PaperPartialFillMode.FULL_REMAINING,
            partial_fill_fraction=Decimal("0.5"),
        )


@pytest.mark.parametrize("fraction", [None, Decimal("0"), Decimal("1.5"), Decimal("-0.1")])
def test_fill_policy_fraction_of_remaining_requires_fraction_in_bounds(
    fraction: Decimal | None,
) -> None:
    with pytest.raises(ValueError, match="partial_fill_fraction in"):
        _fill_policy(
            partial_fill_mode=PaperPartialFillMode.FRACTION_OF_REMAINING,
            partial_fill_fraction=fraction,
        )


def test_fill_policy_accepts_fraction_of_remaining_at_exact_one() -> None:
    policy = _fill_policy(
        partial_fill_mode=PaperPartialFillMode.FRACTION_OF_REMAINING,
        partial_fill_fraction=Decimal("1"),
    )
    assert policy.partial_fill_fraction == Decimal("1")


@pytest.mark.parametrize("state", [PaperOrderState.OPEN, PaperOrderState.PARTIALLY_FILLED,
                                    PaperOrderState.FILLED, PaperOrderState.ACCEPTED])
def test_fill_policy_rejects_unsupported_no_fill_terminal_state(state: PaperOrderState) -> None:
    with pytest.raises(ValueError, match="no_fill_terminal_order_state"):
        _fill_policy(no_fill_terminal_order_state=state)


@pytest.mark.parametrize("state", [PaperOrderState.REJECTED, PaperOrderState.OPEN,
                                    PaperOrderState.FILLED, PaperOrderState.ACCEPTED])
def test_fill_policy_rejects_unsupported_incomplete_terminal_state(state: PaperOrderState) -> None:
    with pytest.raises(ValueError, match="incomplete_terminal_order_state"):
        _fill_policy(incomplete_terminal_order_state=state)


@pytest.mark.parametrize("field", ["maximum_market_age", "step_window_duration", "step_gap"])
def test_fill_policy_rejects_nonpositive_durations(field: str) -> None:
    with pytest.raises(ValueError, match="positive exact timedelta"):
        _fill_policy(**{field: timedelta(0)})


@pytest.mark.parametrize("value", [0, -1, True])
def test_fill_policy_rejects_invalid_maximum_steps(value: object) -> None:
    with pytest.raises(ValueError, match="maximum_steps"):
        _fill_policy(maximum_steps=value)


def test_fill_policy_rejects_negative_slippage_and_float() -> None:
    with pytest.raises(ValueError, match="nonnegative finite Decimal"):
        _fill_policy(slippage_basis_points=Decimal("-1"))
    with pytest.raises(ValueError, match="nonnegative finite Decimal"):
        _fill_policy(slippage_basis_points=1.5)


# ---------------------------------------------------------------------------
# PaperOrderState
# ---------------------------------------------------------------------------


def test_paper_order_state_has_exactly_seven_values() -> None:
    assert {member.value for member in PaperOrderState} == {
        "ACCEPTED",
        "REJECTED",
        "OPEN",
        "PARTIALLY_FILLED",
        "FILLED",
        "CANCELLED",
        "EXPIRED",
    }


# ---------------------------------------------------------------------------
# PaperOrder / PaperOrderEvent
# ---------------------------------------------------------------------------


def test_order_round_trips_and_requires_paper_authority() -> None:
    order = _order()
    assert order.paper_order_id.startswith("paper-order-")
    for bad_authority in (
        ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED,
        ExecutionAuthorityMode.LIVE,
    ):
        with pytest.raises(ValueError, match="must be PAPER"):
            _order(authority=bad_authority)


def test_order_rejects_forged_lineage_subclass() -> None:
    lineage = PaperOrderIntentLineage.for_entry(_execution_intent())
    forged = _ForgedPaperOrderIntentLineage(
        lineage.intent_kind,
        lineage.source_intent_id,
        lineage.source_intent_idempotency_key,
        lineage.source_intent_content_digest,
        lineage.paper_position_id,
    )
    with pytest.raises(TypeError, match="exact PaperOrderIntentLineage"):
        _order(intent_lineage=forged)


def test_order_rejects_nonpositive_original_quantity() -> None:
    with pytest.raises(ValueError, match="positive finite Decimal"):
        _order(original_quantity=Decimal("0"))


def test_order_event_round_trips_and_rejects_negative_ordinal() -> None:
    event = _order_event(0, PaperOrderState.ACCEPTED)
    assert event.paper_order_event_id.startswith("paper-order-event-")
    with pytest.raises(ValueError, match="event_ordinal"):
        PaperOrderEvent.create(
            paper_order_id="paper-order-1",
            event_ordinal=-1,
            state=PaperOrderState.ACCEPTED,
            source_evidence_kind="test-evidence",
            source_evidence_id=None,
            appended_at=NOW,
        )


def test_order_event_allows_none_source_evidence_id_but_rejects_blank() -> None:
    event = _order_event(0, PaperOrderState.ACCEPTED)
    assert event.source_evidence_id is None
    with pytest.raises(ValueError, match="must not be blank"):
        PaperOrderEvent.create(
            paper_order_id="paper-order-1",
            event_ordinal=0,
            state=PaperOrderState.ACCEPTED,
            source_evidence_kind="test-evidence",
            source_evidence_id="   ",
            appended_at=NOW,
        )


# ---------------------------------------------------------------------------
# require_legal_transition / project_paper_order_state
# ---------------------------------------------------------------------------

_LEGAL_PAIRS = [
    (None, PaperOrderState.ACCEPTED),
    (PaperOrderState.ACCEPTED, PaperOrderState.OPEN),
    (PaperOrderState.OPEN, PaperOrderState.PARTIALLY_FILLED),
    (PaperOrderState.OPEN, PaperOrderState.FILLED),
    (PaperOrderState.OPEN, PaperOrderState.CANCELLED),
    (PaperOrderState.OPEN, PaperOrderState.EXPIRED),
    (PaperOrderState.OPEN, PaperOrderState.REJECTED),
    (PaperOrderState.PARTIALLY_FILLED, PaperOrderState.PARTIALLY_FILLED),
    (PaperOrderState.PARTIALLY_FILLED, PaperOrderState.FILLED),
    (PaperOrderState.PARTIALLY_FILLED, PaperOrderState.CANCELLED),
    (PaperOrderState.PARTIALLY_FILLED, PaperOrderState.EXPIRED),
]


@pytest.mark.parametrize(("previous", "next_state"), _LEGAL_PAIRS)
def test_every_legal_transition_is_accepted(
    previous: PaperOrderState | None, next_state: PaperOrderState
) -> None:
    require_legal_transition(previous, next_state)


_TERMINAL_STATES = [
    PaperOrderState.FILLED,
    PaperOrderState.CANCELLED,
    PaperOrderState.EXPIRED,
    PaperOrderState.REJECTED,
]

_ILLEGAL_PAIRS = [
    (PaperOrderState.ACCEPTED, PaperOrderState.FILLED),
    (PaperOrderState.ACCEPTED, PaperOrderState.PARTIALLY_FILLED),
    (PaperOrderState.ACCEPTED, PaperOrderState.REJECTED),
    (PaperOrderState.ACCEPTED, PaperOrderState.ACCEPTED),
    (PaperOrderState.OPEN, PaperOrderState.OPEN),
    (PaperOrderState.OPEN, PaperOrderState.ACCEPTED),
    (PaperOrderState.PARTIALLY_FILLED, PaperOrderState.REJECTED),
    (PaperOrderState.PARTIALLY_FILLED, PaperOrderState.OPEN),
    (PaperOrderState.PARTIALLY_FILLED, PaperOrderState.ACCEPTED),
] + [(terminal, other) for terminal in _TERMINAL_STATES for other in PaperOrderState]


@pytest.mark.parametrize(("previous", "next_state"), _ILLEGAL_PAIRS)
def test_every_illegal_transition_is_rejected(
    previous: PaperOrderState, next_state: PaperOrderState
) -> None:
    with pytest.raises(ValueError, match="illegal order transition"):
        require_legal_transition(previous, next_state)


@pytest.mark.parametrize("state", list(PaperOrderState))
def test_ordinal_zero_must_be_accepted(state: PaperOrderState) -> None:
    if state is PaperOrderState.ACCEPTED:
        require_legal_transition(None, state)
    else:
        with pytest.raises(ValueError, match="ordinal 0 must carry state ACCEPTED"):
            require_legal_transition(None, state)


def test_project_paper_order_state_returns_final_state_of_valid_sequence() -> None:
    events = (
        _order_event(0, PaperOrderState.ACCEPTED),
        _order_event(1, PaperOrderState.OPEN),
        _order_event(2, PaperOrderState.PARTIALLY_FILLED),
        _order_event(3, PaperOrderState.FILLED),
    )
    assert project_paper_order_state(events) is PaperOrderState.FILLED


def test_project_paper_order_state_ignores_supplied_ordering() -> None:
    events = (
        _order_event(1, PaperOrderState.OPEN),
        _order_event(0, PaperOrderState.ACCEPTED),
    )
    assert project_paper_order_state(events) is PaperOrderState.OPEN


def test_project_paper_order_state_rejects_empty_sequence() -> None:
    with pytest.raises(ValueError, match="at least one event"):
        project_paper_order_state(())


def test_project_paper_order_state_rejects_missing_ordinal_zero() -> None:
    with pytest.raises(ValueError, match="contiguous starting at 0"):
        project_paper_order_state((_order_event(1, PaperOrderState.OPEN),))


def test_project_paper_order_state_rejects_non_accepted_ordinal_zero() -> None:
    with pytest.raises(ValueError, match="ordinal 0 must carry state ACCEPTED"):
        project_paper_order_state((_order_event(0, PaperOrderState.OPEN),))


def test_project_paper_order_state_rejects_gap() -> None:
    events = (
        _order_event(0, PaperOrderState.ACCEPTED),
        _order_event(2, PaperOrderState.OPEN),
    )
    with pytest.raises(ValueError, match="contiguous starting at 0"):
        project_paper_order_state(events)


def test_project_paper_order_state_rejects_duplicate_ordinal() -> None:
    events = (
        _order_event(0, PaperOrderState.ACCEPTED),
        _order_event(0, PaperOrderState.ACCEPTED),
    )
    with pytest.raises(ValueError, match="duplicate event_ordinal"):
        project_paper_order_state(events)


def test_project_paper_order_state_rejects_illegal_consecutive_pair() -> None:
    events = (
        _order_event(0, PaperOrderState.ACCEPTED),
        _order_event(1, PaperOrderState.FILLED),
    )
    with pytest.raises(ValueError, match="illegal order transition"):
        project_paper_order_state(events)


def test_project_paper_order_state_rejects_cross_order_events() -> None:
    events = (
        _order_event(0, PaperOrderState.ACCEPTED, paper_order_id="order-a"),
        _order_event(1, PaperOrderState.OPEN, paper_order_id="order-b"),
    )
    with pytest.raises(ValueError, match="same paper_order_id"):
        project_paper_order_state(events)


def test_no_fill_terminal_states_reachable_directly_from_open() -> None:
    for state in (PaperOrderState.REJECTED, PaperOrderState.CANCELLED, PaperOrderState.EXPIRED):
        events = (
            _order_event(0, PaperOrderState.ACCEPTED),
            _order_event(1, PaperOrderState.OPEN),
            _order_event(2, state),
        )
        assert project_paper_order_state(events) is state


def test_incomplete_terminal_states_reachable_after_partial_fill() -> None:
    for state in (PaperOrderState.CANCELLED, PaperOrderState.EXPIRED):
        events = (
            _order_event(0, PaperOrderState.ACCEPTED),
            _order_event(1, PaperOrderState.OPEN),
            _order_event(2, PaperOrderState.PARTIALLY_FILLED),
            _order_event(3, state),
        )
        assert project_paper_order_state(events) is state


# ---------------------------------------------------------------------------
# FillEvaluationPlan / FillEvaluationStep
# ---------------------------------------------------------------------------


def test_plan_round_trips_and_rejects_expiry_before_intent_created_at() -> None:
    plan = _plan()
    assert plan.fill_evaluation_plan_id.startswith("fill-evaluation-plan-")
    with pytest.raises(ValueError, match="must not precede intent_created_at"):
        _plan(plan_expiry_at=NOW - timedelta(seconds=1))


def test_plan_rejects_maximum_steps_below_one() -> None:
    with pytest.raises(ValueError, match="maximum_steps"):
        _plan(maximum_steps=0)


def test_step_round_trips_and_rejects_due_before_window_start() -> None:
    step = _step()
    assert step.fill_evaluation_step_id.startswith("fill-evaluation-step-")
    with pytest.raises(ValueError, match="must not precede evaluation_window_start_at"):
        _step(evaluation_due_at=NOW - timedelta(seconds=1))


def test_step_rejects_negative_ordinal() -> None:
    with pytest.raises(ValueError, match="ordinal"):
        _step(ordinal=-1)


# ---------------------------------------------------------------------------
# FillEvaluationAttempt / diagnostic + resolution variant enums
# ---------------------------------------------------------------------------


def test_attempt_round_trips_and_rejects_wrong_disposition() -> None:
    attempt = FillEvaluationAttempt.create(
        fill_evaluation_step_id="fill-evaluation-step-1",
        evaluated_at=NOW,
        disposition=PAPER_ATTEMPT_DISPOSITION_PENDING_NO_ELIGIBLE_MARKET,
        diagnostic_code=PaperAttemptDiagnosticCode.NO_OBSERVATION_FOR_PAIR,
        worker_identity="worker-1",
        created_at=NOW,
    )
    assert attempt.fill_evaluation_attempt_id.startswith("fill-evaluation-attempt-")
    with pytest.raises(ValueError, match="PENDING_NO_ELIGIBLE_MARKET"):
        FillEvaluationAttempt.create(
            fill_evaluation_step_id="fill-evaluation-step-1",
            evaluated_at=NOW,
            disposition="SOMETHING_ELSE",
            diagnostic_code=PaperAttemptDiagnosticCode.NO_OBSERVATION_FOR_PAIR,
            worker_identity="worker-1",
            created_at=NOW,
        )


def test_attempt_identity_differs_by_evaluated_at_and_worker_identity() -> None:
    base = dict(
        fill_evaluation_step_id="fill-evaluation-step-1",
        disposition=PAPER_ATTEMPT_DISPOSITION_PENDING_NO_ELIGIBLE_MARKET,
        diagnostic_code=PaperAttemptDiagnosticCode.NO_OBSERVATION_FOR_PAIR,
        created_at=NOW,
    )
    attempt_a = FillEvaluationAttempt.create(evaluated_at=NOW, worker_identity="worker-1", **base)
    attempt_b = FillEvaluationAttempt.create(
        evaluated_at=NOW + timedelta(seconds=1), worker_identity="worker-1", **base
    )
    attempt_c = FillEvaluationAttempt.create(evaluated_at=NOW, worker_identity="worker-2", **base)
    ids = {attempt_a.fill_evaluation_attempt_id, attempt_b.fill_evaluation_attempt_id,
           attempt_c.fill_evaluation_attempt_id}
    assert len(ids) == 3


def test_attempt_rejects_blank_worker_identity() -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        FillEvaluationAttempt.create(
            fill_evaluation_step_id="fill-evaluation-step-1",
            evaluated_at=NOW,
            disposition=PAPER_ATTEMPT_DISPOSITION_PENDING_NO_ELIGIBLE_MARKET,
            diagnostic_code=PaperAttemptDiagnosticCode.NO_OBSERVATION_FOR_PAIR,
            worker_identity="  ",
            created_at=NOW,
        )


def test_diagnostic_code_and_resolution_variant_have_frozen_values() -> None:
    assert {member.value for member in PaperAttemptDiagnosticCode} == {
        "NO_OBSERVATION_FOR_PAIR",
        "ALL_OBSERVATIONS_INELIGIBLE",
    }
    assert {member.value for member in PaperStepResolutionVariant} == {
        "MARKET_SELECTED",
        "NO_MARKET",
    }


# ---------------------------------------------------------------------------
# PaperMarketObservationSelection / PaperNoMarketOutcome
# ---------------------------------------------------------------------------


def test_selection_round_trips_and_rejects_due_before_window_start() -> None:
    selection = PaperMarketObservationSelection.create(
        fill_evaluation_step_id="fill-evaluation-step-1",
        fill_evaluation_plan_id="fill-evaluation-plan-1",
        market_observation_id="paper-market-1",
        market_selection_policy_version="market-selection-v1",
        evaluation_window_start_at=NOW,
        evaluation_due_at=NOW + timedelta(minutes=1),
        intent_created_at=NOW,
        selected_at=NOW,
    )
    assert selection.market_observation_selection_id.startswith("market-observation-selection-")
    with pytest.raises(ValueError, match="must not precede evaluation_window_start_at"):
        PaperMarketObservationSelection.create(
            fill_evaluation_step_id="fill-evaluation-step-1",
            fill_evaluation_plan_id="fill-evaluation-plan-1",
            market_observation_id="paper-market-1",
            market_selection_policy_version="market-selection-v1",
            evaluation_window_start_at=NOW,
            evaluation_due_at=NOW - timedelta(seconds=1),
            intent_created_at=NOW,
            selected_at=NOW,
        )


def test_selection_rejects_window_start_before_intent_created_at() -> None:
    with pytest.raises(ValueError, match="must not precede intent_created_at"):
        PaperMarketObservationSelection.create(
            fill_evaluation_step_id="fill-evaluation-step-1",
            fill_evaluation_plan_id="fill-evaluation-plan-1",
            market_observation_id="paper-market-1",
            market_selection_policy_version="market-selection-v1",
            evaluation_window_start_at=NOW - timedelta(seconds=1),
            evaluation_due_at=NOW + timedelta(minutes=1),
            intent_created_at=NOW,
            selected_at=NOW,
        )


def test_no_market_outcome_round_trips_and_fixes_terminal_reason_code() -> None:
    outcome = PaperNoMarketOutcome.create(
        fill_evaluation_step_id="fill-evaluation-step-1",
        evaluation_due_at=NOW,
        resolved_at=NOW,
    )
    assert outcome.terminal_reason_code == NO_MARKET_TERMINAL_REASON_CODE
    with pytest.raises(ValueError, match="REJECTED_NO_MARKET_EVIDENCE"):
        dataclasses.replace(outcome, terminal_reason_code="SOMETHING_ELSE")


# ---------------------------------------------------------------------------
# PaperFill
# ---------------------------------------------------------------------------


def _fill(**overrides: object) -> PaperFill:
    values: dict[str, object] = {
        "fill_evaluation_step_id": "fill-evaluation-step-1",
        "market_observation_selection_id": "market-observation-selection-1",
        "market_observation_id": "paper-market-1",
        "pair": PAIR,
        "side": Side.BUY,
        "fill_quantity": Decimal("400"),
        "fill_price": Decimal("150.010"),
        "reference_price": Decimal("150.005"),
        "slippage_basis_points": Decimal("5"),
        "fill_model_version": "fill-model-v1",
        "remaining_quantity_before": Decimal("1000"),
        "remaining_quantity_after": Decimal("600"),
        "created_at": NOW,
    }
    values.update(overrides)
    return PaperFill.create(**values)  # type: ignore[arg-type]


def test_fill_round_trips_and_is_content_addressed() -> None:
    fill = _fill()
    assert fill.paper_fill_id.startswith("paper-fill-")
    assert _fill().paper_fill_id == fill.paper_fill_id


def test_fill_rejects_overfill_and_wrong_remainder() -> None:
    with pytest.raises(ValueError, match="must not exceed remaining_quantity_before"):
        _fill(fill_quantity=Decimal("1001"))
    with pytest.raises(ValueError, match="before minus fill_quantity"):
        _fill(remaining_quantity_after=Decimal("601"))


def test_fill_rejects_float_inputs() -> None:
    with pytest.raises(ValueError, match="positive finite Decimal"):
        _fill(fill_price=150.01)


def test_fill_remainder_check_runs_under_exact_arithmetic_context() -> None:
    # A half fill of 1000 by 31 threes leaves a remainder needing 31 significant
    # digits, so the check taken under the interpreter's default 28-digit
    # context both rejects the exact remainder and accepts the rounded one.
    remaining_before = Decimal("1000")
    with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
        fill_quantity = remaining_before * Decimal("0.3333333333333333333333333333333")
        remaining_after = remaining_before - fill_quantity
    rounded_remaining_after = Decimal("666.6666666666666666666666667")
    assert remaining_before - fill_quantity == rounded_remaining_after != remaining_after

    fill = _fill(
        fill_quantity=fill_quantity,
        remaining_quantity_before=remaining_before,
        remaining_quantity_after=remaining_after,
    )
    assert fill.remaining_quantity_after == remaining_after

    with pytest.raises(ValueError, match="before minus fill_quantity"):
        _fill(
            fill_quantity=fill_quantity,
            remaining_quantity_before=remaining_before,
            remaining_quantity_after=rounded_remaining_after,
        )


def test_fill_full_remaining_reaches_exactly_zero_remainder() -> None:
    fill = _fill(fill_quantity=Decimal("1000"), remaining_quantity_after=Decimal("0"))
    assert fill.remaining_quantity_after == Decimal("0")


# ---------------------------------------------------------------------------
# Frozen Decimal arithmetic contexts
# ---------------------------------------------------------------------------


def test_exact_arithmetic_context_traps_inexact_division() -> None:
    assert PAPER_EXACT_ARITHMETIC_V1.prec == 50
    with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
        with pytest.raises(decimal.Inexact):
            Decimal(1) / Decimal(3)


def test_quotient_arithmetic_context_allows_rounded_division() -> None:
    assert PAPER_QUOTIENT_ARITHMETIC_V1.prec == 34
    with decimal.localcontext(PAPER_QUOTIENT_ARITHMETIC_V1):
        result = Decimal(1) / Decimal(3)
    assert len(result.as_tuple().digits) <= 34


# ---------------------------------------------------------------------------
# Content-addressed-ID self-checks shared across every B1 evidence type
# ---------------------------------------------------------------------------


def _attempt() -> FillEvaluationAttempt:
    return FillEvaluationAttempt.create(
        fill_evaluation_step_id="fill-evaluation-step-1",
        evaluated_at=NOW,
        disposition=PAPER_ATTEMPT_DISPOSITION_PENDING_NO_ELIGIBLE_MARKET,
        diagnostic_code=PaperAttemptDiagnosticCode.NO_OBSERVATION_FOR_PAIR,
        worker_identity="worker-1",
        created_at=NOW,
    )


def _selection() -> PaperMarketObservationSelection:
    return PaperMarketObservationSelection.create(
        fill_evaluation_step_id="fill-evaluation-step-1",
        fill_evaluation_plan_id="fill-evaluation-plan-1",
        market_observation_id="paper-market-1",
        market_selection_policy_version="market-selection-v1",
        evaluation_window_start_at=NOW,
        evaluation_due_at=NOW + timedelta(minutes=1),
        intent_created_at=NOW,
        selected_at=NOW,
    )


@pytest.mark.parametrize(
    ("build", "id_field"),
    [
        (_market_observation, "market_observation_id"),
        (_fill_policy, "paper_fill_policy_id"),
        (_order, "paper_order_id"),
        (lambda: _order_event(0, PaperOrderState.ACCEPTED), "paper_order_event_id"),
        (_plan, "fill_evaluation_plan_id"),
        (_step, "fill_evaluation_step_id"),
        (_attempt, "fill_evaluation_attempt_id"),
        (_selection, "market_observation_selection_id"),
        (_fill, "paper_fill_id"),
    ],
)
def test_tampered_content_addressed_id_is_rejected(build: object, id_field: str) -> None:
    instance = build()  # type: ignore[operator]
    with pytest.raises(ValueError, match="does not match content"):
        dataclasses.replace(instance, **{id_field: "tampered-id"})


@pytest.mark.parametrize(
    ("build", "version_field", "match"),
    [
        (_market_observation, "observation_contract_version", "unsupported market observation"),
        (_fill_policy, "policy_contract_version", "unsupported fill policy contract"),
        (_order, "order_contract_version", "unsupported order contract"),
        (_plan, "plan_contract_version", "unsupported plan contract"),
        (_step, "step_contract_version", "unsupported step contract"),
        (_fill, "fill_contract_version", "unsupported fill contract"),
    ],
)
def test_wrong_contract_version_is_rejected(build: object, version_field: str, match: str) -> None:
    instance = build()  # type: ignore[operator]
    with pytest.raises(ValueError, match=match):
        dataclasses.replace(instance, **{version_field: "wrong-version-v0"})


def test_order_rejects_non_exact_authority_type() -> None:
    order = _order()
    with pytest.raises(TypeError, match="exact ExecutionAuthorityMode"):
        dataclasses.replace(order, authority="PAPER")


def test_order_event_rejects_non_exact_state_type() -> None:
    event = _order_event(0, PaperOrderState.ACCEPTED)
    with pytest.raises(TypeError, match="exact PaperOrderState"):
        dataclasses.replace(event, state="ACCEPTED")


def test_require_legal_transition_rejects_non_exact_state_types() -> None:
    with pytest.raises(TypeError, match="exact PaperOrderState"):
        require_legal_transition(None, "ACCEPTED")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="exact PaperOrderState or None"):
        require_legal_transition("ACCEPTED", PaperOrderState.OPEN)  # type: ignore[arg-type]


def test_project_paper_order_state_rejects_non_paper_order_event_items() -> None:
    with pytest.raises(TypeError, match="exact PaperOrderEvent"):
        project_paper_order_state((_order_event(0, PaperOrderState.ACCEPTED), "not-an-event"))  # type: ignore[arg-type]
