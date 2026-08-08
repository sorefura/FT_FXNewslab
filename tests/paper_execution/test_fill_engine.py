from datetime import timedelta
from decimal import Decimal

import pytest
from fx_core import CurrencyPair
from swap_bot.models import Side
from swap_bot.paper import (
    PaperAttemptDiagnosticCode,
    PaperFill,
    PaperOrderState,
    PaperPartialFillMode,
    PaperStepResolutionVariant,
)
from swap_bot.paper.fill_engine import (
    MarketSelectedStepEvaluation,
    NoMarketStepEvaluation,
    PendingStepEvaluation,
    StepEvaluationKind,
    assess_observation_eligibility,
    compute_fill_price,
    compute_fill_quantity,
    derive_attempt_diagnostic_code,
    evaluate_fill_evaluation_step,
    is_next_step_legitimate,
    reference_price_for_side,
    select_eligible_observation,
    validate_candidate_observation_type,
    validate_fill_quantity_invariants,
)

from tests.paper_domain.test_contracts import _fill_policy, _market_observation, _plan, _step
from tests.strategy_contracts.factories import NOW, PAIR

_OTHER_PAIR = CurrencyPair.parse("MXN_JPY")


def _plan_and_step(**step_overrides: object) -> tuple:
    plan = _plan(intent_created_at=NOW - timedelta(minutes=1))
    step_values: dict[str, object] = {
        "fill_evaluation_plan_id": plan.fill_evaluation_plan_id,
        "evaluation_window_start_at": NOW,
        "evaluation_due_at": NOW + timedelta(minutes=10),
    }
    step_values.update(step_overrides)
    step = _step(**step_values)
    return plan, step


def _policy(**overrides: object):
    values: dict[str, object] = {"maximum_market_age": timedelta(hours=1)}
    values.update(overrides)
    return _fill_policy(**values)


# ---------------------------------------------------------------------------
# Eligibility clause 1: exact type + base validator
# ---------------------------------------------------------------------------


def test_clause1_rejects_non_exact_observation_type() -> None:
    plan, step = _plan_and_step()
    policy = _policy()
    with pytest.raises(TypeError):
        validate_candidate_observation_type(object())
    with pytest.raises(TypeError):
        assess_observation_eligibility(plan, step, plan, policy, NOW, frozenset())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Eligibility clause 2: pair match
# ---------------------------------------------------------------------------


def test_clause2_pair_mismatch_is_ineligible() -> None:
    plan, step = _plan_and_step()
    policy = _policy()
    observation = _market_observation(pair=_OTHER_PAIR, received_at=NOW, provider_observed_at=NOW)
    result = assess_observation_eligibility(observation, step, plan, policy, NOW, frozenset())
    assert result.pair_match is False
    assert result.eligible is False

    matching = _market_observation(pair=PAIR, received_at=NOW, provider_observed_at=NOW)
    assert assess_observation_eligibility(matching, step, plan, policy, NOW, frozenset()).pair_match


# ---------------------------------------------------------------------------
# Eligibility clause 3: received_at >= intent_created_at, boundary at equality
# ---------------------------------------------------------------------------


def test_clause3_intent_created_at_boundary_equality_is_eligible() -> None:
    # plan.intent_created_at (NOW - 1min) precedes step.window_start (NOW), so
    # this clause can be pinned at its own boundary independent of clause 4.
    plan, step = _plan_and_step()
    policy = _policy()
    on_boundary = _market_observation(
        received_at=plan.intent_created_at, provider_observed_at=plan.intent_created_at
    )
    result = assess_observation_eligibility(on_boundary, step, plan, policy, NOW, frozenset())
    assert result.not_before_intent_created_at is True

    before_boundary = _market_observation(
        received_at=plan.intent_created_at - timedelta(microseconds=1),
        provider_observed_at=plan.intent_created_at - timedelta(microseconds=1),
    )
    result = assess_observation_eligibility(before_boundary, step, plan, policy, NOW, frozenset())
    assert result.not_before_intent_created_at is False


# ---------------------------------------------------------------------------
# Eligibility clause 4: received_at >= window_start, boundary at equality
# ---------------------------------------------------------------------------


