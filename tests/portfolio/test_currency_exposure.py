from datetime import UTC, datetime
from decimal import Decimal

from fx_core import Currency, CurrencyPair, Probability, SignalId
from swap_bot.models import (
    CandidateId,
    PendingIntent,
    PortfolioDecisionId,
    PortfolioDisposition,
    PositionId,
    PositionSnapshot,
    Side,
    TradeCandidate,
)
from swap_bot.portfolio import CurrencyExposureCalculator, PortfolioService

NOW = datetime(2026, 7, 13, tzinfo=UTC)


def _position(identifier: str, pair: str, price: str) -> PositionSnapshot:
    return PositionSnapshot(
        position_id=PositionId(identifier),
        pair=CurrencyPair.parse(pair),
        side=Side.BUY,
        quantity=Decimal("1000"),
        current_price=Decimal(price),
        observed_at=NOW,
    )


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


def test_exposure_aggregates_jpy_short_across_distinct_pairs_and_pending_intents() -> None:
    positions = (
        _position("usd", "USD_JPY", "150"),
        _position("eur", "EUR_JPY", "165"),
    )
    pending = (
        PendingIntent(CurrencyPair.parse("GBP_JPY"), Side.BUY, Decimal("1000"), Decimal("190")),
    )
    snapshot = CurrencyExposureCalculator().calculate(positions, pending, created_at=NOW)
    assert snapshot.amount_for(Currency("JPY")) == Decimal("-505000")


def test_portfolio_reduces_candidate_when_currency_limit_would_be_exceeded() -> None:
    service = PortfolioService({Currency("JPY"): Decimal("100000")})
    decision = service.evaluate(
        _candidate(),
        positions=(),
        pending_intents=(),
        requested_quantity=Decimal("1000"),
        reference_price=Decimal("150"),
        decision_id=PortfolioDecisionId("portfolio-1"),
        created_at=NOW,
    )
    assert decision.disposition is PortfolioDisposition.REDUCE
    assert decision.proposed_quantity == Decimal("666.6666666666666666666666667")

