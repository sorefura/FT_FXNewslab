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
    RuntimeMode,
    SignalAuthorization,
    StrategyAdoptionDecision,
    StrategyAdoptionPolicy,
    StrictCohortIdentity,
    canonical_json,
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
            expected = self._decision_from_row(approval)
            if (
                decision.evidence_snapshot_id != expected.evidence_snapshot_id
                or decision.adoption_policy_version != expected.adoption_policy_version
                or decision.strategy_id != expected.strategy_id
                or decision.strategy_version != expected.strategy_version
            ):
                raise ValueError("revocation does not preserve exact approval identity")
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
        values = (
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
            if row is None or tuple(row)[:-1] != values[:-1]:
                raise ValueError("Signal authorization identity has different content")
        return cursor.rowcount == 1

    def get_authorization(self, authorization_id: str) -> SignalAuthorization:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM live_signal_authorizations WHERE authorization_id = ?",
                (authorization_id,),
            ).fetchone()
        if row is None:
            raise KeyError(authorization_id)
        return self._authorization_from_row(row)

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
        if (
            persisted.decision_type != decision.decision_type
            or persisted.evidence_snapshot_id != decision.evidence_snapshot_id
            or persisted.adoption_policy_version != decision.adoption_policy_version
            or persisted.adoption_policy_content_hash
            != decision.adoption_policy_content_hash
            or persisted.strategy_id != decision.strategy_id
            or persisted.strategy_version != decision.strategy_version
            or persisted.strategy_config_identity != decision.strategy_config_identity
            or persisted.approved_signal_specification
            != decision.approved_signal_specification
            or persisted.adoption_mode != decision.adoption_mode
            or persisted.effective_from != decision.effective_from
            or persisted.expires_at != decision.expires_at
            or persisted.approval_decision_id != decision.approval_decision_id
        ):
            raise ValueError("adoption decision identity already has different content")
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
        return SignalAuthorization(
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


def _json_object(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("persisted adoption JSON must be an object")
    return parsed
