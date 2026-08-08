import decimal
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fx_core import Currency, CurrencyPair
from swap_bot.models import PositionId, Side
from swap_bot.paper import (
    PAPER_EXACT_ARITHMETIC_V1,
    PAPER_QUOTIENT_ARITHMETIC_V1,
    PaperFill,
    PaperOrderEvent,
    PaperOrderIntentLineage,
    PaperOrderState,
)
from swap_bot.paper.ledger import (
    PAPER_SWAP_ACCRUAL_V1,
    PaperAccountBootstrap,
    PaperAccountMarkSet,
    PaperAccountSnapshot,
    PaperAccountSnapshotPositionInput,
    PaperLedgerEntry,
    PaperLedgerEntryKind,
    PaperLedgerIntegrityError,
    PaperPositionApplicationKind,
    PaperPositionFillApplication,
    PaperPositionSide,
    PaperPositionSnapshot,
    PaperReconciledRecordKind,
    PaperReconciliationOutcome,
    PaperReconciliationResult,
    PaperSwapAccrual,
    PaperSwapAccrualCorrection,
    PaperSwapAccrualOutcome,
    PaperSwapAccrualPolicy,
    PaperSwapNonAccrual,
    compute_effective_amount_before,
    compute_position_application_fields,
    evaluate_paper_swap_accrual,
    next_swap_accrual_correction,
    paper_account_equity_v1,
    paper_account_mark_set_required_coverage_v1,
    paper_available_margin_v1,
    paper_gross_exposure_v1,
    paper_open_order_count_v1,
    paper_open_position_count_v1,
    paper_realized_pnl_v1,
    paper_swap_accrual_outcome_v1,
    paper_swap_accrual_v1,
    paper_swap_rollover_instant_v1,
    paper_unrealized_pnl_v1,
    paper_used_margin_v1,
    paper_weighted_average_entry_price_v1,
    project_paper_position_open_quantity,
    rebuild_account_snapshot,
    rebuild_ledger_entries,
    rebuild_position_fill_applications,
    rebuild_position_snapshot,
    validate_correction_chain,
    validate_swap_accrual_correction,
)
from swap_bot.strategy.swap_evidence import OperationalSwapEvidence
from swap_bot.swap import SwapAvailability

from tests.paper_domain.test_contracts import _execution_intent, _liquidation_intent, _order
from tests.strategy_contracts.factories import NOW, PAIR

_OTHER_PAIR = CurrencyPair.parse("MXN_JPY")
_JPY = Currency("JPY")


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


def _bootstrap(**overrides: object) -> PaperAccountBootstrap:
    values: dict[str, object] = {
        "initial_cash": Decimal("1000000"),
        "settlement_currency": _JPY,
        "margin_policy_version": "margin-policy-v1",
        "leverage": Decimal("25"),
        "unrealized_mark_policy_version": "unrealized-mark-policy-v1",
    }
    values.update(overrides)
    return PaperAccountBootstrap.create(**values)  # type: ignore[arg-type]


def _entry_order(**overrides: object):
    values: dict[str, object] = {"side": Side.BUY}
    values.update(overrides)
    return _order(**values)


def _reduce_order(*, paper_position_id: str, side: Side = Side.SELL, **overrides: object):
    liquidation_intent = _liquidation_intent(position_id=PositionId(paper_position_id))
    lineage = PaperOrderIntentLineage.for_emergency_liquidation(
        liquidation_intent, existing_position_side=Side.BUY if side is Side.SELL else Side.SELL
    )
    values: dict[str, object] = {"intent_lineage": lineage, "side": side}
    values.update(overrides)
    return _order(**values)


def _fill(*, quantity: Decimal, price: Decimal, **overrides: object) -> PaperFill:
    values: dict[str, object] = {
        "fill_evaluation_step_id": "step-1",
        "market_observation_selection_id": "selection-1",
        "market_observation_id": "observation-1",
        "pair": PAIR,
        "side": Side.BUY,
        "fill_quantity": quantity,
        "fill_price": price,
        "reference_price": price,
        "slippage_basis_points": Decimal("0"),
        "fill_model_version": "fill-model-v1",
        "remaining_quantity_before": quantity,
        "remaining_quantity_after": Decimal("0"),
        "created_at": NOW,
    }
    values.update(overrides)
    return PaperFill.create(**values)  # type: ignore[arg-type]


def _application(
    *,
    paper_position_id: str,
    paper_order_id: str,
    paper_fill_id: str,
    application_kind: PaperPositionApplicationKind,
    quantity: Decimal,
    price: Decimal,
    open_quantity_after: Decimal,
    realized_pnl_amount: Decimal | None,
    created_at: datetime = NOW,
) -> PaperPositionFillApplication:
    return PaperPositionFillApplication.create(
        paper_position_id=paper_position_id,
        paper_order_id=paper_order_id,
        paper_fill_id=paper_fill_id,
        application_kind=application_kind,
        quantity=quantity,
        price=price,
        open_quantity_after=open_quantity_after,
        realized_pnl_amount=realized_pnl_amount,
        created_at=created_at,
    )


def _observation(**overrides: object):
    from tests.paper_domain.test_contracts import _market_observation

    return _market_observation(**overrides)


def _swap_policy(**overrides: object) -> PaperSwapAccrualPolicy:
    values: dict[str, object] = {
        "policy_version": "swap-policy-v1",
        "unit_basis_base_units": (("JPY_PER_10K_CURRENCY_PER_DAY", Decimal("10000")),),
        "maximum_swap_age": timedelta(days=3),
        "settlement_currency": _JPY,
    }
    values.update(overrides)
    return PaperSwapAccrualPolicy.create(**values)  # type: ignore[arg-type]


_ROLLOVER_DATE = date(2026, 8, 7)
_ROLLOVER_AT = datetime(2026, 8, 7, 0, 0, tzinfo=UTC)


def _swap_evidence(**overrides: object) -> OperationalSwapEvidence:
    values: dict[str, object] = {
        "evidence_contract_version": "operational-swap-evidence-v1",
        "pair": PAIR,
        "availability": SwapAvailability.AVAILABLE,
        "long_received_amount": Decimal("125.00"),
        "short_received_amount": Decimal("-150.00"),
        "unit_basis": "JPY_PER_10K_CURRENCY_PER_DAY",
        "settlement_currency": _JPY,
        "source": "recorded-swap-source",
        "source_version": "recorded-swap-v1",
        "received_at": _ROLLOVER_AT - timedelta(days=1),
        "effective_from": _ROLLOVER_AT - timedelta(days=10),
        "effective_until": _ROLLOVER_AT + timedelta(days=10),
    }
    values.update(overrides)
    if "provider_observed_at" not in overrides:
        values["provider_observed_at"] = values["received_at"] - timedelta(seconds=1)
    from swap_bot.strategy.versions import OPERATIONAL_SWAP_EVIDENCE_VERSION

    values["evidence_contract_version"] = OPERATIONAL_SWAP_EVIDENCE_VERSION
    return OperationalSwapEvidence.create(**values)  # type: ignore[arg-type]


def _accrual(**overrides: object) -> PaperSwapAccrual:
    values: dict[str, object] = {
        "paper_position_id": "paper-position-1",
        "paper_position_snapshot_id": "paper-position-snapshot-1",
        "swap_evidence_id": "swap-evidence-1",
        "rollover_date": _ROLLOVER_DATE,
        "open_quantity": Decimal("1000"),
        "unit_basis": "JPY_PER_10K_CURRENCY_PER_DAY",
        "base_units_per_unit": Decimal("10000"),
        "settlement_currency": _JPY,
        "policy_version": "swap-policy-v1",
        "amount": Decimal("100"),
        "created_at": NOW,
    }
    values.update(overrides)
    return PaperSwapAccrual.create(**values)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# PaperAccountBootstrap
# ---------------------------------------------------------------------------


def test_bootstrap_create_round_trips_and_rejects_bad_fields() -> None:
    bootstrap = _bootstrap()
    assert bootstrap.paper_account_id.startswith("paper-account-")
    with pytest.raises(ValueError):
        _bootstrap(initial_cash=Decimal("0"))
    with pytest.raises(ValueError):
        _bootstrap(leverage=Decimal("0"))
    with pytest.raises(ValueError):
        _bootstrap(settlement_currency=Currency("USD"))


# ---------------------------------------------------------------------------
# Position projection and the entry-after-reduce-only prohibition
# ---------------------------------------------------------------------------


def test_open_quantity_is_sum_of_entries_minus_reduce_only() -> None:
    entry1 = _application(
        paper_position_id="p1",
        paper_order_id="o1",
        paper_fill_id="f1",
        application_kind=PaperPositionApplicationKind.ENTRY,
        quantity=Decimal("400"),
        price=Decimal("100"),
        open_quantity_after=Decimal("400"),
        realized_pnl_amount=None,
    )
    reduce1 = _application(
        paper_position_id="p1",
        paper_order_id="o2",
        paper_fill_id="f2",
        application_kind=PaperPositionApplicationKind.REDUCE_ONLY,
        quantity=Decimal("200"),
        price=Decimal("110"),
        open_quantity_after=Decimal("200"),
        realized_pnl_amount=Decimal("2000"),
    )
    assert project_paper_position_open_quantity((entry1, reduce1)) == Decimal("200")


