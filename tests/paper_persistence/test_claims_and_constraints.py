import sqlite3
from pathlib import Path

import pytest

from tests.paper_persistence._helpers import populate_full_flow


@pytest.fixture(scope="module")
def _flow_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("paper-claims") / "live.sqlite"
    populate_full_flow(path)
    return path


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _one(connection: sqlite3.Connection, sql: str) -> sqlite3.Row:
    row = connection.execute(sql).fetchone()
    assert row is not None, f"expected at least one row for: {sql}"
    return row


# ---------------------------------------------------------------------------
# Step terminal claim: cross-variant linkage triggers
# ---------------------------------------------------------------------------


def test_market_selected_claim_blocks_a_second_claim_for_the_same_step(_flow_db: Path) -> None:
    with _connect(_flow_db) as connection:
        claim = _one(
            connection,
            "SELECT fill_evaluation_step_id FROM live_paper_step_terminal_claims "
            "WHERE variant = 'MARKET_SELECTED' LIMIT 1",
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO live_paper_step_terminal_claims "
                "(fill_evaluation_step_id, variant, resolution_id, resolved_at) "
                "VALUES (?, 'NO_MARKET', 'forged-resolution-1', '2026-01-01T00:00:00+00:00')",
                (claim["fill_evaluation_step_id"],),
            )


def test_no_market_claim_blocks_a_second_claim_for_the_same_step(_flow_db: Path) -> None:
    with _connect(_flow_db) as connection:
        claim = _one(
            connection,
            "SELECT fill_evaluation_step_id FROM live_paper_step_terminal_claims "
            "WHERE variant = 'NO_MARKET' LIMIT 1",
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO live_paper_step_terminal_claims "
                "(fill_evaluation_step_id, variant, resolution_id, resolved_at) "
                "VALUES (?, 'MARKET_SELECTED', 'forged-resolution-2', '2026-01-01T00:00:00+00:00')",
                (claim["fill_evaluation_step_id"],),
            )


def test_claim_check_rejects_resolution_id_equal_to_step_id(_flow_db: Path) -> None:
    with _connect(_flow_db) as connection:
        step = _one(
            connection,
            "SELECT fill_evaluation_step_id FROM live_paper_fill_evaluation_steps "
            "WHERE fill_evaluation_step_id NOT IN "
            "(SELECT fill_evaluation_step_id FROM live_paper_step_terminal_claims) LIMIT 1",
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO live_paper_step_terminal_claims "
                "(fill_evaluation_step_id, variant, resolution_id, resolved_at) "
                "VALUES (?, 'MARKET_SELECTED', ?, '2026-01-01T00:00:00+00:00')",
                (step["fill_evaluation_step_id"], step["fill_evaluation_step_id"]),
            )


