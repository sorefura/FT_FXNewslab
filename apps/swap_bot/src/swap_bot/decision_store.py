import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from .adoption import (
    AdoptionFailureReason,
    AdoptionRejected,
    AuthorizedSignal,
    StrictCohortIdentity,
    adoption_authority_start,
)
from .live_migrations import migrate_live_database
from .models import (
    ApprovedExecutionIntent,
    CandidateId,
    OrderResult,
    PortfolioDecision,
    RiskDecision,
    TradeCandidate,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_candidates (
    id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    pair TEXT NOT NULL,
    side TEXT NOT NULL,
    score REAL NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS live_candidate_signals (
    candidate_id TEXT NOT NULL REFERENCES live_candidates(id),
    signal_id TEXT NOT NULL,
    PRIMARY KEY(candidate_id, signal_id)
);
CREATE TABLE IF NOT EXISTS live_portfolio_decisions (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES live_candidates(id),
    disposition TEXT NOT NULL,
    proposed_quantity TEXT,
    reason_code TEXT NOT NULL,
    exposure_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS live_risk_decisions (
    id TEXT PRIMARY KEY,
    portfolio_decision_id TEXT NOT NULL REFERENCES live_portfolio_decisions(id),
    disposition TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    risk_policy_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS live_execution_intents (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES live_candidates(id),
    risk_decision_id TEXT NOT NULL REFERENCES live_risk_decisions(id),
    pair TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS live_order_results (
    id TEXT PRIMARY KEY,
    execution_intent_id TEXT NOT NULL REFERENCES live_execution_intents(id),
    status TEXT NOT NULL,
    filled_quantity TEXT NOT NULL,
    broker_order_id TEXT,
    error_code TEXT,
    completed_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS live_candidates_no_update
BEFORE UPDATE ON live_candidates BEGIN SELECT RAISE(ABORT, 'candidate is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_candidates_no_delete
BEFORE DELETE ON live_candidates BEGIN SELECT RAISE(ABORT, 'candidate is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_candidate_signals_no_update
BEFORE UPDATE ON live_candidate_signals
BEGIN SELECT RAISE(ABORT, 'candidate lineage is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_candidate_signals_no_delete
BEFORE DELETE ON live_candidate_signals
BEGIN SELECT RAISE(ABORT, 'candidate lineage is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_portfolio_no_update
BEFORE UPDATE ON live_portfolio_decisions
BEGIN SELECT RAISE(ABORT, 'portfolio decision is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_portfolio_no_delete
BEFORE DELETE ON live_portfolio_decisions
BEGIN SELECT RAISE(ABORT, 'portfolio decision is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_risk_no_update
BEFORE UPDATE ON live_risk_decisions BEGIN SELECT RAISE(ABORT, 'risk decision is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_risk_no_delete
BEFORE DELETE ON live_risk_decisions BEGIN SELECT RAISE(ABORT, 'risk decision is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_intents_no_update
BEFORE UPDATE ON live_execution_intents
BEGIN SELECT RAISE(ABORT, 'execution intent is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_intents_no_delete
BEFORE DELETE ON live_execution_intents
BEGIN SELECT RAISE(ABORT, 'execution intent is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_intents_require_consistent_chain
BEFORE INSERT ON live_execution_intents
WHEN NOT EXISTS (
    SELECT 1
    FROM live_risk_decisions AS risk
    JOIN live_portfolio_decisions AS portfolio
      ON portfolio.id = risk.portfolio_decision_id
    WHERE risk.id = NEW.risk_decision_id
      AND portfolio.candidate_id = NEW.candidate_id
)
BEGIN SELECT RAISE(ABORT, 'execution intent decision chain is inconsistent'); END;
CREATE TRIGGER IF NOT EXISTS live_results_no_update
BEFORE UPDATE ON live_order_results BEGIN SELECT RAISE(ABORT, 'order result is immutable'); END;
CREATE TRIGGER IF NOT EXISTS live_results_no_delete
BEFORE DELETE ON live_order_results BEGIN SELECT RAISE(ABORT, 'order result is immutable'); END;
"""


class SQLiteLiveDecisionStore:
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

    def append_candidate(self, candidate: TradeCandidate) -> None:
        with closing(self._connect()) as connection, connection:
            self._insert_candidate(connection, candidate)

    def append_authorized_candidate(
        self,
        candidate: TradeCandidate,
        authorized_signals: tuple[AuthorizedSignal, ...],
    ) -> None:
        candidate_signal_ids = tuple(item.value for item in candidate.signal_ids)
        authorization_signal_ids = tuple(
            item.authorization.signal_id for item in authorized_signals
        )
        if (
            len(set(candidate_signal_ids)) != len(candidate_signal_ids)
            or len(set(authorization_signal_ids)) != len(authorization_signal_ids)
            or set(candidate_signal_ids) != set(authorization_signal_ids)
        ):
            raise AdoptionRejected(
                AdoptionFailureReason.SIGNAL_SPECIFICATION_MISMATCH,
                "Candidate requires one exact authorization per contributing Signal",
            )
        with closing(self._connect()) as connection, connection:
            for authorized in authorized_signals:
                self._validate_candidate_authorization(
                    connection, candidate, authorized
                )
            self._insert_candidate(connection, candidate)
            connection.executemany(
                "INSERT INTO live_candidate_signal_authorizations "
                "VALUES (?, ?, ?, ?)",
                (
                    (
                        candidate.candidate_id.value,
                        authorized.signal.signal_id.value,
                        authorized.authorization.authorization_id,
                        authorized.authorization.adoption_decision_id,
                    )
                    for authorized in authorized_signals
                ),
            )

    def candidate_authorization_lineage(
        self, candidate_id: CandidateId
    ) -> tuple[dict[str, object], ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM live_candidate_signal_authorizations "
                "WHERE candidate_id = ? ORDER BY signal_id",
                (candidate_id.value,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def append_portfolio_decision(self, decision: PortfolioDecision) -> None:
        exposure = {
            item.currency.code: str(item.amount)
            for item in decision.exposure_snapshot.exposures
        }
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO live_portfolio_decisions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    decision.decision_id.value,
                    decision.candidate_id.value,
                    decision.disposition.value,
                    str(decision.proposed_quantity)
                    if decision.proposed_quantity is not None
                    else None,
                    decision.reason_code,
                    json.dumps(exposure, separators=(",", ":"), sort_keys=True),
                    decision.created_at.isoformat(),
                ),
            )

    def append_risk_decision(self, decision: RiskDecision) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO live_risk_decisions VALUES (?, ?, ?, ?, ?, ?)",
                (
                    decision.decision_id.value,
                    decision.portfolio_decision_id.value,
                    decision.disposition.value,
                    decision.reason_code,
                    decision.risk_policy_version,
                    decision.created_at.isoformat(),
                ),
            )

    def append_intent(self, intent: ApprovedExecutionIntent) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO live_execution_intents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    intent.intent_id.value,
                    intent.candidate_id.value,
                    intent.risk_decision_id.value,
                    intent.pair.symbol,
                    intent.side.value,
                    str(intent.quantity),
                    intent.idempotency_key,
                    intent.created_at.isoformat(),
                ),
            )

    def append_order_result(self, result: OrderResult) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO live_order_results VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    result.result_id.value,
                    result.execution_intent_id.value,
                    result.status.value,
                    str(result.filled_quantity),
                    result.broker_order_id,
                    result.error_code,
                    result.completed_at.isoformat(),
                ),
            )

    def decision_chain(self, candidate_id: CandidateId) -> dict[str, object]:
        with closing(self._connect()) as connection, connection:
            candidate = connection.execute(
                "SELECT * FROM live_candidates WHERE id = ?", (candidate_id.value,)
            ).fetchone()
            if candidate is None:
                raise KeyError(candidate_id.value)
            signals = connection.execute(
                "SELECT signal_id FROM live_candidate_signals "
                "WHERE candidate_id = ? ORDER BY signal_id",
                (candidate_id.value,),
            ).fetchall()
            portfolio = connection.execute(
                "SELECT * FROM live_portfolio_decisions WHERE candidate_id = ?",
                (candidate_id.value,),
            ).fetchone()
            risk = (
                connection.execute(
                    "SELECT * FROM live_risk_decisions WHERE portfolio_decision_id = ?",
                    (portfolio["id"],),
                ).fetchone()
                if portfolio is not None
                else None
            )
            intent = (
                connection.execute(
                    "SELECT * FROM live_execution_intents WHERE risk_decision_id = ?",
                    (risk["id"],),
                ).fetchone()
                if risk is not None
                else None
            )
            result = (
                connection.execute(
                    "SELECT * FROM live_order_results WHERE execution_intent_id = ?",
                    (intent["id"],),
                ).fetchone()
                if intent is not None
                else None
            )
        return {
            "candidate_id": candidate_id.value,
            "signal_ids": [item["signal_id"] for item in signals],
            "portfolio": dict(portfolio) if portfolio is not None else None,
            "risk": dict(risk) if risk is not None else None,
            "intent": dict(intent) if intent is not None else None,
            "order_result": dict(result) if result is not None else None,
        }

    @staticmethod
    def _insert_candidate(
        connection: sqlite3.Connection, candidate: TradeCandidate
    ) -> None:
        connection.execute(
            "INSERT INTO live_candidates VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                candidate.candidate_id.value,
                candidate.strategy_id,
                candidate.strategy_version,
                candidate.pair.symbol,
                candidate.side.value,
                candidate.score.value,
                candidate.created_at.isoformat(),
            ),
        )
        connection.executemany(
            "INSERT INTO live_candidate_signals VALUES (?, ?)",
            ((candidate.candidate_id.value, item.value) for item in candidate.signal_ids),
        )

    @staticmethod
    def _validate_candidate_authorization(
        connection: sqlite3.Connection,
        candidate: TradeCandidate,
        authorized: AuthorizedSignal,
    ) -> None:
        authorization = authorized.authorization
        if (
            authorized.signal.signal_id.value != authorization.signal_id
            or authorization.strategy_id != candidate.strategy_id
            or authorization.strategy_version != candidate.strategy_version
            or authorization.authorized_at > candidate.created_at
        ):
            raise AdoptionRejected(
                AdoptionFailureReason.SIGNAL_SPECIFICATION_MISMATCH,
                "Candidate and Signal authorization identities differ",
            )
        persisted = connection.execute(
            "SELECT * FROM live_signal_authorizations WHERE authorization_id = ?",
            (authorization.authorization_id,),
        ).fetchone()
        if persisted is None or tuple(persisted) != (
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
        ):
            raise AdoptionRejected(
                AdoptionFailureReason.SIGNAL_SPECIFICATION_MISMATCH,
                "Signal authorization is not exact persisted Live state",
            )
        approval = connection.execute(
            "SELECT * FROM live_strategy_adoption_decisions "
            "WHERE adoption_decision_id = ? "
            "AND decision_type = 'APPROVED_FOR_STRATEGY'",
            (authorization.adoption_decision_id,),
        ).fetchone()
        if approval is None:
            raise AdoptionRejected(
                AdoptionFailureReason.NO_ACTIVE_ADOPTION,
                "authorization approval no longer exists",
            )
        specification_payload = json.loads(
            approval["approved_signal_specification_json"]
        )
        if not isinstance(specification_payload, dict):
            raise AdoptionRejected(
                AdoptionFailureReason.NO_ACTIVE_ADOPTION,
                "approval Signal specification is malformed",
            )
        specification = StrictCohortIdentity.from_payload(specification_payload)
        if (
            approval["evidence_snapshot_id"] != authorization.evidence_snapshot_id
            or approval["adoption_policy_version"]
            != authorization.adoption_policy_version
            or approval["strategy_id"] != candidate.strategy_id
            or approval["strategy_version"] != candidate.strategy_version
            or approval["adoption_mode"] != authorization.adoption_mode.value
            or not specification.matches_signal(authorized.signal)
        ):
            raise AdoptionRejected(
                AdoptionFailureReason.SIGNAL_SPECIFICATION_MISMATCH,
                "authorization does not preserve the exact approval",
            )
        candidate_at = candidate.created_at.isoformat()
        authority_start = adoption_authority_start(
            datetime.fromisoformat(approval["effective_from"]),
            datetime.fromisoformat(approval["decided_at"]),
        )
        if (
            authorized.signal.created_at < authority_start
            or authorization.authorized_at < authority_start
            or candidate.created_at < authority_start
        ):
            raise AdoptionRejected(
                AdoptionFailureReason.ADOPTION_NOT_YET_EFFECTIVE,
                "approval authority had not started for Signal authorization or Candidate creation",
            )
        if candidate.created_at >= datetime.fromisoformat(approval["expires_at"]):
            raise AdoptionRejected(
                AdoptionFailureReason.ADOPTION_EXPIRED,
                "approval expired before Candidate creation",
            )
        revoked = connection.execute(
            "SELECT 1 FROM live_strategy_adoption_decisions "
            "WHERE decision_type = 'REVOKED' AND approval_decision_id = ? "
            "AND decided_at <= ? LIMIT 1",
            (authorization.adoption_decision_id, candidate_at),
        ).fetchone()
        if revoked is not None:
            raise AdoptionRejected(
                AdoptionFailureReason.ADOPTION_REVOKED,
                "approval was revoked before Candidate creation",
            )