def test_entry_after_reduce_only_is_rejected_at_the_acceptance_interleaving() -> None:
    # Exact interleaving from acceptance.md: entry 400@100, close 200@110, then a
    # rejected further entry Fill; average entry price is still 100.
    entry1 = _application(
        paper_position_id="p1",
        paper_order_id="o1",
        paper_fill_id="f1",
        application_kind=PaperPositionApplicationKind.ENTRY,
        quantity=Decimal("400"),
        price=Decimal("100"),
        open_quantity_after=Decimal("400"),
        realized_pnl_amount=None,
    )
    reduce1 = _application(
        paper_position_id="p1",
        paper_order_id="o2",
        paper_fill_id="f2",
        application_kind=PaperPositionApplicationKind.REDUCE_ONLY,
        quantity=Decimal("200"),
        price=Decimal("110"),
        open_quantity_after=Decimal("200"),
        realized_pnl_amount=Decimal("2000"),
    )
    with pytest.raises(PaperLedgerIntegrityError):
        project_paper_position_open_quantity((entry1, reduce1, entry1))
    with pytest.raises(PaperLedgerIntegrityError):
        compute_position_application_fields(
            (entry1, reduce1),
            position_side=PaperPositionSide.LONG,
            application_kind=PaperPositionApplicationKind.ENTRY,
            quantity=Decimal("100"),
            price=Decimal("105"),
        )
    assert paper_weighted_average_entry_price_v1((entry1,)) == Decimal("100")


def test_reduce_only_exceeding_open_quantity_is_rejected() -> None:
    entry1 = _application(
        paper_position_id="p1",
        paper_order_id="o1",
        paper_fill_id="f1",
        application_kind=PaperPositionApplicationKind.ENTRY,
        quantity=Decimal("100"),
        price=Decimal("100"),
        open_quantity_after=Decimal("100"),
        realized_pnl_amount=None,
    )
    with pytest.raises(PaperLedgerIntegrityError):
        compute_position_application_fields(
            (entry1,),
            position_side=PaperPositionSide.LONG,
            application_kind=PaperPositionApplicationKind.REDUCE_ONLY,
            quantity=Decimal("200"),
            price=Decimal("105"),
        )


def test_weighted_average_entry_price_single_and_multi_fill() -> None:
    single = _application(
        paper_position_id="p1",
        paper_order_id="o1",
        paper_fill_id="f1",
        application_kind=PaperPositionApplicationKind.ENTRY,
        quantity=Decimal("100"),
        price=Decimal("100"),
        open_quantity_after=Decimal("100"),
        realized_pnl_amount=None,
    )
    assert paper_weighted_average_entry_price_v1((single,)) == Decimal("100")

    entry1 = _application(
        paper_position_id="p2",
        paper_order_id="o1",
        paper_fill_id="f1",
        application_kind=PaperPositionApplicationKind.ENTRY,
        quantity=Decimal("100"),
        price=Decimal("100"),
        open_quantity_after=Decimal("100"),
        realized_pnl_amount=None,
    )
    entry2 = _application(
        paper_position_id="p2",
        paper_order_id="o2",
        paper_fill_id="f2",
        application_kind=PaperPositionApplicationKind.ENTRY,
        quantity=Decimal("100"),
        price=Decimal("102"),
        open_quantity_after=Decimal("200"),
        realized_pnl_amount=None,
    )
    average = paper_weighted_average_entry_price_v1((entry1, entry2))
    assert average == Decimal("101")

    reduce1 = _application(
        paper_position_id="p2",
        paper_order_id="o3",
        paper_fill_id="f3",
        application_kind=PaperPositionApplicationKind.REDUCE_ONLY,
        quantity=Decimal("50"),
        price=Decimal("110"),
        open_quantity_after=Decimal("150"),
        realized_pnl_amount=Decimal("450"),
    )
    # Reduce-only applications never change the average entry price.
    assert paper_weighted_average_entry_price_v1((entry1, entry2)) == average
    assert project_paper_position_open_quantity((entry1, entry2, reduce1)) == Decimal("150")


# ---------------------------------------------------------------------------
# Realized PnL, cash-flow-exact identity, and quotient-rounding residue
# ---------------------------------------------------------------------------


def test_realized_pnl_long_and_short_profit_and_loss() -> None:
    assert paper_realized_pnl_v1(
        position_side=PaperPositionSide.LONG,
        average_entry_price=Decimal("100"),
        close_price=Decimal("110"),
        quantity=Decimal("50"),
    ) == Decimal("500")
    assert paper_realized_pnl_v1(
        position_side=PaperPositionSide.LONG,
        average_entry_price=Decimal("100"),
        close_price=Decimal("90"),
        quantity=Decimal("50"),
    ) == Decimal("-500")
    assert paper_realized_pnl_v1(
        position_side=PaperPositionSide.SHORT,
        average_entry_price=Decimal("100"),
        close_price=Decimal("90"),
        quantity=Decimal("50"),
    ) == Decimal("500")
    assert paper_realized_pnl_v1(
        position_side=PaperPositionSide.SHORT,
        average_entry_price=Decimal("100"),
        close_price=Decimal("110"),
        quantity=Decimal("50"),
    ) == Decimal("-500")


def test_cash_flow_exact_identity_for_exactly_representable_basis() -> None:
    # Entry 100@100 + 100@102 -> average 101 exactly; close 150@105 + 50@90.
    entry1 = _application(
        paper_position_id="p1",
        paper_order_id="o1",
        paper_fill_id="f1",
        application_kind=PaperPositionApplicationKind.ENTRY,
        quantity=Decimal("100"),
        price=Decimal("100"),
        open_quantity_after=Decimal("100"),
        realized_pnl_amount=None,
    )
    entry2 = _application(
        paper_position_id="p1",
        paper_order_id="o2",
        paper_fill_id="f2",
        application_kind=PaperPositionApplicationKind.ENTRY,
        quantity=Decimal("100"),
        price=Decimal("102"),
        open_quantity_after=Decimal("200"),
        realized_pnl_amount=None,
    )
    average = paper_weighted_average_entry_price_v1((entry1, entry2))
    assert average == Decimal("101")
    realized1 = paper_realized_pnl_v1(
        position_side=PaperPositionSide.LONG,
        average_entry_price=average,
        close_price=Decimal("105"),
        quantity=Decimal("150"),
    )
    realized2 = paper_realized_pnl_v1(
        position_side=PaperPositionSide.LONG,
        average_entry_price=average,
        close_price=Decimal("90"),
        quantity=Decimal("50"),
    )
    total_realized = realized1 + realized2
    independent_cash_flow = (Decimal("150") * Decimal("105") + Decimal("50") * Decimal("90")) - (
        Decimal("100") * Decimal("100") + Decimal("100") * Decimal("102")
    )
    assert total_realized == independent_cash_flow
    assert total_realized == Decimal("50")


def test_quotient_rounding_residue_for_non_exactly_representable_basis() -> None:
    # Entry fills of 100 at 100 and 200 at 101; average does not terminate.
    entry1 = _application(
        paper_position_id="p1",
        paper_order_id="o1",
        paper_fill_id="f1",
        application_kind=PaperPositionApplicationKind.ENTRY,
        quantity=Decimal("100"),
        price=Decimal("100"),
        open_quantity_after=Decimal("100"),
        realized_pnl_amount=None,
    )
    entry2 = _application(
        paper_position_id="p1",
        paper_order_id="o2",
        paper_fill_id="f2",
        application_kind=PaperPositionApplicationKind.ENTRY,
        quantity=Decimal("200"),
        price=Decimal("101"),
        open_quantity_after=Decimal("300"),
        realized_pnl_amount=None,
    )
    basis = paper_weighted_average_entry_price_v1((entry1, entry2))
    with decimal.localcontext(PAPER_QUOTIENT_ARITHMETIC_V1):
        independent_basis = (
            Decimal("100") * Decimal("100") + Decimal("200") * Decimal("101")
        ) / Decimal("300")
    assert basis == independent_basis

    realized = paper_realized_pnl_v1(
        position_side=PaperPositionSide.LONG,
        average_entry_price=basis,
        close_price=Decimal("105"),
        quantity=Decimal("300"),
    )
    naive_cash_flow = Decimal("300") * Decimal("105") - (
        Decimal("100") * Decimal("100") + Decimal("200") * Decimal("101")
    )
    # naive_cash_flow - realized == 300*105 - 30200 - (105 - basis)*300
    #                             == 300*basis - 30200, an exact identity that
    # never re-divides 30200/300 (which does not terminate) and isolates the
    # rounding residue introduced by the frozen quotient basis alone.
    with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
        residue = naive_cash_flow - realized
        expected_residue = Decimal("300") * basis - Decimal("30200")
    assert residue == expected_residue
    assert residue != 0  # the rounding is real, not a coincidental exact match


