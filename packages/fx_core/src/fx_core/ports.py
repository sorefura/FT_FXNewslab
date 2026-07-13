from typing import Protocol

from .currency import Currency
from .feature import CurrencyFundamentalFeature
from .ids import FeatureId
from .observation import NewsObservation


class LlmFeatureExtractor(Protocol):
    def extract(
        self,
        observation: NewsObservation,
        *,
        feature_id: FeatureId,
        currency: Currency,
    ) -> CurrencyFundamentalFeature: ...

