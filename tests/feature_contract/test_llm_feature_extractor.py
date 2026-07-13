from datetime import UTC

import pytest
from fx_core import Currency, FeatureId
from swap_bot.llm_feature import ProviderLlmFeatureExtractor, RecordedFeatureProvider

from tests.factories import NOW, observation


def _extractor(response: dict[str, object]) -> ProviderLlmFeatureExtractor:
    return ProviderLlmFeatureExtractor(
        RecordedFeatureProvider(response),
        producer_version="producer-v1",
        model_version="model-v1",
        prompt_version="prompt-v1",
        clock=lambda: NOW.astimezone(UTC),
    )


def test_llm_feature_output_contains_measurements_and_versions() -> None:
    result = _extractor(
        {
            "event_type": "inflation",
            "factor_scores": {"inflation": 0.4},
            "impact_strength": 0.6,
            "confidence": 0.8,
        }
    ).extract(observation(), feature_id=FeatureId("feature-1"), currency=Currency("USD"))
    assert result.currency == Currency("USD")
    assert result.versions.model_version == "model-v1"
    assert result.observation_ids == (observation().observation_id,)


@pytest.mark.parametrize(
    "field",
    ["action", "side", "quantity", "leverage", "order", "order_action", "target_pair"],
)
def test_llm_feature_output_rejects_order_and_action_fields(field: str) -> None:
    response = {
        "event_type": "inflation",
        "factor_scores": {"inflation": 0.4},
        "impact_strength": 0.6,
        "confidence": 0.8,
        field: "BUY",
    }
    with pytest.raises(ValueError, match="forbidden action fields"):
        _extractor(response).extract(
            observation(), feature_id=FeatureId("feature-1"), currency=Currency("USD")
        )
