import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fx_core import (
    Currency,
    CurrencyFundamentalFeature,
    CurrencyTarget,
    DirectionScore,
    FactorScore,
    FeatureId,
    FundamentalFactor,
    Horizon,
    NewsObservation,
    ObservationId,
    Probability,
    Signal,
    SignalId,
    VersionMetadata,
)
from fx_signal_store import SQLiteSignalStore


def test_research_contract_reads_versioned_signal_and_complete_lineage(tmp_path: Path) -> None:
    now = datetime(2026, 7, 13, tzinfo=UTC)
    path = tmp_path / "research.sqlite3"
    store = SQLiteSignalStore(path)
    observation = NewsObservation(
        observation_id=ObservationId("research-observation"),
        source="contract-fixture",
        title="Recorded event",
        body="Recorded body",
        published_at=now,
        first_seen_at=now,
        content_hash=hashlib.sha256(b"Recorded event\nRecorded body").hexdigest(),
        payload_reference="fixture://research-contract",
        normalizer_version="normalizer-v1",
    )
    feature = CurrencyFundamentalFeature(
        feature_id=FeatureId("research-feature"),
        observation_ids=(observation.observation_id,),
        currency=Currency("USD"),
        event_type=FundamentalFactor.INFLATION,
        factor_scores=(FactorScore(FundamentalFactor.INFLATION, DirectionScore(0.4)),),
        impact_strength=Probability(0.6),
        confidence=Probability(0.8),
        versions=VersionMetadata(
            producer_version="producer-v1",
            model_version="model-v1",
            prompt_version="prompt-v1",
        ),
        created_at=now,
    )
    signal = Signal(
        signal_id=SignalId("research-signal"),
        target=CurrencyTarget(Currency("USD")),
        signal_type="currency_fundamental",
        direction=DirectionScore(0.4),
        strength=Probability(0.6),
        confidence=Probability(0.8),
        horizon=Horizon.DAYS_3,
        observed_at=now,
        created_at=now,
        source_feature_ids=(feature.feature_id,),
        versions=VersionMetadata(scorer_version="scorer-v1"),
    )
    store.append_observation(observation)
    store.append_feature(feature)
    store.append_signal(signal)

    read = store.list_signals(
        target="USD", horizon=Horizon.DAYS_3, scorer_version="scorer-v1"
    )
    lineage = store.get_lineage(signal.signal_id)

    assert read == (signal,)
    assert lineage.feature_ids == (feature.feature_id,)
    assert lineage.observation_ids == (observation.observation_id,)
    with sqlite3.connect(path) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute("DELETE FROM features WHERE id = 'research-feature'")
