from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .currency import Currency
from .ids import FeatureId, ObservationId
from .time import require_utc
from .values import DirectionScore, Probability
from .versioning import VersionMetadata


class FundamentalFactor(Enum):
    MONETARY_POLICY = "monetary_policy"
    INFLATION = "inflation"
    GROWTH = "growth"
    EMPLOYMENT = "employment"
    GEOPOLITICAL_RISK = "geopolitical_risk"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class FactorScore:
    factor: FundamentalFactor
    direction: DirectionScore


@dataclass(frozen=True, slots=True)
class CurrencyFundamentalFeature:
    feature_id: FeatureId
    observation_ids: tuple[ObservationId, ...]
    currency: Currency
    event_type: FundamentalFactor
    factor_scores: tuple[FactorScore, ...]
    impact_strength: Probability
    confidence: Probability
    versions: VersionMetadata
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.observation_ids:
            raise ValueError("Feature requires at least one source Observation")
        if not self.factor_scores:
            raise ValueError("Feature requires at least one factor score")
        factors = [item.factor for item in self.factor_scores]
        if len(factors) != len(set(factors)):
            raise ValueError("Feature factor scores must be unique")
        self.versions.require_feature_versions()
        require_utc(self.created_at, "feature.created_at")

