import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from fx_signal_store import SQLiteSignalStore

from .application import CollectOnceService, ProduceSignalsOnceService
from .feature_production import ProviderLlmFeatureExtractor, RecordedFeatureProvider
from .infrastructure.http_client import HttpGetPolicy, UrllibHttpClient
from .persistence import SQLiteIngestionStateStore
from .source_registry import build_source


def main() -> None:
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
    produce.add_argument("--provider-fixture", required=True)
    produce.add_argument("--producer-version", default="recorded-feature-provider-v1")
    produce.add_argument("--model-version", default="recorded-model-v1")
    produce.add_argument("--prompt-version", default="currency-fundamental-prompt-v1")

    args = parser.parse_args()
    database = Path(args.database)
    signal_store = SQLiteSignalStore(database)
    state_store = SQLiteIngestionStateStore(database)
    if args.command == "collect-once":
        http = UrllibHttpClient(
            HttpGetPolicy(timeout_seconds=args.timeout, maximum_attempts=args.attempts)
        )
        payload = asdict(
            CollectOnceService(signal_store, state_store).run(
                build_source(args.source, http, limit=args.limit),
                fetched_at=datetime.now(UTC),
            )
        )
    else:
        responses = json.loads(Path(args.provider_fixture).read_text(encoding="utf-8"))
        extractor = ProviderLlmFeatureExtractor(
            RecordedFeatureProvider(responses),
            producer_version=args.producer_version,
            model_version=args.model_version,
            prompt_version=args.prompt_version,
            clock=lambda: datetime.now(UTC),
        )
        payload = asdict(
            ProduceSignalsOnceService(
                signal_store, state_store, clock=lambda: datetime.now(UTC)
            ).run(extractor)
        )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
