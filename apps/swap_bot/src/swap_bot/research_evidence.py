import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from fx_core.time import require_utc

from .adoption import (
    SUPPORTED_EVALUATION_SNAPSHOT_VERSION,
    AdoptionFailureReason,
    AdoptionRejected,
    ResearchValidationEvidence,
    ResearchValidationStatus,
    StrictCohortIdentity,
    digest,
)


class ResearchValidationEvidenceSource(Protocol):
    def read(self, assessment_id: str) -> ResearchValidationEvidence: ...


class SQLiteResearchValidationEvidenceSource:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _connect_read_only(self) -> sqlite3.Connection:
        database_uri = f"file:{self.path.resolve().as_posix()}?mode=ro"
        connection = sqlite3.connect(database_uri, uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA query_only = ON")
        return connection

    def read(self, assessment_id: str) -> ResearchValidationEvidence:
        if not assessment_id.strip():
            raise AdoptionRejected(
                AdoptionFailureReason.RESEARCH_EVIDENCE_NOT_FOUND,
                "assessment ID must not be blank",
            )
        try:
            with closing(self._connect_read_only()) as connection:
                connection.execute("BEGIN")
                assessment = connection.execute(
                    "SELECT * FROM research_validation_assessments WHERE assessment_id = ?",
                    (assessment_id,),
                ).fetchone()
                if assessment is None:
                    raise AdoptionRejected(
                        AdoptionFailureReason.RESEARCH_EVIDENCE_NOT_FOUND,
                        f"Research assessment not found: {assessment_id}",
                    )
                report = connection.execute(
                    "SELECT * FROM research_evaluation_reports WHERE report_id = ?",
                    (assessment["report_id"],),
                ).fetchone()
                run = connection.execute(
                    "SELECT * FROM research_evaluation_runs WHERE run_id = ?",
                    (assessment["evaluation_run_id"],),
                ).fetchone()
                policy = connection.execute(
                    "SELECT * FROM research_validation_policies WHERE policy_version = ?",
                    (assessment["policy_version"],),
                ).fetchone()
                snapshot = connection.execute(
                    "SELECT * FROM research_evaluation_input_snapshots WHERE run_id = ?",
                    (assessment["evaluation_run_id"],),
                ).fetchone()
                evidence = self._validated_evidence(
                    assessment, report, run, policy, snapshot
                )
                connection.execute("COMMIT")
                return evidence
        except AdoptionRejected:
            raise
        except (OSError, sqlite3.Error) as error:
            raise AdoptionRejected(
                AdoptionFailureReason.RESEARCH_EVIDENCE_NOT_FOUND,
                f"Research evidence database is unavailable: {error}",
            ) from error
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise AdoptionRejected(
                AdoptionFailureReason.RESEARCH_LINEAGE_INVALID,
                f"Research evidence is malformed: {error}",
            ) from error

    @staticmethod
    def _validated_evidence(
        assessment: sqlite3.Row,
        report: sqlite3.Row | None,
        run: sqlite3.Row | None,
        policy: sqlite3.Row | None,
        snapshot: sqlite3.Row | None,
    ) -> ResearchValidationEvidence:
        if report is None or run is None or policy is None or snapshot is None:
            raise AdoptionRejected(
                AdoptionFailureReason.RESEARCH_LINEAGE_INVALID,
                "Research assessment lineage is incomplete",
            )
        if report["run_id"] != assessment["evaluation_run_id"]:
            raise AdoptionRejected(
                AdoptionFailureReason.RESEARCH_LINEAGE_INVALID,
                "Research report belongs to another Evaluation Run",
            )
        if run["run_id"] != assessment["evaluation_run_id"]:
            raise AdoptionRejected(
                AdoptionFailureReason.RESEARCH_LINEAGE_INVALID,
                "Research Evaluation Run identity differs",
            )
        if policy["content_hash"] != assessment["policy_content_hash"]:
            raise AdoptionRejected(
                AdoptionFailureReason.RESEARCH_LINEAGE_INVALID,
                "Research policy hash differs from the assessment",
            )
        if snapshot["snapshot_version"] != SUPPORTED_EVALUATION_SNAPSHOT_VERSION:
            raise AdoptionRejected(
                AdoptionFailureReason.RESEARCH_CONTRACT_UNSUPPORTED,
                f"unsupported Evaluation snapshot: {snapshot['snapshot_version']}",
            )

        policy_payload = _json_object(policy["policy_json"], "Research policy")
        cohort_payload = _json_object(report["cohort_identity_json"], "cohort")
        metrics_payload = _json_object(report["metrics_json"], "metrics")
        input_snapshot_payload = _json_object(snapshot["snapshot_json"], "input snapshot")
        conditions = _condition_results(assessment["condition_results_json"])
        if digest(policy_payload) != policy["content_hash"]:
            raise AdoptionRejected(
                AdoptionFailureReason.RESEARCH_LINEAGE_INVALID,
                "Research policy content does not match its hash",
            )
        if digest(input_snapshot_payload) != snapshot["snapshot_identity_hash"]:
            raise AdoptionRejected(
                AdoptionFailureReason.RESEARCH_LINEAGE_INVALID,
                "Evaluation input snapshot does not match its hash",
            )
        if input_snapshot_payload.get("version") != snapshot["snapshot_version"]:
            raise AdoptionRejected(
                AdoptionFailureReason.RESEARCH_LINEAGE_INVALID,
                "Evaluation input snapshot payload version differs",
            )
        cohort = StrictCohortIdentity.from_payload(cohort_payload)
        if report["cohort_id"] != "evaluation-cohort-" + cohort.content_hash:
            raise AdoptionRejected(
                AdoptionFailureReason.RESEARCH_LINEAGE_INVALID,
                "Research report cohort identity differs from its payload",
            )
        timestamps = {
            "assessment_created_at": _utc(assessment["created_at"]),
            "report_created_at": _utc(report["created_at"]),
            "run_created_at": _utc(run["created_at"]),
            "research_policy_created_at": _utc(policy["created_at"]),
        }
        return ResearchValidationEvidence(
            assessment_id=assessment["assessment_id"],
            status=ResearchValidationStatus(assessment["status"]),
            evaluation_run_id=assessment["evaluation_run_id"],
            report_id=assessment["report_id"],
            research_policy_version=assessment["policy_version"],
            research_policy_content_hash=assessment["policy_content_hash"],
            research_policy_payload=policy_payload,
            condition_results_payload=conditions,
            cohort=cohort,
            metrics_payload=metrics_payload,
            input_snapshot_version=snapshot["snapshot_version"],
            input_snapshot_identity_hash=snapshot["snapshot_identity_hash"],
            input_snapshot_payload=input_snapshot_payload,
            **timestamps,
        )


def _json_object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, str):
        raise ValueError(f"{label} JSON must be text")
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} JSON must be an object")
    return parsed


def _condition_results(value: object) -> list[list[object]]:
    if not isinstance(value, str):
        raise ValueError("condition results JSON must be text")
    parsed = json.loads(value)
    if not isinstance(parsed, list) or not all(
        isinstance(item, list)
        and len(item) == 2
        and isinstance(item[0], str)
        and isinstance(item[1], bool)
        for item in parsed
    ):
        raise ValueError("condition results must contain name/boolean pairs")
    return parsed


def _utc(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Research timestamp must be text")
    parsed = datetime.fromisoformat(value)
    require_utc(parsed, "Research evidence timestamp")
    return parsed
