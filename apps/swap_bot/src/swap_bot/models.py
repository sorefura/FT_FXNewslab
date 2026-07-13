from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from fx_core import Currency, CurrencyPair, Probability, SignalId
from fx_core.time import require_utc


@dataclass(frozen=True, slots=True)
class _LiveId:
    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError(f"{type(self).__name__} must not be empty")


@dataclass(frozen=True, slots=True)
class CandidateId(_LiveId):
    pass


@dataclass(frozen=True, slots=True)
class PortfolioDecisionId(_LiveId):
    pass


@dataclass(frozen=True, slots=True)
class RiskDecisionId(_LiveId):
    pass


@dataclass(frozen=True, slots=True)
class ExecutionIntentId(_LiveId):
    pass


@dataclass(frozen=True, slots=True)
class OrderResultId(_LiveId):
    pass


@dataclass(frozen=True, slots=True)
class PositionId(_LiveId):
    pass


class Side(Enum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> Decimal:
        return Decimal("1") if self is Side.BUY else Decimal("-1")


class PortfolioDisposition(Enum):
    ACCEPT = "ACCEPT"
    REDUCE = "REDUCE"
    REJECT = "REJECT"


class RiskDisposition(Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class OrderStatus(Enum):
    NOT_SUBMITTED = "NOT_SUBMITTED"
    DUPLICATE = "DUPLICATE"
    SUBMITTED = "SUBMITTED"
    PARTIAL_FILL = "PARTIAL_FILL"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class TradeCandidate:
    candidate_id: CandidateId
    strategy_id: str
    strategy_version: str
    pair: CurrencyPair
    side: Side
    score: Probability
    signal_ids: tuple[SignalId, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.strategy_id.strip() or not self.strategy_version.strip():
            raise ValueError("Strategy identity and version are required")
        if not self.signal_ids:
            raise ValueError("TradeCandidate requires contributing Signal IDs")
        require_utc(self.created_at, "candidate.created_at")


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    position_id: PositionId
    pair: CurrencyPair
    side: Side
    quantity: Decimal
    current_price: Decimal
    observed_at: datetime

    def __post_init__(self) -> None:
        if self.quantity <= 0 or self.current_price <= 0:
            raise ValueError("Position quantity and price must be positive")
        require_utc(self.observed_at, "position.observed_at")


@dataclass(frozen=True, slots=True)
class PendingIntent:
    pair: CurrencyPair
    side: Side
    quantity: Decimal
    reference_price: Decimal

    def __post_init__(self) -> None:
        if self.quantity <= 0 or self.reference_price <= 0:
            raise ValueError("Pending quantity and reference price must be positive")


@dataclass(frozen=True, slots=True)
class CurrencyExposure:
    currency: Currency
    amount: Decimal


@dataclass(frozen=True, slots=True)
class CurrencyExposureSnapshot:
    exposures: tuple[CurrencyExposure, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        require_utc(self.created_at, "exposure.created_at")

    def amount_for(self, currency: Currency) -> Decimal:
        return next(
            (item.amount for item in self.exposures if item.currency == currency), Decimal(0)
        )


@dataclass(frozen=True, slots=True)
class PortfolioDecision:
    decision_id: PortfolioDecisionId
    candidate_id: CandidateId
    disposition: PortfolioDisposition
    proposed_quantity: Decimal | None
    reason_code: str
    exposure_snapshot: CurrencyExposureSnapshot
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.reason_code.strip():
            raise ValueError("Portfolio reason_code is required")
        if self.disposition is PortfolioDisposition.REJECT and self.proposed_quantity is not None:
            raise ValueError("Rejected PortfolioDecision cannot propose quantity")
        if self.disposition is not PortfolioDisposition.REJECT and (
            self.proposed_quantity is None or self.proposed_quantity <= 0
        ):
            raise ValueError("Accepted or reduced PortfolioDecision requires positive quantity")
        require_utc(self.created_at, "portfolio_decision.created_at")


@dataclass(frozen=True, slots=True)
class RiskDecision:
    decision_id: RiskDecisionId
    portfolio_decision_id: PortfolioDecisionId
    disposition: RiskDisposition
    reason_code: str
    risk_policy_version: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.reason_code.strip() or not self.risk_policy_version.strip():
            raise ValueError("Risk reason and policy version are required")
        require_utc(self.created_at, "risk_decision.created_at")


@dataclass(frozen=True, slots=True)
class ApprovedExecutionIntent:
    intent_id: ExecutionIntentId
    candidate_id: CandidateId
    risk_decision_id: RiskDecisionId
    pair: CurrencyPair
    side: Side
    quantity: Decimal
    idempotency_key: str
    created_at: datetime

    def __post_init__(self) -> None:
        if self.quantity <= 0 or not self.idempotency_key.strip():
            raise ValueError("Execution intent requires quantity and idempotency key")
        require_utc(self.created_at, "execution_intent.created_at")


@dataclass(frozen=True, slots=True)
class ApprovedLiquidationIntent:
    intent_id: ExecutionIntentId
    risk_decision_id: RiskDecisionId
    position_id: PositionId
    pair: CurrencyPair
    quantity: Decimal
    idempotency_key: str
    created_at: datetime

    def __post_init__(self) -> None:
        if self.quantity <= 0 or not self.idempotency_key.strip():
            raise ValueError("Liquidation intent requires quantity and idempotency key")
        require_utc(self.created_at, "liquidation_intent.created_at")


@dataclass(frozen=True, slots=True)
class OrderResult:
    result_id: OrderResultId
    execution_intent_id: ExecutionIntentId
    status: OrderStatus
    filled_quantity: Decimal
    broker_order_id: str | None
    error_code: str | None
    completed_at: datetime

    def __post_init__(self) -> None:
        if self.filled_quantity < 0:
            raise ValueError("filled_quantity cannot be negative")
        require_utc(self.completed_at, "order_result.completed_at")


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    margin_ratio: Decimal
    observed_at: datetime

    def __post_init__(self) -> None:
        if self.margin_ratio < 0:
            raise ValueError("margin_ratio cannot be negative")
        require_utc(self.observed_at, "account.observed_at")