# ---------------------------------------------------------------------------
# Unrealized PnL
# ---------------------------------------------------------------------------


def test_unrealized_pnl_marks_long_with_bid_and_short_with_ask() -> None:
    observation = _observation(bid=Decimal("150.000"), ask=Decimal("150.010"))
    long_pnl = paper_unrealized_pnl_v1(
        position_side=PaperPositionSide.LONG,
        average_entry_price=Decimal("149.500"),
        observation=observation,
        open_quantity=Decimal("1000"),
    )
    short_pnl = paper_unrealized_pnl_v1(
        position_side=PaperPositionSide.SHORT,
        average_entry_price=Decimal("149.500"),
        observation=observation,
        open_quantity=Decimal("1000"),
    )
    assert long_pnl == (Decimal("150.000") - Decimal("149.500")) * Decimal("1000")
    assert short_pnl == (Decimal("149.500") - Decimal("150.010")) * Decimal("1000")
    assert long_pnl != short_pnl


# ---------------------------------------------------------------------------
# Mark-set coverage
# ---------------------------------------------------------------------------


def test_mark_set_required_coverage_variants() -> None:
    open_pairs = frozenset({PAIR})
    with_order = paper_account_mark_set_required_coverage_v1(
        pre_transaction_open_pairs=open_pairs, order_pair=_OTHER_PAIR
    )
    assert with_order == frozenset({PAIR, _OTHER_PAIR})
    no_fill = paper_account_mark_set_required_coverage_v1(
        pre_transaction_open_pairs=open_pairs, order_pair=None
    )
    assert no_fill == open_pairs


def test_mark_set_exact_coverage_accepted() -> None:
    coverage = frozenset({PAIR, _OTHER_PAIR})
    observations = (
        _observation(pair=_OTHER_PAIR, received_at=NOW, provider_observed_at=NOW),
        _observation(pair=PAIR, received_at=NOW, provider_observed_at=NOW),
    )
    mark_set = PaperAccountMarkSet.create(
        tuple(sorted(observations, key=lambda o: o.pair.symbol)),
        coverage_set=coverage,
        bounding_instant=NOW,
    )
    assert {o.pair for o in mark_set.observations} == coverage


def test_mark_set_rejects_missing_duplicate_and_extra_pair() -> None:
    coverage = frozenset({PAIR, _OTHER_PAIR})
    only_one = (_observation(pair=PAIR, received_at=NOW, provider_observed_at=NOW),)
    with pytest.raises(ValueError):
        PaperAccountMarkSet.create(only_one, coverage_set=coverage, bounding_instant=NOW)

    duplicate = (
        _observation(pair=PAIR, received_at=NOW, provider_observed_at=NOW),
        _observation(pair=PAIR, received_at=NOW, provider_observed_at=NOW),
    )
    with pytest.raises(ValueError):
        PaperAccountMarkSet.create(duplicate, coverage_set=frozenset({PAIR}), bounding_instant=NOW)

    extra = (
        _observation(pair=PAIR, received_at=NOW, provider_observed_at=NOW),
        _observation(pair=_OTHER_PAIR, received_at=NOW, provider_observed_at=NOW),
    )
    with pytest.raises(ValueError):
        PaperAccountMarkSet.create(extra, coverage_set=frozenset({PAIR}), bounding_instant=NOW)


def test_mark_set_no_fill_transaction_coverage_variant() -> None:
    coverage = paper_account_mark_set_required_coverage_v1(
        pre_transaction_open_pairs=frozenset({PAIR}), order_pair=None
    )
    observation = (_observation(pair=PAIR, received_at=NOW, provider_observed_at=NOW),)
    mark_set = PaperAccountMarkSet.create(observation, coverage_set=coverage, bounding_instant=NOW)
    assert mark_set.observations[0].pair == PAIR


def test_mark_set_received_at_must_not_exceed_bounding_instant() -> None:
    coverage = frozenset({PAIR})
    late = (
        _observation(
            pair=PAIR, received_at=NOW + timedelta(seconds=1), provider_observed_at=NOW
        ),
    )
    with pytest.raises(ValueError):
        PaperAccountMarkSet.create(late, coverage_set=coverage, bounding_instant=NOW)
    on_time = (_observation(pair=PAIR, received_at=NOW, provider_observed_at=NOW),)
    PaperAccountMarkSet.create(on_time, coverage_set=coverage, bounding_instant=NOW)


# ---------------------------------------------------------------------------
# Remaining formulas: gross exposure, equity, used/available margin
# ---------------------------------------------------------------------------


def test_gross_exposure_equity_and_margin_formulas() -> None:
    observation = _observation(bid=Decimal("150.000"), ask=Decimal("150.010"))
    gross = paper_gross_exposure_v1(
        ((PaperPositionSide.LONG, Decimal("1000"), observation),)
    )
    assert gross == Decimal("150000.000")

    equity = paper_account_equity_v1(
        cash=Decimal("1000000"),
        realized_pnl_total=Decimal("500"),
        accrued_swap_total=Decimal("-20"),
        unrealized_pnl_total=Decimal("300"),
    )
    assert equity == Decimal("1000780")

    used_margin = paper_used_margin_v1(gross_exposure=gross, leverage=Decimal("25"))
    with decimal.localcontext(PAPER_QUOTIENT_ARITHMETIC_V1):
        expected_used_margin = Decimal("150000.000") / Decimal("25")
    assert used_margin == expected_used_margin

    available_margin = paper_available_margin_v1(equity=equity, used_margin=used_margin)
    assert available_margin == equity - used_margin


# ---------------------------------------------------------------------------
# Cardinality aggregates and their boundaries
# ---------------------------------------------------------------------------


def test_open_position_count_boundary() -> None:
    assert paper_open_position_count_v1({"p1": Decimal("0"), "p2": Decimal("100")}) == 1
    assert paper_open_position_count_v1({}) == 0


def test_open_order_count_excludes_orders_with_no_event_before_projection() -> None:
    accepted = PaperOrderEvent.create(
        paper_order_id="o1",
        event_ordinal=0,
        state=PaperOrderState.ACCEPTED,
        source_evidence_kind="test",
        source_evidence_id=None,
        appended_at=NOW,
    )
    opened = PaperOrderEvent.create(
        paper_order_id="o1",
        event_ordinal=1,
        state=PaperOrderState.OPEN,
        source_evidence_kind="test",
        source_evidence_id=None,
        appended_at=NOW,
    )
    filled = PaperOrderEvent.create(
        paper_order_id="o2",
        event_ordinal=0,
        state=PaperOrderState.ACCEPTED,
        source_evidence_kind="test",
        source_evidence_id=None,
        appended_at=NOW,
    )
    partial = PaperOrderEvent.create(
        paper_order_id="o3",
        event_ordinal=0,
        state=PaperOrderState.ACCEPTED,
        source_evidence_kind="test",
        source_evidence_id=None,
        appended_at=NOW,
    )
    partial_opened = PaperOrderEvent.create(
        paper_order_id="o3",
        event_ordinal=1,
        state=PaperOrderState.OPEN,
        source_evidence_kind="test",
        source_evidence_id=None,
        appended_at=NOW,
    )
    partial2 = PaperOrderEvent.create(
        paper_order_id="o3",
        event_ordinal=2,
        state=PaperOrderState.PARTIALLY_FILLED,
        source_evidence_kind="test",
        source_evidence_id=None,
        appended_at=NOW,
    )
    # o4 exists only after this boundary: its truncated event tuple is empty.
    count = paper_open_order_count_v1(
        {
            "o1": (accepted, opened),
            "o2": (filled,),
            "o3": (partial, partial_opened, partial2),
            "o4": (),
        }
    )
    assert count == 3  # o1 OPEN, o2 ACCEPTED, o3 PARTIALLY_FILLED all count; o4 excluded


# ---------------------------------------------------------------------------
# Swap accrual precedence
# ---------------------------------------------------------------------------


def test_swap_accrual_produces_frozen_formula_result() -> None:
    policy = _swap_policy()
    evidence = _swap_evidence()
    outcome = paper_swap_accrual_outcome_v1(
        evidence=evidence,
        position_pair=PAIR,
        open_quantity=Decimal("1000"),
        policy=policy,
        rollover_at=_ROLLOVER_AT,
    )
    assert outcome is PaperSwapAccrualOutcome.ACCRUED
    amount = paper_swap_accrual_v1(
        open_quantity=Decimal("1000"),
        received_amount=Decimal("125.00"),
        base_units_per_unit=Decimal("10000"),
    )
    with decimal.localcontext(PAPER_QUOTIENT_ARITHMETIC_V1):
        ratio = Decimal("1000") / Decimal("10000")
    with decimal.localcontext(PAPER_EXACT_ARITHMETIC_V1):
        expected = Decimal("125.00") * ratio
    assert amount == expected