def test_clause4_window_start_boundary_equality_is_eligible() -> None:
    plan, step = _plan_and_step()
    policy = _policy()
    on_boundary = _market_observation(
        received_at=step.evaluation_window_start_at,
        provider_observed_at=step.evaluation_window_start_at,
    )
    result = assess_observation_eligibility(on_boundary, step, plan, policy, NOW, frozenset())
    assert result.not_before_window_start is True

    before_boundary = _market_observation(
        received_at=step.evaluation_window_start_at - timedelta(microseconds=1),
        provider_observed_at=step.evaluation_window_start_at - timedelta(microseconds=1),
    )
    result = assess_observation_eligibility(before_boundary, step, plan, policy, NOW, frozenset())
    assert result.not_before_window_start is False


# ---------------------------------------------------------------------------
# Eligibility clause 5: received_at <= due, boundary at equality
# ---------------------------------------------------------------------------


def test_clause5_due_boundary_equality_is_eligible() -> None:
    plan, step = _plan_and_step()
    policy = _policy()
    on_boundary = _market_observation(
        received_at=step.evaluation_due_at, provider_observed_at=step.evaluation_due_at
    )
    result = assess_observation_eligibility(
        on_boundary, step, plan, policy, step.evaluation_due_at, frozenset()
    )
    assert result.not_after_due is True

    after_boundary = _market_observation(
        received_at=step.evaluation_due_at + timedelta(microseconds=1),
        provider_observed_at=step.evaluation_due_at,
    )
    after_boundary_evaluated_at = step.evaluation_due_at + timedelta(microseconds=1)
    result = assess_observation_eligibility(
        after_boundary, step, plan, policy, after_boundary_evaluated_at, frozenset()
    )
    assert result.not_after_due is False


# ---------------------------------------------------------------------------
# Eligibility clause 6: locally available (received_at <= evaluated_at)
# ---------------------------------------------------------------------------


def test_clause6_local_availability_boundary_equality_is_eligible() -> None:
    plan, step = _plan_and_step()
    policy = _policy()
    observation = _market_observation(
        received_at=NOW + timedelta(seconds=5), provider_observed_at=NOW
    )
    on_boundary = assess_observation_eligibility(
        observation, step, plan, policy, observation.received_at, frozenset()
    )
    assert on_boundary.locally_available is True

    just_before = observation.received_at - timedelta(microseconds=1)
    not_yet = assess_observation_eligibility(
        observation, step, plan, policy, just_before, frozenset()
    )
    assert not_yet.locally_available is False


# ---------------------------------------------------------------------------
# Eligibility clause 7: provider_observed_at <= received_at and <= due
# ---------------------------------------------------------------------------


def test_clause7_provider_observed_at_bound() -> None:
    # PaperMarketObservation's own base validator already forbids
    # provider_observed_at > received_at at construction, so only the
    # "provider_observed_at <= due" half of this clause is independently
    # reachable through a legitimately constructed observation.
    plan, step = _plan_and_step()
    policy = _policy()
    received_at = NOW + timedelta(seconds=10)
    on_boundary = _market_observation(received_at=received_at, provider_observed_at=received_at)
    result = assess_observation_eligibility(
        on_boundary, step, plan, policy, received_at, frozenset()
    )
    assert result.provider_observed_at_bound is True

    after_due_received_at = step.evaluation_due_at + timedelta(seconds=1)
    after_due = _market_observation(
        received_at=after_due_received_at,
        provider_observed_at=step.evaluation_due_at + timedelta(microseconds=1),
    )
    result = assess_observation_eligibility(
        after_due, step, plan, policy, after_due_received_at, frozenset()
    )
    assert result.provider_observed_at_bound is False


# ---------------------------------------------------------------------------
# Eligibility clause 8: freshness, boundary at equality to maximum_market_age
# ---------------------------------------------------------------------------


def test_clause8_freshness_boundary_equality_is_eligible() -> None:
    plan, step = _plan_and_step()
    policy = _policy(maximum_market_age=timedelta(minutes=5))
    provider_at_boundary = step.evaluation_due_at - policy.maximum_market_age
    on_boundary = _market_observation(
        received_at=step.evaluation_due_at, provider_observed_at=provider_at_boundary
    )
    result = assess_observation_eligibility(
        on_boundary, step, plan, policy, step.evaluation_due_at, frozenset()
    )
    assert result.fresh_enough is True

    stale = _market_observation(
        received_at=step.evaluation_due_at,
        provider_observed_at=provider_at_boundary - timedelta(microseconds=1),
    )
    result = assess_observation_eligibility(
        stale, step, plan, policy, step.evaluation_due_at, frozenset()
    )
    assert result.fresh_enough is False