def test_selection_linkage_trigger_rejects_no_claim_wrong_variant_and_wrong_resolution_id(
    _flow_db: Path,
) -> None:
    with _connect(_flow_db) as connection:
        existing_selection = _one(
            connection, "SELECT * FROM live_paper_market_observation_selections LIMIT 1"
        )
        no_market_claim = _one(
            connection,
            "SELECT fill_evaluation_step_id FROM live_paper_step_terminal_claims "
            "WHERE variant = 'NO_MARKET' LIMIT 1",
        )
        unresolved_step = _one(
            connection,
            "SELECT fill_evaluation_step_id, fill_evaluation_plan_id "
            "FROM live_paper_fill_evaluation_steps "
            "WHERE fill_evaluation_step_id NOT IN "
            "(SELECT fill_evaluation_step_id FROM live_paper_step_terminal_claims) LIMIT 1",
        )

        def _insert_selection(step_id: str, selection_id: str) -> None:
            connection.execute(
                "INSERT INTO live_paper_market_observation_selections "
                "(market_observation_selection_id, fill_evaluation_step_id, "
                "fill_evaluation_plan_id, market_observation_id, "
                "market_selection_policy_version, evaluation_window_start_at, "
                "evaluation_due_at, intent_created_at, selected_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    selection_id,
                    step_id,
                    existing_selection["fill_evaluation_plan_id"],
                    existing_selection["market_observation_id"],
                    "selection-v1",
                    existing_selection["evaluation_window_start_at"],
                    existing_selection["evaluation_due_at"],
                    existing_selection["intent_created_at"],
                    "2026-01-01T00:00:00+00:00",
                ),
            )

        # No claim at all for this Step.
        with pytest.raises(sqlite3.IntegrityError):
            _insert_selection(
                unresolved_step["fill_evaluation_step_id"], "forged-selection-no-claim"
            )

        # A claim exists but carries the NO_MARKET variant.
        with pytest.raises(sqlite3.IntegrityError):
            _insert_selection(
                no_market_claim["fill_evaluation_step_id"], "forged-selection-wrong-variant"
            )

        # A MARKET_SELECTED claim exists but names a different resolution_id.
        with pytest.raises(sqlite3.IntegrityError):
            _insert_selection(
                existing_selection["fill_evaluation_step_id"],
                "forged-selection-wrong-resolution-id",
            )


def test_no_market_outcome_linkage_trigger_rejects_no_claim_wrong_variant_and_wrong_resolution_id(
    _flow_db: Path,
) -> None:
    with _connect(_flow_db) as connection:
        existing_outcome = _one(connection, "SELECT * FROM live_paper_no_market_outcomes LIMIT 1")
        market_selected_claim = _one(
            connection,
            "SELECT fill_evaluation_step_id FROM live_paper_step_terminal_claims "
            "WHERE variant = 'MARKET_SELECTED' LIMIT 1",
        )
        unresolved_step = _one(
            connection,
            "SELECT fill_evaluation_step_id FROM live_paper_fill_evaluation_steps "
            "WHERE fill_evaluation_step_id NOT IN "
            "(SELECT fill_evaluation_step_id FROM live_paper_step_terminal_claims) LIMIT 1",
        )

        def _insert_outcome(step_id: str, outcome_id: str) -> None:
            connection.execute(
                "INSERT INTO live_paper_no_market_outcomes "
                "(no_market_outcome_id, fill_evaluation_step_id, terminal_reason_code, "
                "evaluation_due_at, resolved_at) VALUES (?, ?, ?, ?, ?)",
                (
                    outcome_id,
                    step_id,
                    existing_outcome["terminal_reason_code"],
                    existing_outcome["evaluation_due_at"],
                    "2026-01-01T00:00:00+00:00",
                ),
            )

        with pytest.raises(sqlite3.IntegrityError):
            _insert_outcome(unresolved_step["fill_evaluation_step_id"], "forged-outcome-no-claim")
        with pytest.raises(sqlite3.IntegrityError):
            _insert_outcome(
                market_selected_claim["fill_evaluation_step_id"], "forged-outcome-wrong-variant"
            )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_outcome(
                existing_outcome["fill_evaluation_step_id"], "forged-outcome-wrong-resolution-id"
            )


# ---------------------------------------------------------------------------
# Swap rollover claim: cross-variant linkage triggers
# ---------------------------------------------------------------------------


def test_rollover_claim_check_rejects_evidence_id_equal_to_position_id(_flow_db: Path) -> None:
    with _connect(_flow_db) as connection:
        position = _one(connection, "SELECT paper_position_id FROM live_paper_positions LIMIT 1")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO live_paper_swap_rollover_claims "
                "(paper_position_id, rollover_date, variant, evidence_id, resolved_at) "
                "VALUES (?, '2099-01-01', 'ACCRUED', ?, '2026-01-01T00:00:00+00:00')",
                (position["paper_position_id"], position["paper_position_id"]),
            )