@pytest.mark.parametrize(
    ("outcome", "build_kwargs"),
    [
        (PaperSwapAccrualOutcome.NOT_ACCRUED_SWAP_MISSING, {"evidence": None}),
        (
            PaperSwapAccrualOutcome.NOT_ACCRUED_PAIR_MISMATCH,
            {"evidence": _swap_evidence(pair=_OTHER_PAIR)},
        ),
        (
            PaperSwapAccrualOutcome.NOT_ACCRUED_SWAP_UNAVAILABLE,
            {
                "evidence": _swap_evidence(
                    availability=SwapAvailability.UNAVAILABLE,
                    long_received_amount=None,
                    short_received_amount=None,
                    unit_basis=None,
                    settlement_currency=None,
                )
            },
        ),
        (
            PaperSwapAccrualOutcome.NOT_ACCRUED_UNSUPPORTED_UNIT_BASIS,
            {"evidence": _swap_evidence(unit_basis="UNSUPPORTED_BASIS")},
        ),
        (
            PaperSwapAccrualOutcome.NOT_ACCRUED_SWAP_STALE,
            {
                "evidence": _swap_evidence(
                    effective_from=_ROLLOVER_AT + timedelta(days=1),
                    received_at=_ROLLOVER_AT - timedelta(days=2),
                    effective_until=None,
                )
            },
        ),
        (
            PaperSwapAccrualOutcome.NOT_ACCRUED_POSITION_NOT_OPEN,
            {"open_quantity": Decimal("0")},
        ),
    ],
)
def test_swap_accrual_non_accrual_outcomes(outcome, build_kwargs) -> None:
    kwargs: dict[str, object] = {
        "evidence": _swap_evidence(),
        "position_pair": PAIR,
        "open_quantity": Decimal("1000"),
        "policy": _swap_policy(),
        "rollover_at": _ROLLOVER_AT,
    }
    kwargs.update(build_kwargs)
    assert paper_swap_accrual_outcome_v1(**kwargs) is outcome


def test_swap_accrual_unsupported_settlement_currency() -> None:
    evidence = _swap_evidence(settlement_currency=Currency("USD"))
    outcome = paper_swap_accrual_outcome_v1(
        evidence=evidence,
        position_pair=PAIR,
        open_quantity=Decimal("1000"),
        policy=_swap_policy(),
        rollover_at=_ROLLOVER_AT,
    )
    assert outcome is PaperSwapAccrualOutcome.NOT_ACCRUED_UNSUPPORTED_SETTLEMENT_CURRENCY


def test_swap_accrual_stale_because_received_after_rollover_no_lookahead() -> None:
    evidence = _swap_evidence(received_at=_ROLLOVER_AT + timedelta(seconds=1))
    outcome = paper_swap_accrual_outcome_v1(
        evidence=evidence,
        position_pair=PAIR,
        open_quantity=Decimal("1000"),
        policy=_swap_policy(),
        rollover_at=_ROLLOVER_AT,
    )
    assert outcome is PaperSwapAccrualOutcome.NOT_ACCRUED_SWAP_STALE


def test_swap_accrual_stale_because_maximum_age_exceeded() -> None:
    evidence = _swap_evidence(
        effective_from=_ROLLOVER_AT - timedelta(days=10),
        received_at=_ROLLOVER_AT - timedelta(days=5),
        effective_until=None,
    )
    outcome = paper_swap_accrual_outcome_v1(
        evidence=evidence,
        position_pair=PAIR,
        open_quantity=Decimal("1000"),
        policy=_swap_policy(maximum_swap_age=timedelta(days=3)),
        rollover_at=_ROLLOVER_AT,
    )
    assert outcome is PaperSwapAccrualOutcome.NOT_ACCRUED_SWAP_STALE


def test_swap_accrual_precedence_with_two_simultaneous_non_accrual_conditions() -> None:
    # Pair mismatch AND non-AVAILABLE both hold; pair mismatch (clause 2) wins
    # over availability (clause 3).
    evidence = _swap_evidence(
        pair=_OTHER_PAIR,
        availability=SwapAvailability.UNKNOWN,
        long_received_amount=None,
        short_received_amount=None,
        unit_basis=None,
        settlement_currency=None,
    )
    outcome = paper_swap_accrual_outcome_v1(
        evidence=evidence,
        position_pair=PAIR,
        open_quantity=Decimal("1000"),
        policy=_swap_policy(),
        rollover_at=_ROLLOVER_AT,
    )
    assert outcome is PaperSwapAccrualOutcome.NOT_ACCRUED_PAIR_MISMATCH


def test_swap_accrual_open_ended_effective_until_none() -> None:
    evidence = _swap_evidence(effective_until=None)
    outcome = paper_swap_accrual_outcome_v1(
        evidence=evidence,
        position_pair=PAIR,
        open_quantity=Decimal("1000"),
        policy=_swap_policy(maximum_swap_age=timedelta(days=365)),
        rollover_at=_ROLLOVER_AT + timedelta(days=100),
    )
    assert outcome is PaperSwapAccrualOutcome.ACCRUED


def test_swap_accrual_window_and_age_equality_boundaries_are_eligible() -> None:
    # rollover_at == effective_from and rollover_at - received_at ==
    # maximum_swap_age exactly; both are inclusive boundaries.
    evidence = _swap_evidence(
        effective_from=_ROLLOVER_AT,
        effective_until=_ROLLOVER_AT + timedelta(days=30),
        received_at=_ROLLOVER_AT - timedelta(days=3),
    )
    outcome = paper_swap_accrual_outcome_v1(
        evidence=evidence,
        position_pair=PAIR,
        open_quantity=Decimal("1000"),
        policy=_swap_policy(maximum_swap_age=timedelta(days=3)),
        rollover_at=_ROLLOVER_AT,
    )
    assert outcome is PaperSwapAccrualOutcome.ACCRUED

    # rollover_at == effective_until is also inclusive.
    evidence_at_upper_bound = _swap_evidence(
        effective_from=_ROLLOVER_AT - timedelta(days=30),
        effective_until=_ROLLOVER_AT,
        received_at=_ROLLOVER_AT - timedelta(days=1),
    )
    outcome_at_upper_bound = paper_swap_accrual_outcome_v1(
        evidence=evidence_at_upper_bound,
        position_pair=PAIR,
        open_quantity=Decimal("1000"),
        policy=_swap_policy(maximum_swap_age=timedelta(days=3)),
        rollover_at=_ROLLOVER_AT,
    )
    assert outcome_at_upper_bound is PaperSwapAccrualOutcome.ACCRUED


def test_evaluate_paper_swap_accrual_writes_non_accrual_record_for_missing_evidence() -> None:
    record = evaluate_paper_swap_accrual(
        evidence=None,
        paper_position_id="p1",
        paper_position_snapshot_id="snap-1",
        position_pair=PAIR,
        position_side=PaperPositionSide.LONG,
        open_quantity=Decimal("1000"),
        policy=_swap_policy(),
        rollover_date=_ROLLOVER_DATE,
        created_at=NOW,
    )
    assert type(record) is PaperSwapNonAccrual
    assert record.outcome is PaperSwapAccrualOutcome.NOT_ACCRUED_SWAP_MISSING


def test_evaluate_paper_swap_accrual_writes_accrual_for_long_and_short() -> None:
    long_record = evaluate_paper_swap_accrual(
        evidence=_swap_evidence(),
        paper_position_id="p1",
        paper_position_snapshot_id="snap-1",
        position_pair=PAIR,
        position_side=PaperPositionSide.LONG,
        open_quantity=Decimal("1000"),
        policy=_swap_policy(),
        rollover_date=_ROLLOVER_DATE,
        created_at=NOW,
    )
    short_record = evaluate_paper_swap_accrual(
        evidence=_swap_evidence(),
        paper_position_id="p1",
        paper_position_snapshot_id="snap-1",
        position_pair=PAIR,
        position_side=PaperPositionSide.SHORT,
        open_quantity=Decimal("1000"),
        policy=_swap_policy(),
        rollover_date=_ROLLOVER_DATE,
        created_at=NOW,
    )
    assert type(long_record) is PaperSwapAccrual
    assert type(short_record) is PaperSwapAccrual
    assert long_record.amount > 0
    assert short_record.amount < 0


# ---------------------------------------------------------------------------
# rollover instant derivation
# ---------------------------------------------------------------------------


def test_rollover_instant_rejects_non_date_types() -> None:
    class _DateSubclass(date):
        pass

    with pytest.raises(TypeError):
        paper_swap_rollover_instant_v1(datetime(2026, 8, 7, 0, 0, tzinfo=UTC))
    with pytest.raises(TypeError):
        paper_swap_rollover_instant_v1("2026-08-07")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        paper_swap_rollover_instant_v1(_DateSubclass(2026, 8, 7))


