import argparse
import json
from datetime import UTC, datetime

from .adoption import AdoptionRejected
from .adoption_application import (
    ApproveSignalAdoptionOnceService,
    RevokeSignalAdoptionOnceService,
    adoption_policy_from_file,
)
from .adoption_reader import SQLiteAdoptionDecisionReader
from .adoption_store import SQLiteAdoptionStore
from .research_evidence import SQLiteResearchValidationEvidenceSource
from .shadow import run_fixture_file


def main() -> int:
    parser = argparse.ArgumentParser(prog="swap_bot")
    subparsers = parser.add_subparsers(dest="command", required=True)
    shadow = subparsers.add_parser("shadow-once")
    shadow.add_argument("--fixture", required=True)
    shadow.add_argument("--database")
    approve = subparsers.add_parser("approve-signal-adoption-once")
    approve.add_argument("--research-database", required=True)
    approve.add_argument("--live-database", required=True)
    approve.add_argument("--assessment-id", required=True)
    approve.add_argument("--policy", required=True)
    approve.add_argument("--approved-by", required=True)
    approve.add_argument("--reason", required=True)
    approve.add_argument("--apply", action="store_true")
    revoke = subparsers.add_parser("revoke-signal-adoption-once")
    revoke.add_argument("--live-database", required=True)
    revoke.add_argument("--approval-decision-id", required=True)
    revoke.add_argument("--revoked-by", required=True)
    revoke.add_argument("--reason", required=True)
    revoke.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "shadow-once":
            result = run_fixture_file(args.fixture, args.database)
        elif args.command == "approve-signal-adoption-once":
            store = SQLiteAdoptionStore(args.live_database) if args.apply else None
            result = ApproveSignalAdoptionOnceService(
                SQLiteResearchValidationEvidenceSource(args.research_database),
                clock=lambda: datetime.now(UTC),
            ).run(
                assessment_id=args.assessment_id,
                policy=adoption_policy_from_file(args.policy),
                approved_by=args.approved_by,
                reason=args.reason,
                apply=args.apply,
                store=store,
            ).summary()
        else:
            store = SQLiteAdoptionStore(args.live_database) if args.apply else None
            result = RevokeSignalAdoptionOnceService(
                SQLiteAdoptionDecisionReader(args.live_database),
                clock=lambda: datetime.now(UTC),
            ).run(
                approval_decision_id=args.approval_decision_id,
                revoked_by=args.revoked_by,
                reason=args.reason,
                apply=args.apply,
                store=store,
            ).summary()
    except AdoptionRejected as error:
        result = {"persisted": False, "failure_reasons": [error.reason.value]}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