def test_accrual_linkage_trigger_rejects_no_claim_wrong_variant_and_wrong_evidence_id(
    _flow_db: Path,
) -> None:
    with _connect(_flow_db) as connection:
        accrual = _one(connection, "SELECT * FROM live_paper_swap_accruals LIMIT 1")
        not_accrued_claim = _one(
            connection,
            "SELECT paper_position_id, rollover_date FROM live_paper_swap_rollover_claims "
            "WHERE variant = 'NOT_ACCRUED' LIMIT 1",
        )

        def _insert_accrual(position_id: str, rollover_date: str, accrual_id: str) -> None:
            connection.execute(
                "INSERT INTO live_paper_swap_accruals "
                "(paper_swap_accrual_id, accrual_contract_version, paper_position_id, "
                "paper_position_snapshot_id, swap_evidence_id, rollover_date, open_quantity, "
                "unit_basis, base_units_per_unit, settlement_currency, policy_version, "
                "formula_version, amount, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    accrual_id,
                    accrual["accrual_contract_version"],
                    position_id,
                    accrual["paper_position_snapshot_id"],
                    accrual["swap_evidence_id"],
                    rollover_date,
                    accrual["open_quantity"],
                    accrual["unit_basis"],
                    accrual["base_units_per_unit"],
                    accrual["settlement_currency"],
                    accrual["policy_version"],
                    accrual["formula_version"],
                    accrual["amount"],
                    "2026-01-01T00:00:00+00:00",
                ),
            )

        # No claim at all for this (position, date).
        with pytest.raises(sqlite3.IntegrityError):
            _insert_accrual(accrual["paper_position_id"], "2099-06-01", "forged-accrual-no-claim")
        # A claim exists but carries NOT_ACCRUED.
        with pytest.raises(sqlite3.IntegrityError):
            _insert_accrual(
                not_accrued_claim["paper_position_id"],
                not_accrued_claim["rollover_date"],
                "forged-accrual-wrong-variant",
            )
        # An ACCRUED claim exists but names a different evidence_id.
        with pytest.raises(sqlite3.IntegrityError):
            _insert_accrual(
                accrual["paper_position_id"],
                accrual["rollover_date"],
                "forged-accrual-wrong-evidence-id",
            )


def test_non_accrual_linkage_trigger_rejects_no_claim_wrong_variant_and_wrong_evidence_id(
    _flow_db: Path,
) -> None:
    with _connect(_flow_db) as connection:
        non_accrual = _one(connection, "SELECT * FROM live_paper_swap_non_accruals LIMIT 1")
        accrued_claim = _one(
            connection,
            "SELECT paper_position_id, rollover_date FROM live_paper_swap_rollover_claims "
            "WHERE variant = 'ACCRUED' LIMIT 1",
        )

        def _insert_non_accrual(position_id: str, rollover_date: str, non_accrual_id: str) -> None:
            connection.execute(
                "INSERT INTO live_paper_swap_non_accruals "
                "(paper_swap_non_accrual_id, non_accrual_contract_version, paper_position_id, "
                "paper_position_snapshot_id, swap_evidence_id, rollover_date, outcome, "
                "policy_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    non_accrual_id,
                    non_accrual["non_accrual_contract_version"],
                    position_id,
                    non_accrual["paper_position_snapshot_id"],
                    non_accrual["swap_evidence_id"],
                    rollover_date,
                    non_accrual["outcome"],
                    non_accrual["policy_version"],
                    "2026-01-01T00:00:00+00:00",
                ),
            )

        with pytest.raises(sqlite3.IntegrityError):
            _insert_non_accrual(
                non_accrual["paper_position_id"], "2099-06-02", "forged-non-accrual-no-claim"
            )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_non_accrual(
                accrued_claim["paper_position_id"],
                accrued_claim["rollover_date"],
                "forged-non-accrual-wrong-variant",
            )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_non_accrual(
                non_accrual["paper_position_id"],
                non_accrual["rollover_date"],
                "forged-non-accrual-wrong-evidence-id",
            )


