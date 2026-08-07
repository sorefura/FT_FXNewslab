import dataclasses
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
from unittest.mock import patch

import pytest
import swap_bot.ordinary_close_store as ordinary_close_store
from fx_core import (
    FeatureId,
    Horizon,
    PairScore,
    PairTarget,
    Probability,
    Signal,
    SignalId,
    VersionMetadata,
)
from swap_bot.adoption import RuntimeMode, StrictCohortIdentity
from swap_bot.adoption_application import (
    ApproveSignalAdoptionOnceService,
    RevokeSignalAdoptionOnceService,
)
from swap_bot.adoption_gate import LiveAdoptionGate
from swap_bot.adoption_store import SQLiteAdoptionStore
from swap_bot.decision_store import _SCHEMA
from swap_bot.execution_authority import ExecutionAuthorityMode
from swap_bot.live_migrations import _apply_migration_exact
from swap_bot.models import Side
from swap_bot.operational_swap import (
    OperationalSwapReadDisposition,
    OperationalSwapReadResult,
    OperationalSwapResolution,
    OperationalSwapResolutionOutcome,
    SQLiteOperationalSwapStore,
)
from swap_bot.ordinary_close_store import (
    OrdinaryClosePersistenceConflict,
    OrdinaryClosePersistenceDisposition,
    OrdinaryCloseReservationDisposition,
    OrdinaryCloseReservationIntegrityError,
    OrdinaryCloseReservationPersistenceResult,
    SQLiteOrdinaryCloseStore,
)
from swap_bot.research_evidence import SQLiteResearchValidationEvidenceSource
from swap_bot.strategy import (
    OperationalPositionExitEvaluationResult,
    OrdinaryCloseAllocationPolicy,
    OrdinaryClosePortfolioDecision,
    OrdinaryClosePortfolioDisposition,
    OrdinaryCloseReservationSnapshot,
    OrdinaryCloseRiskOutcome,
    OrdinaryCloseRiskPolicy,
    OrdinaryCloseRiskReason,
    OrdinaryPositionExitWorkItem,
    PositionCloseCapacityEvidence,
    PositionExitEvaluationOutcome,
    SignalAdoptionResolutionOutcome,
    SignalAdoptionTerminalResolution,
)

from tests.adoption_factories import adoption_policy, cohort_payload, seed_research_evidence
from tests.strategy_contracts.factories import (
    NOW,
    PAIR,
    position_exit_input,
    strategy_config,
    swap_evidence,
)


@dataclass(frozen=True)
class _Fixture:
    live: Path
    adoption_store: SQLiteAdoptionStore
    approval_id: str
    work_item: OrdinaryPositionExitWorkItem


def _fixture(
    tmp_path: Path,
    *,
    side: Side = Side.BUY,
    score: float = -0.5001,
    authority: ExecutionAuthorityMode = ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED,
    target_fraction: Decimal = Decimal("1"),
    expires_at: datetime | None = None,
    revoke_at: datetime | None = None,
) -> _Fixture:
    config = strategy_config()
    pair = PAIR
    signal_created_at = NOW
    signal = Signal(
        signal_id=SignalId("signal-pair-1"),
        target=PairTarget(pair),
        signal_type="pair_fundamental",
        direction=PairScore(score),
        strength=Probability(0.9),
        confidence=Probability(0.8),
        horizon=Horizon.DAYS_3,
        observed_at=signal_created_at - timedelta(seconds=1),
        created_at=signal_created_at,
        source_feature_ids=(FeatureId("feature-1"), FeatureId("feature-2")),
        versions=VersionMetadata(
            producer_version="producer-v1",
            model_version="model-v1",
            prompt_version="prompt-v1",
            scorer_version="fundamental-scorer-v1",
            transformation_version="currency-pair-v1",
        ),
    )
    pair_cohort = cohort_payload(
        signal_type=signal.signal_type,
        target_type="pair",
        target_value=pair.symbol,
        producer_version=signal.versions.producer_version,
        model_version=signal.versions.model_version,
        prompt_version=signal.versions.prompt_version,
        scorer_version=signal.versions.scorer_version,
        transformation_version=signal.versions.transformation_version,
    )
    research = tmp_path / "research.sqlite3"
    live = tmp_path / "live.sqlite3"
    seed_research_evidence(research, cohort=pair_cohort)
    adoption_store = SQLiteAdoptionStore(live)
    authority_start = signal_created_at - timedelta(minutes=1)
    resolved_expires_at = (
        expires_at if expires_at is not None else signal_created_at + timedelta(days=1)
    )
    policy = adoption_policy(
        strategy_id=config.strategy_id,
        strategy_version=config.strategy_version,
        strategy_config_identity=config.strategy_config_identity,
        expected_cohort=StrictCohortIdentity.from_payload(pair_cohort),
        effective_from=authority_start,
        expires_at=resolved_expires_at,
    )
    approval = ApproveSignalAdoptionOnceService(
        SQLiteResearchValidationEvidenceSource(research),
        clock=lambda: authority_start,
    ).run(
        assessment_id="assessment-validated-1",
        policy=policy,
        approved_by="phase-reviewer",
        reason="validated pair strategy",
        apply=True,
        store=adoption_store,
    )
    if revoke_at is not None:
        RevokeSignalAdoptionOnceService(
            adoption_store, clock=lambda: revoke_at
        ).run(
            approval_decision_id=approval.adoption_decision_id,
            revoked_by="test-revoker",
            reason="test revocation",
            apply=True,
            store=adoption_store,
        )
    authorized_at = signal_created_at + timedelta(seconds=1)
    authorized = LiveAdoptionGate(adoption_store).authorize(
        signal,
        strategy_id=config.strategy_id,
        strategy_version=config.strategy_version,
        strategy_config_identity=config.strategy_config_identity,
        runtime_mode=RuntimeMode.SHADOW,
        authorized_at=authorized_at,
    )
    evaluated_at = authorized_at + timedelta(seconds=1)
    swap = swap_evidence(pair=pair)
    evidence_input = position_exit_input(
        position_changes={
            "existing_position_side": side,
            "pair": pair,
            "position_opened_at": NOW - timedelta(days=1),
        },
        authorized_pair_signal=authorized,
        swap_evidence=swap,
        approved_strategy_config_identity=config.strategy_config_identity,
        evaluated_at=evaluated_at,
    )
    context = evidence_input.evidence_context
    swap_resolution = OperationalSwapResolution.create(
        pair=pair,
        source=swap.source,
        source_version=swap.source_version,
        requested_at=evaluated_at,
        outcome=OperationalSwapResolutionOutcome.EVIDENCE,
        reason_code="RECORDED_EVIDENCE",
        evidence=swap,
    )
    signal_resolution = SignalAdoptionTerminalResolution.create(
        outcome=SignalAdoptionResolutionOutcome.AUTHORIZED,
        signal_selection_checkpoint_id=context.signal_selection_checkpoint_id,
        selection_request_id="pair-request-1",
        selection_claim_id="pair-claim-1",
        selection_snapshot_id="pair-selection-1",
        selection_completion_id="pair-completion-1",
        prior_adoption_decision_id=context.prior_adoption_decision_id,
        adoption_state_evidence_id=context.adoption_state_evidence_id,
        reason_code="AUTHORIZED",
        resolved_at=evaluated_at,
        authorized_signal=authorized,
    )
    work_item = OrdinaryPositionExitWorkItem.create(
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
        signal_resolution=signal_resolution,
        swap_resolution=swap_resolution,
        allocation_policy=OrdinaryCloseAllocationPolicy("allocation-v1", target_fraction),
        risk_policy=OrdinaryCloseRiskPolicy("risk-v1", timedelta(hours=1)),
        authority=authority,
    )
    return _Fixture(live, adoption_store, approval.adoption_decision_id, work_item)


