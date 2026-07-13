from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Protocol

from fx_core import (
    Currency,
    CurrencyFundamentalFeature,
    DirectionScore,
    FactorScore,
    FeatureId,
    FundamentalFactor,
    LlmFeatureExtractor,
    NewsObservation,
    Probability,
    VersionMetadata,
)


class VersionedLlmFeatureExtractor(LlmFeatureExtractor, Protocol):
    versions: VersionMetadata


class StructuredFeatureProvider(Protocol):
    def extract(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


class RecordedFeatureProvider:
    def __init__(self, responses: Mapping[str, Mapping[str, Any]]) -> None:
        self._responses = {key: dict(value) for key, value in responses.items()}

    def extract(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        observation_id = str(payload["observation_id"])
        if observation_id not in self._responses:
            raise KeyError(f"No recorded Feature response for {observation_id}")
        return self._responses[observation_id]


class ProviderLlmFeatureExtractor:
    _forbidden_fields = {
        "action",
        "side",
        "quantity",
        "units",
        "leverage",
        "order",
        "order_action",
        "target_pair",
        "suggested_leverage",
        "buy",
        "sell",
        "hold",
        "exit",
    }

    def __init__(
        self,
        provider: StructuredFeatureProvider,
        *,
        producer_version: str,
        model_version: str,
        prompt_version: str,
        clock: Callable[[], datetime],
    ) -> None:
        self.versions = VersionMetadata(
            producer_version=producer_version,
            model_version=model_version,
            prompt_version=prompt_version,
        )
        self.versions.require_feature_versions()
        self._provider = provider
        self._clock = clock

    def extract(
        self,
        observation: NewsObservation,
        *,
        feature_id: FeatureId,
        currency: Currency,
    ) -> CurrencyFundamentalFeature:
        response = self._provider.extract(
            {
                "observation_id": observation.observation_id.value,
                "currency": currency.code,
                "title": observation.title,
                "body": observation.body,
                "first_seen_at": observation.first_seen_at.isoformat(),
            }
        )
        forbidden = self._forbidden_fields.intersection(key.lower() for key in response)
        if forbidden:
            raise ValueError(
                f"LLM Feature output contains forbidden action fields: {sorted(forbidden)}"
            )
        factors_raw = response.get("factor_scores")
        if not isinstance(factors_raw, Mapping) or not factors_raw:
            raise ValueError("factor_scores must be a non-empty object")
        factors = tuple(
            FactorScore(FundamentalFactor(str(name)), DirectionScore(float(value)))
            for name, value in sorted(factors_raw.items())
        )
        return CurrencyFundamentalFeature(
            feature_id=feature_id,
            observation_ids=(observation.observation_id,),
            currency=currency,
            event_type=FundamentalFactor(str(response["event_type"])),
            factor_scores=factors,
            impact_strength=Probability(float(response["impact_strength"])),
            confidence=Probability(float(response["confidence"])),
            versions=self.versions,
            created_at=self._clock(),
        )