# ---------------------------------------------------------------------------
# Eligibility clause 9: not already selected by this plan
# ---------------------------------------------------------------------------


def test_clause9_excludes_already_selected_observation() -> None:
    plan, step = _plan_and_step()
    policy = _policy()
    observation = _market_observation(received_at=NOW, provider_observed_at=NOW)
    already_selected = frozenset({observation.market_observation_id})

    result = assess_observation_eligibility(observation, step, plan, policy, NOW, already_selected)
    assert result.not_already_selected is False
    assert result.eligible is False

    excluded = select_eligible_observation(
        (observation,), step, plan, policy, NOW, already_selected
    )
    assert excluded is None
    included = select_eligible_observation((observation,), step, plan, policy, NOW, frozenset())
    assert included is observation


# ---------------------------------------------------------------------------
# Reviewer's divergence case: outcome follows the supplied candidate tuple
# ---------------------------------------------------------------------------


def test_reviewer_divergence_case_selects_earliest_of_supplied_candidates() -> None:
    plan, step = _plan_and_step()
    policy = _policy()

    observation_a = _market_observation(
        received_at=NOW + timedelta(seconds=5), provider_observed_at=NOW + timedelta(seconds=4)
    )
    # Call 1: A is supplied, but not yet locally available -> no selection.
    call_one = select_eligible_observation(
        (observation_a,), step, plan, policy, NOW + timedelta(seconds=3), frozenset()
    )
    assert call_one is None

    observation_b = _market_observation(
        received_at=NOW + timedelta(seconds=6),
        provider_observed_at=NOW + timedelta(seconds=4),
        bid=Decimal("150.010"),
        ask=Decimal("150.015"),
    )
    # Call 2: candidate tuple is exactly the persisted-equivalent set {A, B}.
    # A now became locally available and sorts first, so the pure function
    # must select A even though B was the only observation this particular
    # call would have supplied on its own.
    call_two = select_eligible_observation(
        (observation_a, observation_b), step, plan, policy, NOW + timedelta(seconds=7), frozenset()
    )
    assert call_two is observation_a


# ---------------------------------------------------------------------------
# Ordering-tie fixture: equal received_at, equal (received_at, provider), and
# mixed whole-second/microsecond timestamps
# ---------------------------------------------------------------------------


def test_ordering_tie_fixture_selects_in_frozen_sort_order() -> None:
    plan, step = _plan_and_step()
    policy = _policy()
    evaluated_at = NOW + timedelta(seconds=10)

    earliest_microsecond = _market_observation(
        received_at=NOW + timedelta(microseconds=500_000), provider_observed_at=NOW
    )
    tie_group_first = _market_observation(
        received_at=NOW + timedelta(seconds=1),
        provider_observed_at=NOW + timedelta(microseconds=500_000),
    )
    tie_provider = NOW + timedelta(seconds=1)
    full_tie_a = _market_observation(
        received_at=NOW + timedelta(seconds=2),
        provider_observed_at=tie_provider,
        bid=Decimal("150.000"),
        ask=Decimal("150.005"),
    )
    full_tie_b = _market_observation(
        received_at=NOW + timedelta(seconds=2),
        provider_observed_at=tie_provider,
        bid=Decimal("150.001"),
        ask=Decimal("150.006"),
    )
    latest = _market_observation(
        received_at=NOW + timedelta(seconds=3), provider_observed_at=tie_provider
    )

    candidates = (latest, full_tie_b, tie_group_first, earliest_microsecond, full_tie_a)
    expected_full_tie_winner = min(full_tie_a, full_tie_b, key=lambda o: o.market_observation_id)
    expected_full_tie_loser = full_tie_b if expected_full_tie_winner is full_tie_a else full_tie_a

    selected_ids: list[str] = []
    already_selected: set[str] = set()
    for _ in range(len(candidates)):
        picked = select_eligible_observation(
            candidates, step, plan, policy, evaluated_at, frozenset(already_selected)
        )
        assert picked is not None
        selected_ids.append(picked.market_observation_id)
        already_selected.add(picked.market_observation_id)

    expected_order = [
        earliest_microsecond.market_observation_id,
        tie_group_first.market_observation_id,
        expected_full_tie_winner.market_observation_id,
        expected_full_tie_loser.market_observation_id,
        latest.market_observation_id,
    ]
    assert selected_ids == expected_order


