import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .adoption import (
    AdoptionDecisionType,
    AdoptionMode,
    ResearchValidationEvidenceSnapshot,
    ResearchValidationStatus,
    RuntimeMode,
    SignalAuthorization,
    StrategyAdoptionDecision,
    StrategyAdoptionPolicy,
    StrictCohortIdentity,
    approval_decision,
    canonical_json,
    digest,
    revocation_decision,
)
from .decision_store import _SCHEMA
from .live_migrations import migrate_live_database


@dataclass(frozen=True, slots=True)
class ApplyAdoptionResult:
    evidence_created: bool
    policy_created: bool
    decision_created: bool

    @property
    def reused(self) -> bool:
        return not self.evidence_created and not self.policy_created and not self.decision_created


@dataclass(frozen=True, slots=True)
class PersistedAdoptionAuthority:
    authorization: SignalAuthorization
    approval: StrategyAdoptionDecision
    evidence_snapshot: ResearchValidationEvidenceSnapshot
    policy: StrategyAdoptionPolicy
    revocations: tuple[StrategyAdoptionDecision, ...]


class SQLiteAdoptionStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.executescript(_SCHEMA)
            migrate_live_database(connection)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def apply_approval(
        self,
        snapshot: ResearchValidationEvidenceSnapshot,
        policy: StrategyAdoptionPolicy,
        decision: StrategyAdoptionDecision,
    ) -> ApplyAdoptionResult:
        if decision.decision_type is not AdoptionDecisionType.APPROVED_FOR_STRATEGY:
            raise ValueError("apply_approval requires an approval decision")
        self._validate_approval(snapshot, policy, decision)
        with closing(self._connect()) as connection, connection:
            evidence_created = self._append_evidence(connection, snapshot)
            policy_created = self._append_policy(connection, policy, decision.decided_at)
            decision_created = self._append_decision(connection, decision)
        return ApplyAdoptionResult(evidence_created, policy_created, decision_created)

    def append_revocation(self, decision: StrategyAdoptionDecision) -> bool:
        if decision.decision_type is not AdoptionDecisionType.REVOKED:
            raise ValueError("append_revocation requires a revocation decision")
        with closing(self._connect()) as connection, connection:
            approval = connection.execute(
                "SELECT * FROM live_strategy_adoption_decisions "
                "WHERE adoption_decision_id = ? AND decision_type = 'APPROVED_FOR_STRATEGY'",
                (decision.approval_decision_id,),
            ).fetchone()
            if approval is None:
                raise ValueError("approval decision does not exist")
            persisted_approval = self._decision_from_row(approval)
            expected = revocation_decision(
                persisted_approval,
                decided_at=decision.decided_at,
                actor=decision.actor,
                reason=decision.reason,
            )
            if decision != expected:
                raise ValueError("revocation is not derived from the persisted approval")
            return self._append_decision(connection, decision)

    def get_decision(self, decision_id: str) -> StrategyAdoptionDecision:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM live_strategy_adoption_decisions "
                "WHERE adoption_decision_id = ?",
                (decision_id,),
            ).fetchone()
        if row is None:
            raise KeyError(decision_id)
        return self._decision_from_row(row)

    def list_approvals(
        self, *, strategy_id: str, strategy_version: str
    ) -> tuple[StrategyAdoptionDecision, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM live_strategy_adoption_decisions "
                "WHERE decision_type = 'APPROVED_FOR_STRATEGY' "
                "AND strategy_id = ? AND strategy_version = ? "
                "ORDER BY adoption_decision_id",
                (strategy_id, strategy_version),
            ).fetchall()
        return tuple(self._decision_from_row(row) for row in rows)

    def is_revoked_at(self, approval_decision_id: str, at: datetime) -> bool:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT 1 FROM live_strategy_adoption_decisions "
                "WHERE decision_type = 'REVOKED' AND approval_decision_id = ? "
                "AND decided_at <= ? LIMIT 1",
                (approval_decision_id, at.isoformat()),
            ).fetchone()
        return row is not None

    def append_authorization(self, authorization: SignalAuthorization) -> bool:
        self._validate_authorization_integrity(authorization)
        values = _authorization_values(authorization)
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO live_signal_authorizations "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )
            row = connection.execute(
                "SELECT * FROM live_signal_authorizations WHERE authorization_id = ?",
                (authorization.authorization_id,),
            ).fetchone()
            if row is None or tuple(row) != values:
                raise ValueError("Signal authorization identity has different content")
            self._append_or_compare_authorization_commitment(connection, authorization)
        return cursor.rowcount == 1

    def get_authorization(self, authorization_id: str) -> SignalAuthorization:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM live_signal_authorizations WHERE authorization_id = ?",
                (authorization_id,),
            ).fetchone()
            if row is not None:
                authorization = self._authorization_from_row(row)
                self._validate_authorization_commitment_on(connection, authorization)
        if row is None:
            raise KeyError(authorization_id)
        return authorization

    def find_authorization(
        self,
        *,
        signal_id: str,
        adoption_decision_id: str,
        strategy_id: str,
        strategy_version: str,
        runtime_mode: RuntimeMode,
    ) -> SignalAuthorization | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM live_signal_authorizations "
                "WHERE signal_id = ? AND adoption_decision_id = ? "
                "AND strategy_id = ? AND strategy_version = ? AND runtime_mode = ?",
                (
                    signal_id,
                    adoption_decision_id,
                    strategy_id,
                    strategy_version,
                    runtime_mode.value,
                ),
            ).fetchone()
            if row is None:
                return None
            authorization = self._authorization_from_row(row)
            self._validate_authorization_commitment_on(connection, authorization)
            return authorization

    @classmethod
    def get_authority_on(
        cls, connection: sqlite3.Connection, authorization_id: str
    ) -> PersistedAdoptionAuthority:
        authorization_row = connection.execute(
            "SELECT * FROM live_signal_authorizations WHERE authorization_id = ?",
            (authorization_id,),
        ).fetchone()
        if authorization_row is None:
            raise KeyError(authorization_id)
        authorization = cls._authorization_from_row(authorization_row)
        authorization.validate_intrinsic_identity()
        cls._validate_authorization_commitment_on(connection, authorization)
        approval_row = connection.execute(
            "SELECT * FROM live_strategy_adoption_decisions "
            "WHERE adoption_decision_id = ? AND decision_type = 'APPROVED_FOR_STRATEGY'",
            (authorization.adoption_decision_id,),
        ).fetchone()
        if approval_row is None:
            raise ValueError("authorization does not reference a persisted approval")
        approval = cls._decision_from_row(approval_row)
        evidence_row = connection.execute(
            "SELECT * FROM live_research_validation_evidence_snapshots "
            "WHERE evidence_snapshot_id = ?",
            (authorization.evidence_snapshot_id,),
        ).fetchone()
        if evidence_row is None:
            raise ValueError("authorization evidence snapshot is missing")
        evidence = cls._evidence_from_row(evidence_row)
        policy_row = connection.execute(
            "SELECT content_hash, policy_json FROM live_strategy_adoption_policies "
            "WHERE adoption_policy_version = ?",
            (authorization.adoption_policy_version,),
        ).fetchone()
        if policy_row is None:
            raise ValueError("authorization adoption policy is missing")
        policy = StrategyAdoptionPolicy.from_mapping(_json_object(policy_row["policy_json"]))
        if policy.content_hash != policy_row["content_hash"]:
            raise ValueError("persisted adoption policy content hash does not match")
        ResearchValidationEvidenceSnapshot.validate_intrinsic_integrity(evidence)
        cls._validate_approval(evidence, policy, approval)
        if (
            approval.evidence_snapshot_id != authorization.evidence_snapshot_id
            or approval.adoption_policy_version != authorization.adoption_policy_version
            or approval.strategy_id != authorization.strategy_id
            or approval.strategy_version != authorization.strategy_version
            or approval.adoption_mode is not authorization.adoption_mode
        ):
            raise ValueError("persisted authorization lineage is inconsistent")
        revocation_rows = connection.execute(
            "SELECT * FROM live_strategy_adoption_decisions "
            "WHERE approval_decision_id = ? ORDER BY adoption_decision_id",
            (approval.adoption_decision_id,),
        ).fetchall()
        revocations: list[StrategyAdoptionDecision] = []
        for row in revocation_rows:
            revocation = cls._decision_from_row(row)
            if type(revocation) is not StrategyAdoptionDecision:
                raise TypeError("persisted revocation must be exact StrategyAdoptionDecision")
            StrategyAdoptionDecision.__post_init__(revocation)
            if revocation.decision_type is not AdoptionDecisionType.REVOKED:
                raise ValueError("persisted approval-linked decision is not a revocation")
            expected = revocation_decision(
                approval,
                decided_at=revocation.decided_at,
                actor=revocation.actor,
                reason=revocation.reason,
            )
            if revocation != expected:
                raise ValueError("persisted revocation is not derived from exact approval")
            revocations.append(revocation)
        return PersistedAdoptionAuthority(
            authorization, approval, evidence, policy, tuple(revocations)
        )

    def count_rows(self, table: str) -> int:
        allowed = {
            "live_research_validation_evidence_snapshots",
            "live_strategy_adoption_policies",
            "live_strategy_adoption_decisions",
            "live_signal_authorizations",
            "live_candidate_signal_authorizations",
        }
        if table not in allowed:
            raise ValueError("unsupported adoption table")
        with closing(self._connect()) as connection:
            return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    @staticmethod
    def _validate_approval(
        snapshot: ResearchValidationEvidenceSnapshot,
        policy: StrategyAdoptionPolicy,
        decision: StrategyAdoptionDecision,
    ) -> None:
        if snapshot.status is not ResearchValidationStatus.VALIDATED_FOR_RESEARCH:
            raise ValueError("Research evidence is not validated for adoption")
        snapshot.validate_intrinsic_integrity()
        cohort = StrictCohortIdentity.from_payload(snapshot.cohort_identity_payload)
        if (
            snapshot.research_policy_version
            != policy.expected_research_policy_version
            or cohort != policy.expected_cohort
        ):
            raise ValueError("Research evidence does not match the adoption policy")
        policy_content_hash = digest(policy.identity_payload)
        if policy.content_hash != policy_content_hash:
            raise ValueError("adoption policy content hash does not match")
        expected = approval_decision(
            snapshot,
            policy,
            decided_at=decision.decided_at,
            actor=decision.actor,
            reason=decision.reason,
        )
        if decision != expected:
            raise ValueError("approval is not derived from the exact evidence and policy")

    @staticmethod
    def _append_evidence(
        connection: sqlite3.Connection,
        snapshot: ResearchValidationEvidenceSnapshot,
    ) -> bool:
        values = (
            snapshot.evidence_snapshot_id,
            snapshot.source_contract_version,
            snapshot.assessment_id,
            snapshot.evaluation_run_id,
            snapshot.report_id,
            snapshot.research_policy_version,
            snapshot.research_policy_content_hash,
            snapshot.status.value,
            canonical_json(snapshot.cohort_identity_payload),
            snapshot.cohort_identity_hash,
            canonical_json(snapshot.metric_payload),
            snapshot.metric_payload_hash,
            canonical_json(snapshot.condition_results_payload),
            snapshot.input_snapshot_version,
            snapshot.input_snapshot_identity_hash,
            canonical_json(snapshot.input_snapshot_payload),
            canonical_json(snapshot.research_policy_payload),
            snapshot.assessment_created_at.isoformat(),
            snapshot.report_created_at.isoformat(),
            snapshot.run_created_at.isoformat(),
            snapshot.research_policy_created_at.isoformat(),
            snapshot.imported_at.isoformat(),
        )
        cursor = connection.execute(
            "INSERT OR IGNORE INTO live_research_validation_evidence_snapshots "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
        row = connection.execute(
            "SELECT * FROM live_research_validation_evidence_snapshots "
            "WHERE evidence_snapshot_id = ? OR assessment_id = ?",
            (snapshot.evidence_snapshot_id, snapshot.assessment_id),
        ).fetchone()
        if row is None or tuple(row)[:-1] != values[:-1]:
            raise ValueError("Research evidence identity already has different content")
        return cursor.rowcount == 1

    @staticmethod
    def _append_policy(
        connection: sqlite3.Connection,
        policy: StrategyAdoptionPolicy,
        created_at: datetime,
    ) -> bool:
        policy_json = canonical_json(policy.identity_payload)
        cursor = connection.execute(
            "INSERT OR IGNORE INTO live_strategy_adoption_policies VALUES (?, ?, ?, ?)",
            (
                policy.adoption_policy_version,
                policy.content_hash,
                policy_json,
                created_at.isoformat(),
            ),
        )
        row = connection.execute(
            "SELECT content_hash, policy_json FROM live_strategy_adoption_policies "
            "WHERE adoption_policy_version = ?",
            (policy.adoption_policy_version,),
        ).fetchone()
        if row is None or (row["content_hash"], row["policy_json"]) != (
            policy.content_hash,
            policy_json,
        ):
            raise ValueError("adoption policy version already has different content")
        return cursor.rowcount == 1

    @staticmethod
    def _append_decision(
        connection: sqlite3.Connection, decision: StrategyAdoptionDecision
    ) -> bool:
        values = (
            decision.adoption_decision_id,
            decision.decision_type.value,
            decision.evidence_snapshot_id,
            decision.adoption_policy_version,
            decision.adoption_policy_content_hash,
            decision.strategy_id,
            decision.strategy_version,
            decision.strategy_config_identity,
            canonical_json(decision.approved_signal_specification.payload),
            decision.adoption_mode.value,
            decision.effective_from.isoformat(),
            decision.expires_at.isoformat(),
            decision.decided_at.isoformat(),
            decision.actor,
            decision.reason,
            decision.approval_decision_id,
        )
        cursor = connection.execute(
            "INSERT OR IGNORE INTO live_strategy_adoption_decisions "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
        row = connection.execute(
            "SELECT * FROM live_strategy_adoption_decisions WHERE adoption_decision_id = ?",
            (decision.adoption_decision_id,),
        ).fetchone()
        if row is None:
            raise ValueError("adoption decision was not persisted")
        persisted = SQLiteAdoptionStore._decision_from_row(row)
        if persisted.authority_payload != decision.authority_payload:
            raise ValueError("adoption decision identity already has different content")
        if cursor.rowcount == 1 and persisted != decision:
            raise ValueError("new adoption decision did not preserve exact content")
        return cursor.rowcount == 1

    @staticmethod
    def _decision_from_row(row: sqlite3.Row) -> StrategyAdoptionDecision:
        specification = _json_object(row["approved_signal_specification_json"])
        return StrategyAdoptionDecision(
            adoption_decision_id=row["adoption_decision_id"],
            decision_type=AdoptionDecisionType(row["decision_type"]),
            evidence_snapshot_id=row["evidence_snapshot_id"],
            adoption_policy_version=row["adoption_policy_version"],
            adoption_policy_content_hash=row["adoption_policy_content_hash"],
            strategy_id=row["strategy_id"],
            strategy_version=row["strategy_version"],
            strategy_config_identity=row["strategy_config_identity"],
            approved_signal_specification=StrictCohortIdentity.from_payload(specification),
            adoption_mode=AdoptionMode(row["adoption_mode"]),
            effective_from=datetime.fromisoformat(row["effective_from"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            decided_at=datetime.fromisoformat(row["decided_at"]),
            actor=row["actor"],
            reason=row["reason"],
            approval_decision_id=row["approval_decision_id"],
        )

    @staticmethod
    def _authorization_from_row(row: sqlite3.Row) -> SignalAuthorization:
        authorization = SignalAuthorization(
            authorization_id=row["authorization_id"],
            signal_id=row["signal_id"],
            adoption_decision_id=row["adoption_decision_id"],
            evidence_snapshot_id=row["evidence_snapshot_id"],
            adoption_policy_version=row["adoption_policy_version"],
            strategy_id=row["strategy_id"],
            strategy_version=row["strategy_version"],
            adoption_mode=AdoptionMode(row["adoption_mode"]),
            runtime_mode=RuntimeMode(row["runtime_mode"]),
            authorized_at=datetime.fromisoformat(row["authorized_at"]),
        )
        SQLiteAdoptionStore._validate_authorization_integrity(authorization)
        return authorization

    @staticmethod
    def _validate_authorization_integrity(authorization: object) -> None:
        if type(authorization) is not SignalAuthorization:
            raise TypeError("authorization must be exact SignalAuthorization")
        SignalAuthorization.validate_intrinsic_identity(authorization)

    @staticmethod
    def _append_or_compare_authorization_commitment(
        connection: sqlite3.Connection, authorization: SignalAuthorization
    ) -> None:
        values = _authorization_values(authorization)
        connection.execute(
            "INSERT OR IGNORE INTO live_signal_authorization_content_commitments "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
        SQLiteAdoptionStore._validate_authorization_commitment_on(
            connection, authorization
        )

    @staticmethod
    def _validate_authorization_commitment_on(
        connection: sqlite3.Connection, authorization: SignalAuthorization
    ) -> None:
        row = connection.execute(
            "SELECT * FROM live_signal_authorization_content_commitments "
            "WHERE authorization_id = ?",
            (authorization.authorization_id,),
        ).fetchone()
        if row is None or tuple(row) != _authorization_values(authorization):
            raise ValueError("persisted Signal authorization content commitment differs")

    @staticmethod
    def _evidence_from_row(row: sqlite3.Row) -> ResearchValidationEvidenceSnapshot:
        return ResearchValidationEvidenceSnapshot(
            evidence_snapshot_id=row["evidence_snapshot_id"],
            source_contract_version=row["source_contract_version"],
            assessment_id=row["assessment_id"],
            evaluation_run_id=row["evaluation_run_id"],
            report_id=row["report_id"],
            research_policy_version=row["research_policy_version"],
            research_policy_content_hash=row["research_policy_content_hash"],
            status=ResearchValidationStatus(row["status"]),
            cohort_identity_payload=_json_object(row["cohort_identity_json"]),
            cohort_identity_hash=row["cohort_identity_hash"],
            metric_payload=_json_object(row["metric_payload_json"]),
            metric_payload_hash=row["metric_payload_hash"],
            condition_results_payload=json.loads(row["condition_results_json"]),
            input_snapshot_version=row["input_snapshot_version"],
            input_snapshot_identity_hash=row["input_snapshot_identity_hash"],
            input_snapshot_payload=_json_object(row["input_snapshot_json"]),
            research_policy_payload=_json_object(row["research_policy_json"]),
            assessment_created_at=datetime.fromisoformat(row["assessment_created_at"]),
            report_created_at=datetime.fromisoformat(row["report_created_at"]),
            run_created_at=datetime.fromisoformat(row["run_created_at"]),
            research_policy_created_at=datetime.fromisoformat(
                row["research_policy_created_at"]
            ),
            imported_at=datetime.fromisoformat(row["imported_at"]),
        )


def _json_object(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("persisted adoption JSON must be an object")
    return parsed


def _authorization_values(authorization: SignalAuthorization) -> tuple[str, ...]:
    return (
        authorization.authorization_id,
        authorization.signal_id,
        authorization.adoption_decision_id,
        authorization.evidence_snapshot_id,
        authorization.adoption_policy_version,
        authorization.strategy_id,
        authorization.strategy_version,
        authorization.adoption_mode.value,
        authorization.runtime_mode.value,
        authorization.authorized_at.isoformat(),
    )