def test_rollover_instant_is_utc_midnight_and_boundary_changes_outcome() -> None:
    assert paper_swap_rollover_instant_v1(date(2026, 8, 7)) == datetime(
        2026, 8, 7, 0, 0, 0, 0, tzinfo=UTC
    )
    # An evidence window ending exactly at 2026-08-07 00:00 UTC is eligible for
    # that rollover date but stale for the next UTC day.
    evidence = _swap_evidence(
        effective_from=NOW - timedelta(days=10),
        effective_until=datetime(2026, 8, 7, 0, 0, tzinfo=UTC),
        received_at=NOW - timedelta(days=10),
    )
    policy = _swap_policy(maximum_swap_age=timedelta(days=365))
    same_day_outcome = paper_swap_accrual_outcome_v1(
        evidence=evidence,
        position_pair=PAIR,
        open_quantity=Decimal("1000"),
        policy=policy,
        rollover_at=paper_swap_rollover_instant_v1(date(2026, 8, 7)),
    )
    next_day_outcome = paper_swap_accrual_outcome_v1(
        evidence=evidence,
        position_pair=PAIR,
        open_quantity=Decimal("1000"),
        policy=policy,
        rollover_at=paper_swap_rollover_instant_v1(date(2026, 8, 8)),
    )
    assert same_day_outcome is PaperSwapAccrualOutcome.ACCRUED
    assert next_day_outcome is PaperSwapAccrualOutcome.NOT_ACCRUED_SWAP_STALE


# ---------------------------------------------------------------------------
# Swap accrual correction chain
# ---------------------------------------------------------------------------


def test_two_sequential_corrections_converge_to_last_replacement_amount() -> None:
    accrual = _accrual(amount=Decimal("100"))
    correction1 = next_swap_accrual_correction(
        original_accrual=accrual,
        existing_chain=(),
        chain_ordinal=1,
        predecessor_correction_id=None,
        replacement_amount=Decimal("120"),
        correction_reason="rate correction 1",
        swap_evidence_id="swap-evidence-2",
        created_at=NOW,
    )
    assert correction1.effective_amount_before == Decimal("100")
    assert correction1.delta_amount == Decimal("20")

    correction2 = next_swap_accrual_correction(
        original_accrual=accrual,
        existing_chain=(correction1,),
        chain_ordinal=2,
        predecessor_correction_id=correction1.correction_id,
        replacement_amount=Decimal("130"),
        correction_reason="rate correction 2",
        swap_evidence_id="swap-evidence-3",
        created_at=NOW,
    )
    assert correction2.effective_amount_before == Decimal("120")
    assert correction2.delta_amount == Decimal("10")

    ledger_amounts = (accrual.amount, correction1.delta_amount, correction2.delta_amount)
    assert ledger_amounts == (Decimal("100"), Decimal("20"), Decimal("10"))
    total = sum(ledger_amounts, start=Decimal(0))
    assert total == Decimal("130")


def test_oscillating_chain_converges_with_three_distinct_correction_ids() -> None:
    accrual = _accrual(amount=Decimal("100"))
    correction1 = next_swap_accrual_correction(
        original_accrual=accrual,
        existing_chain=(),
        chain_ordinal=1,
        predecessor_correction_id=None,
        replacement_amount=Decimal("120"),
        correction_reason="up",
        swap_evidence_id="swap-evidence-2",
        created_at=NOW,
    )
    correction2 = next_swap_accrual_correction(
        original_accrual=accrual,
        existing_chain=(correction1,),
        chain_ordinal=2,
        predecessor_correction_id=correction1.correction_id,
        replacement_amount=Decimal("100"),
        correction_reason="down",
        swap_evidence_id="swap-evidence-3",
        created_at=NOW,
    )
    correction3 = next_swap_accrual_correction(
        original_accrual=accrual,
        existing_chain=(correction1, correction2),
        chain_ordinal=3,
        predecessor_correction_id=correction2.correction_id,
        replacement_amount=Decimal("120"),
        correction_reason="up again",
        swap_evidence_id="swap-evidence-4",
        created_at=NOW,
    )
    deltas = (correction1.delta_amount, correction2.delta_amount, correction3.delta_amount)
    assert deltas == (Decimal("20"), Decimal("-20"), Decimal("20"))
    ledger_amounts = (accrual.amount, *deltas)
    assert ledger_amounts == (Decimal("100"), Decimal("20"), Decimal("-20"), Decimal("20"))
    assert sum(ledger_amounts, start=Decimal(0)) == Decimal("120")

    correction_ids = {
        correction1.correction_id,
        correction2.correction_id,
        correction3.correction_id,
    }
    assert len(correction_ids) == 3
    assert correction1.correction_id != correction3.correction_id


def test_chain_integrity_rejections() -> None:
    accrual = _accrual(amount=Decimal("100"))
    correction1 = next_swap_accrual_correction(
        original_accrual=accrual,
        existing_chain=(),
        chain_ordinal=1,
        predecessor_correction_id=None,
        replacement_amount=Decimal("120"),
        correction_reason="up",
        swap_evidence_id="swap-evidence-2",
        created_at=NOW,
    )

    with pytest.raises(PaperLedgerIntegrityError):
        # wrong ordinal
        validate_correction_chain((), chain_ordinal=2, predecessor_correction_id=None)
    with pytest.raises(PaperLedgerIntegrityError):
        validate_correction_chain(
            (correction1,), chain_ordinal=2, predecessor_correction_id="not-the-real-predecessor"
        )
    with pytest.raises(PaperLedgerIntegrityError):
        validate_correction_chain((), chain_ordinal=1, predecessor_correction_id="should-be-none")
    with pytest.raises(PaperLedgerIntegrityError):
        validate_correction_chain(
            (correction1,), chain_ordinal=2, predecessor_correction_id=None
        )  # predecessor required above ordinal 1

    # A self-consistent correction (its own ID matches its own content) that
    # disagrees with the actual chain total is caught by the semantic check.
    wrong_effective_before = PaperSwapAccrualCorrection.create(
        corrected_accrual_id=accrual.paper_swap_accrual_id,
        chain_ordinal=2,
        predecessor_correction_id=correction1.correction_id,
        effective_amount_before=Decimal("999"),
        replacement_amount=Decimal("130"),
        correction_reason="wrong basis",
        swap_evidence_id="swap-evidence-3",
        created_at=NOW,
    )
    with pytest.raises(PaperLedgerIntegrityError):
        validate_swap_accrual_correction(
            wrong_effective_before, original_accrual=accrual, existing_chain=(correction1,)
        )


def test_effective_amount_before_uses_current_not_original_amount() -> None:
    accrual = _accrual(amount=Decimal("100"))
    correction1 = next_swap_accrual_correction(
        original_accrual=accrual,
        existing_chain=(),
        chain_ordinal=1,
        predecessor_correction_id=None,
        replacement_amount=Decimal("120"),
        correction_reason="up",
        swap_evidence_id="swap-evidence-2",
        created_at=NOW,
    )
    assert compute_effective_amount_before(accrual.amount, (correction1,)) == Decimal("120")
    assert compute_effective_amount_before(accrual.amount, ()) == Decimal("100")


# ---------------------------------------------------------------------------
# Reconciliation: PaperReconciliationResult basics
# ---------------------------------------------------------------------------


def test_reconciliation_result_matched_iff_both_mismatch_tuples_empty() -> None:
    matched = PaperReconciliationResult.create(
        paper_account_id="paper-account-1",
        reconciled_position_ids=("p1", "p2"),
        highest_application_seq=5,
        highest_ledger_entry_seq=3,
        highest_order_event_seq=2,
        mismatched_record_kinds=(),
        mismatched_record_ids=(),
        created_at=NOW,
    )
    assert matched.outcome is PaperReconciliationOutcome.MATCHED

    mismatched = PaperReconciliationResult.create(
        paper_account_id="paper-account-1",
        reconciled_position_ids=("p1", "p2"),
        highest_application_seq=5,
        highest_ledger_entry_seq=3,
        highest_order_event_seq=2,
        mismatched_record_kinds=(PaperReconciledRecordKind.LEDGER_ENTRY,),
        mismatched_record_ids=("ledger-entry-1",),
        created_at=NOW,
    )
    assert mismatched.outcome is PaperReconciliationOutcome.MISMATCHED


# ---------------------------------------------------------------------------
# Reconciliation rebuild 1: POSITION_FILL_APPLICATION
# ---------------------------------------------------------------------------


def _matched_entry_reduce_fixture():
    entry_order = _entry_order()
    entry_position_id = entry_order.intent_lineage.paper_position_id
    entry_fill = _fill(quantity=Decimal("400"), price=Decimal("100"))
    entry_application = PaperPositionFillApplication.create(
        paper_position_id=entry_position_id,
        paper_order_id=entry_order.paper_order_id,
        paper_fill_id=entry_fill.paper_fill_id,
        application_kind=PaperPositionApplicationKind.ENTRY,
        quantity=entry_fill.fill_quantity,
        price=entry_fill.fill_price,
        open_quantity_after=Decimal("400"),
        realized_pnl_amount=None,
        created_at=NOW,
    )
    reduce_order = _reduce_order(paper_position_id=entry_position_id)
    reduce_fill = _fill(quantity=Decimal("200"), price=Decimal("110"))
    reduce_application = PaperPositionFillApplication.create(
        paper_position_id=entry_position_id,
        paper_order_id=reduce_order.paper_order_id,
        paper_fill_id=reduce_fill.paper_fill_id,
        application_kind=PaperPositionApplicationKind.REDUCE_ONLY,
        quantity=reduce_fill.fill_quantity,
        price=reduce_fill.fill_price,
        open_quantity_after=Decimal("200"),
        realized_pnl_amount=Decimal("2000"),
        created_at=NOW,
    )
    orders = {entry_order.paper_order_id: entry_order, reduce_order.paper_order_id: reduce_order}
    fills = {entry_fill.paper_fill_id: entry_fill, reduce_fill.paper_fill_id: reduce_fill}
    return entry_application, reduce_application, orders, fills


