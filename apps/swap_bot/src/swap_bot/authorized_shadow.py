from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from fx_core import Signal

from .adoption import RuntimeMode
from .adoption_gate import LiveAdoptionGate
from .decision_store import SQLiteLiveDecisionStore
from .execution import ExecutionService
from .models import (
    AccountSnapshot,
    ExecutionIntentId,
    OrderStatus,
    PendingIntent,
    PortfolioDecisionId,
    PositionSnapshot,
    RiskDecisionId,
)
from .portfolio import PortfolioService
from .ports import Strategy
from .risk import RiskService


@dataclass(frozen=True, slots=True)
class AuthorizedShadowCycleResult:
    authorization_id: str
    candidate_id: str
    portfolio_disposition: str
    risk_disposition: str
    order_status: OrderStatus


class AuthorizedShadowCycleService:
    def __init__(
        self,
        *,
        adoption_gate: LiveAdoptionGate,
        strategy: Strategy,
        portfolio: PortfolioService,
        risk: RiskService,
        execution: ExecutionService,
        decisions: SQLiteLiveDecisionStore,
    ) -> None:
        self._adoption_gate = adoption_gate
        self._strategy = strategy
        self._portfolio = portfolio
        self._risk = risk
        self._execution = execution
        self._decisions = decisions

    def run(
        self,
        signal: Signal,
        *,
        strategy_id: str,
        strategy_version: str,
        strategy_config_identity: str | None,
        runtime_mode: RuntimeMode,
        positions: Sequence[PositionSnapshot],
        pending_intents: Sequence[PendingIntent],
        account: AccountSnapshot,
        requested_quantity: Decimal,
        reference_price: Decimal,
        portfolio_decision_id: PortfolioDecisionId,
        risk_decision_id: RiskDecisionId,
        execution_intent_id: ExecutionIntentId,
        idempotency_key: str,
        cycle_at: datetime,
    ) -> AuthorizedShadowCycleResult:
        authorized = self._adoption_gate.authorize(
            signal,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            strategy_config_identity=strategy_config_identity,
            runtime_mode=runtime_mode,
            authorized_at=cycle_at,
        )
        candidate = self._strategy.evaluate((authorized,))
        if candidate is None:
            raise ValueError("Strategy produced no TradeCandidate for the shadow cycle")
        self._decisions.append_authorized_candidate(candidate, (authorized,))
        portfolio = self._portfolio.evaluate(
            candidate,
            positions=positions,
            pending_intents=pending_intents,
            requested_quantity=requested_quantity,
            reference_price=reference_price,
            decision_id=portfolio_decision_id,
            created_at=cycle_at,
        )
        risk = self._risk.evaluate(
            portfolio,
            candidate,
            account=account,
            positions=positions,
            decision_id=risk_decision_id,
            created_at=cycle_at,
        )
        intent = self._risk.create_execution_intent(
            risk,
            portfolio,
            candidate,
            intent_id=execution_intent_id,
            idempotency_key=idempotency_key,
            created_at=cycle_at,
        )
        result = self._execution.submit(intent)
        self._decisions.append_portfolio_decision(portfolio)
        self._decisions.append_risk_decision(risk)
        self._decisions.append_intent(intent)
        self._decisions.append_order_result(result)
        return AuthorizedShadowCycleResult(
            authorization_id=authorized.authorization.authorization_id,
            candidate_id=candidate.candidate_id.value,
            portfolio_disposition=portfolio.disposition.value,
            risk_disposition=risk.disposition.value,
            order_status=result.status,
        )
