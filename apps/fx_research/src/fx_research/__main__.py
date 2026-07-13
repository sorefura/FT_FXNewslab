import argparse
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from fx_signal_store import SQLiteSignalStore

from .application import CollectOnceService, ProduceSignalsOnceService
from .feature_production import (
    ProviderLlmFeatureExtractor,
    RecordedFeatureProvider,
    StructuredFeatureProvider,
)
from .infrastructure.http_client import HttpGetPolicy, UrllibHttpClient
from .infrastructure.openai import (
    OPENAI_FEATURE_PROMPT_VERSION,
    OpenAIStructuredFeatureProvider,
    UrllibOpenAIResponseTransport,
)
from .persistence import SQLiteIngestionStateStore
from .source_registry import build_source


def main() -> int:
    parser = argparse.ArgumentParser(prog="fx_research")
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect = subparsers.add_parser("collect-once")
    collect.add_argument("--source", required=True)
    collect.add_argument("--database", required=True)
    collect.add_argument("--limit", type=int, default=20)
    collect.add_argument("--timeout", type=float, default=10.0)
    collect.add_argument("--attempts", type=int, default=3)

    produce = subparsers.add_parser("produce-signals-once")
    produce.add_argument("--database", required=True)
    produce.add_argument("--provider", choices=("recorded", "openai"), required=True)
    produce.add_argument("--provider-fixture")
    produce.add_argument("--producer-version")
    produce.add_argument("--model-version")
    produce.add_argument("--model")
    produce.add_argument("--prompt-version", default=OPENAI_FEATURE_PROMPT_VERSION)
    produce.add_argument("--timeout", type=float, default=30.0)
    produce.add_argument("--allow-partial-success", action="store_true")

    args = parser.parse_args()
    if args.command == "collect-once":
        database = Path(args.database)
        signal_store = SQLiteSignalStore(database)
        state_store = SQLiteIngestionStateStore(database)
        http = UrllibHttpClient(
            HttpGetPolicy(timeout_seconds=args.timeout, maximum_attempts=args.attempts)
        )
        payload = asdict(
            CollectOnceService(signal_store, state_store).run(
                build_source(args.source, http, limit=args.limit),
                fetched_at=datetime.now(UTC),
            )
        )
        exit_code = 0
    else:
        extractor = _build_extractor(args, parser)
        database = Path(args.database)
        result = ProduceSignalsOnceService(
            SQLiteSignalStore(database),
            SQLiteIngestionStateStore(database),
            clock=lambda: datetime.now(UTC),
        ).run(extractor)
        payload = asdict(result)
        exit_code = int(
            result.failed > 0
            and not (args.allow_partial_success and result.completed > 0)
        )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return exit_code


def _build_extractor(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> ProviderLlmFeatureExtractor:
    provider: StructuredFeatureProvider
    if args.provider == "recorded":
        if not args.provider_fixture:
            parser.error("--provider-fixture is required for --provider recorded")
        try:
            responses = json.loads(
                Path(args.provider_fixture).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as error:
            parser.error(f"recorded provider fixture is unavailable: {type(error).__name__}")
        if not isinstance(responses, dict):
            parser.error("recorded provider fixture must contain a JSON object")
        provider = RecordedFeatureProvider(responses)
        producer_version = args.producer_version or "recorded-feature-provider-v1"
        model_version = args.model_version or "recorded-model-v1"
    else:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            parser.error("OPENAI_API_KEY is required for --provider openai")
        if not args.model:
            parser.error("--model is required for --provider openai")
        provider = OpenAIStructuredFeatureProvider(
            UrllibOpenAIResponseTransport(api_key),
            model=args.model,
            timeout_seconds=args.timeout,
        )
        producer_version = args.producer_version or "openai-structured-feature-provider-v1"
        model_version = args.model
    return ProviderLlmFeatureExtractor(
        provider,
        producer_version=producer_version,
        model_version=model_version,
        prompt_version=args.prompt_version,
        clock=lambda: datetime.now(UTC),
    )


if __name__ == "__main__":
    raise SystemExit(main())
