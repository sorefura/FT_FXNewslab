import os

import pytest
from fx_research.infrastructure.openai import (
    OpenAIStructuredFeatureProvider,
    UrllibOpenAIResponseTransport,
)


@pytest.mark.openai_smoke
def test_openai_can_extract_structured_currency_feature() -> None:
    if os.getenv("RUN_OPENAI_SMOKE") != "1":
        pytest.skip("set RUN_OPENAI_SMOKE=1 to call OpenAI")
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_SMOKE_MODEL")
    if not api_key or not model:
        pytest.skip("OPENAI_API_KEY and OPENAI_SMOKE_MODEL are required")
    provider = OpenAIStructuredFeatureProvider(
        UrllibOpenAIResponseTransport(api_key),
        model=model,
        timeout_seconds=30,
    )

    payload = provider.extract(
        {
            "currency": "USD",
            "title": "Recorded central bank statement",
            "body": "The policy rate was left unchanged.",
        }
    )

    assert set(payload) == {
        "event_type",
        "factor_scores",
        "impact_strength",
        "confidence",
    }
