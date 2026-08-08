from __future__ import annotations

import decimal
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from fx_core.time import require_utc

from ..models import Side
from .contracts import (
    PAPER_EXACT_ARITHMETIC_V1,
    FillEvaluationPlan,
    FillEvaluationStep,
    PaperAttemptDiagnosticCode,
    PaperFill,
    PaperFillPolicy,
    PaperMarketObservation,
    PaperMarketObservationSelection,
    PaperNoMarketOutcome,
    PaperOrderEvent,
    PaperOrderState,
    PaperPartialFillMode,
    PaperStepResolutionVariant,
    require_legal_transition,
)


class StepEvaluationKind(StrEnum):
    MARKET_SELECTED = "MARKET_SELECTED"
    PENDING_NO_ELIGIBLE_MARKET = "PENDING_NO_ELIGIBLE_MARKET"
    NO_MARKET = "NO_MARKET"


# ---------------------------------------------------------------------------
# Eligibility (spec.md "Deterministic market selection", clauses 1-9)
# ---------------------------------------------------------------------------


def validate_candidate_observation_type(observation: object) -> PaperMarketObservation:
    # Clause 1 is a type contract, not a boundary comparison, so it raises
    # instead of contributing a boolean to ObservationEligibility.
    if type(observation) is not PaperMarketObservation:
        raise TypeError("observation must be exact PaperMarketObservation")
    PaperMarketObservation.__post_init__(observation)
    return observation


@dataclass(frozen=True, slots=True)
class ObservationEligibility:
    pair_match: bool
    not_before_intent_created_at: bool
    not_before_window_start: bool
    not_after_due: bool
    locally_available: bool
    provider_observed_at_bound: bool
    fresh_enough: bool
    not_already_selected: bool

    @property
    def eligible(self) -> bool:
        return (
            self.pair_match
            and self.not_before_intent_created_at
            and self.not_before_window_start
            and self.not_after_due
            and self.locally_available
            and self.provider_observed_at_bound
            and self.fresh_enough
            and self.not_already_selected
        )


def assess_observation_eligibility(
    observation: PaperMarketObservation,
    step: FillEvaluationStep,
    plan: FillEvaluationPlan,
    policy: PaperFillPolicy,
    evaluated_at: datetime,
    already_selected_observation_ids: frozenset[str],
) -> ObservationEligibility:
    validate_candidate_observation_type(observation)
    if type(step) is not FillEvaluationStep:
        raise TypeError("step must be exact FillEvaluationStep")
    if type(plan) is not FillEvaluationPlan:
        raise TypeError("plan must be exact FillEvaluationPlan")
    if type(policy) is not PaperFillPolicy:
        raise TypeError("policy must be exact PaperFillPolicy")
    if type(evaluated_at) is not datetime:
        raise TypeError("evaluated_at must be exact datetime")
    require_utc(evaluated_at, "evaluated_at")
    if type(already_selected_observation_ids) is not frozenset:
        raise TypeError("already_selected_observation_ids must be exact frozenset")

    # Each field below is its own fail-closed check; none is short-circuited
    # into the others so a boundary test can pin exactly one clause False.
    return ObservationEligibility(
        pair_match=observation.pair == plan.pair,
        not_before_intent_created_at=plan.intent_created_at <= observation.received_at,
        not_before_window_start=step.evaluation_window_start_at <= observation.received_at,
        not_after_due=observation.received_at <= step.evaluation_due_at,
        locally_available=observation.received_at <= evaluated_at,
        provider_observed_at_bound=(
            observation.provider_observed_at <= observation.received_at
            and observation.provider_observed_at <= step.evaluation_due_at
        ),
        fresh_enough=(
            step.evaluation_due_at - observation.provider_observed_at
        )
        <= policy.maximum_market_age,
        not_already_selected=observation.market_observation_id
        not in already_selected_observation_ids,
    )


def select_eligible_observation(
    candidate_observations: tuple[PaperMarketObservation, ...],
    step: FillEvaluationStep,
    plan: FillEvaluationPlan,
    policy: PaperFillPolicy,
    evaluated_at: datetime,
    already_selected_observation_ids: frozenset[str],
) -> PaperMarketObservation | None:
    if type(candidate_observations) is not tuple:
        raise TypeError("candidate_observations must be exact tuple")
    eligible = [
        observation
        for observation in candidate_observations
        if assess_observation_eligibility(
            observation, step, plan, policy, evaluated_at, already_selected_observation_ids
        ).eligible
    ]
    if not eligible:
        return None
    eligible.sort(key=lambda o: (o.received_at, o.provider_observed_at, o.market_observation_id))
    return eligible[0]


