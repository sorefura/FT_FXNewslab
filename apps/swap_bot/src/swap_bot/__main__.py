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
    adoption_mode = ""
    try:
        if args.command == "shadow-once":
            result = run_fixture_file(args.fixture, args.database)
        elif args.command == "approve-signal-adoption-once":
            store = SQLiteAdoptionStore(args.live_database) if args.apply else None
            policy = adoption_policy_from_file(args.policy)
            adoption_mode = policy.adoption_mode.value
            result = ApproveSignalAdoptionOnceService(
                SQLiteResearchValidationEvidenceSource(args.research_database),
                clock=lambda: datetime.now(UTC),
            ).run(
                assessment_id=args.assessment_id,
                policy=policy,
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
        result = _failure_summary(args.command, error, adoption_mode)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 1
    except (KeyError, OSError, ValueError) as error:
        reason = (
            "ADOPTION_POLICY_MISMATCH"
            if args.command == "approve-signal-adoption-once"
            else "NO_ACTIVE_ADOPTION"
        )
        result = {
            "persisted": False,
            "would_approve": False,
            "would_revoke": False,
            "failure_reasons": [reason],
            "detail": str(error),
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _failure_summary(
    command: str, error: AdoptionRejected, adoption_mode: str
) -> dict[str, object]:
    if command != "approve-signal-adoption-once":
        return {
            "would_revoke": False,
            "persisted": False,
            "reused": False,
            "failure_reasons": [error.reason.value],
        }
    return {
        "assessment_found": error.reason.value != "RESEARCH_EVIDENCE_NOT_FOUND",
        "research_status": error.context.get("research_status", ""),
        "research_lineage_valid": error.reason.value
        not in {
            "RESEARCH_EVIDENCE_NOT_FOUND",
            "RESEARCH_LINEAGE_INVALID",
            "RESEARCH_CONTRACT_UNSUPPORTED",
        },
        "evidence_snapshot_id": "",
        "policy_match": False,
        "adoption_mode": adoption_mode,
        "would_approve": False,
        "persisted": False,
        "reused": False,
        "failure_reasons": [error.reason.value],
    }


if __name__ == "__main__":
    raise SystemExit(main())