# ---------------------------------------------------------------------------
# Slippage / fill-price formula, proved against independent literals
# ---------------------------------------------------------------------------


def test_buy_slippage_formula_against_independent_literal() -> None:
    price = compute_fill_price(Decimal("150.000"), Side.BUY, Decimal("5"))
    assert price == Decimal("150.075")
    assert price > Decimal("150.000")


def test_sell_slippage_formula_against_independent_literal() -> None:
    price = compute_fill_price(Decimal("149.995"), Side.SELL, Decimal("5"))
    assert price == Decimal("149.9200025")
    assert price < Decimal("149.995")


def test_zero_slippage_equals_reference_price_exactly() -> None:
    assert compute_fill_price(Decimal("150.000"), Side.BUY, Decimal("0")) == Decimal("150.000")
    assert compute_fill_price(Decimal("149.995"), Side.SELL, Decimal("0")) == Decimal("149.995")


def test_reference_price_uses_ask_for_buy_and_bid_for_sell() -> None:
    observation = _market_observation(bid=Decimal("150.000"), ask=Decimal("150.010"))
    assert reference_price_for_side(observation, Side.BUY) == Decimal("150.010")
    assert reference_price_for_side(observation, Side.SELL) == Decimal("150.000")


# ---------------------------------------------------------------------------
# Partial-fill quantity rule and the worked 1000 -> 400 -> 600 example
# ---------------------------------------------------------------------------


def test_full_remaining_fills_the_whole_remainder() -> None:
    policy = _policy(
        partial_fill_mode=PaperPartialFillMode.FULL_REMAINING, partial_fill_fraction=None
    )
    assert compute_fill_quantity(policy, Decimal("1000")) == Decimal("1000")


def test_fraction_of_remaining_fills_exact_fraction() -> None:
    policy = _policy(
        partial_fill_mode=PaperPartialFillMode.FRACTION_OF_REMAINING,
        partial_fill_fraction=Decimal("0.4"),
    )
    assert compute_fill_quantity(policy, Decimal("1000")) == Decimal("400")


def test_zero_negative_and_overfill_quantities_fail_closed() -> None:
    with pytest.raises(ValueError):
        validate_fill_quantity_invariants(Decimal("0"), Decimal("1000"))
    with pytest.raises(ValueError):
        validate_fill_quantity_invariants(Decimal("-1"), Decimal("1000"))
    with pytest.raises(ValueError):
        validate_fill_quantity_invariants(Decimal("1000.01"), Decimal("1000"))
    # Does not raise for a legitimate boundary value.
    validate_fill_quantity_invariants(Decimal("1000"), Decimal("1000"))


def test_worked_example_step0_partial_then_step1_full_sums_to_original() -> None:
    plan = _plan(original_quantity=Decimal("1000"), maximum_steps=3, intent_created_at=NOW)
    policy_step0 = _fill_policy(
        market_selection_policy_version="market-selection-v1",
        partial_fill_mode=PaperPartialFillMode.FRACTION_OF_REMAINING,
        partial_fill_fraction=Decimal("0.4"),
        maximum_market_age=timedelta(hours=1),
    )
    step0 = _step(
        fill_evaluation_plan_id=plan.fill_evaluation_plan_id,
        ordinal=0,
        evaluation_window_start_at=NOW,
        evaluation_due_at=NOW + timedelta(minutes=1),
        remaining_quantity_before=Decimal("1000"),
        fill_policy_id=policy_step0.paper_fill_policy_id,
    )
    observation0 = _market_observation(received_at=NOW, provider_observed_at=NOW)

    result0 = evaluate_fill_evaluation_step(
        step=step0,
        plan=plan,
        policy=policy_step0,
        candidate_observations=(observation0,),
        already_selected_observation_ids=frozenset(),
        fills_so_far=(),
        evaluated_at=NOW,
        next_order_event_ordinal=1,
    )
    assert isinstance(result0, MarketSelectedStepEvaluation)
    assert result0.fill.fill_quantity == Decimal("400")
    assert result0.fill.remaining_quantity_after == Decimal("600")
    assert [event.state for event in result0.order_events] == [PaperOrderState.PARTIALLY_FILLED]

    policy_step1 = _fill_policy(
        market_selection_policy_version="market-selection-v1",
        partial_fill_mode=PaperPartialFillMode.FULL_REMAINING,
        partial_fill_fraction=None,
        maximum_market_age=timedelta(hours=1),
    )
    step1 = _step(
        fill_evaluation_plan_id=plan.fill_evaluation_plan_id,
        ordinal=1,
        evaluation_window_start_at=NOW + timedelta(minutes=1, seconds=1),
        evaluation_due_at=NOW + timedelta(minutes=2, seconds=1),
        remaining_quantity_before=result0.fill.remaining_quantity_after,
        fill_policy_id=policy_step1.paper_fill_policy_id,
    )
    observation1 = _market_observation(
        received_at=step1.evaluation_window_start_at,
        provider_observed_at=step1.evaluation_window_start_at,
    )

    result1 = evaluate_fill_evaluation_step(
        step=step1,
        plan=plan,
        policy=policy_step1,
        candidate_observations=(observation1,),
        already_selected_observation_ids=frozenset({observation0.market_observation_id}),
        fills_so_far=(result0.fill,),
        evaluated_at=step1.evaluation_window_start_at,
        next_order_event_ordinal=2,
    )
    assert isinstance(result1, MarketSelectedStepEvaluation)
    assert step1.remaining_quantity_before == Decimal("600")
    assert result1.fill.fill_quantity == Decimal("600")
    assert result1.fill.remaining_quantity_after == Decimal("0")
    assert [event.state for event in result1.order_events] == [PaperOrderState.FILLED]

    ordered_sum = result0.fill.fill_quantity + result1.fill.fill_quantity
    assert ordered_sum == plan.original_quantity == Decimal("1000")