def test_rebuild_position_fill_applications_matched() -> None:
    entry_application, reduce_application, orders, fills = _matched_entry_reduce_fixture()
    mismatches = rebuild_position_fill_applications(
        (entry_application, reduce_application), fills, orders
    )
    assert mismatches == ()


@pytest.mark.parametrize(
    "tamper",
    [
        "open_quantity_after",
        "realized_pnl_amount",
        "paper_position_id",
        "quantity",
        "paper_order_id",
        "price",
    ],
)
def test_rebuild_position_fill_applications_detects_each_tampered_field(tamper: str) -> None:
    entry_application, reduce_application, orders, fills = _matched_entry_reduce_fixture()
    if tamper == "open_quantity_after":
        tampered = PaperPositionFillApplication.create(
            paper_position_id=reduce_application.paper_position_id,
            paper_order_id=reduce_application.paper_order_id,
            paper_fill_id=reduce_application.paper_fill_id,
            application_kind=reduce_application.application_kind,
            quantity=reduce_application.quantity,
            price=reduce_application.price,
            open_quantity_after=Decimal("199"),
            realized_pnl_amount=reduce_application.realized_pnl_amount,
            created_at=reduce_application.created_at,
        )
    elif tamper == "realized_pnl_amount":
        tampered = PaperPositionFillApplication.create(
            paper_position_id=reduce_application.paper_position_id,
            paper_order_id=reduce_application.paper_order_id,
            paper_fill_id=reduce_application.paper_fill_id,
            application_kind=reduce_application.application_kind,
            quantity=reduce_application.quantity,
            price=reduce_application.price,
            open_quantity_after=reduce_application.open_quantity_after,
            realized_pnl_amount=Decimal("1"),
            created_at=reduce_application.created_at,
        )
    elif tamper == "paper_position_id":
        tampered = PaperPositionFillApplication.create(
            paper_position_id="paper-position-forged",
            paper_order_id=reduce_application.paper_order_id,
            paper_fill_id=reduce_application.paper_fill_id,
            application_kind=reduce_application.application_kind,
            quantity=reduce_application.quantity,
            price=reduce_application.price,
            open_quantity_after=reduce_application.open_quantity_after,
            realized_pnl_amount=reduce_application.realized_pnl_amount,
            created_at=reduce_application.created_at,
        )
    elif tamper == "quantity":
        tampered = PaperPositionFillApplication.create(
            paper_position_id=reduce_application.paper_position_id,
            paper_order_id=reduce_application.paper_order_id,
            paper_fill_id=reduce_application.paper_fill_id,
            application_kind=reduce_application.application_kind,
            quantity=Decimal("199"),
            price=reduce_application.price,
            open_quantity_after=reduce_application.open_quantity_after,
            realized_pnl_amount=reduce_application.realized_pnl_amount,
            created_at=reduce_application.created_at,
        )
    elif tamper == "paper_order_id":
        # Point at a different order that already resolves in the fixture's
        # orders mapping (the entry order instead of the reduce order).
        tampered = PaperPositionFillApplication.create(
            paper_position_id=reduce_application.paper_position_id,
            paper_order_id=entry_application.paper_order_id,
            paper_fill_id=reduce_application.paper_fill_id,
            application_kind=reduce_application.application_kind,
            quantity=reduce_application.quantity,
            price=reduce_application.price,
            open_quantity_after=reduce_application.open_quantity_after,
            realized_pnl_amount=reduce_application.realized_pnl_amount,
            created_at=reduce_application.created_at,
        )
    else:
        tampered = PaperPositionFillApplication.create(
            paper_position_id=reduce_application.paper_position_id,
            paper_order_id=reduce_application.paper_order_id,
            paper_fill_id=reduce_application.paper_fill_id,
            application_kind=reduce_application.application_kind,
            quantity=reduce_application.quantity,
            price=Decimal("999"),
            open_quantity_after=reduce_application.open_quantity_after,
            realized_pnl_amount=reduce_application.realized_pnl_amount,
            created_at=reduce_application.created_at,
        )
    mismatches = rebuild_position_fill_applications((entry_application, tampered), fills, orders)
    assert mismatches == (tampered.paper_position_fill_application_id,)


def test_rebuild_position_fill_applications_detects_unresolvable_order_id() -> None:
    # A paper_order_id absent from the orders mapping entirely (e.g. a
    # B4-hydrated orders mapping incomplete relative to persisted
    # applications) must report a typed mismatch, never raise KeyError.
    entry_application, reduce_application, orders, fills = _matched_entry_reduce_fixture()
    tampered = PaperPositionFillApplication.create(
        paper_position_id=reduce_application.paper_position_id,
        paper_order_id="paper-order-does-not-exist",
        paper_fill_id=reduce_application.paper_fill_id,
        application_kind=reduce_application.application_kind,
        quantity=reduce_application.quantity,
        price=reduce_application.price,
        open_quantity_after=reduce_application.open_quantity_after,
        realized_pnl_amount=reduce_application.realized_pnl_amount,
        created_at=reduce_application.created_at,
    )
    mismatches = rebuild_position_fill_applications((entry_application, tampered), fills, orders)
    assert mismatches == (tampered.paper_position_fill_application_id,)


def test_rebuild_position_fill_applications_detects_wrong_persisted_kind() -> None:
    entry_application, reduce_application, orders, fills = _matched_entry_reduce_fixture()
    # A close Fill persisted as ENTRY: kind disagrees with the owning order's
    # intent_lineage.intent_kind even though open_quantity_after/realized_pnl
    # are self-consistent for that (wrong) kind.
    wrong_kind = PaperPositionFillApplication.create(
        paper_position_id=reduce_application.paper_position_id,
        paper_order_id=reduce_application.paper_order_id,
        paper_fill_id=reduce_application.paper_fill_id,
        application_kind=PaperPositionApplicationKind.ENTRY,
        quantity=reduce_application.quantity,
        price=reduce_application.price,
        open_quantity_after=entry_application.open_quantity_after + reduce_application.quantity,
        realized_pnl_amount=None,
        created_at=reduce_application.created_at,
    )
    mismatches = rebuild_position_fill_applications((entry_application, wrong_kind), fills, orders)
    assert mismatches == (wrong_kind.paper_position_fill_application_id,)


def test_rebuild_position_fill_applications_detects_entry_after_reduce_only_on_read_path() -> None:
    entry_application, reduce_application, orders, fills = _matched_entry_reduce_fixture()
    second_entry_order = _entry_order(
        intent_lineage=PaperOrderIntentLineage.for_entry(
            _execution_intent(idempotency_key="different-entry-idem")
        )
    )
    second_entry_fill = _fill(quantity=Decimal("50"), price=Decimal("103"))
    second_entry_application = PaperPositionFillApplication.create(
        paper_position_id=entry_application.paper_position_id,
        paper_order_id=second_entry_order.paper_order_id,
        paper_fill_id=second_entry_fill.paper_fill_id,
        application_kind=PaperPositionApplicationKind.ENTRY,
        quantity=second_entry_fill.fill_quantity,
        price=second_entry_fill.fill_price,
        open_quantity_after=Decimal("250"),
        realized_pnl_amount=None,
        created_at=NOW,
    )
    orders = dict(orders)
    orders[second_entry_order.paper_order_id] = second_entry_order
    fills = dict(fills)
    fills[second_entry_fill.paper_fill_id] = second_entry_fill
    mismatches = rebuild_position_fill_applications(
        (entry_application, reduce_application, second_entry_application), fills, orders
    )
    assert second_entry_application.paper_position_fill_application_id in mismatches


# ---------------------------------------------------------------------------
# Reconciliation rebuild 2: LEDGER_ENTRY
# ---------------------------------------------------------------------------


