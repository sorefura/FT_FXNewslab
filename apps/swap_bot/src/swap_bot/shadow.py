import hashlib
import json
import tempfile
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from fx_core import (
    Currency,
    CurrencyPair,
    CurrencyPairSignalTransformer,
    FeatureId,
    FundamentalSignalScorer,
    NewsObservation,
    ObservationId,
    Probability,
    SignalId,
)
from fx_signal_store import SQLiteSignalStore

from .decision_store import SQLiteLiveDecisionStore
from .execution import ExecutionService
from .idempotency import SQLiteIdempotencyStore
from .llm_feature import ProviderLlmFeatureExtractor, RecordedFeatureProvider
from .models import (
    AccountSnapshot,
    CandidateId,
    ExecutionIntentId,
    PortfolioDecisionId,
    PositionId,
    PositionSnapshot,
    RiskDecisionId,
    Side,
    TradeCandidate,
)
from .portfolio import PortfolioService
from .risk import RiskPolicy, RiskService


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed


def run_shadow_cycle(fixture: dict[str, Any], database: str | Path) -> dict[str, object]:
    database_path = Path(database)
    now = _utc(fixture["now"])
    observation_data = fixture["observation"]
    content = f"{observation_data['title']}\n{observation_data['body']}".encode()
    observation = NewsObservation(
        observation_id=ObservationId(observation_data["id"]),
        source=observation_data["source"],
        title=observation_data["title"],
        body=observation_data["body"],
        published_at=_utc(observation_data["published_at"]),
        first_seen_at=_utc(observation_data["first_seen_at"]),
        content_hash=hashlib.sha256(content).hexdigest(),
        payload_reference=observation_data["payload_reference"],
        normalizer_version=observation_data["normalizer_version"],
    )
    signal_store = SQLiteSignalStore(database_path)
    signal_store.append_observation(observation)

    scorer = FundamentalSignalScorer()
    currency_signals = {}
    for feature_data in fixture["features"]:
        extractor = ProviderLlmFeatureExtractor(
            RecordedFeatureProvider(feature_data["provider_response"]),
            producer_version=feature_data["producer_version"],
            model_version=feature_data["model_version"],
            prompt_version=feature_data["prompt_version"],
            clock=lambda: now,
        )
        currency = Currency(feature_data["currency"])
        feature = extractor.extract(
            observation,
            feature_id=FeatureId(feature_data["feature_id"]),
            currency=currency,
        )
        signal = scorer.score(
            feature,
            signal_id=SignalId(feature_data["signal_id"]),
            observed_at=observation.first_seen_at,
            created_at=now,
        )
        signal_store.append_feature(feature)
        signal_store.append_signal(signal)
        currency_signals[currency] = signal

    pair = CurrencyPair.parse(fixture["candidate"]["pair"])
    pair_signal = CurrencyPairSignalTransformer().transform(
        currency_signals[pair.base],
        currency_signals[pair.quote],
        pair=pair,
        signal_id=SignalId(fixture["pair_signal_id"]),
        created_at=now,
    )
    signal_store.append_signal(pair_signal)

    candidate_data = fixture["candidate"]
    candidate = TradeCandidate(
        candidate_id=CandidateId(candidate_data["id"]),
        strategy_id="shadow-fixture",
        strategy_version=candidate_data["strategy_version"],
        pair=pair,
        side=Side(candidate_data["side"]),
        score=Probability(float(candidate_data["score"])),
        signal_ids=(pair_signal.signal_id,),
        created_at=now,
    )
    positions = tuple(
        PositionSnapshot(
            position_id=PositionId(item["id"]),
            pair=CurrencyPair.parse(item["pair"]),
            side=Side(item["side"]),
            quantity=Decimal(item["quantity"]),
            current_price=Decimal(item["current_price"]),
            observed_at=now,
        )
        for item in fixture.get("positions", [])
    )
    limits = {
        Currency(code): Decimal(value)
        for code, value in fixture["portfolio"]["currency_limits"].items()
    }
    portfolio = PortfolioService(limits).evaluate(
        candidate,
        positions=positions,
        pending_intents=(),
        requested_quantity=Decimal(candidate_data["quantity"]),
        reference_price=Decimal(candidate_data["reference_price"]),
        decision_id=PortfolioDecisionId(fixture["portfolio"]["decision_id"]),
        created_at=now,
    )
    risk_service = RiskService(
        RiskPolicy(
            version=fixture["risk"]["policy_version"],
            minimum_margin_ratio=Decimal(fixture["risk"]["minimum_margin_ratio"]),
            maximum_positions_per_pair=int(fixture["risk"]["maximum_positions_per_pair"]),
            maximum_account_age=timedelta(seconds=int(fixture["risk"]["maximum_account_age_seconds"])),
        )
    )
    risk = risk_service.evaluate(
        portfolio,
        candidate,
        account=AccountSnapshot(
            margin_ratio=Decimal(fixture["risk"]["account_margin_ratio"]),
            observed_at=now,
        ),
        positions=positions,
        decision_id=RiskDecisionId(fixture["risk"]["decision_id"]),
        created_at=now,
    )
    intent = risk_service.create_execution_intent(
        risk,
        portfolio,
        candidate,
        intent_id=ExecutionIntentId(fixture["execution"]["intent_id"]),
        idempotency_key=fixture["execution"]["idempotency_key"],
        created_at=now,
    )
    result = ExecutionService(SQLiteIdempotencyStore(database_path)).submit(intent)

    decisions = SQLiteLiveDecisionStore(database_path)
    decisions.append_candidate(candidate)
    decisions.append_portfolio_decision(portfolio)
    decisions.append_risk_decision(risk)
    decisions.append_intent(intent)
    decisions.append_order_result(result)
    chain = decisions.decision_chain(candidate.candidate_id)
    lineage = signal_store.get_lineage(pair_signal.signal_id)
    return {
        "signal_id": pair_signal.signal_id.value,
        "feature_ids": [item.value for item in lineage.feature_ids],
        "observation_ids": [item.value for item in lineage.observation_ids],
        "candidate_id": candidate.candidate_id.value,
        "portfolio_disposition": portfolio.disposition.value,
        "risk_disposition": risk.disposition.value,
        "order_status": result.status.value,
        "broker_submit_calls": 0,
        "decision_chain_complete": all(
            chain[key] is not None for key in ("portfolio", "risk", "intent", "order_result")
        ),
    }


def run_fixture_file(
    fixture_path: str | Path, database: str | Path | None = None
) -> dict[str, object]:
    fixture = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    if database is not None:
        return run_shadow_cycle(fixture, database)
    with tempfile.TemporaryDirectory() as directory:
        return run_shadow_cycle(fixture, Path(directory) / "shadow.sqlite3")
