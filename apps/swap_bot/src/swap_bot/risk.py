from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from .models import (
    AccountSnapshot,
    ApprovedExecutionIntent,
    ApprovedLiquidationIntent,
    ExecutionIntentId,
    PortfolioDecision,
    PortfolioDisposition,
    PositionSnapshot,
    RiskDecision,
    RiskDecisionId,
    RiskDisposition,
    TradeCandidate,
)


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    version: str
    minimum_margin_ratio: Decimal
    maximum_positions_per_pair: int
    maximum_account_age: timedelta

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("Risk policy version is required")
        if self.minimum_margin_ratio < 0 or self.maximum_positions_per_pair < 1:
            raise ValueError("Risk limits are invalid")
        if self.maximum_account_age <= timedelta(0):
            raise ValueError("maximum_account_age must be positive")


class RiskService:
    def __init__(self, policy: RiskPolicy) -> None:
        self._policy = policy

    def evaluate(
        self,
        portfolio_decision: PortfolioDecision,
        candidate: TradeCandidate,
        *,
        account: AccountSnapshot,
        positions: Sequence[PositionSnapshot],
        decision_id: RiskDecisionId,
        created_at: datetime,
    ) -> RiskDecision:
        self._require_candidate_chain(portfolio_decision, candidate)
        reason = "approved"
        disposition = RiskDisposition.APPROVE
        if portfolio_decision.disposition is PortfolioDisposition.REJECT:
            disposition = RiskDisposition.REJECT
            reason = "portfolio_rejected"
        elif created_at - account.observed_at > self._policy.maximum_account_age:
            disposition = RiskDisposition.REJECT
            reason = "stale_account_data"
        elif account.margin_ratio < self._policy.minimum_margin_ratio:
            disposition = RiskDisposition.REJECT
            reason = "margin_kill_switch"
        elif (
            sum(1 for position in positions if position.pair == candidate.pair)
            >= self._policy.maximum_positions_per_pair
        ):
            disposition = RiskDisposition.REJECT
            reason = "max_positions_per_pair"
        return RiskDecision(
            decision_id=decision_id,
            portfolio_decision_id=portfolio_decision.decision_id,
            disposition=disposition,
            reason_code=reason,
            risk_policy_version=self._policy.version,
            created_at=created_at,
        )

    def create_execution_intent(
        self,
        risk_decision: RiskDecision,
        portfolio_decision: PortfolioDecision,
        candidate: TradeCandidate,
        *,
        intent_id: ExecutionIntentId,
        idempotency_key: str,
        created_at: datetime,
    ) -> ApprovedExecutionIntent:
        self._require_decision_chain(risk_decision, portfolio_decision, candidate)
        if risk_decision.disposition is not RiskDisposition.APPROVE:
            raise ValueError("Rejected RiskDecision cannot create an ExecutionIntent")
        if portfolio_decision.proposed_quantity is None:
            raise ValueError("PortfolioDecision has no approved quantity")
        return ApprovedExecutionIntent(
            intent_id=intent_id,
            candidate_id=candidate.candidate_id,
            risk_decision_id=risk_decision.decision_id,
            pair=candidate.pair,
            side=candidate.side,
            quantity=portfolio_decision.proposed_quantity,
            idempotency_key=idempotency_key,
            created_at=created_at,
        )

    def create_liquidation_intents(
        self,
        risk_decision: RiskDecision,
        positions: Sequence[PositionSnapshot],
        *,
        intent_id_prefix: str,
        created_at: datetime,
    ) -> tuple[ApprovedLiquidationIntent, ...]:
        if risk_decision.reason_code != "margin_kill_switch":
            raise ValueError("Liquidation requires a margin kill-switch decision")
        return tuple(
            ApprovedLiquidationIntent(
                intent_id=ExecutionIntentId(f"{intent_id_prefix}:{index}"),
                risk_decision_id=risk_decision.decision_id,
                position_id=position.position_id,
                pair=position.pair,
                quantity=position.quantity,
                idempotency_key=f"liquidate:{risk_decision.decision_id.value}:{position.position_id.value}",
                created_at=created_at,
            )
            for index, position in enumerate(positions)
        )

    @staticmethod
    def _require_candidate_chain(
        portfolio_decision: PortfolioDecision, candidate: TradeCandidate
    ) -> None:
        if portfolio_decision.candidate_id != candidate.candidate_id:
            raise ValueError("PortfolioDecision does not belong to TradeCandidate")

    @classmethod
    def _require_decision_chain(
        cls,
        risk_decision: RiskDecision,
        portfolio_decision: PortfolioDecision,
        candidate: TradeCandidate,
    ) -> None:
        if risk_decision.portfolio_decision_id != portfolio_decision.decision_id:
            raise ValueError("RiskDecision does not belong to PortfolioDecision")
        cls._require_candidate_chain(portfolio_decision, candidate)
