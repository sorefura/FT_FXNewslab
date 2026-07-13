from dataclasses import dataclass
from datetime import datetime

from .currency import Currency, CurrencyPair
from .ids import FeatureId, SignalId
from .time import Horizon, require_utc
from .values import DirectionScore, PairScore, Probability
from .versioning import VersionMetadata


@dataclass(frozen=True, slots=True)
class CurrencyTarget:
    currency: Currency


@dataclass(frozen=True, slots=True)
class PairTarget:
    pair: CurrencyPair


SignalTarget = CurrencyTarget | PairTarget
SignalDirection = DirectionScore | PairScore


@dataclass(frozen=True, slots=True)
class Signal:
    signal_id: SignalId
    target: SignalTarget
    signal_type: str
    direction: SignalDirection
    strength: Probability
    confidence: Probability
    horizon: Horizon
    observed_at: datetime
    created_at: datetime
    source_feature_ids: tuple[FeatureId, ...]
    versions: VersionMetadata

    def __post_init__(self) -> None:
        if not self.signal_type.strip():
            raise ValueError("signal_type must not be blank")
        if not self.source_feature_ids:
            raise ValueError("Signal requires at least one source Feature")
        if isinstance(self.target, CurrencyTarget) and not isinstance(
            self.direction, DirectionScore
        ):
            raise TypeError("Currency Signal requires DirectionScore")
        if isinstance(self.target, PairTarget) and not isinstance(self.direction, PairScore):
            raise TypeError("Pair Signal requires PairScore")
        self.versions.require_signal_versions()
        require_utc(self.observed_at, "signal.observed_at")
        require_utc(self.created_at, "signal.created_at")
        if self.created_at < self.observed_at:
            raise ValueError("Signal cannot be created before it was observed")