# ---------------------------------------------------------------------------
# Frozen minimum constraint set: direct conflicting inserts
# ---------------------------------------------------------------------------


def test_second_plan_for_one_order_is_rejected(_flow_db: Path) -> None:
    with _connect(_flow_db) as connection:
        plan = _one(connection, "SELECT * FROM live_paper_fill_evaluation_plans LIMIT 1")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO live_paper_fill_evaluation_plans "
                "(fill_evaluation_plan_id, plan_contract_version, paper_order_id, pair, side, "
                "original_quantity, fill_policy_id, intent_created_at, maximum_steps, "
                "plan_expiry_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "forged-plan-1",
                    plan["plan_contract_version"],
                    plan["paper_order_id"],
                    plan["pair"],
                    plan["side"],
                    plan["original_quantity"],
                    plan["fill_policy_id"],
                    plan["intent_created_at"],
                    plan["maximum_steps"],
                    plan["plan_expiry_at"],
                    plan["created_at"],
                ),
            )


def test_second_step_for_one_plan_ordinal_is_rejected(_flow_db: Path) -> None:
    with _connect(_flow_db) as connection:
        step = _one(connection, "SELECT * FROM live_paper_fill_evaluation_steps LIMIT 1")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO live_paper_fill_evaluation_steps "
                "(fill_evaluation_step_id, step_contract_version, fill_evaluation_plan_id, "
                "ordinal, evaluation_window_start_at, evaluation_due_at, "
                "remaining_quantity_before, fill_policy_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "forged-step-1",
                    step["step_contract_version"],
                    step["fill_evaluation_plan_id"],
                    step["ordinal"],
                    step["evaluation_window_start_at"],
                    step["evaluation_due_at"],
                    step["remaining_quantity_before"],
                    step["fill_policy_id"],
                    step["created_at"],
                ),
            )


def test_second_selection_for_one_step_is_rejected(_flow_db: Path) -> None:
    with _connect(_flow_db) as connection:
        selection = _one(
            connection, "SELECT * FROM live_paper_market_observation_selections LIMIT 1"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO live_paper_market_observation_selections "
                "(market_observation_selection_id, fill_evaluation_step_id, "
                "fill_evaluation_plan_id, market_observation_id, "
                "market_selection_policy_version, evaluation_window_start_at, "
                "evaluation_due_at, intent_created_at, selected_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    selection["market_observation_selection_id"] + "-dup",
                    selection["fill_evaluation_step_id"],
                    selection["fill_evaluation_plan_id"],
                    selection["market_observation_id"],
                    selection["market_selection_policy_version"],
                    selection["evaluation_window_start_at"],
                    selection["evaluation_due_at"],
                    selection["intent_created_at"],
                    selection["selected_at"],
                ),
            )


def test_second_fill_for_one_selection_is_rejected(_flow_db: Path) -> None:
    with _connect(_flow_db) as connection:
        fill = _one(connection, "SELECT * FROM live_paper_fills LIMIT 1")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO live_paper_fills "
                "(paper_fill_id, fill_contract_version, fill_evaluation_step_id, "
                "market_observation_selection_id, market_observation_id, pair, side, "
                "fill_quantity, fill_price, reference_price, slippage_basis_points, "
                "fill_model_version, remaining_quantity_before, remaining_quantity_after, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "forged-fill-1",
                    fill["fill_contract_version"],
                    fill["fill_evaluation_step_id"],
                    fill["market_observation_selection_id"],
                    fill["market_observation_id"],
                    fill["pair"],
                    fill["side"],
                    fill["fill_quantity"],
                    fill["fill_price"],
                    fill["reference_price"],
                    fill["slippage_basis_points"],
                    fill["fill_model_version"],
                    fill["remaining_quantity_before"],
                    fill["remaining_quantity_after"],
                    fill["created_at"],
                ),
            )