def test_rebuild_ledger_entries_matched_and_amount_tamper_detected() -> None:
    entry_application, reduce_application, orders, fills = _matched_entry_reduce_fixture()
    ledger_entry = PaperLedgerEntry.create(
        paper_account_id="paper-account-1",
        paper_position_id=reduce_application.paper_position_id,
        entry_kind=PaperLedgerEntryKind.REALIZED_PNL,
        settlement_currency=_JPY,
        amount=reduce_application.realized_pnl_amount,
        source_evidence_kind="PAPER_POSITION_FILL_APPLICATION",
        source_evidence_id=reduce_application.paper_position_fill_application_id,
        formula_version="paper-realized-pnl-v1",
        created_at=NOW,
    )
    realized_pnl_sources = {
        reduce_application.paper_position_fill_application_id: reduce_application
    }
    position_accounts = {reduce_application.paper_position_id: "paper-account-1"}
    mismatches = rebuild_ledger_entries(
        (ledger_entry,),
        realized_pnl_sources=realized_pnl_sources,
        swap_accruals={},
        swap_corrections={},
        position_accounts=position_accounts,
    )
    assert mismatches == ()

    tampered_entry = PaperLedgerEntry.create(
        paper_account_id="paper-account-1",
        paper_position_id=reduce_application.paper_position_id,
        entry_kind=PaperLedgerEntryKind.REALIZED_PNL,
        settlement_currency=_JPY,
        amount=Decimal("1"),
        source_evidence_kind="PAPER_POSITION_FILL_APPLICATION",
        source_evidence_id=reduce_application.paper_position_fill_application_id,
        formula_version="paper-realized-pnl-v1",
        created_at=NOW,
    )
    mismatches = rebuild_ledger_entries(
        (tampered_entry,),
        realized_pnl_sources=realized_pnl_sources,
        swap_accruals={},
        swap_corrections={},
        position_accounts=position_accounts,
    )
    assert mismatches == (tampered_entry.ledger_entry_id,)


def test_rebuild_ledger_entries_cross_account_mismatch() -> None:
    entry_application, reduce_application, orders, fills = _matched_entry_reduce_fixture()
    wrong_account_entry = PaperLedgerEntry.create(
        paper_account_id="paper-account-OTHER",
        paper_position_id=reduce_application.paper_position_id,
        entry_kind=PaperLedgerEntryKind.REALIZED_PNL,
        settlement_currency=_JPY,
        amount=reduce_application.realized_pnl_amount,
        source_evidence_kind="PAPER_POSITION_FILL_APPLICATION",
        source_evidence_id=reduce_application.paper_position_fill_application_id,
        formula_version="paper-realized-pnl-v1",
        created_at=NOW,
    )
    realized_pnl_sources = {
        reduce_application.paper_position_fill_application_id: reduce_application
    }
    position_accounts = {reduce_application.paper_position_id: "paper-account-1"}
    mismatches = rebuild_ledger_entries(
        (wrong_account_entry,),
        realized_pnl_sources=realized_pnl_sources,
        swap_accruals={},
        swap_corrections={},
        position_accounts=position_accounts,
    )
    assert mismatches == (wrong_account_entry.ledger_entry_id,)


# ---------------------------------------------------------------------------
# Reconciliation rebuild 3: POSITION_SNAPSHOT
# ---------------------------------------------------------------------------


def test_rebuild_position_snapshot_matched_and_field_tamper_detected() -> None:
    entry_application, reduce_application, orders, fills = _matched_entry_reduce_fixture()
    swap_entry = PaperLedgerEntry.create(
        paper_account_id="paper-account-1",
        paper_position_id=entry_application.paper_position_id,
        entry_kind=PaperLedgerEntryKind.SWAP_ACCRUAL,
        settlement_currency=_JPY,
        amount=Decimal("30"),
        source_evidence_kind="PAPER_SWAP_ACCRUAL",
        source_evidence_id="paper-swap-accrual-1",
        formula_version=PAPER_SWAP_ACCRUAL_V1,
        created_at=NOW,
    )
    snapshot = PaperPositionSnapshot.create(
        paper_account_id="paper-account-1",
        paper_position_id=entry_application.paper_position_id,
        pair=PAIR,
        position_side=PaperPositionSide.LONG,
        open_quantity=Decimal("200"),
        average_entry_price=Decimal("100"),
        realized_pnl_total=Decimal("2000"),
        accrued_swap_total=Decimal("30"),
        highest_application_seq=2,
        highest_ledger_entry_seq=1,
        created_at=NOW,
    )
    assert rebuild_position_snapshot(
        snapshot,
        paper_account_id="paper-account-1",
        pair=PAIR,
        position_side=PaperPositionSide.LONG,
        applications=(entry_application, reduce_application),
        swap_ledger_entries=(swap_entry,),
        highest_application_seq=2,
        highest_ledger_entry_seq=1,
    )

    tampered_snapshot = PaperPositionSnapshot.create(
        paper_account_id="paper-account-1",
        paper_position_id=entry_application.paper_position_id,
        pair=PAIR,
        position_side=PaperPositionSide.LONG,
        open_quantity=Decimal("200"),
        average_entry_price=Decimal("100"),
        realized_pnl_total=Decimal("1999"),
        accrued_swap_total=Decimal("30"),
        highest_application_seq=2,
        highest_ledger_entry_seq=1,
        created_at=NOW,
    )
    assert not rebuild_position_snapshot(
        tampered_snapshot,
        paper_account_id="paper-account-1",
        pair=PAIR,
        position_side=PaperPositionSide.LONG,
        applications=(entry_application, reduce_application),
        swap_ledger_entries=(swap_entry,),
        highest_application_seq=2,
        highest_ledger_entry_seq=1,
    )


@pytest.mark.parametrize(
    "override",
    [
        {"paper_account_id": "paper-account-OTHER"},
        {"pair": _OTHER_PAIR},
        {"position_side": PaperPositionSide.SHORT},
        {"open_quantity": Decimal("199")},
        {"average_entry_price": Decimal("99")},
        {"realized_pnl_total": Decimal("1")},
        {"accrued_swap_total": Decimal("1")},
        {"highest_application_seq": 99},
        {"highest_ledger_entry_seq": 99},
    ],
)
def test_rebuild_position_snapshot_detects_every_retained_field(override: dict) -> None:
    entry_application, reduce_application, orders, fills = _matched_entry_reduce_fixture()
    swap_entry = PaperLedgerEntry.create(
        paper_account_id="paper-account-1",
        paper_position_id=entry_application.paper_position_id,
        entry_kind=PaperLedgerEntryKind.SWAP_ACCRUAL,
        settlement_currency=_JPY,
        amount=Decimal("30"),
        source_evidence_kind="PAPER_SWAP_ACCRUAL",
        source_evidence_id="paper-swap-accrual-1",
        formula_version=PAPER_SWAP_ACCRUAL_V1,
        created_at=NOW,
    )
    base_kwargs: dict[str, object] = {
        "paper_account_id": "paper-account-1",
        "paper_position_id": entry_application.paper_position_id,
        "pair": PAIR,
        "position_side": PaperPositionSide.LONG,
        "open_quantity": Decimal("200"),
        "average_entry_price": Decimal("100"),
        "realized_pnl_total": Decimal("2000"),
        "accrued_swap_total": Decimal("30"),
        "highest_application_seq": 2,
        "highest_ledger_entry_seq": 1,
        "created_at": NOW,
    }
    base_kwargs.update(override)
    tampered_snapshot = PaperPositionSnapshot.create(**base_kwargs)  # type: ignore[arg-type]
    assert not rebuild_position_snapshot(
        tampered_snapshot,
        paper_account_id="paper-account-1",
        pair=PAIR,
        position_side=PaperPositionSide.LONG,
        applications=(entry_application, reduce_application),
        swap_ledger_entries=(swap_entry,),
        highest_application_seq=2,
        highest_ledger_entry_seq=1,
    )


# ---------------------------------------------------------------------------
# Reconciliation rebuild 4: ACCOUNT_SNAPSHOT
# ---------------------------------------------------------------------------


def _account_snapshot_fixture():
    bootstrap = _bootstrap()
    entry_application, reduce_application, orders, fills = _matched_entry_reduce_fixture()
    ledger_entry = PaperLedgerEntry.create(
        paper_account_id=bootstrap.paper_account_id,
        paper_position_id=reduce_application.paper_position_id,
        entry_kind=PaperLedgerEntryKind.REALIZED_PNL,
        settlement_currency=_JPY,
        amount=reduce_application.realized_pnl_amount,
        source_evidence_kind="PAPER_POSITION_FILL_APPLICATION",
        source_evidence_id=reduce_application.paper_position_fill_application_id,
        formula_version="paper-realized-pnl-v1",
        created_at=NOW,
    )
    observation = _observation(pair=PAIR, bid=Decimal("150.000"), ask=Decimal("150.010"))
    position_input = PaperAccountSnapshotPositionInput(
        paper_position_id=entry_application.paper_position_id,
        pair=PAIR,
        position_side=PaperPositionSide.LONG,
        applications=(entry_application, reduce_application),
    )
    open_quantity = Decimal("200")
    average_entry_price = Decimal("100")
    unrealized = paper_unrealized_pnl_v1(
        position_side=PaperPositionSide.LONG,
        average_entry_price=average_entry_price,
        observation=observation,
        open_quantity=open_quantity,
    )
    gross = paper_gross_exposure_v1(((PaperPositionSide.LONG, open_quantity, observation),))
    equity = paper_account_equity_v1(
        cash=bootstrap.initial_cash,
        realized_pnl_total=reduce_application.realized_pnl_amount,
        accrued_swap_total=Decimal(0),
        unrealized_pnl_total=unrealized,
    )
    used_margin = paper_used_margin_v1(gross_exposure=gross, leverage=bootstrap.leverage)
    available_margin = paper_available_margin_v1(equity=equity, used_margin=used_margin)
    base_kwargs: dict[str, object] = {
        "paper_account_id": bootstrap.paper_account_id,
        "cash": bootstrap.initial_cash,
        "realized_pnl_total": reduce_application.realized_pnl_amount,
        "unrealized_pnl_total": unrealized,
        "accrued_swap_total": Decimal(0),
        "equity": equity,
        "used_margin": used_margin,
        "available_margin": available_margin,
        "gross_exposure": gross,
        "open_position_count": 1,
        "open_order_count": 0,
        "mark_observation_ids": (observation.market_observation_id,),
        "highest_application_seq": 2,
        "highest_ledger_entry_seq": 1,
        "highest_order_event_seq": 0,
        "margin_policy_version": bootstrap.margin_policy_version,
        "unrealized_mark_policy_version": bootstrap.unrealized_mark_policy_version,
        "created_at": NOW,
    }
    snapshot = PaperAccountSnapshot.create(**base_kwargs)  # type: ignore[arg-type]
    return bootstrap, snapshot, position_input, ledger_entry, observation, base_kwargs


