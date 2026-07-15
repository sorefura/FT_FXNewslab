from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fx_core import Currency, CurrencyPair, Probability
from swap_bot.adoption import (
    AdoptionFailureReason,
    AdoptionMode,
    AdoptionRejected,
    AuthorizedSignal,
    RuntimeMode,
    revocation_decision,
)
from swap_bot.adoption_application import ApproveSignalAdoptionOnceService
from swap_bot.adoption_gate import LiveAdoptionGate
from swap_bot.adoption_store import SQLiteAdoptionStore
from swap_bot.authorized_shadow import AuthorizedShadowCycleService
from swap_bot.decision_store import SQLiteLiveDecisionStore
from swap_bot.execution import ExecutionService
from swap_bot.idempotency import SQLiteIdempotencyStore
from swap_bot.models import (
    AccountSnapshot,
    CandidateId,
    ExecutionIntentId,
    OrderResult,
    OrderStatus,
    PortfolioDecisionId,
    RiskDecisionId,
    Side,
    TradeCandidate,
)
from swap_bot.portfolio import PortfolioService
from swap_bot.research_evidence import SQLiteResearchValidationEvidenceSource
from swap_bot.risk import RiskPolicy, RiskService

from tests.adoption_factories import (
    NOW,
    adoptable_signal,
    adoption_policy,
    seed_research_evidence,
)


class RecordingStrategy:
    def __init__(self) -> None:
        self.calls = 0
        self.received: tuple[AuthorizedSignal, ...] = ()

    def evaluate(
        self, signals: tuple[AuthorizedSignal, ...]
    ) -> TradeCandidate | None:
        self.calls += 1
        self.received = signals
        signal = signals[0]
        return TradeCandidate(
            candidate_id=CandidateId(
                f"candidate-authorized-{signal.signal.signal_id.value}"
            ),
            strategy_id=signal.authorization.strategy_id,
            strategy_version=signal.authorization.strategy_version,
            pair=CurrencyPair.parse("USD_JPY"),
            side=Side.BUY,
            score=Probability(0.7),
            signal_ids=(signal.signal.signal_id,),
            created_at=signal.authorization.authorized_at,
        )


class CountingBrokerGateway:
    def __init__(self) -> None:
        self.submit_calls = 0

    def submit(self, intent) -> OrderResult:  # type: ignore[no-untyped-def]
        self.submit_calls += 1
        raise AssertionError(f"shadow Execution attempted Broker submit: {intent}")


def _approved_cycle(
    tmp_path: Path,
):  # type: ignore[no-untyped-def]
    research = tmp_path / "research.sqlite3"
    live = tmp_path / "live.sqlite3"
    seed_research_evidence(research)
    adoption_store = SQLiteAdoptionStore(live)
    approval = ApproveSignalAdoptionOnceService(
        SQLiteResearchValidationEvidenceSource(research), clock=lambda: NOW
    ).run(
        assessment_id="assessment-validated-1",
        policy=adoption_policy(adoption_mode=AdoptionMode.LIVE_ELIGIBLE),
        approved_by="reviewer@example.com",
        reason="shadow adoption proof",
        apply=True,
        store=adoption_store,
    )
    strategy = RecordingStrategy()
    broker = CountingBrokerGateway()
    decisions = SQLiteLiveDecisionStore(live)
    service = AuthorizedShadowCycleService(
        adoption_gate=LiveAdoptionGate(adoption_store),
        strategy=strategy,
        portfolio=PortfolioService(
            {Currency("USD"): Decimal("1000000"), Currency("JPY"): Decimal("200000000")}
        ),
        risk=RiskService(
            RiskPolicy(
                version="risk-shadow-v1",
                minimum_margin_ratio=Decimal("1.2"),
                maximum_positions_per_pair=3,
                maximum_account_age=timedelta(minutes=1),
            )
        ),
        execution=ExecutionService(SQLiteIdempotencyStore(live), broker),
        decisions=decisions,
    )
    return service, strategy, broker, adoption_store, decisions, approval


def _run(service: AuthorizedShadowCycleService, signal_id: str = "signal-adopted-1"):
    return service.run(  # type: ignore[no-any-return]
        adoptable_signal(signal_id),
        strategy_id="validated-signal-shadow",
        strategy_version="strategy-v1",
        strategy_config_identity=None,
        runtime_mode=RuntimeMode.SHADOW,
        positions=(),
        pending_intents=(),
        account=AccountSnapshot(margin_ratio=Decimal("2.0"), observed_at=NOW),
        requested_quantity=Decimal("1000"),
        reference_price=Decimal("150"),
        portfolio_decision_id=PortfolioDecisionId(f"portfolio-{signal_id}"),
        risk_decision_id=RiskDecisionId(f"risk-{signal_id}"),
        execution_intent_id=ExecutionIntentId(f"intent-{signal_id}"),
        idempotency_key=f"shadow:{signal_id}",
        cycle_at=NOW,
    )


def test_explicit_adoption_reaches_full_shadow_chain_without_broker_submit(
    tmp_path: Path,
) -> None:
    service, strategy, broker, _, decisions, _ = _approved_cycle(tmp_path)

    result = _run(service)

    assert strategy.calls == 1
    assert strategy.received[0].signal.signal_id.value == "signal-adopted-1"
    assert result.portfolio_disposition == "ACCEPT"
    assert result.risk_disposition == "APPROVE"
    assert result.order_status is OrderStatus.NOT_SUBMITTED
    assert broker.submit_calls == 0
    chain = decisions.decision_chain(CandidateId(result.candidate_id))
    assert chain["order_result"]["status"] == "NOT_SUBMITTED"  # type: ignore[index]
    assert decisions.candidate_authorization_lineage(CandidateId(result.candidate_id))[0][
        "authorization_id"
    ] == result.authorization_id


def test_validated_research_evidence_without_live_adoption_creates_no_candidate(
    tmp_path: Path,
) -> None:
    live = tmp_path / "live.sqlite3"
    strategy = RecordingStrategy()
    broker = CountingBrokerGateway()
    decisions = SQLiteLiveDecisionStore(live)
    service = AuthorizedShadowCycleService(
        adoption_gate=LiveAdoptionGate(SQLiteAdoptionStore(live)),
        strategy=strategy,
        portfolio=PortfolioService({}),
        risk=RiskService(
            RiskPolicy("risk-v1", Decimal("1"), 1, timedelta(minutes=1))
        ),
        execution=ExecutionService(SQLiteIdempotencyStore(live), broker),
        decisions=decisions,
    )

    with pytest.raises(AdoptionRejected) as rejected:
        _run(service)

    assert rejected.value.reason is AdoptionFailureReason.NO_ACTIVE_ADOPTION
    assert strategy.calls == 0
    assert broker.submit_calls == 0


def test_revocation_stops_the_next_cycle_before_strategy(tmp_path: Path) -> None:
    service, strategy, broker, adoption_store, _, approval_result = _approved_cycle(
        tmp_path
    )
    first = _run(service)
    approval = adoption_store.get_decision(approval_result.adoption_decision_id)
    adoption_store.append_revocation(
        revocation_decision(
            approval,
            decided_at=NOW,
            actor="reviewer@example.com",
            reason="stop adoption",
        )
    )

    with pytest.raises(AdoptionRejected) as rejected:
        _run(service, "signal-adopted-2")

    assert first.order_status is OrderStatus.NOT_SUBMITTED
    assert rejected.value.reason is AdoptionFailureReason.ADOPTION_REVOKED
    assert strategy.calls == 1
    assert broker.submit_calls == 0