def derive_attempt_diagnostic_code(
    candidate_observations: tuple[PaperMarketObservation, ...],
    plan: FillEvaluationPlan,
) -> PaperAttemptDiagnosticCode:
    # Only meaningful once selection has already failed to find an eligible
    # observation; the precedence is over the candidate set, not the eligible
    # subset, per spec.md's PaperAttemptDiagnosticCode precedence.
    if type(candidate_observations) is not tuple:
        raise TypeError("candidate_observations must be exact tuple")
    if type(plan) is not FillEvaluationPlan:
        raise TypeError("plan must be exact FillEvaluationPlan")
    for observation in candidate_observations:
        validate_candidate_observation_type(observation)
    if not any(observation.pair == plan.pair for observation in candidate_observations):
        return PaperAttemptDiagnosticCode.NO_OBSERVATION_FOR_PAIR
    return PaperAttemptDiagnosticCode.ALL_OBSERVATIONS_INELIGIBLE


# ---------------------------------------------------------------------------
# Fill computation (spec.md "Deterministic fill computation")
# ---------------------------------------------------------------------------


def reference_price_for_side(observation: PaperMarketObservation, side: Side) -> Decimal:
    validate_candidate_observation_type(observation)
    if type(side) is not Side:
        raise TypeError("side must be exact Side")
    return observation.ask if side is Side.BUY else observation.bid


def compute_fill_price(
    reference_price: Decimal, side: Side, slippage_basis_points: Decimal
) -> Decimal:
    if (
        type(reference_price) is not Decimal
        or not reference_price.is_finite()
        or reference_price <= 0
    ):
        raise ValueError("reference_price must be a positive finite Decimal")
    if type(side) is not Side:
        raise TypeError("side must be exact Side")
    if (
        type(slippage_basis_points) is not Decimal
        or not slippage_basis_points.is_finite()
        or slippage_basis_points < 0
    ):
        raise ValueError("slippage_basis_points must be a nonnegative finite Decimal")
    with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
        adjustment = reference_price * slippage_basis_points * Decimal("0.0001")
        fill_price = (
            reference_price + adjustment if side is Side.BUY else reference_price - adjustment
        )
    if type(fill_price) is not Decimal or not fill_price.is_finite() or fill_price <= 0:
        raise ValueError("fill_price must resolve to a positive finite Decimal")
    return fill_price


def validate_fill_quantity_invariants(
    fill_quantity: Decimal, remaining_quantity_before: Decimal
) -> None:
    # Split out from compute_fill_quantity so the "0 < fill_quantity <=
    # remaining_quantity_before" invariant is independently testable against
    # contrived zero/negative/overfill values, not only values the frozen
    # formulas can themselves produce.
    if type(fill_quantity) is not Decimal or type(remaining_quantity_before) is not Decimal:
        raise TypeError("fill_quantity and remaining_quantity_before must be exact Decimal")
    if (
        not fill_quantity.is_finite()
        or not (Decimal(0) < fill_quantity <= remaining_quantity_before)
    ):
        raise ValueError("fill_quantity must be positive and not exceed remaining_quantity_before")


def compute_fill_quantity(policy: PaperFillPolicy, remaining_quantity_before: Decimal) -> Decimal:
    if type(policy) is not PaperFillPolicy:
        raise TypeError("policy must be exact PaperFillPolicy")
    if (
        type(remaining_quantity_before) is not Decimal
        or not remaining_quantity_before.is_finite()
        or remaining_quantity_before <= 0
    ):
        raise ValueError("remaining_quantity_before must be a positive finite Decimal")
    with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
        if policy.partial_fill_mode is PaperPartialFillMode.FULL_REMAINING:
            fill_quantity = remaining_quantity_before
        else:
            fraction = policy.partial_fill_fraction
            assert fraction is not None  # guaranteed by PaperFillPolicy.__post_init__
            fill_quantity = remaining_quantity_before * fraction
    validate_fill_quantity_invariants(fill_quantity, remaining_quantity_before)
    return fill_quantity


def is_next_step_legitimate(
    *,
    resolution_variant: PaperStepResolutionVariant,
    fill: PaperFill | None,
    remaining_quantity_after: Decimal,
    resolved_ordinal: int,
    maximum_steps: int,
) -> bool:
    if type(resolution_variant) is not PaperStepResolutionVariant:
        raise TypeError("resolution_variant must be exact PaperStepResolutionVariant")
    if fill is not None and type(fill) is not PaperFill:
        raise TypeError("fill must be exact PaperFill or None")
    if type(remaining_quantity_after) is not Decimal:
        raise TypeError("remaining_quantity_after must be exact Decimal")
    if (
        type(resolved_ordinal) is not int
        or isinstance(resolved_ordinal, bool)
        or resolved_ordinal < 0
    ):
        raise ValueError("resolved_ordinal must be an exact int >= 0")
    if type(maximum_steps) is not int or isinstance(maximum_steps, bool) or maximum_steps < 1:
        raise ValueError("maximum_steps must be an exact int >= 1")
    return (
        resolution_variant is PaperStepResolutionVariant.MARKET_SELECTED
        and fill is not None
        and fill.fill_quantity > 0
        and remaining_quantity_after > 0
        and (resolved_ordinal + 1) < maximum_steps
    )