def _persist(fixture: _Fixture):  # type: ignore[no-untyped-def]
    return SQLiteOrdinaryCloseStore(fixture.live).evaluate_and_persist(
        fixture.work_item, config=strategy_config()
    )


def _counts(path: Path) -> tuple[int, int, int, int, int]:
    with sqlite3.connect(path) as connection:
        return tuple(
            int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "live_ordinary_close_work_items",
                "live_ordinary_close_capacity_evidence",
                "live_ordinary_close_signal_resolutions",
                "live_ordinary_close_operational_evaluations",
                "live_ordinary_close_candidates",
            )
        )  # type: ignore[return-value]


def _versions(path: Path) -> tuple[str, ...]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT version FROM live_schema_migrations ORDER BY version"
        ).fetchall()
    return tuple(row[0] for row in rows)


_B5_SEQUENCE = (
    "0001_validated_signal_live_adoption.sql",
    "0002_candidate_authorization_integrity.sql",
    "0003_operational_swap_evidence.sql",
    "0004_production_entry_strategy.sql",
    "0005_ordinary_close_path.sql",
)


# ---------------------------------------------------------------------------
# Migration convergence
# ---------------------------------------------------------------------------


def test_live_migrations_create_and_reopen_through_0005(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript(_SCHEMA)

    SQLiteOrdinaryCloseStore(path)
    SQLiteOrdinaryCloseStore(path)

    assert _versions(path) == _B5_SEQUENCE


def test_legacy_0004_database_upgrades_through_0005(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript(_SCHEMA)
        migration_root = files("swap_bot").joinpath("migrations")
        for migration_name in _B5_SEQUENCE[:-1]:
            connection.executescript(
                migration_root.joinpath(migration_name).read_text(encoding="utf-8")
            )
        connection.execute(
            "CREATE TABLE live_schema_migrations "
            "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        for version in _B5_SEQUENCE[:-1]:
            connection.execute(
                "INSERT INTO live_schema_migrations VALUES (?, '2026-07-18T00:00:00+00:00')",
                (version,),
            )

    SQLiteOrdinaryCloseStore(path)

    assert _versions(path) == _B5_SEQUENCE


def test_migration_body_failure_rolls_back_body_and_marker(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE live_schema_migrations "
            "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        with pytest.raises(sqlite3.OperationalError):
            _apply_migration_exact(
                connection,
                migration_name="9998_close_failure.sql",
                migration_sql=(
                    "CREATE TABLE close_body_failure (id INTEGER); "
                    "INSERT INTO absent VALUES (1);"
                ),
            )
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'close_body_failure'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM live_schema_migrations WHERE version = '9998_close_failure.sql'"
        ).fetchone() is None
        _apply_migration_exact(
            connection,
            migration_name="9998_close_failure.sql",
            migration_sql="CREATE TABLE close_body_failure (id INTEGER);",
        )
    assert _versions(path) == ("9998_close_failure.sql",)


def test_concurrent_initializers_converge_on_one_marker_per_migration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "live.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript(_SCHEMA)

    with ThreadPoolExecutor(max_workers=2) as executor:
        tuple(executor.map(lambda _: SQLiteOrdinaryCloseStore(path), range(2)))

    assert _versions(path) == _B5_SEQUENCE


# ---------------------------------------------------------------------------
# Authenticate, re-evaluate, append-or-compare
# ---------------------------------------------------------------------------


def test_close_candidate_insert_and_exact_replay_preserve_content(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)

    inserted = _persist(fixture)
    reused = _persist(fixture)

    assert inserted.disposition is OrdinaryClosePersistenceDisposition.INSERTED
    assert reused.disposition is OrdinaryClosePersistenceDisposition.REUSED_IDENTICAL
    assert reused.result == inserted.result
    assert inserted.result.evaluation.outcome is PositionExitEvaluationOutcome.CLOSE_CANDIDATE
    assert _counts(fixture.live) == (1, 1, 1, 1, 1)
    with sqlite3.connect(fixture.live) as connection:
        connection.row_factory = sqlite3.Row
        evaluation_payload = json.loads(
            connection.execute(
                "SELECT evaluation_json FROM live_ordinary_close_operational_evaluations"
            ).fetchone()[0]
        )
        candidate_payload = json.loads(
            connection.execute(
                "SELECT candidate_json FROM live_ordinary_close_candidates"
            ).fetchone()[0]
        )
        for legacy_table in (
            "live_candidates",
            "live_portfolio_decisions",
            "live_risk_decisions",
            "live_execution_intents",
        ):
            assert connection.execute(
                f"SELECT COUNT(*) FROM {legacy_table}"
            ).fetchone()[0] == 0
    assert evaluation_payload == inserted.result.evaluation.identity_payload
    candidate = inserted.result.evaluation.close_candidate
    assert candidate is not None
    assert candidate_payload == candidate.identity_payload


def test_keep_persists_zero_candidates(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, score=0.0)

    result = _persist(fixture)

    assert result.result.evaluation.outcome is PositionExitEvaluationOutcome.KEEP
    with sqlite3.connect(fixture.live) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM live_ordinary_close_candidates"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM live_ordinary_close_operational_evaluations"
        ).fetchone()[0] == 1


def test_live_authority_rejects_before_any_writes(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, authority=ExecutionAuthorityMode.SHADOW_NOT_SUBMITTED)
    live_work_item = OrdinaryPositionExitWorkItem.create(
        evaluation_input=fixture.work_item.evaluation_input,
        capacity=fixture.work_item.capacity,
        signal_resolution=fixture.work_item.signal_resolution,
        swap_resolution=fixture.work_item.swap_resolution,
        allocation_policy=fixture.work_item.allocation_policy,
        risk_policy=fixture.work_item.risk_policy,
        authority=ExecutionAuthorityMode.LIVE,
    )

    with pytest.raises(ValueError, match="LIVE authority is prohibited"):
        SQLiteOrdinaryCloseStore(fixture.live).evaluate_and_persist(
            live_work_item, config=strategy_config()
        )

    assert _counts(fixture.live) == (0, 0, 0, 0, 0)


def test_persisted_authorization_for_another_config_rejects_without_writes(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    another_config = strategy_config(positive_entry_threshold=PairScore(0.6))

    with pytest.raises(ValueError, match="does not approve the exact Strategy config"):
        SQLiteOrdinaryCloseStore(fixture.live).evaluate_and_persist(
            fixture.work_item, config=another_config
        )

    assert _counts(fixture.live) == (0, 0, 0, 0, 0)


def test_replay_rejects_conflicting_persisted_config_without_row_changes(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    _persist(fixture)
    with sqlite3.connect(fixture.live) as connection:
        connection.execute("DROP TRIGGER live_news_filtered_carry_configs_no_update")
        connection.execute(
            "UPDATE live_news_filtered_carry_configs SET config_json = 'forged'"
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM live_news_filtered_carry_configs"
        ).fetchone()[0] == 1
    before = _counts(fixture.live)

    with pytest.raises(OrdinaryClosePersistenceConflict, match="Strategy config"):
        _persist(fixture)

    assert _counts(fixture.live) == before == (1, 1, 1, 1, 1)
    with sqlite3.connect(fixture.live) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM live_news_filtered_carry_configs"
        ).fetchone()[0] == 1


def test_replay_rejects_conflicting_persisted_swap_evidence_without_row_changes(
    tmp_path: Path,
) -> None:
    # live_operational_swap_evidence is content-addressed: swap_evidence_id is a
    # digest over every other column, so any raw-SQL row tamper fails hydration's
    # own self-check (OperationalSwapIntegrityError) before the store's replay
    # comparison ever runs. Simulate a persisted-content mismatch by stubbing the
    # read the store performs, leaving the table itself untouched.
    fixture = _fixture(tmp_path)
    _persist(fixture)
    tampered = swap_evidence(source_version="tampered-swap-v2")
    before = _counts(fixture.live)

    with patch.object(
        SQLiteOperationalSwapStore,
        "get_exact_on",
        return_value=OperationalSwapReadResult(OperationalSwapReadDisposition.FOUND, tampered),
    ), pytest.raises(OrdinaryClosePersistenceConflict, match="Swap Evidence"):
        _persist(fixture)

    assert _counts(fixture.live) == before == (1, 1, 1, 1, 1)
    with sqlite3.connect(fixture.live) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM live_operational_swap_evidence"
        ).fetchone()[0] == 1


def test_replay_rejects_missing_capacity_parent_without_repair(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _persist(fixture)
    with sqlite3.connect(fixture.live) as connection:
        connection.execute("DROP TRIGGER live_ordinary_close_capacity_evidence_no_delete")
        connection.execute("DELETE FROM live_ordinary_close_capacity_evidence")

    with pytest.raises(OrdinaryClosePersistenceConflict, match="capacity evidence"):
        _persist(fixture)

    with sqlite3.connect(fixture.live) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM live_ordinary_close_capacity_evidence"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM live_ordinary_close_operational_evaluations"
        ).fetchone()[0] == 1


def test_replay_rejects_conflicting_persisted_resolution_without_row_changes(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    _persist(fixture)
    with sqlite3.connect(fixture.live) as connection:
        connection.execute("DROP TRIGGER live_ordinary_close_signal_resolutions_no_update")
        connection.execute(
            "UPDATE live_ordinary_close_signal_resolutions SET reason_code = 'forged'"
        )
    before = _counts(fixture.live)

    with pytest.raises(OrdinaryClosePersistenceConflict, match="lacks its exact"):
        _persist(fixture)

    assert _counts(fixture.live) == before == (1, 1, 1, 1, 1)


def test_replay_rejects_conflicting_persisted_work_item_without_row_changes(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    _persist(fixture)
    with sqlite3.connect(fixture.live) as connection:
        connection.execute("DROP TRIGGER live_ordinary_close_work_items_no_update")
        connection.execute(
            "UPDATE live_ordinary_close_work_items SET authority = 'PAPER'"
        )
    before = _counts(fixture.live)

    with pytest.raises(OrdinaryClosePersistenceConflict, match="lacks its exact"):
        _persist(fixture)

    assert _counts(fixture.live) == before == (1, 1, 1, 1, 1)


def test_replay_rejects_missing_candidate_without_repairing_corruption(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    inserted = _persist(fixture)
    assert inserted.result.evaluation.close_candidate is not None
    with sqlite3.connect(fixture.live) as connection:
        connection.execute("DROP TRIGGER live_ordinary_close_candidates_no_delete")
        connection.execute("DELETE FROM live_ordinary_close_candidates")

    with pytest.raises(OrdinaryClosePersistenceConflict, match="lacks its exact"):
        _persist(fixture)

    with sqlite3.connect(fixture.live) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM live_ordinary_close_operational_evaluations"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM live_ordinary_close_candidates"
        ).fetchone()[0] == 0


def test_immutable_tables_reject_update_and_delete(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _persist(fixture)

    with sqlite3.connect(fixture.live) as connection:
        for table, mutating_column, id_column, id_value in (
            (
                "live_ordinary_close_work_items",
                "authority",
                "work_item_id",
                fixture.work_item.work_item_id,
            ),
            (
                "live_ordinary_close_capacity_evidence",
                "source",
                "capacity_evidence_id",
                fixture.work_item.capacity.capacity_evidence_id,
            ),
        ):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                connection.execute(
                    f"UPDATE {table} SET {mutating_column} = {mutating_column} "
                    f"WHERE {id_column} = ?",
                    (id_value,),
                )
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                connection.execute(f"DELETE FROM {table} WHERE {id_column} = ?", (id_value,))


def test_concurrent_identical_writers_insert_once_and_reuse_once(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    store = SQLiteOrdinaryCloseStore(fixture.live)

    def write_once() -> OrdinaryClosePersistenceDisposition:
        return store.evaluate_and_persist(
            fixture.work_item, config=strategy_config()
        ).disposition

    with ThreadPoolExecutor(max_workers=2) as executor:
        dispositions = tuple(executor.map(lambda _: write_once(), range(2)))

    assert sorted(dispositions) == sorted(
        (
            OrdinaryClosePersistenceDisposition.INSERTED,
            OrdinaryClosePersistenceDisposition.REUSED_IDENTICAL,
        )
    )
    assert _counts(fixture.live) == (1, 1, 1, 1, 1)


# ---------------------------------------------------------------------------
# B4 - atomic Portfolio/Risk decision and capacity reservation
# ---------------------------------------------------------------------------


def _reserve(
    fixture: _Fixture,
    result: OperationalPositionExitEvaluationResult,
    *,
    capacity: PositionCloseCapacityEvidence,
    allocation_policy: OrdinaryCloseAllocationPolicy,
):  # type: ignore[no-untyped-def]
    return SQLiteOrdinaryCloseStore(fixture.live).evaluate_and_persist_reservation(
        result,
        capacity=capacity,
        allocation_policy=allocation_policy,
        risk_policy=fixture.work_item.risk_policy,
        authority=fixture.work_item.authority,
    )


def _reservation_counts(path: Path) -> tuple[int, int, int]:
    with sqlite3.connect(path) as connection:
        return tuple(
            int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "live_ordinary_close_portfolio_decisions",
                "live_ordinary_close_risk_decisions",
                "live_ordinary_close_approved_intents",
            )
        )  # type: ignore[return-value]


def _second_close_result(
    fixture: _Fixture,
    *,
    open_quantity: Decimal,
    target_fraction: Decimal = Decimal("1"),
    checkpoint_id: str = "position-checkpoint-2",
) -> tuple[
    OperationalPositionExitEvaluationResult,
    PositionCloseCapacityEvidence,
    OrdinaryCloseAllocationPolicy,
]:
    # A distinct Candidate for the *same* Position, standing in for a later capacity
    # observation: same Position/Pair/Side, later evaluated_at (so its Candidate ID
    # differs) and its own fresh capacity checkpoint, persisted as a second B3 root.
    work_item = fixture.work_item
    later_input = dataclasses.replace(
        work_item.evaluation_input,
        evaluated_at=work_item.evaluation_input.evaluated_at + timedelta(seconds=10),
    )
    later_capacity = PositionCloseCapacityEvidence.create(
        capacity_contract_version=work_item.capacity.capacity_contract_version,
        position_id=work_item.capacity.position_id,
        position_evidence_id=work_item.capacity.position_evidence_id,
        pair=work_item.capacity.pair,
        existing_position_side=work_item.capacity.existing_position_side,
        position_observed_at=work_item.capacity.position_observed_at,
        open_quantity=open_quantity,
        quantity_unit="BASE_UNITS",
        source=work_item.capacity.source,
        checkpoint_id=checkpoint_id,
    )
    allocation_policy = OrdinaryCloseAllocationPolicy("allocation-v1", target_fraction)
    later_work_item = OrdinaryPositionExitWorkItem.create(
        evaluation_input=later_input,
        capacity=later_capacity,
        signal_resolution=work_item.signal_resolution,
        swap_resolution=work_item.swap_resolution,
        allocation_policy=allocation_policy,
        risk_policy=work_item.risk_policy,
        authority=work_item.authority,
    )
    persisted = SQLiteOrdinaryCloseStore(fixture.live).evaluate_and_persist(
        later_work_item, config=strategy_config()
    )
    return persisted.result, later_capacity, allocation_policy


def test_reservation_approve_persists_portfolio_risk_and_one_intent(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, target_fraction=Decimal("0.5"))
    result = _persist(fixture).result

    reservation = _reserve(
        fixture,
        result,
        capacity=fixture.work_item.capacity,
        allocation_policy=fixture.work_item.allocation_policy,
    )

    assert reservation.disposition is OrdinaryCloseReservationDisposition.INSERTED
    assert reservation.portfolio_decision.disposition is OrdinaryClosePortfolioDisposition.ACCEPT
    assert reservation.portfolio_decision.close_candidate_id == (
        result.evaluation.close_candidate.close_candidate_id  # type: ignore[union-attr]
    )
    assert reservation.risk_decision.outcome is OrdinaryCloseRiskOutcome.APPROVE
    assert reservation.intent is not None
    assert reservation.intent.quantity == Decimal("500")
    assert _reservation_counts(fixture.live) == (1, 1, 1)


def test_reservation_exact_replay_returns_identical_chain(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, target_fraction=Decimal("0.5"))
    result = _persist(fixture).result

    first = _reserve(
        fixture,
        result,
        capacity=fixture.work_item.capacity,
        allocation_policy=fixture.work_item.allocation_policy,
    )
    second = _reserve(
        fixture,
        result,
        capacity=fixture.work_item.capacity,
        allocation_policy=fixture.work_item.allocation_policy,
    )

    assert first.disposition is OrdinaryCloseReservationDisposition.INSERTED
    assert second.disposition is OrdinaryCloseReservationDisposition.REUSED_IDENTICAL
    assert second.portfolio_decision == first.portfolio_decision
    assert second.risk_decision == first.risk_decision
    assert second.intent == first.intent
    assert _reservation_counts(fixture.live) == (1, 1, 1)


def test_reservation_replay_is_independent_of_later_reservations(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, target_fraction=Decimal("0.5"))
    a_result = _persist(fixture).result

    a_reservation = _reserve(
        fixture,
        a_result,
        capacity=fixture.work_item.capacity,
        allocation_policy=fixture.work_item.allocation_policy,
    )
    assert a_reservation.portfolio_decision.available_before == Decimal("1000")
    assert a_reservation.portfolio_decision.allocated_quantity == Decimal("500")

    b_result, b_capacity, b_allocation = _second_close_result(
        fixture, open_quantity=Decimal("1000"), target_fraction=Decimal("1")
    )
    b_reservation = _reserve(fixture, b_result, capacity=b_capacity, allocation_policy=b_allocation)
    # B's later capacity observation sees A's already-committed 500 reservation, so
    # only 500 of the newly observed 1000 remains available to it.
    assert b_reservation.portfolio_decision.available_before == Decimal("500")
    assert b_reservation.portfolio_decision.disposition is OrdinaryClosePortfolioDisposition.REDUCE
    assert b_reservation.portfolio_decision.allocated_quantity == Decimal("500")

    replayed = _reserve(
        fixture,
        a_result,
        capacity=fixture.work_item.capacity,
        allocation_policy=fixture.work_item.allocation_policy,
    )
    # A's own original decision must come back unchanged: still available_before ==
    # 1000, never recomputed against the combined 1000 now reserved by A and B.
    assert replayed.disposition is OrdinaryCloseReservationDisposition.REUSED_IDENTICAL
    assert replayed.portfolio_decision == a_reservation.portfolio_decision
    assert replayed.portfolio_decision.available_before == Decimal("1000")


def test_reservation_portfolio_reject_is_linked_to_risk_reject_with_no_intent(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, target_fraction=Decimal("1"))
    a_result = _persist(fixture).result
    _reserve(
        fixture,
        a_result,
        capacity=fixture.work_item.capacity,
        allocation_policy=fixture.work_item.allocation_policy,
    )  # reserves the full open_quantity of 1000

    b_result, b_capacity, b_allocation = _second_close_result(
        fixture, open_quantity=Decimal("1000"), target_fraction=Decimal("1")
    )
    reservation = _reserve(fixture, b_result, capacity=b_capacity, allocation_policy=b_allocation)

    assert reservation.portfolio_decision.disposition is OrdinaryClosePortfolioDisposition.REJECT
    assert reservation.risk_decision.outcome is OrdinaryCloseRiskOutcome.REJECT
    assert reservation.risk_decision.reason is OrdinaryCloseRiskReason.PORTFOLIO_REJECTED
    assert reservation.intent is None
    with sqlite3.connect(fixture.live) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM live_ordinary_close_approved_intents"
        ).fetchone()[0] == 1


def test_distinct_concurrent_close_requests_never_overreserve(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, target_fraction=Decimal("0.7"))
    a_result = _persist(fixture).result
    b_result, b_capacity, b_allocation = _second_close_result(
        fixture, open_quantity=Decimal("1000"), target_fraction=Decimal("0.7")
    )
    store = SQLiteOrdinaryCloseStore(fixture.live)

    def reserve_a():  # type: ignore[no-untyped-def]
        return store.evaluate_and_persist_reservation(
            a_result,
            capacity=fixture.work_item.capacity,
            allocation_policy=fixture.work_item.allocation_policy,
            risk_policy=fixture.work_item.risk_policy,
            authority=fixture.work_item.authority,
        )

    def reserve_b():  # type: ignore[no-untyped-def]
        return store.evaluate_and_persist_reservation(
            b_result,
            capacity=b_capacity,
            allocation_policy=b_allocation,
            risk_policy=fixture.work_item.risk_policy,
            authority=fixture.work_item.authority,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda fn: fn(), (reserve_a, reserve_b)))

    total_reserved = sum(
        (r.intent.quantity if r.intent is not None else Decimal("0")) for r in results
    )
    # Whichever request is serialized first gets the full 700 (ACCEPT); the other
    # sees only 300 remaining of the shared 1000 and is REDUCEd to it - never over.
    assert total_reserved == Decimal("1000")
    with sqlite3.connect(fixture.live) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM live_ordinary_close_approved_intents"
        ).fetchone()[0] == 2


def test_identical_concurrent_reservation_requests_converge_once(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, target_fraction=Decimal("0.5"))
    result = _persist(fixture).result
    store = SQLiteOrdinaryCloseStore(fixture.live)

    def reserve_once():  # type: ignore[no-untyped-def]
        return store.evaluate_and_persist_reservation(
            result,
            capacity=fixture.work_item.capacity,
            allocation_policy=fixture.work_item.allocation_policy,
            risk_policy=fixture.work_item.risk_policy,
            authority=fixture.work_item.authority,
        ).disposition

    with ThreadPoolExecutor(max_workers=2) as executor:
        dispositions = tuple(executor.map(lambda _: reserve_once(), range(2)))

    assert sorted(dispositions) == sorted(
        (
            OrdinaryCloseReservationDisposition.INSERTED,
            OrdinaryCloseReservationDisposition.REUSED_IDENTICAL,
        )
    )
    assert _reservation_counts(fixture.live) == (1, 1, 1)


def test_reservation_rejects_missing_capacity_parent_without_writes(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result = _persist(fixture).result
    with sqlite3.connect(fixture.live) as connection:
        connection.execute("DROP TRIGGER live_ordinary_close_capacity_evidence_no_delete")
        connection.execute("DELETE FROM live_ordinary_close_capacity_evidence")

    with pytest.raises(OrdinaryClosePersistenceConflict, match="capacity"):
        _reserve(
            fixture,
            result,
            capacity=fixture.work_item.capacity,
            allocation_policy=fixture.work_item.allocation_policy,
        )

    assert _reservation_counts(fixture.live) == (0, 0, 0)


def test_reservation_rejects_missing_operational_evaluation_parent_without_writes(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    result = _persist(fixture).result
    with sqlite3.connect(fixture.live) as connection:
        connection.execute("DROP TRIGGER live_ordinary_close_operational_evaluations_no_delete")
        connection.execute("DELETE FROM live_ordinary_close_operational_evaluations")

    with pytest.raises(OrdinaryClosePersistenceConflict, match="operational evaluation"):
        _reserve(
            fixture,
            result,
            capacity=fixture.work_item.capacity,
            allocation_policy=fixture.work_item.allocation_policy,
        )

    assert _reservation_counts(fixture.live) == (0, 0, 0)


def test_approved_intents_trigger_rejects_insert_without_risk_approve(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    with sqlite3.connect(fixture.live) as connection:
        connection.execute(
            "INSERT INTO live_ordinary_close_risk_decisions VALUES "
            "('forged-risk-reject-1', 'forged-portfolio-decision-1', "
            "'risk-v1', 3600000000, 'REJECT', 'PORTFOLIO_REJECTED')"
        )
        with pytest.raises(sqlite3.IntegrityError, match="Risk APPROVE"):
            connection.execute(
                "INSERT INTO live_ordinary_close_approved_intents "
                "(close_candidate_id, portfolio_decision_id, risk_decision_id, "
                "capacity_evidence_id, position_id, pair, side, quantity, authority, "
                "idempotency_key, created_at) VALUES "
                "('forged-candidate-1', 'forged-portfolio-decision-1', "
                "'forged-risk-reject-1', 'forged-capacity-1', 'position-1', 'USD_JPY', "
                "'SELL', '1', 'SHADOW_NOT_SUBMITTED', 'forged-idempotency-1', "
                "'2026-01-01T00:00:00+00:00')"
            )


def test_reservation_tables_reject_update_and_delete(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, target_fraction=Decimal("0.5"))
    result = _persist(fixture).result
    reservation = _reserve(
        fixture,
        result,
        capacity=fixture.work_item.capacity,
        allocation_policy=fixture.work_item.allocation_policy,
    )
    assert reservation.intent is not None

    with sqlite3.connect(fixture.live) as connection:
        for table, mutating_column, id_column, id_value in (
            (
                "live_ordinary_close_portfolio_decisions",
                "disposition",
                "portfolio_decision_id",
                reservation.portfolio_decision.portfolio_decision_id,
            ),
            (
                "live_ordinary_close_risk_decisions",
                "outcome",
                "risk_decision_id",
                reservation.risk_decision.risk_decision_id,
            ),
            (
                "live_ordinary_close_approved_intents",
                "quantity",
                "close_candidate_id",
                reservation.intent.close_candidate_id,
            ),
        ):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                connection.execute(
                    f"UPDATE {table} SET {mutating_column} = {mutating_column} "
                    f"WHERE {id_column} = ?",
                    (id_value,),
                )
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                connection.execute(f"DELETE FROM {table} WHERE {id_column} = ?", (id_value,))


def test_reservation_rejects_capacity_not_bound_to_this_candidates_work_item(
    tmp_path: Path,
) -> None:
    # A and B are distinct, independently-persisted CLOSE Candidates/capacities for
    # the same Position/Pair/Side (same pattern _second_close_result already uses
    # for "a later capacity observation"). Requesting A's evaluation_result together
    # with B's capacity must be rejected even though Position/Pair/Side all match.
    fixture = _fixture(tmp_path, target_fraction=Decimal("0.5"))
    a_result = _persist(fixture).result
    _b_result, b_capacity, _b_allocation = _second_close_result(
        fixture, open_quantity=Decimal("1000"), target_fraction=Decimal("0.5")
    )

    with pytest.raises(OrdinaryClosePersistenceConflict, match="not the capacity bound"):
        _reserve(
            fixture,
            a_result,
            capacity=b_capacity,
            allocation_policy=fixture.work_item.allocation_policy,
        )

    assert _reservation_counts(fixture.live) == (0, 0, 0)


def test_reservation_failure_between_portfolio_and_risk_insert_leaves_zero_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, target_fraction=Decimal("0.5"))
    result = _persist(fixture).result

    def fail(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("test forced risk-decision insert failure")

    monkeypatch.setattr(ordinary_close_store, "_insert_risk_decision", fail)
    with pytest.raises(RuntimeError, match="forced risk-decision insert failure"):
        _reserve(
            fixture,
            result,
            capacity=fixture.work_item.capacity,
            allocation_policy=fixture.work_item.allocation_policy,
        )

    assert _reservation_counts(fixture.live) == (0, 0, 0)


def test_replay_hydration_failure_on_corrupted_portfolio_decision_raises_integrity_error(
    tmp_path: Path,
) -> None:
    # Corrupting one hydration path (Portfolio decision) is enough to prove the
    # pattern: _hydrate_risk_decision and _hydrate_intent follow the identical
    # try/except-and-reraise-as-OrdinaryCloseReservationIntegrityError shape.
    fixture = _fixture(tmp_path, target_fraction=Decimal("0.5"))
    result = _persist(fixture).result
    reservation = _reserve(
        fixture,
        result,
        capacity=fixture.work_item.capacity,
        allocation_policy=fixture.work_item.allocation_policy,
    )

    with sqlite3.connect(fixture.live) as connection:
        connection.execute("DROP TRIGGER live_ordinary_close_portfolio_decisions_no_update")
        connection.execute(
            "UPDATE live_ordinary_close_portfolio_decisions "
            "SET reservation_snapshot_json = 'not-json' WHERE portfolio_decision_id = ?",
            (reservation.portfolio_decision.portfolio_decision_id,),
        )

    with pytest.raises(OrdinaryCloseReservationIntegrityError, match="malformed"):
        _reserve(
            fixture,
            result,
            capacity=fixture.work_item.capacity,
            allocation_policy=fixture.work_item.allocation_policy,
        )


# ---------------------------------------------------------------------------
# First-insert append-or-compare corruption (a differently-seeded ID collision
# from the replay-conflict tests above, which corrupt an already-persisted row)
# ---------------------------------------------------------------------------


def test_first_insert_rejects_preexisting_different_config_content(tmp_path: Path) -> None:
    # capacity/resolution/work_item share this exact INSERT-OR-IGNORE-then-compare
    # shape; forging one of them is enough to prove the pattern generalizes.
    fixture = _fixture(tmp_path)
    SQLiteOrdinaryCloseStore(fixture.live)
    with sqlite3.connect(fixture.live) as connection:
        connection.execute(
            "INSERT INTO live_news_filtered_carry_configs VALUES (?, ?)",
            (strategy_config().strategy_config_identity, "forged-config-json"),
        )

    with pytest.raises(OrdinaryClosePersistenceConflict, match="Strategy config"):
        _persist(fixture)

    assert _counts(fixture.live) == (0, 0, 0, 0, 0)
    with sqlite3.connect(fixture.live) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM live_news_filtered_carry_configs"
        ).fetchone()[0] == 1


def test_first_insert_rejects_preexisting_different_capacity_content(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    SQLiteOrdinaryCloseStore(fixture.live)
    real_values = ordinary_close_store._capacity_values(fixture.work_item.capacity)
    forged_values = real_values[:7] + ("999",) + real_values[8:]
    with sqlite3.connect(fixture.live) as connection:
        connection.execute(
            "INSERT INTO live_ordinary_close_capacity_evidence "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            forged_values,
        )

    with pytest.raises(OrdinaryClosePersistenceConflict, match="capacity evidence"):
        _persist(fixture)

    # The one forged capacity row remains (it predates the rolled-back attempt);
    # every other table stays untouched by the failed transaction.
    assert _counts(fixture.live) == (0, 1, 0, 0, 0)


def test_first_insert_rejects_preexisting_different_resolution_content(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    SQLiteOrdinaryCloseStore(fixture.live)
    real_values = ordinary_close_store._resolution_values(fixture.work_item.signal_resolution)
    forged_values = real_values[:9] + ("forged-reason",) + real_values[10:]
    with sqlite3.connect(fixture.live) as connection:
        connection.execute(
            "INSERT INTO live_ordinary_close_signal_resolutions "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            forged_values,
        )

    with pytest.raises(OrdinaryClosePersistenceConflict, match="resolution"):
        _persist(fixture)

    # The one forged resolution row remains; every other table stays untouched.
    assert _counts(fixture.live) == (0, 0, 1, 0, 0)


def test_first_insert_rejects_preexisting_different_work_item_content(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    SQLiteOrdinaryCloseStore(fixture.live)
    real_values = ordinary_close_store._work_item_values(fixture.work_item)
    forged_values = real_values[:8] + ("PAPER",) + real_values[9:]
    with sqlite3.connect(fixture.live) as connection:
        connection.execute(
            "INSERT INTO live_ordinary_close_work_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            forged_values,
        )

    with pytest.raises(OrdinaryClosePersistenceConflict, match="work item"):
        _persist(fixture)

    # The one forged work item row remains; every other table stays untouched.
    assert _counts(fixture.live) == (1, 0, 0, 0, 0)


def test_replay_rejects_conflicting_persisted_evaluation_without_row_changes(
    tmp_path: Path,
) -> None:
    # Unlike config/capacity/resolution/work_item, _append_or_compare_evaluation
    # runs unconditionally on both the fresh-insert and replay branches, so its own
    # conflict raise is reached by corrupting an already-persisted row and
    # replaying - not by pre-seeding before a first insert.
    fixture = _fixture(tmp_path)
    _persist(fixture)
    with sqlite3.connect(fixture.live) as connection:
        connection.execute("DROP TRIGGER live_ordinary_close_operational_evaluations_no_update")
        connection.execute(
            "UPDATE live_ordinary_close_operational_evaluations SET evaluation_json = 'forged'"
        )
    before = _counts(fixture.live)

    with pytest.raises(OrdinaryClosePersistenceConflict, match="operational evaluation"):
        _persist(fixture)

    assert _counts(fixture.live) == before == (1, 1, 1, 1, 1)


# ---------------------------------------------------------------------------
# Hydration corruption beyond the Portfolio decision pattern proven above
# ---------------------------------------------------------------------------


def test_replay_hydration_failure_on_corrupted_risk_decision_raises_integrity_error(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, target_fraction=Decimal("0.5"))
    result = _persist(fixture).result
    reservation = _reserve(
        fixture,
        result,
        capacity=fixture.work_item.capacity,
        allocation_policy=fixture.work_item.allocation_policy,
    )

    with sqlite3.connect(fixture.live) as connection:
        connection.execute("DROP TRIGGER live_ordinary_close_risk_decisions_no_update")
        connection.execute(
            "UPDATE live_ordinary_close_risk_decisions "
            "SET maximum_capacity_age_us = 0 WHERE risk_decision_id = ?",
            (reservation.risk_decision.risk_decision_id,),
        )

    with pytest.raises(OrdinaryCloseReservationIntegrityError, match="malformed"):
        _reserve(
            fixture,
            result,
            capacity=fixture.work_item.capacity,
            allocation_policy=fixture.work_item.allocation_policy,
        )


def test_replay_hydration_failure_on_corrupted_intent_raises_integrity_error(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, target_fraction=Decimal("0.5"))
    result = _persist(fixture).result
    reservation = _reserve(
        fixture,
        result,
        capacity=fixture.work_item.capacity,
        allocation_policy=fixture.work_item.allocation_policy,
    )
    assert reservation.intent is not None

    with sqlite3.connect(fixture.live) as connection:
        connection.execute("DROP TRIGGER live_ordinary_close_approved_intents_no_update")
        connection.execute(
            "UPDATE live_ordinary_close_approved_intents "
            "SET quantity = 'not-a-decimal' WHERE close_candidate_id = ?",
            (reservation.intent.close_candidate_id,),
        )

    with pytest.raises(OrdinaryCloseReservationIntegrityError, match="malformed"):
        _reserve(
            fixture,
            result,
            capacity=fixture.work_item.capacity,
            allocation_policy=fixture.work_item.allocation_policy,
        )


# ---------------------------------------------------------------------------
# Missing parent - fail closed, no repair
# ---------------------------------------------------------------------------


def test_reservation_replay_rejects_portfolio_decision_missing_its_risk_decision(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, target_fraction=Decimal("0.5"))
    result = _persist(fixture).result
    candidate = result.evaluation.close_candidate
    assert candidate is not None

    # A real, correctly content-addressed Portfolio decision inserted directly
    # (bypassing evaluate_and_persist_reservation, which would also insert its
    # Risk decision atomically) so it hydrates cleanly and only its missing Risk
    # decision is exercised.
    orphan_portfolio_decision = OrdinaryClosePortfolioDecision.create(
        operational_evaluation_id=result.operational_evaluation_id,
        candidate=candidate,
        capacity=fixture.work_item.capacity,
        allocation_policy=fixture.work_item.allocation_policy,
        reservation_snapshot=OrdinaryCloseReservationSnapshot(
            fixture.work_item.capacity.position_id, ()
        ),
    )
    with sqlite3.connect(fixture.live) as connection:
        ordinary_close_store._insert_portfolio_decision(connection, orphan_portfolio_decision)

    with pytest.raises(OrdinaryCloseReservationIntegrityError, match="lacks its Risk decision"):
        _reserve(
            fixture,
            result,
            capacity=fixture.work_item.capacity,
            allocation_policy=fixture.work_item.allocation_policy,
        )


def test_reservation_rejects_missing_work_item_parent_without_writes(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result = _persist(fixture).result
    with sqlite3.connect(fixture.live) as connection:
        connection.execute("DROP TRIGGER live_ordinary_close_work_items_no_delete")
        connection.execute("DELETE FROM live_ordinary_close_work_items")

    with pytest.raises(OrdinaryClosePersistenceConflict, match="persisted work item"):
        _reserve(
            fixture,
            result,
            capacity=fixture.work_item.capacity,
            allocation_policy=fixture.work_item.allocation_policy,
        )

    assert _reservation_counts(fixture.live) == (0, 0, 0)


# ---------------------------------------------------------------------------
# Adoption authority persistence - negative cases at the ordinary-close boundary
# ---------------------------------------------------------------------------


def test_persisted_authorization_content_mismatch_rejects_without_writes(
    tmp_path: Path,
) -> None:
    # authorized_at is not part of SignalAuthorization's content-addressed ID, so
    # tampering it leaves intrinsic identity valid while diverging from the
    # in-memory authorization the work item was built with. Both the authorization
    # row and its separate content-commitment row must be tampered consistently -
    # otherwise SQLiteAdoptionStore's own commitment cross-check (a different,
    # earlier defense) rejects first.
    fixture = _fixture(tmp_path)
    with sqlite3.connect(fixture.live) as connection:
        connection.execute("DROP TRIGGER live_signal_authorization_no_update")
        connection.execute("DROP TRIGGER live_signal_authorization_commitment_no_update")
        for table in (
            "live_signal_authorizations",
            "live_signal_authorization_content_commitments",
        ):
            connection.execute(
                f"UPDATE {table} SET authorized_at = '2020-01-01T00:00:00+00:00'"
            )

    with pytest.raises(ValueError, match="differs from persisted authorization"):
        _persist(fixture)

    assert _counts(fixture.live) == (0, 0, 0, 0, 0)


# _validate_persisted_signal_authority's "must not predate authority" check
# (all three of signal.created_at/supplied.authorized_at/evaluated_at before
# authority_start) is not exercised here: LiveAdoptionGate._ineligible_reason
# independently rejects `at < authority_start or signal.created_at <
# authority_start` at authorize() time, using the same adoption_authority_start()
# derivation from the same persisted approval, so no AuthorizedSignal reaching
# this store can carry a Signal or authorized_at older than its own authority
# window. Confirmed unreachable via the legitimate authorize-then-persist path;
# not forced via tampering.


def test_expired_authority_rejects_without_writes(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, expires_at=NOW + timedelta(seconds=1, microseconds=500))

    with pytest.raises(ValueError, match="approval is expired"):
        _persist(fixture)

    assert _counts(fixture.live) == (0, 0, 0, 0, 0)


def test_revoked_authority_rejects_without_writes(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, revoke_at=NOW + timedelta(seconds=1, microseconds=500))

    with pytest.raises(ValueError, match="approval is revoked"):
        _persist(fixture)

    assert _counts(fixture.live) == (0, 0, 0, 0, 0)


# ---------------------------------------------------------------------------
# Reservation result structural consistency (exact-type result validation)
# ---------------------------------------------------------------------------


def test_reservation_result_rejects_approve_outcome_without_an_intent(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, target_fraction=Decimal("0.5"))
    result = _persist(fixture).result
    reservation = _reserve(
        fixture,
        result,
        capacity=fixture.work_item.capacity,
        allocation_policy=fixture.work_item.allocation_policy,
    )
    assert reservation.risk_decision.outcome is OrdinaryCloseRiskOutcome.APPROVE

    with pytest.raises(TypeError, match="Risk APPROVE requires exact ApprovedCloseIntent"):
        OrdinaryCloseReservationPersistenceResult(
            OrdinaryCloseReservationDisposition.INSERTED,
            reservation.portfolio_decision,
            reservation.risk_decision,
            None,
        )


def test_reservation_result_rejects_risk_decision_from_another_portfolio_decision(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, target_fraction=Decimal("0.5"))
    a_result = _persist(fixture).result
    a_reservation = _reserve(
        fixture,
        a_result,
        capacity=fixture.work_item.capacity,
        allocation_policy=fixture.work_item.allocation_policy,
    )
    b_result, b_capacity, b_allocation = _second_close_result(
        fixture, open_quantity=Decimal("1000"), target_fraction=Decimal("0.5")
    )
    b_reservation = _reserve(fixture, b_result, capacity=b_capacity, allocation_policy=b_allocation)

    with pytest.raises(ValueError, match="risk_decision does not belong to portfolio_decision"):
        OrdinaryCloseReservationPersistenceResult(
            OrdinaryCloseReservationDisposition.INSERTED,
            a_reservation.portfolio_decision,
            b_reservation.risk_decision,
            a_reservation.intent,
        )


def test_reservation_result_rejects_intent_from_another_portfolio_decision(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, target_fraction=Decimal("0.5"))
    a_result = _persist(fixture).result
    a_reservation = _reserve(
        fixture,
        a_result,
        capacity=fixture.work_item.capacity,
        allocation_policy=fixture.work_item.allocation_policy,
    )
    b_result, b_capacity, b_allocation = _second_close_result(
        fixture, open_quantity=Decimal("1000"), target_fraction=Decimal("0.5")
    )
    b_reservation = _reserve(fixture, b_result, capacity=b_capacity, allocation_policy=b_allocation)
    assert a_reservation.intent is not None and b_reservation.intent is not None
    assert a_reservation.intent.portfolio_decision_id != b_reservation.intent.portfolio_decision_id

    with pytest.raises(ValueError, match="intent does not belong to portfolio_decision"):
        OrdinaryCloseReservationPersistenceResult(
            OrdinaryCloseReservationDisposition.INSERTED,
            a_reservation.portfolio_decision,
            a_reservation.risk_decision,
            b_reservation.intent,
        )


def test_reservation_result_rejects_reject_outcome_carrying_an_intent(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, target_fraction=Decimal("1"))
    a_result = _persist(fixture).result
    a_reservation = _reserve(
        fixture,
        a_result,
        capacity=fixture.work_item.capacity,
        allocation_policy=fixture.work_item.allocation_policy,
    )
    b_result, b_capacity, b_allocation = _second_close_result(
        fixture, open_quantity=Decimal("1000"), target_fraction=Decimal("1")
    )
    b_reservation = _reserve(fixture, b_result, capacity=b_capacity, allocation_policy=b_allocation)
    assert b_reservation.risk_decision.outcome is OrdinaryCloseRiskOutcome.REJECT
    assert a_reservation.intent is not None

    with pytest.raises(ValueError, match="non-APPROVE Risk outcome cannot carry an Intent"):
        OrdinaryCloseReservationPersistenceResult(
            OrdinaryCloseReservationDisposition.INSERTED,
            b_reservation.portfolio_decision,
            b_reservation.risk_decision,
            a_reservation.intent,
        )


# ---------------------------------------------------------------------------
# LIVE defense-in-depth directly on the reservation persistence entry point
# ---------------------------------------------------------------------------


def test_reservation_persistence_rejects_live_authority_directly_before_any_write(
    tmp_path: Path,
) -> None:
    # The application composition layer already blocks LIVE before ever calling
    # this method; this proves the store's own guard also holds for a caller that
    # invokes evaluate_and_persist_reservation directly.
    fixture = _fixture(tmp_path, target_fraction=Decimal("1"))
    result = _persist(fixture).result

    with pytest.raises(ValueError, match="LIVE authority is prohibited"):
        SQLiteOrdinaryCloseStore(fixture.live).evaluate_and_persist_reservation(
            result,
            capacity=fixture.work_item.capacity,
            allocation_policy=fixture.work_item.allocation_policy,
            risk_policy=fixture.work_item.risk_policy,
            authority=ExecutionAuthorityMode.LIVE,
        )

    assert _reservation_counts(fixture.live) == (0, 0, 0)