def test_rebuild_account_snapshot_matched() -> None:
    bootstrap, snapshot, position_input, ledger_entry, observation, _ = _account_snapshot_fixture()
    assert rebuild_account_snapshot(
        snapshot,
        bootstrap=bootstrap,
        positions=(position_input,),
        ledger_entries=(ledger_entry,),
        observations_by_pair={PAIR.symbol: observation},
        order_events_by_order={},
        highest_application_seq=2,
        highest_ledger_entry_seq=1,
        highest_order_event_seq=0,
    )


@pytest.mark.parametrize(
    "override",
    [
        {"cash": Decimal("999999")},
        {"realized_pnl_total": Decimal("1")},
        {"unrealized_pnl_total": Decimal("1")},
        {"accrued_swap_total": Decimal("1")},
        {"equity": Decimal("1")},
        {"used_margin": Decimal("1")},
        {"available_margin": Decimal("1")},
        {"gross_exposure": Decimal("1")},
        {"open_position_count": 0},
        {"open_order_count": 5},
        {"mark_observation_ids": ("forged-observation-id",)},
        {"highest_application_seq": 99},
        {"highest_ledger_entry_seq": 99},
        {"highest_order_event_seq": 99},
    ],
)
def test_rebuild_account_snapshot_detects_every_retained_aggregate(override: dict) -> None:
    bootstrap, _, position_input, ledger_entry, observation, base_kwargs = (
        _account_snapshot_fixture()
    )
    tampered_kwargs = dict(base_kwargs)
    tampered_kwargs.update(override)
    tampered = PaperAccountSnapshot.create(**tampered_kwargs)  # type: ignore[arg-type]
    assert not rebuild_account_snapshot(
        tampered,
        bootstrap=bootstrap,
        positions=(position_input,),
        ledger_entries=(ledger_entry,),
        observations_by_pair={PAIR.symbol: observation},
        order_events_by_order={},
        highest_application_seq=2,
        highest_ledger_entry_seq=1,
        highest_order_event_seq=0,
    )


def test_rebuild_account_snapshot_two_pairs_all_aggregates_include_both() -> None:
    bootstrap = _bootstrap()
    entry_order_1 = _entry_order()
    position_id_1 = entry_order_1.intent_lineage.paper_position_id
    fill_1 = _fill(quantity=Decimal("1000"), price=Decimal("150"), pair=PAIR)
    application_1 = PaperPositionFillApplication.create(
        paper_position_id=position_id_1,
        paper_order_id=entry_order_1.paper_order_id,
        paper_fill_id=fill_1.paper_fill_id,
        application_kind=PaperPositionApplicationKind.ENTRY,
        quantity=fill_1.fill_quantity,
        price=fill_1.fill_price,
        open_quantity_after=Decimal("1000"),
        realized_pnl_amount=None,
        created_at=NOW,
    )
    entry_order_2 = _entry_order(
        intent_lineage=PaperOrderIntentLineage.for_entry(
            _execution_intent(idempotency_key="second-pair-entry", pair=_OTHER_PAIR)
        ),
        pair=_OTHER_PAIR,
    )
    position_id_2 = entry_order_2.intent_lineage.paper_position_id
    fill_2 = _fill(quantity=Decimal("2000"), price=Decimal("6.0"), pair=_OTHER_PAIR)
    application_2 = PaperPositionFillApplication.create(
        paper_position_id=position_id_2,
        paper_order_id=entry_order_2.paper_order_id,
        paper_fill_id=fill_2.paper_fill_id,
        application_kind=PaperPositionApplicationKind.ENTRY,
        quantity=fill_2.fill_quantity,
        price=fill_2.fill_price,
        open_quantity_after=Decimal("2000"),
        realized_pnl_amount=None,
        created_at=NOW,
    )
    observation_1 = _observation(pair=PAIR, bid=Decimal("151.000"), ask=Decimal("151.010"))
    observation_2 = _observation(pair=_OTHER_PAIR, bid=Decimal("6.100"), ask=Decimal("6.110"))
    positions = (
        PaperAccountSnapshotPositionInput(
            paper_position_id=position_id_1,
            pair=PAIR,
            position_side=PaperPositionSide.LONG,
            applications=(application_1,),
        ),
        PaperAccountSnapshotPositionInput(
            paper_position_id=position_id_2,
            pair=_OTHER_PAIR,
            position_side=PaperPositionSide.LONG,
            applications=(application_2,),
        ),
    )
    observations_by_pair = {PAIR.symbol: observation_1, _OTHER_PAIR.symbol: observation_2}

    unrealized_both = paper_unrealized_pnl_v1(
        position_side=PaperPositionSide.LONG,
        average_entry_price=Decimal("150"),
        observation=observation_1,
        open_quantity=Decimal("1000"),
    ) + paper_unrealized_pnl_v1(
        position_side=PaperPositionSide.LONG,
        average_entry_price=Decimal("6.0"),
        observation=observation_2,
        open_quantity=Decimal("2000"),
    )
    gross_both = paper_gross_exposure_v1(
        (
            (PaperPositionSide.LONG, Decimal("1000"), observation_1),
            (PaperPositionSide.LONG, Decimal("2000"), observation_2),
        )
    )
    equity = paper_account_equity_v1(
        cash=bootstrap.initial_cash,
        realized_pnl_total=Decimal(0),
        accrued_swap_total=Decimal(0),
        unrealized_pnl_total=unrealized_both,
    )
    used_margin = paper_used_margin_v1(gross_exposure=gross_both, leverage=bootstrap.leverage)
    available_margin = paper_available_margin_v1(equity=equity, used_margin=used_margin)
    base_kwargs: dict[str, object] = {
        "paper_account_id": bootstrap.paper_account_id,
        "cash": bootstrap.initial_cash,
        "realized_pnl_total": Decimal(0),
        "unrealized_pnl_total": unrealized_both,
        "accrued_swap_total": Decimal(0),
        "equity": equity,
        "used_margin": used_margin,
        "available_margin": available_margin,
        "gross_exposure": gross_both,
        "open_position_count": 2,
        "open_order_count": 0,
        "mark_observation_ids": (
            (observation_1.market_observation_id, observation_2.market_observation_id)
            if PAIR.symbol < _OTHER_PAIR.symbol
            else (observation_2.market_observation_id, observation_1.market_observation_id)
        ),
        "highest_application_seq": 2,
        "highest_ledger_entry_seq": 0,
        "highest_order_event_seq": 0,
        "margin_policy_version": bootstrap.margin_policy_version,
        "unrealized_mark_policy_version": bootstrap.unrealized_mark_policy_version,
        "created_at": NOW,
    }
    snapshot = PaperAccountSnapshot.create(**base_kwargs)  # type: ignore[arg-type]
    assert rebuild_account_snapshot(
        snapshot,
        bootstrap=bootstrap,
        positions=positions,
        ledger_entries=(),
        observations_by_pair=observations_by_pair,
        order_events_by_order={},
        highest_application_seq=2,
        highest_ledger_entry_seq=0,
        highest_order_event_seq=0,
    )

    # Omitting the second Pair (self-consistently, i.e. a forged
    # open_position_count) changes every one of the aggregates, so it fails
    # to reconcile against the true two-Pair rebuild.
    single_pair_kwargs = dict(base_kwargs)
    single_pair_kwargs["open_position_count"] = 1
    single_pair_snapshot = PaperAccountSnapshot.create(**single_pair_kwargs)  # type: ignore[arg-type]
    assert not rebuild_account_snapshot(
        single_pair_snapshot,
        bootstrap=bootstrap,
        positions=positions,
        ledger_entries=(),
        observations_by_pair=observations_by_pair,
        order_events_by_order={},
        highest_application_seq=2,
        highest_ledger_entry_seq=0,
        highest_order_event_seq=0,
    )
