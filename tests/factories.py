import hashlib
from datetime import UTC, datetime

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

NOW = datetime(2026, 7, 13, tzinfo=UTC)


def observation(identifier: str = "obs-1") -> NewsObservation:
    body = "Recorded policy statement"
    return NewsObservation(
        observation_id=ObservationId(identifier),
        source="fixture",
        title="Policy statement",
        body=body,
        published_at=NOW,
        first_seen_at=NOW,
        content_hash=hashlib.sha256(f"Policy statement\n{body}".encode()).hexdigest(),
        payload_reference=f"fixture://{identifier}",
        normalizer_version="normalizer-v1",
    )


def feature(
    identifier: str = "feature-1", observation_id: str = "obs-1"
) -> CurrencyFundamentalFeature:
    return CurrencyFundamentalFeature(
        feature_id=FeatureId(identifier),
        observation_ids=(ObservationId(observation_id),),
        currency=Currency("USD"),
        event_type=FundamentalFactor.MONETARY_POLICY,
        factor_scores=(
            FactorScore(FundamentalFactor.MONETARY_POLICY, DirectionScore(0.6)),
        ),
        impact_strength=Probability(0.7),
        confidence=Probability(0.8),
        versions=VersionMetadata(
            producer_version="producer-v1",
            model_version="model-v1",
            prompt_version="prompt-v1",
        ),
        created_at=NOW,
    )


def signal(identifier: str = "signal-1", feature_id: str = "feature-1") -> Signal:
    return Signal(
        signal_id=SignalId(identifier),
        target=CurrencyTarget(Currency("USD")),
        signal_type="currency_fundamental",
        direction=DirectionScore(0.6),
        strength=Probability(0.7),
        confidence=Probability(0.8),
        horizon=Horizon.DAYS_3,
        observed_at=NOW,
        created_at=NOW,
        source_feature_ids=(FeatureId(feature_id),),
        versions=VersionMetadata(
            producer_version="producer-v1",
            model_version="model-v1",
            prompt_version="prompt-v1",
            scorer_version="scorer-v1",
        ),
    )
