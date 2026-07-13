from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fx_core import Currency, CurrencyPair, Probability, SignalId
from swap_bot.models import (
    AccountSnapshot,
    CandidateId,
    ExecutionIntentId,
    PortfolioDecisionId,
    PortfolioDisposition,
    PositionId,
    PositionSnapshot,
    RiskDecisionId,
    RiskDisposition,
    Side,
    TradeCandidate,
)
from swap_bot.portfolio import PortfolioService
from swap_bot.risk import RiskPolicy, RiskService

NOW = datetime(2026, 7, 13, tzinfo=UTC)


def _candidate() -> TradeCandidate:
    return TradeCandidate(
        candidate_id=CandidateId("candidate-1"),
        strategy_id="fixture",
        strategy_version="fixture-v1",
        pair=CurrencyPair.parse("USD_JPY"),
        side=Side.BUY,
        score=Probability(0.8),
        signal_ids=(SignalId("signal-1"),),
        created_at=NOW,
    )


def _portfolio():
    return PortfolioService({Currency("JPY"): Decimal("1000000")}).evaluate(
        _candidate(),
        positions=(),
        pending_intents=(),
        requested_quantity=Decimal("1000"),
        reference_price=Decimal("150"),
        decision_id=PortfolioDecisionId("portfolio-1"),
        created_at=NOW,
    )


def _risk() -> RiskService:
    return RiskService(RiskPolicy("risk-v1", Decimal("1.0"), 1, timedelta(minutes=1)))


def test_risk_rejects_stale_account_data_with_structured_reason() -> None:
    decision = _risk().evaluate(
        _portfolio(),
        _candidate(),
        account=AccountSnapshot(Decimal("2.0"), NOW - timedelta(minutes=2)),
        positions=(),
        decision_id=RiskDecisionId("risk-1"),
        created_at=NOW,
    )
    assert decision.disposition is RiskDisposition.REJECT
    assert decision.reason_code == "stale_account_data"


def test_risk_rejects_existing_same_pair_position() -> None:
    position = PositionSnapshot(
        PositionId("position-1"),
        CurrencyPair.parse("USD_JPY"),
        Side.BUY,
        Decimal("1000"),
        Decimal("150"),
        NOW,
    )
    decision = _risk().evaluate(
        _portfolio(),
        _candidate(),
        account=AccountSnapshot(Decimal("2.0"), NOW),
        positions=(position,),
        decision_id=RiskDecisionId("risk-1"),
        created_at=NOW,
    )
    assert decision.reason_code == "max_positions_per_pair"


def test_rejected_risk_decision_cannot_create_execution_intent() -> None:
    portfolio = _portfolio()
    decision = _risk().evaluate(
        portfolio,
        _candidate(),
        account=AccountSnapshot(Decimal("0.5"), NOW),
        positions=(),
        decision_id=RiskDecisionId("risk-1"),
        created_at=NOW,
    )
    with pytest.raises(ValueError):
        _risk().create_execution_intent(
            decision,
            portfolio,
            _candidate(),
            intent_id=ExecutionIntentId("intent-1"),
            idempotency_key="key-1",
            created_at=NOW,
        )


def test_margin_kill_switch_creates_liquidation_intent_without_broker_dependency() -> None:
    position = PositionSnapshot(
        PositionId("position-1"),
        CurrencyPair.parse("USD_JPY"),
        Side.BUY,
        Decimal("1000"),
        Decimal("150"),
        NOW,
    )
    decision = _risk().evaluate(
        _portfolio(),
        _candidate(),
        account=AccountSnapshot(Decimal("0.5"), NOW),
        positions=(position,),
        decision_id=RiskDecisionId("risk-1"),
        created_at=NOW,
    )
    intents = _risk().create_liquidation_intents(
        decision, (position,), intent_id_prefix="liquidate", created_at=NOW
    )
    assert intents[0].position_id == position.position_id
    assert _portfolio().disposition is PortfolioDisposition.ACCEPT

