import json
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fx_research.errors import FeatureProductionError

OPENAI_FEATURE_PROMPT_VERSION = "currency-fundamental-prompt-v1"
OPENAI_FEATURE_PROMPT = """Extract currency fundamental features from the supplied news.
Return only the requested event_type, factor_scores, impact_strength, and confidence.
Do not recommend trades or produce BUY, SELL, HOLD, EXIT, pair, side, quantity,
leverage, or order information. Direction values describe currency value impact
from -1.0 to 1.0. Strength and confidence range from 0.0 to 1.0."""

_FACTOR_NAMES = (
    "monetary_policy",
    "inflation",
    "growth",
    "employment",
    "geopolitical_risk",
    "other",
)
_FEATURE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "event_type": {"type": "string", "enum": list(_FACTOR_NAMES)},
        "factor_scores": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "factor": {"type": "string", "enum": list(_FACTOR_NAMES)},
                    "direction": {"type": "number", "minimum": -1.0, "maximum": 1.0},
                },
                "required": ["factor", "direction"],
                "additionalProperties": False,
            },
        },
        "impact_strength": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "required": ["event_type", "factor_scores", "impact_strength", "confidence"],
    "additionalProperties": False,
}


class OpenAIResponseTransport(Protocol):
    def create(
        self, request: Mapping[str, Any], *, timeout_seconds: float
    ) -> Mapping[str, Any]: ...


class UrllibOpenAIResponseTransport:
    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = "https://api.openai.com/v1/responses",
    ) -> None:
        if not api_key.strip():
            raise ValueError("OPENAI_API_KEY must not be blank")
        self._api_key = api_key
        self._endpoint = endpoint

    def create(
        self, request: Mapping[str, Any], *, timeout_seconds: float
    ) -> Mapping[str, Any]:
        if timeout_seconds <= 0:
            raise ValueError("OpenAI timeout must be positive")
        http_request = Request(
            self._endpoint,
            data=json.dumps(request).encode(),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(http_request, timeout=timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise FeatureProductionError(
                f"OpenAI API returned HTTP {error.code}"
            ) from error
        except (TimeoutError, URLError) as error:
            raise FeatureProductionError(
                f"OpenAI API request failed: {type(error).__name__}"
            ) from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise FeatureProductionError("OpenAI API returned malformed JSON") from error
        if not isinstance(payload, Mapping):
            raise FeatureProductionError("OpenAI API response must be an object")
        return payload


class OpenAIStructuredFeatureProvider:
    def __init__(
        self,
        transport: OpenAIResponseTransport,
        *,
        model: str,
        timeout_seconds: float,
    ) -> None:
        if not model.strip():
            raise ValueError("OpenAI model must not be blank")
        if timeout_seconds <= 0:
            raise ValueError("OpenAI timeout must be positive")
        self._transport = transport
        self._model = model
        self._timeout_seconds = timeout_seconds

    def extract(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        request = {
            "model": self._model,
            "input": [
                {"role": "system", "content": OPENAI_FEATURE_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "currency": payload["currency"],
                            "title": payload["title"],
                            "body": payload["body"],
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "currency_fundamental_feature",
                    "strict": True,
                    "schema": _FEATURE_SCHEMA,
                }
            },
        }
        response = self._transport.create(
            request, timeout_seconds=self._timeout_seconds
        )
        structured = self._structured_output(response)
        factors = structured.get("factor_scores")
        if not isinstance(factors, list) or not factors:
            raise FeatureProductionError("OpenAI factor_scores must be a non-empty array")
        factor_scores: dict[str, Any] = {}
        for item in factors:
            if not isinstance(item, Mapping):
                raise FeatureProductionError("OpenAI factor score must be an object")
            factor = item.get("factor")
            if not isinstance(factor, str) or factor in factor_scores:
                raise FeatureProductionError("OpenAI factor names must be unique strings")
            factor_scores[factor] = item.get("direction")
        return {**structured, "factor_scores": factor_scores}

    @staticmethod
    def _structured_output(response: Mapping[str, Any]) -> dict[str, Any]:
        output = response.get("output")
        if not isinstance(output, list):
            raise FeatureProductionError("OpenAI response has no output list")
        for message in output:
            if not isinstance(message, Mapping) or message.get("type") != "message":
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, Mapping) or part.get("type") != "output_text":
                    continue
                text = part.get("text")
                if not isinstance(text, str):
                    break
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError as error:
                    raise FeatureProductionError(
                        "OpenAI structured output is malformed JSON"
                    ) from error
                if not isinstance(parsed, dict):
                    raise FeatureProductionError(
                        "OpenAI structured output must be an object"
                    )
                return parsed
        raise FeatureProductionError("OpenAI response contains no structured output")
