import argparse
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from fx_core import CurrencyPair
from fx_signal_store import SQLiteSignalStore

from .application import CollectOnceService, ProduceSignalsOnceService
from .evaluation import ValidationPolicy
from .evaluation_application import (
    EvaluateSignalsOnceService,
    validation_policy_from_mapping,
)
from .evaluation_persistence import SQLiteEvaluationStore
from .feature_production import (
    ProviderLlmFeatureExtractor,
    RecordedFeatureProvider,
    StructuredFeatureProvider,
)
from .forward import MarketDataSource
from .forward_application import ObserveForwardOnceService
from .forward_persistence import SQLiteForwardEvaluationStore
from .infrastructure.gmo_fx import GmoFxMarketDataSource, UrllibGmoFxTransport
from .infrastructure.http_client import HttpGetPolicy, UrllibHttpClient
from .infrastructure.oanda import OandaV20CandleSource, UrllibOandaTransport
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

    observe = subparsers.add_parser("observe-forward-once")
    observe.add_argument("--database", required=True)
    observe.add_argument("--provider", choices=("gmo-fx", "oanda"), default="gmo-fx")
    observe.add_argument("--pair", required=True)

    evaluate = subparsers.add_parser("evaluate-signals-once")
    evaluate.add_argument("--database", required=True)
    evaluate.add_argument("--validation-policy")

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
    elif args.command == "produce-signals-once":
        extractor = _build_extractor(args, parser)
        database = Path(args.database)
        production_result = ProduceSignalsOnceService(
            SQLiteSignalStore(database),
            SQLiteIngestionStateStore(database),
            clock=lambda: datetime.now(UTC),
        ).run(extractor)
        payload = asdict(production_result)
        exit_code = int(
            production_result.failed > 0
            and not (
                args.allow_partial_success and production_result.completed > 0
            )
        )
    elif args.command == "observe-forward-once":
        database = Path(args.database)
        source = _build_market_source(args, parser)
        forward_result = ObserveForwardOnceService(
            SQLiteSignalStore(database),
            SQLiteForwardEvaluationStore(database),
            clock=lambda: datetime.now(UTC),
        ).run(source, instrument=_parse_pair(args.pair, parser))
        payload = asdict(forward_result)
        exit_code = int(forward_result.failed > 0)
    else:
        database = Path(args.database)
        policy = _load_validation_policy(args.validation_policy, parser)
        evaluation_result = EvaluateSignalsOnceService(
            SQLiteEvaluationStore(database),
            clock=lambda: datetime.now(UTC),
        ).run(policy)
        payload = asdict(evaluation_result)
        exit_code = int(evaluation_result.failed > 0)
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


def _build_market_source(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> MarketDataSource:
    if args.provider == "gmo-fx":
        timeout_seconds = _environment_timeout(
            "GMO_FX_API_TIMEOUT_SECONDS", parser
        )
        return GmoFxMarketDataSource(
            UrllibGmoFxTransport(),
            base_url=os.getenv(
                "GMO_FX_PUBLIC_API_BASE_URL",
                "https://forex-api.coin.z.com/public",
            ),
            timeout_seconds=timeout_seconds,
        )
    api_token = os.getenv("OANDA_API_TOKEN")
    if not api_token:
        parser.error("OANDA_API_TOKEN is required for --provider oanda")
    base_url = os.getenv("OANDA_API_BASE_URL", "https://api-fxpractice.oanda.com")
    timeout_seconds = _environment_timeout("OANDA_API_TIMEOUT_SECONDS", parser)
    return OandaV20CandleSource(
        UrllibOandaTransport(),
        api_token=api_token,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
    )


def _environment_timeout(name: str, parser: argparse.ArgumentParser) -> float:
    timeout_text = os.getenv(name, "10")
    try:
        timeout_seconds = float(timeout_text)
    except ValueError:
        parser.error(f"{name} must be numeric")
    return timeout_seconds


def _parse_pair(value: str, parser: argparse.ArgumentParser) -> CurrencyPair:
    try:
        return CurrencyPair.parse(value)
    except ValueError:
        parser.error("--pair must use BASE_QUOTE or BASE/QUOTE")


def _load_validation_policy(
    path: str | None, parser: argparse.ArgumentParser
) -> ValidationPolicy | None:
    if path is None:
        return None
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        parser.error(f"validation policy is unavailable: {type(error).__name__}")
    if not isinstance(payload, dict):
        parser.error("validation policy must contain a JSON object")
    try:
        return validation_policy_from_mapping(payload)
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
