import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest
from fx_core import Currency, FeatureId, NewsObservation, ObservationId
from fx_research.errors import FeatureProductionError
from fx_research.feature_production import ProviderLlmFeatureExtractor
from fx_research.infrastructure.openai import OpenAIStructuredFeatureProvider

NOW = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)


class FakeOpenAITransport:
    def __init__(self, response: Mapping[str, Any]) -> None:
        self.response = response
        self.requests: list[Mapping[str, Any]] = []
        self.timeouts: list[float] = []

    def create(
        self, request: Mapping[str, Any], *, timeout_seconds: float
    ) -> Mapping[str, Any]:
        self.requests.append(request)
        self.timeouts.append(timeout_seconds)
        return self.response


def _response(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps(payload)}],
            }
        ]
    }


def _observation() -> NewsObservation:
    return NewsObservation(
        observation_id=ObservationId("obs-openai-contract"),
        source="fed.press_monetary.rss",
        title="Federal Reserve policy statement",
        body="The Committee maintained its target range.",
        published_at=NOW,
        first_seen_at=NOW,
        content_hash="a" * 64,
        payload_reference="https://www.federalreserve.gov/example.htm",
        normalizer_version="fed-rss-v1",
    )


def test_openai_adapter_returns_provider_neutral_feature_payload() -> None:
    transport = FakeOpenAITransport(
        _response(
            {
                "event_type": "monetary_policy",
                "factor_scores": [
                    {"factor": "monetary_policy", "direction": 0.4},
                    {"factor": "inflation", "direction": 0.1},
                ],
                "impact_strength": 0.7,
                "confidence": 0.8,
            }
        )
    )
    provider = OpenAIStructuredFeatureProvider(
        transport, model="recorded-openai-model", timeout_seconds=12.5
    )
    extractor = ProviderLlmFeatureExtractor(
        provider,
        producer_version="openai-provider-v1",
        model_version="recorded-openai-model",
        prompt_version="currency-fundamental-prompt-v1",
        clock=lambda: NOW,
    )

    feature = extractor.extract(
        _observation(),
        feature_id=FeatureId("feature-openai-contract"),
        currency=Currency("USD"),
    )

    assert feature.currency == Currency("USD")
    assert feature.versions.producer_version == "openai-provider-v1"
    assert feature.versions.model_version == "recorded-openai-model"
    assert feature.versions.prompt_version == "currency-fundamental-prompt-v1"
    assert transport.timeouts == [12.5]
    request = transport.requests[0]
    assert request["model"] == "recorded-openai-model"
    assert request["text"]["format"]["type"] == "json_schema"
    assert set(json.loads(request["input"][1]["content"])) == {
        "currency",
        "title",
        "body",
    }


@pytest.mark.parametrize("field", ["action", "side", "quantity", "leverage", "order"])
def test_openai_payload_cannot_bypass_forbidden_action_fields(field: str) -> None:
    transport = FakeOpenAITransport(
        _response(
            {
                "event_type": "monetary_policy",
                "factor_scores": [
                    {"factor": "monetary_policy", "direction": 0.4}
                ],
                "impact_strength": 0.7,
                "confidence": 0.8,
                field: "BUY",
            }
        )
    )
    extractor = ProviderLlmFeatureExtractor(
        OpenAIStructuredFeatureProvider(
            transport, model="recorded-openai-model", timeout_seconds=10
        ),
        producer_version="openai-provider-v1",
        model_version="recorded-openai-model",
        prompt_version="currency-fundamental-prompt-v1",
        clock=lambda: NOW,
    )

    with pytest.raises(ValueError, match="forbidden action fields"):
        extractor.extract(
            _observation(),
            feature_id=FeatureId("feature-openai-forbidden"),
            currency=Currency("USD"),
        )


def test_openai_malformed_response_is_not_converted_to_neutral_feature() -> None:
    provider = OpenAIStructuredFeatureProvider(
        FakeOpenAITransport({"output": []}),
        model="recorded-openai-model",
        timeout_seconds=10,
    )

    with pytest.raises(FeatureProductionError, match="no structured output"):
        provider.extract({"currency": "USD", "title": "title", "body": "body"})