# ---------------------------------------------------------------------------
# Next-Step derivation rule
# ---------------------------------------------------------------------------


def _fill_fixture(quantity: Decimal = Decimal("400")) -> PaperFill:
    return PaperFill.create(
        fill_evaluation_step_id="fill-evaluation-step-1",
        market_observation_selection_id="market-observation-selection-1",
        market_observation_id="paper-market-1",
        pair=PAIR,
        side=Side.BUY,
        fill_quantity=quantity,
        fill_price=Decimal("150.010"),
        reference_price=Decimal("150.000"),
        slippage_basis_points=Decimal("5"),
        fill_model_version="fill-model-v1",
        remaining_quantity_before=Decimal("1000"),
        remaining_quantity_after=Decimal("1000") - quantity,
        created_at=NOW,
    )


def test_next_step_rejects_non_market_selected_predecessor() -> None:
    assert (
        is_next_step_legitimate(
            resolution_variant=PaperStepResolutionVariant.NO_MARKET,
            fill=None,
            remaining_quantity_after=Decimal("600"),
            resolved_ordinal=0,
            maximum_steps=3,
        )
        is False
    )


def test_next_step_rejects_zero_remaining_quantity() -> None:
    assert (
        is_next_step_legitimate(
            resolution_variant=PaperStepResolutionVariant.MARKET_SELECTED,
            fill=_fill_fixture(quantity=Decimal("1000")),
            remaining_quantity_after=Decimal("0"),
            resolved_ordinal=0,
            maximum_steps=3,
        )
        is False
    )


def test_next_step_rejects_when_ordinal_plus_one_reaches_maximum_steps() -> None:
    assert (
        is_next_step_legitimate(
            resolution_variant=PaperStepResolutionVariant.MARKET_SELECTED,
            fill=_fill_fixture(),
            remaining_quantity_after=Decimal("600"),
            resolved_ordinal=2,
            maximum_steps=3,
        )
        is False
    )


def test_next_step_is_legitimate_when_all_conditions_hold() -> None:
    assert (
        is_next_step_legitimate(
            resolution_variant=PaperStepResolutionVariant.MARKET_SELECTED,
            fill=_fill_fixture(),
            remaining_quantity_after=Decimal("600"),
            resolved_ordinal=0,
            maximum_steps=3,
        )
        is True
    )


# ---------------------------------------------------------------------------
# PaperAttemptDiagnosticCode precedence
# ---------------------------------------------------------------------------


def test_diagnostic_code_no_observation_for_pair() -> None:
    plan, _ = _plan_and_step()
    candidates = (
        _market_observation(pair=_OTHER_PAIR, received_at=NOW, provider_observed_at=NOW),
    )
    code = derive_attempt_diagnostic_code(candidates, plan)
    assert code is PaperAttemptDiagnosticCode.NO_OBSERVATION_FOR_PAIR


