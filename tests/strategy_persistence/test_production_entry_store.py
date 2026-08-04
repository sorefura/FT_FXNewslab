import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fx_core import PairScore
from fx_signal_store import PairSignalMaterializerResult, reconstruct_materialized_pair_signal
from swap_bot.adoption import (
    AuthorizedSignal,
    RuntimeMode,
    StrictCohortIdentity,
    revocation_decision,
)
from swap_bot.adoption_application import ApproveSignalAdoptionOnceService
from swap_bot.adoption_gate import LiveAdoptionGate
from swap_bot.adoption_store import SQLiteAdoptionStore
from swap_bot.operational_swap import (
    OperationalSwapResolution,
    OperationalSwapResolutionOutcome,
)
from swap_bot.production_strategy_store import (
    ProductionEntryPersistenceConflict,
    ProductionEntryPersistenceDisposition,
    SQLiteProductionEntryStore,
)
from swap_bot.research_evidence import SQLiteResearchValidationEvidenceSource
from swap_bot.strategy import ProductionEntryEvaluationInput

from tests.adoption_factories import adoption_policy, cohort_payload, seed_research_evidence
from tests.pair_signal_materialization.test_live_authorization_bridge import _selected_result
from tests.strategy_contracts.factories import strategy_config, swap_evidence


@dataclass(frozen=True)
class _Fixture:
    live: Path
    adoption_store: SQLiteAdoptionStore
    approval_id: str
    materializer_result: PairSignalMaterializerResult
    resolution: OperationalSwapResolution
    evaluation_input: ProductionEntryEvaluationInput