# ---------------------------------------------------------------------------
# One typed per-Step evaluation result (spec.md B2 scope statement)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MarketSelectedStepEvaluation:
    kind: StepEvaluationKind
    selection: PaperMarketObservationSelection
    fill: PaperFill
    order_events: tuple[PaperOrderEvent, ...]

    def __post_init__(self) -> None:
        if self.kind is not StepEvaluationKind.MARKET_SELECTED:
            raise ValueError("kind must be MARKET_SELECTED")
        if type(self.selection) is not PaperMarketObservationSelection:
            raise TypeError("selection must be exact PaperMarketObservationSelection")
        if type(self.fill) is not PaperFill:
            raise TypeError("fill must be exact PaperFill")
        if type(self.order_events) is not tuple or not (1 <= len(self.order_events) <= 2):
            raise ValueError("order_events must carry one or two PaperOrderEvent")
        for event in self.order_events:
            if type(event) is not PaperOrderEvent:
                raise TypeError("order_events entries must be exact PaperOrderEvent")


@dataclass(frozen=True, slots=True)
class PendingStepEvaluation:
    kind: StepEvaluationKind
    diagnostic_code: PaperAttemptDiagnosticCode

    def __post_init__(self) -> None:
        if self.kind is not StepEvaluationKind.PENDING_NO_ELIGIBLE_MARKET:
            raise ValueError("kind must be PENDING_NO_ELIGIBLE_MARKET")
        if type(self.diagnostic_code) is not PaperAttemptDiagnosticCode:
            raise TypeError("diagnostic_code must be exact PaperAttemptDiagnosticCode")


@dataclass(frozen=True, slots=True)
class NoMarketStepEvaluation:
    kind: StepEvaluationKind
    outcome: PaperNoMarketOutcome
    diagnostic_code: PaperAttemptDiagnosticCode
    order_events: tuple[PaperOrderEvent, ...]

    def __post_init__(self) -> None:
        if self.kind is not StepEvaluationKind.NO_MARKET:
            raise ValueError("kind must be NO_MARKET")
        if type(self.outcome) is not PaperNoMarketOutcome:
            raise TypeError("outcome must be exact PaperNoMarketOutcome")
        if type(self.diagnostic_code) is not PaperAttemptDiagnosticCode:
            raise TypeError("diagnostic_code must be exact PaperAttemptDiagnosticCode")
        if type(self.order_events) is not tuple or len(self.order_events) != 1:
            raise ValueError("order_events must carry exactly one PaperOrderEvent")
        if type(self.order_events[0]) is not PaperOrderEvent:
            raise TypeError("order_events entries must be exact PaperOrderEvent")


StepEvaluationResult = MarketSelectedStepEvaluation | PendingStepEvaluation | NoMarketStepEvaluation