def test_diagnostic_code_all_observations_ineligible() -> None:
    plan, step = _plan_and_step()
    # Matches Pair but received_at precedes intent_created_at -> ineligible.
    ineligible = _market_observation(
        pair=PAIR,
        received_at=plan.intent_created_at - timedelta(seconds=1),
        provider_observed_at=plan.intent_created_at - timedelta(seconds=1),
    )
    assert (
        derive_attempt_diagnostic_code((ineligible,), plan)
        is PaperAttemptDiagnosticCode.ALL_OBSERVATIONS_INELIGIBLE
    )


# ---------------------------------------------------------------------------
# PENDING vs terminal NO_MARKET, boundary at due equality
# ---------------------------------------------------------------------------


def test_no_eligible_observation_before_due_is_pending() -> None:
    plan, step = _plan_and_step()
    policy = _policy()
    result = evaluate_fill_evaluation_step(
        step=step,
        plan=plan,
        policy=policy,
        candidate_observations=(),
        already_selected_observation_ids=frozenset(),
        fills_so_far=(),
        evaluated_at=step.evaluation_due_at - timedelta(microseconds=1),
        next_order_event_ordinal=1,
    )
    assert isinstance(result, PendingStepEvaluation)
    assert result.kind is StepEvaluationKind.PENDING_NO_ELIGIBLE_MARKET


def test_no_eligible_observation_at_due_boundary_is_terminal_no_market() -> None:
    plan, step = _plan_and_step()
    policy = _policy()
    result = evaluate_fill_evaluation_step(
        step=step,
        plan=plan,
        policy=policy,
        candidate_observations=(),
        already_selected_observation_ids=frozenset(),
        fills_so_far=(),
        evaluated_at=step.evaluation_due_at,
        next_order_event_ordinal=1,
    )
    assert isinstance(result, NoMarketStepEvaluation)
    assert result.outcome.terminal_reason_code == "REJECTED_NO_MARKET_EVIDENCE"
    produced_states = [event.state for event in result.order_events]
    assert produced_states == [policy.no_fill_terminal_order_state]


def test_market_selected_at_final_step_emits_partial_then_incomplete_terminal_pair() -> None:
    policy = _policy(
        partial_fill_mode=PaperPartialFillMode.FRACTION_OF_REMAINING,
        partial_fill_fraction=Decimal("0.4"),
    )
    plan = _plan(maximum_steps=2, intent_created_at=NOW)
    step = _step(
        fill_evaluation_plan_id=plan.fill_evaluation_plan_id,
        ordinal=1,
        evaluation_window_start_at=NOW,
        evaluation_due_at=NOW + timedelta(minutes=1),
        remaining_quantity_before=Decimal("1000"),
        fill_policy_id=policy.paper_fill_policy_id,
    )
    observation = _market_observation(received_at=NOW, provider_observed_at=NOW)

    result = evaluate_fill_evaluation_step(
        step=step,
        plan=plan,
        policy=policy,
        candidate_observations=(observation,),
        already_selected_observation_ids=frozenset(),
        fills_so_far=(),
        evaluated_at=NOW,
        next_order_event_ordinal=1,
    )

    assert isinstance(result, MarketSelectedStepEvaluation)
    assert result.fill.fill_quantity == Decimal("400")
    assert result.fill.remaining_quantity_after == Decimal("600")
    assert [event.state for event in result.order_events] == [
        PaperOrderState.PARTIALLY_FILLED,
        policy.incomplete_terminal_order_state,
    ]
    assert [event.event_ordinal for event in result.order_events] == [1, 2]


def test_no_market_after_a_prior_fill_uses_incomplete_terminal_state() -> None:
    plan, step = _plan_and_step(remaining_quantity_before=Decimal("600"))
    policy = _policy()
    prior_fill = _fill_fixture(quantity=Decimal("400"))
    result = evaluate_fill_evaluation_step(
        step=step,
        plan=plan,
        policy=policy,
        candidate_observations=(),
        already_selected_observation_ids=frozenset(),
        fills_so_far=(prior_fill,),
        evaluated_at=step.evaluation_due_at,
        next_order_event_ordinal=2,
    )
    assert isinstance(result, NoMarketStepEvaluation)
    produced_states = [event.state for event in result.order_events]
    assert produced_states == [policy.incomplete_terminal_order_state]