def _fixture(
    tmp_path: Path,
    *,
    equality_times: bool = False,
    authority_start_at_signal: bool = False,
) -> _Fixture:
    result = _selected_result()
    signal = reconstruct_materialized_pair_signal(result)
    config = strategy_config()
    pair_cohort = cohort_payload(
        signal_type=signal.signal_type,
        target_type="pair",
        target_value=signal.target.pair.symbol,  # type: ignore[union-attr]
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
    authority_start = (
        signal.created_at
        if authority_start_at_signal
        else signal.created_at - timedelta(minutes=1)
    )
    policy = adoption_policy(
        strategy_id=config.strategy_id,
        strategy_version=config.strategy_version,
        strategy_config_identity=config.strategy_config_identity,
        expected_cohort=StrictCohortIdentity.from_payload(pair_cohort),
        effective_from=authority_start,
        expires_at=signal.created_at + timedelta(days=1),
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
    authorized_at = (
        signal.created_at
        if equality_times
        else signal.created_at + timedelta(seconds=1)
    )
    authorized = LiveAdoptionGate(adoption_store).authorize(
        signal,
        strategy_id=config.strategy_id,
        strategy_version=config.strategy_version,
        strategy_config_identity=config.strategy_config_identity,
        runtime_mode=RuntimeMode.SHADOW,
        authorized_at=authorized_at,
    )
    evaluated_at = authorized_at if equality_times else authorized_at + timedelta(seconds=1)
    swap = swap_evidence(pair=result.request.pair)
    resolution = OperationalSwapResolution.create(
        pair=result.request.pair,
        source=swap.source,
        source_version=swap.source_version,
        requested_at=evaluated_at,
        outcome=OperationalSwapResolutionOutcome.EVIDENCE,
        reason_code="RECORDED_EVIDENCE",
        evidence=swap,
    )
    return _Fixture(
        live,
        adoption_store,
        approval.adoption_decision_id,
        result,
        resolution,
        ProductionEntryEvaluationInput(
            authorized_pair_signal=authorized,
            approved_strategy_config_identity=config.strategy_config_identity,
            evaluated_pair=result.request.pair,
            swap_evidence=swap,
            evaluated_at=evaluated_at,
        ),
    )


def _persist(fixture: _Fixture):  # type: ignore[no-untyped-def]
    return SQLiteProductionEntryStore(fixture.live).evaluate_and_persist(
        config=strategy_config(),
        materializer_result=fixture.materializer_result,
        swap_resolution=fixture.resolution,
        evaluation_input=fixture.evaluation_input,
    )


def _b4_counts(path: Path) -> tuple[int, int, int, int]:
    with sqlite3.connect(path) as connection:
        return tuple(
            int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "live_news_filtered_carry_configs",
                "live_operational_swap_evidence",
                "live_production_entry_evaluations",
                "live_production_trade_candidates",
            )
        )  # type: ignore[return-value]


def test_candidate_insert_and_exact_replay_preserve_numeric_evidence(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)

    inserted = _persist(fixture)
    reused = _persist(fixture)

    assert inserted.disposition is ProductionEntryPersistenceDisposition.INSERTED
    assert reused.disposition is ProductionEntryPersistenceDisposition.REUSED_IDENTICAL
    assert reused.evaluation == inserted.evaluation
    assert inserted.evaluation.candidate is not None
    with sqlite3.connect(fixture.live) as connection:
        connection.row_factory = sqlite3.Row
        evaluation_payload = json.loads(
            connection.execute(
                "SELECT evaluation_json FROM live_production_entry_evaluations"
            ).fetchone()[0]
        )
        candidate_payload = json.loads(
            connection.execute(
                "SELECT candidate_json FROM live_production_trade_candidates"
            ).fetchone()[0]
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM live_production_entry_evaluations"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM live_production_trade_candidates"
        ).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM live_candidates").fetchone()[0] == 0
    candidate = inserted.evaluation.candidate
    assert candidate_payload["pair_score"] == candidate.pair_score.value
    assert candidate_payload["confidence"] == candidate.confidence.value
    assert evaluation_payload == inserted.evaluation.identity_payload


def test_equality_signal_authorization_and_evaluation_times_is_allowed(tmp_path: Path) -> None:
    fixture = _fixture(
        tmp_path,
        equality_times=True,
        authority_start_at_signal=True,
    )

    result = _persist(fixture)

    signal = fixture.evaluation_input.authorized_pair_signal.signal
    authorization = fixture.evaluation_input.authorized_pair_signal.authorization
    assert signal.created_at == authorization.authorized_at == result.evaluation.evaluated_at


def test_authorization_immediately_before_authority_start_rejects_without_writes(
    tmp_path: Path,
) -> None:
    fixture = _fixture(
        tmp_path,
        equality_times=True,
        authority_start_at_signal=True,
    )
    supplied = fixture.evaluation_input.authorized_pair_signal
    backdated = replace(
        supplied.authorization,
        authorized_at=supplied.signal.created_at - timedelta(microseconds=1),
    )
    tampered_input = replace(
        fixture.evaluation_input,
        authorized_pair_signal=AuthorizedSignal(supplied.signal, backdated),
    )
    with sqlite3.connect(fixture.live) as connection:
        connection.execute("DROP TRIGGER live_signal_authorization_no_update")
        connection.execute(
            "DROP TRIGGER live_signal_authorization_commitment_no_update"
        )
        connection.execute(
            "UPDATE live_signal_authorizations SET authorized_at = ?",
            (backdated.authorized_at.isoformat(),),
        )
        connection.execute(
            "UPDATE live_signal_authorization_content_commitments "
            "SET authorized_at = ?",
            (backdated.authorized_at.isoformat(),),
        )

    with pytest.raises(ValueError, match="must not predate authority"):
        SQLiteProductionEntryStore(fixture.live).evaluate_and_persist(
            config=strategy_config(),
            materializer_result=fixture.materializer_result,
            swap_resolution=fixture.resolution,
            evaluation_input=tampered_input,
        )
    assert _b4_counts(fixture.live) == (0, 0, 0, 0)


def test_authorization_before_signal_creation_rejects_without_writes(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    supplied = fixture.evaluation_input.authorized_pair_signal
    backdated = replace(
        supplied.authorization,
        authorized_at=supplied.signal.created_at - timedelta(microseconds=1),
    )
    tampered_input = replace(
        fixture.evaluation_input,
        authorized_pair_signal=AuthorizedSignal(supplied.signal, backdated),
    )
    with sqlite3.connect(fixture.live) as connection:
        connection.execute("DROP TRIGGER live_signal_authorization_no_update")
        connection.execute(
            "DROP TRIGGER live_signal_authorization_commitment_no_update"
        )
        for table in (
            "live_signal_authorizations",
            "live_signal_authorization_content_commitments",
        ):
            connection.execute(
                f"UPDATE {table} SET authorized_at = ?",
                (backdated.authorized_at.isoformat(),),
            )

    with pytest.raises(ValueError, match="order is invalid"):
        SQLiteProductionEntryStore(fixture.live).evaluate_and_persist(
            config=strategy_config(),
            materializer_result=fixture.materializer_result,
            swap_resolution=fixture.resolution,
            evaluation_input=tampered_input,
        )
    assert _b4_counts(fixture.live) == (0, 0, 0, 0)


def test_evaluation_before_authorization_rejects_without_writes(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    authorization = fixture.evaluation_input.authorized_pair_signal.authorization
    backdated_input = replace(
        fixture.evaluation_input,
        evaluated_at=authorization.authorized_at - timedelta(microseconds=1),
    )

    with pytest.raises(ValueError, match="order is invalid"):
        SQLiteProductionEntryStore(fixture.live).evaluate_and_persist(
            config=strategy_config(),
            materializer_result=fixture.materializer_result,
            swap_resolution=fixture.resolution,
            evaluation_input=backdated_input,
        )
    assert _b4_counts(fixture.live) == (0, 0, 0, 0)


def test_persisted_approval_for_another_config_rejects_without_writes(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    another_config = strategy_config(positive_entry_threshold=PairScore(0.6))
    mismatched_input = replace(
        fixture.evaluation_input,
        approved_strategy_config_identity=another_config.strategy_config_identity,
    )

    with pytest.raises(ValueError, match="does not approve the exact Strategy config"):
        SQLiteProductionEntryStore(fixture.live).evaluate_and_persist(
            config=another_config,
            materializer_result=fixture.materializer_result,
            swap_resolution=fixture.resolution,
            evaluation_input=mismatched_input,
        )
    assert _b4_counts(fixture.live) == (0, 0, 0, 0)


def test_materialization_and_authorized_signal_content_mismatch_has_no_writes(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    supplied = fixture.evaluation_input.authorized_pair_signal
    forged_signal = replace(supplied.signal, direction=PairScore(0.75))
    mismatched_input = replace(
        fixture.evaluation_input,
        authorized_pair_signal=AuthorizedSignal(forged_signal, supplied.authorization),
    )

    with pytest.raises(ValueError, match="differs from materialization evidence"):
        SQLiteProductionEntryStore(fixture.live).evaluate_and_persist(
            config=strategy_config(),
            materializer_result=fixture.materializer_result,
            swap_resolution=fixture.resolution,
            evaluation_input=mismatched_input,
        )
    assert _b4_counts(fixture.live) == (0, 0, 0, 0)


def test_skip_persists_no_candidate(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    no_carry = swap_evidence(
        pair=fixture.evaluation_input.evaluated_pair,
        long_received_amount=Decimal("0"),
        short_received_amount=Decimal("-1"),
    )
    resolution = OperationalSwapResolution.create(
        pair=fixture.evaluation_input.evaluated_pair,
        source=no_carry.source,
        source_version=no_carry.source_version,
        requested_at=fixture.evaluation_input.evaluated_at,
        outcome=OperationalSwapResolutionOutcome.EVIDENCE,
        reason_code="RECORDED_EVIDENCE",
        evidence=no_carry,
    )
    evaluation_input = ProductionEntryEvaluationInput(
        authorized_pair_signal=fixture.evaluation_input.authorized_pair_signal,
        approved_strategy_config_identity=fixture.evaluation_input.approved_strategy_config_identity,
        evaluated_pair=fixture.evaluation_input.evaluated_pair,
        swap_evidence=no_carry,
        evaluated_at=fixture.evaluation_input.evaluated_at,
    )

    persisted = SQLiteProductionEntryStore(fixture.live).evaluate_and_persist(
        config=strategy_config(),
        materializer_result=fixture.materializer_result,
        swap_resolution=resolution,
        evaluation_input=evaluation_input,
    )

    assert persisted.evaluation.candidate is None
    with sqlite3.connect(fixture.live) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM live_production_trade_candidates"
        ).fetchone()[0] == 0


def test_non_evidence_resolution_stops_before_transaction(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    missing = OperationalSwapResolution.create(
        pair=fixture.resolution.pair,
        source="recorded-swap-source",
        source_version="recorded-swap-v1",
        requested_at=fixture.evaluation_input.evaluated_at,
        outcome=OperationalSwapResolutionOutcome.MISSING,
        reason_code="NO_INPUT",
        evidence=None,
    )

    with pytest.raises(ValueError, match="only EVIDENCE"):
        SQLiteProductionEntryStore(fixture.live).evaluate_and_persist(
            config=strategy_config(),
            materializer_result=fixture.materializer_result,
            swap_resolution=missing,
            evaluation_input=fixture.evaluation_input,
        )

    with sqlite3.connect(fixture.live) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM live_operational_swap_evidence"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM live_production_entry_evaluations"
        ).fetchone()[0] == 0


def test_intrinsically_valid_persisted_live_authorization_stops_before_b4(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    supplied = fixture.evaluation_input.authorized_pair_signal
    live_authorization = replace(
        supplied.authorization,
        runtime_mode=RuntimeMode.LIVE,
    )
    live_authorization = replace(
        live_authorization,
        authorization_id=live_authorization.expected_authorization_id,
    )
    live_input = replace(
        fixture.evaluation_input,
        authorized_pair_signal=AuthorizedSignal(supplied.signal, live_authorization),
    )
    with sqlite3.connect(fixture.live) as connection:
        connection.execute("DROP TRIGGER live_signal_authorization_no_update")
        connection.execute(
            "DROP TRIGGER live_signal_authorization_commitment_no_update"
        )
        for table in (
            "live_signal_authorizations",
            "live_signal_authorization_content_commitments",
        ):
            connection.execute(
                f"UPDATE {table} SET authorization_id = ?, runtime_mode = 'LIVE'",
                (live_authorization.authorization_id,),
            )

    with pytest.raises(ValueError, match="LIVE authority is prohibited"):
        SQLiteProductionEntryStore(fixture.live).evaluate_and_persist(
            config=strategy_config(),
            materializer_result=fixture.materializer_result,
            swap_resolution=fixture.resolution,
            evaluation_input=live_input,
        )
    assert _b4_counts(fixture.live) == (0, 0, 0, 0)


def test_config_conflict_rolls_back_swap_and_evaluation(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    config = strategy_config()
    with sqlite3.connect(fixture.live) as connection:
        connection.execute(
            "INSERT INTO live_news_filtered_carry_configs VALUES (?, ?)",
            (config.strategy_config_identity, '{"forged":true}'),
        )

    with pytest.raises(ProductionEntryPersistenceConflict, match="config identity"):
        _persist(fixture)

    with sqlite3.connect(fixture.live) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM live_operational_swap_evidence"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM live_production_entry_evaluations"
        ).fetchone()[0] == 0


def test_replay_rejects_missing_candidate_without_repairing_corruption(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    inserted = _persist(fixture)
    assert inserted.evaluation.candidate is not None
    with sqlite3.connect(fixture.live) as connection:
        connection.execute(
            "DROP TRIGGER live_production_trade_candidates_no_delete"
        )
        connection.execute("DELETE FROM live_production_trade_candidates")

    with pytest.raises(ProductionEntryPersistenceConflict, match="lacks its exact"):
        _persist(fixture)

    with sqlite3.connect(fixture.live) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM live_production_entry_evaluations"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM live_production_trade_candidates"
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("table", "trigger", "message"),
    (
        (
            "live_news_filtered_carry_configs",
            "live_news_filtered_carry_configs_no_delete",
            "Strategy config",
        ),
        (
            "live_operational_swap_evidence",
            "live_operational_swap_evidence_no_delete",
            "Swap Evidence",
        ),
    ),
)
def test_replay_rejects_missing_parent_without_repair(
    tmp_path: Path, table: str, trigger: str, message: str
) -> None:
    fixture = _fixture(tmp_path)
    _persist(fixture)
    with sqlite3.connect(fixture.live) as connection:
        connection.execute(f"DROP TRIGGER {trigger}")
        connection.execute(f"DELETE FROM {table}")

    with pytest.raises(ProductionEntryPersistenceConflict, match=message):
        _persist(fixture)

    with sqlite3.connect(fixture.live) as connection:
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM live_production_entry_evaluations"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM live_production_trade_candidates"
        ).fetchone()[0] == 1


@pytest.mark.parametrize(
    ("column", "forged_value"),
    (
        ("materialization_request_id", "pair-signal-request-forged"),
        ("pair_signal_content_hash", "signal-content-forged"),
    ),
)
def test_replay_rejects_conflicting_materialization_lineage_without_row_changes(
    tmp_path: Path, column: str, forged_value: str
) -> None:
    fixture = _fixture(tmp_path)
    _persist(fixture)
    with sqlite3.connect(fixture.live) as connection:
        connection.execute("DROP TRIGGER live_production_entry_evaluations_no_update")
        connection.execute(
            f"UPDATE live_production_entry_evaluations SET {column} = ?",
            (forged_value,),
        )
    before = _b4_counts(fixture.live)

    with pytest.raises(ProductionEntryPersistenceConflict, match="different content"):
        _persist(fixture)

    assert _b4_counts(fixture.live) == before == (1, 1, 1, 1)
    with sqlite3.connect(fixture.live) as connection:
        assert connection.execute(
            f"SELECT {column} FROM live_production_entry_evaluations"
        ).fetchone() == (forged_value,)


def test_expiry_at_evaluation_instant_rejects_without_partial_rows(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    approval = fixture.adoption_store.get_decision(fixture.approval_id)
    expired_input = ProductionEntryEvaluationInput(
        authorized_pair_signal=fixture.evaluation_input.authorized_pair_signal,
        approved_strategy_config_identity=fixture.evaluation_input.approved_strategy_config_identity,
        evaluated_pair=fixture.evaluation_input.evaluated_pair,
        swap_evidence=fixture.evaluation_input.swap_evidence,
        evaluated_at=approval.expires_at,
    )

    with pytest.raises(ValueError, match="expired"):
        SQLiteProductionEntryStore(fixture.live).evaluate_and_persist(
            config=strategy_config(),
            materializer_result=fixture.materializer_result,
            swap_resolution=fixture.resolution,
            evaluation_input=expired_input,
        )

    with sqlite3.connect(fixture.live) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM live_production_entry_evaluations"
        ).fetchone()[0] == 0


def test_revocation_at_evaluation_instant_is_effective(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture.adoption_store.append_revocation(
        revocation_decision(
            fixture.adoption_store.get_decision(fixture.approval_id),
            decided_at=fixture.evaluation_input.evaluated_at,
            actor="phase-reviewer",
            reason="withdrawn",
        )
    )

    with pytest.raises(ValueError, match="revoked"):
        _persist(fixture)


def test_later_revocation_allows_historical_replay(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture.adoption_store.append_revocation(
        revocation_decision(
            fixture.adoption_store.get_decision(fixture.approval_id),
            decided_at=fixture.evaluation_input.evaluated_at + timedelta(seconds=1),
            actor="phase-reviewer",
            reason="later withdrawal",
        )
    )

    assert _persist(fixture).disposition is ProductionEntryPersistenceDisposition.INSERTED


def test_corrupt_later_revocation_fails_integrity_before_historical_replay(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    fixture.adoption_store.append_revocation(
        revocation_decision(
            fixture.adoption_store.get_decision(fixture.approval_id),
            decided_at=fixture.evaluation_input.evaluated_at + timedelta(seconds=1),
            actor="phase-reviewer",
            reason="later withdrawal",
        )
    )
    with sqlite3.connect(fixture.live) as connection:
        connection.execute("DROP TRIGGER live_adoption_decision_no_update")
        connection.execute(
            "UPDATE live_strategy_adoption_decisions SET strategy_version = 'forged' "
            "WHERE decision_type = 'REVOKED'"
        )

    with pytest.raises(ValueError, match="not derived from exact approval"):
        _persist(fixture)

    with sqlite3.connect(fixture.live) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM live_production_entry_evaluations"
        ).fetchone()[0] == 0


def test_forged_persisted_authorization_id_fails_intrinsic_identity(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    supplied = fixture.evaluation_input.authorized_pair_signal
    forged_authorization = replace(
        supplied.authorization,
        authorization_id="signal-authorization-forged",
    )
    forged_input = object.__new__(ProductionEntryEvaluationInput)
    for field in fixture.evaluation_input.__dataclass_fields__:
        object.__setattr__(
            forged_input,
            field,
            (
                AuthorizedSignal(supplied.signal, forged_authorization)
                if field == "authorized_pair_signal"
                else getattr(fixture.evaluation_input, field)
            ),
        )
    with sqlite3.connect(fixture.live) as connection:
        connection.execute("DROP TRIGGER live_signal_authorization_no_update")
        connection.execute(
            "UPDATE live_signal_authorizations SET authorization_id = ?",
            (forged_authorization.authorization_id,),
        )

    with pytest.raises(ValueError, match="ID does not match intrinsic authority"):
        SQLiteProductionEntryStore(fixture.live).evaluate_and_persist(
            config=strategy_config(),
            materializer_result=fixture.materializer_result,
            swap_resolution=fixture.resolution,
            evaluation_input=forged_input,
        )

    with sqlite3.connect(fixture.live) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM live_production_entry_evaluations"
        ).fetchone()[0] == 0


def test_tampered_authorized_at_fails_content_commitment_before_b4_writes(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    supplied = fixture.evaluation_input.authorized_pair_signal
    tampered_authorization = replace(
        supplied.authorization,
        authorized_at=supplied.authorization.authorized_at + timedelta(microseconds=1),
    )
    tampered_input = replace(
        fixture.evaluation_input,
        authorized_pair_signal=AuthorizedSignal(supplied.signal, tampered_authorization),
    )
    with sqlite3.connect(fixture.live) as connection:
        connection.execute("DROP TRIGGER live_signal_authorization_no_update")
        connection.execute(
            "UPDATE live_signal_authorizations SET authorized_at = ?",
            (tampered_authorization.authorized_at.isoformat(),),
        )

    with pytest.raises(ValueError, match="content commitment differs"):
        SQLiteProductionEntryStore(fixture.live).evaluate_and_persist(
            config=strategy_config(),
            materializer_result=fixture.materializer_result,
            swap_resolution=fixture.resolution,
            evaluation_input=tampered_input,
        )

    with sqlite3.connect(fixture.live) as connection:
        for table in (
            "live_news_filtered_carry_configs",
            "live_operational_swap_evidence",
            "live_production_entry_evaluations",
            "live_production_trade_candidates",
        ):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_populated_0002_authorization_is_backfilled_and_replays_after_upgrade(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    authorization_id = (
        fixture.evaluation_input.authorized_pair_signal.authorization.authorization_id
    )
    with sqlite3.connect(fixture.live) as connection:
        connection.execute(
            "DROP TABLE live_signal_authorization_content_commitments"
        )
        connection.execute("DROP TABLE live_production_trade_candidates")
        connection.execute("DROP TABLE live_production_entry_evaluations")
        connection.execute("DROP TABLE live_news_filtered_carry_configs")
        connection.execute(
            "DELETE FROM live_schema_migrations "
            "WHERE version = '0004_production_entry_strategy.sql'"
        )

    SQLiteProductionEntryStore(fixture.live)

    with sqlite3.connect(fixture.live) as connection:
        commitment = connection.execute(
            "SELECT authorization_id FROM live_signal_authorization_content_commitments"
        ).fetchone()
    assert commitment == (authorization_id,)
    assert _persist(fixture).disposition is ProductionEntryPersistenceDisposition.INSERTED


def test_concurrent_identical_writers_insert_once_and_reuse_once(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    store = SQLiteProductionEntryStore(fixture.live)

    def write_once() -> ProductionEntryPersistenceDisposition:
        return store.evaluate_and_persist(
            config=strategy_config(),
            materializer_result=fixture.materializer_result,
            swap_resolution=fixture.resolution,
            evaluation_input=fixture.evaluation_input,
        ).disposition

    with ThreadPoolExecutor(max_workers=2) as executor:
        dispositions = tuple(executor.map(lambda _: write_once(), range(2)))

    assert sorted(dispositions) == sorted(
        (
            ProductionEntryPersistenceDisposition.INSERTED,
            ProductionEntryPersistenceDisposition.REUSED_IDENTICAL,
        )
    )