def evaluate_fill_evaluation_step(
    *,
    step: FillEvaluationStep,
    plan: FillEvaluationPlan,
    policy: PaperFillPolicy,
    candidate_observations: tuple[PaperMarketObservation, ...],
    already_selected_observation_ids: frozenset[str],
    fills_so_far: tuple[PaperFill, ...],
    evaluated_at: datetime,
    next_order_event_ordinal: int,
) -> StepEvaluationResult:
    if type(step) is not FillEvaluationStep:
        raise TypeError("step must be exact FillEvaluationStep")
    if type(plan) is not FillEvaluationPlan:
        raise TypeError("plan must be exact FillEvaluationPlan")
    if type(policy) is not PaperFillPolicy:
        raise TypeError("policy must be exact PaperFillPolicy")
    if type(candidate_observations) is not tuple:
        raise TypeError("candidate_observations must be exact tuple")
    if type(already_selected_observation_ids) is not frozenset:
        raise TypeError("already_selected_observation_ids must be exact frozenset")
    if type(fills_so_far) is not tuple:
        raise TypeError("fills_so_far must be exact tuple")
    for fill_so_far in fills_so_far:
        if type(fill_so_far) is not PaperFill:
            raise TypeError("fills_so_far entries must be exact PaperFill")
    if type(evaluated_at) is not datetime:
        raise TypeError("evaluated_at must be exact datetime")
    require_utc(evaluated_at, "evaluated_at")
    if (
        type(next_order_event_ordinal) is not int
        or isinstance(next_order_event_ordinal, bool)
        or next_order_event_ordinal < 0
    ):
        raise ValueError("next_order_event_ordinal must be an exact int >= 0")

    previous_order_state = (
        PaperOrderState.OPEN if not fills_so_far else PaperOrderState.PARTIALLY_FILLED
    )

    selected = select_eligible_observation(
        candidate_observations, step, plan, policy, evaluated_at, already_selected_observation_ids
    )

    if selected is not None:
        selection = PaperMarketObservationSelection.create(
            fill_evaluation_step_id=step.fill_evaluation_step_id,
            fill_evaluation_plan_id=plan.fill_evaluation_plan_id,
            market_observation_id=selected.market_observation_id,
            market_selection_policy_version=policy.market_selection_policy_version,
            evaluation_window_start_at=step.evaluation_window_start_at,
            evaluation_due_at=step.evaluation_due_at,
            intent_created_at=plan.intent_created_at,
            selected_at=evaluated_at,
        )
        reference_price = reference_price_for_side(selected, plan.side)
        fill_price = compute_fill_price(reference_price, plan.side, policy.slippage_basis_points)
        fill_quantity = compute_fill_quantity(policy, step.remaining_quantity_before)
        with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
            remaining_quantity_after = step.remaining_quantity_before - fill_quantity
            already_filled_total = sum(
                (fill_so_far.fill_quantity for fill_so_far in fills_so_far), start=Decimal(0)
            )
            ordered_fill_total = already_filled_total + fill_quantity
        if ordered_fill_total > plan.original_quantity:
            raise ValueError("ordered Fill sum must not exceed the plan's original_quantity")

        fill = PaperFill.create(
            fill_evaluation_step_id=step.fill_evaluation_step_id,
            market_observation_selection_id=selection.market_observation_selection_id,
            market_observation_id=selected.market_observation_id,
            pair=plan.pair,
            side=plan.side,
            fill_quantity=fill_quantity,
            fill_price=fill_price,
            reference_price=reference_price,
            slippage_basis_points=policy.slippage_basis_points,
            fill_model_version=policy.fill_model_version,
            remaining_quantity_before=step.remaining_quantity_before,
            remaining_quantity_after=remaining_quantity_after,
            created_at=evaluated_at,
        )

        if remaining_quantity_after == 0:
            produced_states: tuple[PaperOrderState, ...] = (PaperOrderState.FILLED,)
        elif (step.ordinal + 1) == plan.maximum_steps:
            produced_states = (
                PaperOrderState.PARTIALLY_FILLED,
                policy.incomplete_terminal_order_state,
            )
        else:
            produced_states = (PaperOrderState.PARTIALLY_FILLED,)

        order_events: list[PaperOrderEvent] = []
        running_previous_state = previous_order_state
        for offset, state in enumerate(produced_states):
            require_legal_transition(running_previous_state, state)
            order_events.append(
                PaperOrderEvent.create(
                    paper_order_id=plan.paper_order_id,
                    event_ordinal=next_order_event_ordinal + offset,
                    state=state,
                    source_evidence_kind="PAPER_FILL",
                    source_evidence_id=fill.paper_fill_id,
                    appended_at=evaluated_at,
                )
            )
            running_previous_state = state

        return MarketSelectedStepEvaluation(
            kind=StepEvaluationKind.MARKET_SELECTED,
            selection=selection,
            fill=fill,
            order_events=tuple(order_events),
        )

    diagnostic_code = derive_attempt_diagnostic_code(candidate_observations, plan)

    if evaluated_at < step.evaluation_due_at:
        return PendingStepEvaluation(
            kind=StepEvaluationKind.PENDING_NO_ELIGIBLE_MARKET,
            diagnostic_code=diagnostic_code,
        )

    outcome = PaperNoMarketOutcome.create(
        fill_evaluation_step_id=step.fill_evaluation_step_id,
        evaluation_due_at=step.evaluation_due_at,
        resolved_at=evaluated_at,
    )
    terminal_state = (
        policy.incomplete_terminal_order_state
        if fills_so_far
        else policy.no_fill_terminal_order_state
    )
    require_legal_transition(previous_order_state, terminal_state)
    order_event = PaperOrderEvent.create(
        paper_order_id=plan.paper_order_id,
        event_ordinal=next_order_event_ordinal,
        state=terminal_state,
        source_evidence_kind="PAPER_NO_MARKET_OUTCOME",
        source_evidence_id=outcome.no_market_outcome_id,
        appended_at=evaluated_at,
    )
    return NoMarketStepEvaluation(
        kind=StepEvaluationKind.NO_MARKET,
        outcome=outcome,
        diagnostic_code=diagnostic_code,
        order_events=(order_event,),
    )
