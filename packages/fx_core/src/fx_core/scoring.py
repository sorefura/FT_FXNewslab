from datetime import datetime

from .currency import CurrencyPair
from .feature import CurrencyFundamentalFeature, FundamentalFactor
from .ids import SignalId
from .signal import CurrencyTarget, PairTarget, Signal
from .time import Horizon
from .values import DirectionScore, PairScore, Probability
from .versioning import VersionMetadata


class FundamentalSignalScorer:
    def __init__(self, scorer_version: str = "fundamental-scorer-v1") -> None:
        if not scorer_version.strip():
            raise ValueError("scorer_version must not be blank")
        self.scorer_version = scorer_version

    def score(
        self,
        feature: CurrencyFundamentalFeature,
        *,
        signal_id: SignalId,
        observed_at: datetime,
        created_at: datetime,
    ) -> Signal:
        direction = sum(item.direction.value for item in feature.factor_scores) / len(
            feature.factor_scores
        )
        horizon = (
            Horizon.DAY_1
            if feature.event_type in {FundamentalFactor.GEOPOLITICAL_RISK, FundamentalFactor.OTHER}
            else Horizon.DAYS_3
        )
        return Signal(
            signal_id=signal_id,
            target=CurrencyTarget(feature.currency),
            signal_type="currency_fundamental",
            direction=DirectionScore(direction),
            strength=feature.impact_strength,
            confidence=feature.confidence,
            horizon=horizon,
            observed_at=observed_at,
            created_at=created_at,
            source_feature_ids=(feature.feature_id,),
            versions=VersionMetadata(
                producer_version=feature.versions.producer_version,
                model_version=feature.versions.model_version,
                prompt_version=feature.versions.prompt_version,
                scorer_version=self.scorer_version,
            ),
        )


class CurrencyPairSignalTransformer:
    def __init__(self, transformation_version: str = "currency-pair-v1") -> None:
        if not transformation_version.strip():
            raise ValueError("transformation_version must not be blank")
        self.transformation_version = transformation_version

    def transform(
        self,
        base_signal: Signal,
        quote_signal: Signal,
        *,
        pair: CurrencyPair,
        signal_id: SignalId,
        created_at: datetime,
    ) -> Signal:
        if not isinstance(base_signal.target, CurrencyTarget) or not isinstance(
            quote_signal.target, CurrencyTarget
        ):
            raise TypeError("Pair transformation requires Currency Signals")
        if base_signal.target.currency != pair.base or quote_signal.target.currency != pair.quote:
            raise ValueError("Currency Signals do not match the requested pair")
        if base_signal.horizon != quote_signal.horizon:
            raise ValueError("Pair transformation requires matching Horizons")
        if not isinstance(base_signal.direction, DirectionScore) or not isinstance(
            quote_signal.direction, DirectionScore
        ):
            raise TypeError("Currency Signals require DirectionScore")
        source_ids = tuple(
            dict.fromkeys(base_signal.source_feature_ids + quote_signal.source_feature_ids)
        )
        versions = VersionMetadata(
            producer_version=self._combine_versions(
                base_signal.versions.producer_version, quote_signal.versions.producer_version
            ),
            model_version=self._combine_versions(
                base_signal.versions.model_version, quote_signal.versions.model_version
            ),
            prompt_version=self._combine_versions(
                base_signal.versions.prompt_version, quote_signal.versions.prompt_version
            ),
            scorer_version=self._combine_versions(
                base_signal.versions.scorer_version, quote_signal.versions.scorer_version
            ),
            transformation_version=self.transformation_version,
        )
        return Signal(
            signal_id=signal_id,
            target=PairTarget(pair),
            signal_type="pair_fundamental",
            direction=PairScore(base_signal.direction.value - quote_signal.direction.value),
            strength=Probability((base_signal.strength.value + quote_signal.strength.value) / 2),
            confidence=Probability(
                min(base_signal.confidence.value, quote_signal.confidence.value)
            ),
            horizon=base_signal.horizon,
            observed_at=max(base_signal.observed_at, quote_signal.observed_at),
            created_at=created_at,
            source_feature_ids=source_ids,
            versions=versions,
        )

    @staticmethod
    def _combine_versions(base: str | None, quote: str | None) -> str | None:
        if base == quote:
            return base
        return f"base={base or 'none'}|quote={quote or 'none'}"
