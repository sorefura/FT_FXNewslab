import argparse
import json

from .shadow import run_fixture_file


def main() -> None:
    parser = argparse.ArgumentParser(prog="swap_bot")
    subparsers = parser.add_subparsers(dest="command", required=True)
    shadow = subparsers.add_parser("shadow-once")
    shadow.add_argument("--fixture", required=True)
    shadow.add_argument("--database")
    args = parser.parse_args()
    result = run_fixture_file(args.fixture, args.database)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

