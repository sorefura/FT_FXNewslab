from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(
    cwd: Path,
    argv: list[str],
    *,
    expected: int = 0,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != expected:
        command = " ".join(argv)
        raise AssertionError(
            f"unexpected exit {result.returncode}, expected {expected}: {command}\n{result.stdout}"
        )
    return result


def _gate(repo: Path, script: Path, *arguments: str, expected: int = 0) -> dict[str, object]:
    result = _run(repo, [sys.executable, str(script), *arguments], expected=expected)
    if expected != 0:
        return {"output": result.stdout}
    return json.loads(result.stdout)


def _write_manifest(repo: Path) -> None:
    python = json.dumps(sys.executable)
    check_script = json.dumps("phase_check.py")
    log_tamper_script = json.dumps("log_tamper_check.py")
    content = f"""schema_version = 1
phase = "M9"
spec_files = ["spec.md"]
acceptance_files = ["acceptance.md"]
units = ["B1", "final"]
reviewer_agent = "phase_reviewer"
final_reviewer_agent = "phase_final_reviewer"
implementer_agent = "phase_implementer"
escalated_implementer_agent = "phase_implementer_escalated"
escalate_after_rejections = 1

[[checks]]
name = "smoke"
scope = "all"
argv = [{python}, {check_script}]

[[checks]]
name = "log-guard"
scope = "all"
argv = [{python}, {log_tamper_script}]
"""
    (repo / "phase.toml").write_text(content, encoding="utf-8")


def main() -> int:
    script = Path(__file__).with_name("phase_gate.py").resolve()
    with tempfile.TemporaryDirectory(prefix="phase-gate-selftest-") as temporary:
        root = Path(temporary)
        repo = root / "repo"
        repo.mkdir()
        _run(repo, ["git", "init", "-b", "main"])
        _run(repo, ["git", "config", "user.name", "Phase Gate Selftest"])
        _run(repo, ["git", "config", "user.email", "phase-gate@invalid.local"])
        (repo / "spec.md").write_text("# Frozen specification\n", encoding="utf-8")
        (repo / "acceptance.md").write_text("# Frozen acceptance\n", encoding="utf-8")
        (repo / "phase_check.py").write_text(
            "from pathlib import Path\n"
            "import os\n\n"
            "if os.environ.get('PHASE_GATE_MUTATE_CHECK') == '1':\n"
            "    Path('mutated-by-check.txt').write_text('mutation\\n', encoding='utf-8')\n"
            "if os.environ.get('PHASE_GATE_FAIL_CHECK') == '1':\n"
            "    print('check failed')\n"
            "    raise SystemExit(1)\n"
            "print('check passed')\n",
            encoding="utf-8",
        )
        (repo / "log_tamper_check.py").write_text(
            "from pathlib import Path\n"
            "import os\n\n"
            "if os.environ.get('PHASE_GATE_TAMPER_LOGS') == '1':\n"
            "    logs = sorted(Path('.phase-runs/M9/checks').rglob('smoke.log'))\n"
            "    if logs:\n"
            "        logs[-1].write_text('tampered log\\n', encoding='utf-8')\n"
            "print('log guard passed')\n",
            encoding="utf-8",
        )
        _write_manifest(repo)
        initial_manifest = (repo / "phase.toml").read_text(encoding="utf-8")
        (repo / "ignored-phase.toml").write_text(
            initial_manifest.replace('phase = "M9"', 'phase = "M8"', 1).replace(
                'spec_files = ["spec.md"]', 'spec_files = ["ignored-spec.md"]', 1
            ),
            encoding="utf-8",
        )
        (repo / ".gitignore").write_text("ignored-spec.md\n", encoding="utf-8")
        _run(repo, ["git", "add", "."])
        _run(repo, ["git", "commit", "-m", "freeze design"])

        (repo / "ignored-spec.md").write_text("# Ignored frozen input\n", encoding="utf-8")
        ignored_frozen = _gate(
            repo,
            script,
            "init",
            "--manifest",
            "ignored-phase.toml",
            expected=2,
        )
        assert "frozen file is not tracked at HEAD" in str(ignored_frozen["output"])
        (repo / "ignored-spec.md").unlink()

        manifest_path = repo / "phase.toml"
        original_manifest = manifest_path.read_text(encoding="utf-8")
        manifest_path.write_text(
            original_manifest.replace('name = "smoke"', 'name = "../escape"', 1),
            encoding="utf-8",
        )
        unsafe_name = _gate(repo, script, "init", "--manifest", "phase.toml", expected=2)
        assert "check names must use only" in str(unsafe_name["output"])
        duplicate_check = (
            "\n[[checks]]\n"
            'name = "SMOKE"\n'
            'scope = "all"\n'
            f"argv = [{json.dumps(sys.executable)}, {json.dumps('phase_check.py')}]\n"
        )
        manifest_path.write_text(original_manifest + duplicate_check, encoding="utf-8")
        colliding_name = _gate(repo, script, "init", "--manifest", "phase.toml", expected=2)
        assert "case-insensitive filesystems" in str(colliding_name["output"])
        manifest_path.write_text(
            original_manifest.replace('name = "smoke"', 'name = "CON"', 1),
            encoding="utf-8",
        )
        reserved_name = _gate(repo, script, "init", "--manifest", "phase.toml", expected=2)
        assert "reserved on Windows" in str(reserved_name["output"])
        manifest_path.write_text(
            original_manifest.replace('phase = "M9"', 'phase = "CON.txt"', 1),
            encoding="utf-8",
        )
        reserved_phase = _gate(repo, script, "init", "--manifest", "phase.toml", expected=2)
        assert "invalid phase or unit name" in str(reserved_phase["output"])
        manifest_path.write_text(
            original_manifest.replace('phase = "M9"', 'phase = "M9."', 1),
            encoding="utf-8",
        )
        trailing_dot = _gate(repo, script, "init", "--manifest", "phase.toml", expected=2)
        assert "invalid phase or unit name" in str(trailing_dot["output"])
        manifest_path.write_text(
            original_manifest.replace('units = ["B1", "final"]', 'units = ["B1", "b1"]', 1),
            encoding="utf-8",
        )
        aliased_units = _gate(repo, script, "init", "--manifest", "phase.toml", expected=2)
        assert "case-insensitive filesystems" in str(aliased_units["output"])
        manifest_path.write_text(original_manifest, encoding="utf-8")

        initialized = _gate(repo, script, "init", "--manifest", "phase.toml")
        assert initialized["status"] == "ready_for_unit"
        assert initialized["current_unit"] == "B1"

        state_path = repo / ".phase-runs" / "M9" / "state.json"
        original_state = state_path.read_text(encoding="utf-8")
        forged_state = json.loads(original_state)
        forged_state["status"] = "complete"
        state_path.write_text(json.dumps(forged_state), encoding="utf-8")
        state_failure = _gate(repo, script, "status", "--phase", "M9", expected=2)
        assert "integrity seal is invalid" in str(state_failure["output"])
        state_path.write_text(original_state, encoding="utf-8")

        _run(repo, ["git", "reflog", "expire", "--expire=now", "--all"])
        _run(repo, ["git", "gc", "--prune=now"])
        _gate(repo, script, "verify", "--phase", "M9")
        (repo / "before-first-unit.txt").write_text("outside transition\n", encoding="utf-8")
        initial_gap = _gate(
            repo,
            script,
            "start-unit",
            "--phase",
            "M9",
            "--unit",
            "B1",
            expected=2,
        )
        assert "changed before the unit transition" in str(initial_gap["output"])
        (repo / "before-first-unit.txt").unlink()
        _gate(repo, script, "start-unit", "--phase", "M9", "--unit", "B1")
        _run(repo, ["git", "reflog", "expire", "--expire=now", "--all"])
        _run(repo, ["git", "gc", "--prune=now"])
        (repo / "implementation.txt").write_text("first attempt\n", encoding="utf-8")
        os.environ["PHASE_GATE_MUTATE_CHECK"] = "1"
        mutating_check = _gate(
            repo,
            script,
            "run-checks",
            "--phase",
            "M9",
            "--scope",
            "unit",
            expected=2,
        )
        os.environ.pop("PHASE_GATE_MUTATE_CHECK")
        assert '"mutated_reviewable_tree": true' in str(mutating_check["output"])
        (repo / "mutated-by-check.txt").unlink()
        os.environ["PHASE_GATE_TAMPER_LOGS"] = "1"
        tampered_logs = _gate(
            repo,
            script,
            "run-checks",
            "--phase",
            "M9",
            "--scope",
            "unit",
            expected=2,
        )
        os.environ.pop("PHASE_GATE_TAMPER_LOGS")
        assert '"logs_intact": false' in str(tampered_logs["output"])
        _gate(repo, script, "run-checks", "--phase", "M9", "--scope", "unit")
        (repo / "implementation.txt").write_text("changed after checks\n", encoding="utf-8")
        stale_checks = _gate(
            repo,
            script,
            "prepare-review",
            "--phase",
            "M9",
            expected=2,
        )
        assert "changed after the latest successful checks" in str(stale_checks["output"])
        (repo / "implementation.txt").write_text("first attempt\n", encoding="utf-8")
        state_before_prepare = state_path.read_text(encoding="utf-8")
        orphaned_review = _gate(repo, script, "prepare-review", "--phase", "M9")
        assert orphaned_review["attempt"] == 1
        state_path.write_text(state_before_prepare, encoding="utf-8")
        first_review = _gate(repo, script, "prepare-review", "--phase", "M9")
        assert first_review["attempt"] == 1

        request_changes = root / "request-changes.md"
        request_changes.write_text(
            "VERDICT: REQUEST_CHANGES\n"
            f"REVIEW_NONCE: {first_review['review_nonce']}\n\n"
            "- P1: correct the implementation.\n",
            encoding="utf-8",
        )
        nested_verdict = repo / str(first_review["bundle"]) / "extra" / "verdict.md"
        nested_verdict.parent.mkdir()
        nested_verdict.write_text("unexpected nested evidence\n", encoding="utf-8")
        nested_verdict_failure = _gate(
            repo,
            script,
            "record-review",
            "--phase",
            "M9",
            "--thread-id",
            "nested-verdict-thread",
            "--verdict-file",
            str(request_changes),
            expected=2,
        )
        assert "bundle contents changed" in str(nested_verdict_failure["output"])
        nested_verdict.unlink()
        nested_verdict.parent.rmdir()
        diff_path = repo / str(first_review["bundle"]) / "changes.diff"
        original_diff = diff_path.read_bytes()
        diff_path.write_bytes(b"fabricated diff\n")
        bundle_failure = _gate(
            repo,
            script,
            "record-review",
            "--phase",
            "M9",
            "--thread-id",
            "tampered-bundle-thread",
            "--verdict-file",
            str(request_changes),
            expected=2,
        )
        assert "bundle contents changed" in str(bundle_failure["output"])
        diff_path.write_bytes(original_diff)
        state_before_record = state_path.read_text(encoding="utf-8")
        _gate(
            repo,
            script,
            "record-review",
            "--phase",
            "M9",
            "--thread-id",
            "review-thread-1",
            "--verdict-file",
            str(request_changes),
        )
        state_path.write_text(state_before_record, encoding="utf-8")
        rejected = _gate(
            repo,
            script,
            "record-review",
            "--phase",
            "M9",
            "--thread-id",
            "review-thread-1",
            "--verdict-file",
            str(request_changes),
        )
        assert rejected["status"] == "changes_requested"
        assert rejected["escalation_required"] is True

        _gate(repo, script, "start-unit", "--phase", "M9", "--unit", "B1")
        _gate(repo, script, "run-checks", "--phase", "M9", "--scope", "unit")
        unchanged_correction = _gate(
            repo,
            script,
            "prepare-review",
            "--phase",
            "M9",
            expected=2,
        )
        assert "has not changed since REQUEST_CHANGES" in str(unchanged_correction["output"])
        (repo / "implementation.txt").write_text("corrected\n", encoding="utf-8")
        _gate(repo, script, "run-checks", "--phase", "M9", "--scope", "unit")
        second_review = _gate(repo, script, "prepare-review", "--phase", "M9")
        assert second_review["attempt"] == 2

        approve = root / "approve.md"
        approve.write_text("VERDICT: APPROVE\n", encoding="utf-8")
        nonce_failure = _gate(
            repo,
            script,
            "record-review",
            "--phase",
            "M9",
            "--thread-id",
            "missing-nonce-thread",
            "--verdict-file",
            str(approve),
            expected=2,
        )
        assert "does not attest" in str(nonce_failure["output"])
        approve.write_text(
            f"VERDICT: APPROVE\nREVIEW_NONCE: {second_review['review_nonce']}\n",
            encoding="utf-8",
        )
        duplicate = _gate(
            repo,
            script,
            "record-review",
            "--phase",
            "M9",
            "--thread-id",
            "review-thread-1",
            "--verdict-file",
            str(approve),
            expected=2,
        )
        assert "already been used" in str(duplicate["output"])

        approved = _gate(
            repo,
            script,
            "record-review",
            "--phase",
            "M9",
            "--thread-id",
            "review-thread-2",
            "--verdict-file",
            str(approve),
        )
        assert approved["status"] == "unit_approved"
        (repo / "implementation.txt").write_text("changed after approval\n", encoding="utf-8")
        changed_after_approval = _gate(
            repo,
            script,
            "advance",
            "--phase",
            "M9",
            expected=2,
        )
        assert "changed after unit approval" in str(changed_after_approval["output"])
        (repo / "implementation.txt").write_text("corrected\n", encoding="utf-8")
        advanced = _gate(repo, script, "advance", "--phase", "M9")
        assert advanced["current_unit"] == "final"

        (repo / "between-units.txt").write_text("outside transition\n", encoding="utf-8")
        between_units_gap = _gate(
            repo,
            script,
            "start-unit",
            "--phase",
            "M9",
            "--unit",
            "final",
            expected=2,
        )
        assert "changed before the unit transition" in str(between_units_gap["output"])
        (repo / "between-units.txt").unlink()
        _gate(repo, script, "start-unit", "--phase", "M9", "--unit", "final")
        (repo / "second-unit.txt").write_text("second unit\n", encoding="utf-8")
        (repo / "non-utf8.txt").write_bytes(b"review exact byte: \xff\n")
        _gate(repo, script, "run-checks", "--phase", "M9", "--scope", "unit")
        third_review = _gate(repo, script, "prepare-review", "--phase", "M9")
        assert b"review exact byte: \xff" in (
            repo / str(third_review["bundle"]) / "changes.diff"
        ).read_bytes()
        approve.write_text(
            f"VERDICT: APPROVE\nREVIEW_NONCE: {third_review['review_nonce']}\n",
            encoding="utf-8",
        )
        _gate(
            repo,
            script,
            "record-review",
            "--phase",
            "M9",
            "--thread-id",
            "review-thread-3",
            "--verdict-file",
            str(approve),
        )
        phase_review = _gate(repo, script, "advance", "--phase", "M9")
        assert phase_review["status"] == "phase_review"

        (repo / "before-phase-checks.txt").write_text("outside transition\n", encoding="utf-8")
        phase_gap = _gate(
            repo,
            script,
            "run-checks",
            "--phase",
            "M9",
            "--scope",
            "phase",
            expected=2,
        )
        assert "changed before initial phase checks" in str(phase_gap["output"])
        (repo / "before-phase-checks.txt").unlink()
        os.environ["PHASE_GATE_FAIL_CHECK"] = "1"
        _gate(
            repo,
            script,
            "run-checks",
            "--phase",
            "M9",
            "--scope",
            "phase",
            expected=2,
        )
        os.environ.pop("PHASE_GATE_FAIL_CHECK")
        failed_phase_status = _gate(repo, script, "status", "--phase", "M9")
        assert failed_phase_status["status"] == "phase_checks_failed"
        assert failed_phase_status["implementer_agent"] == "phase_implementer_escalated"
        (repo / "phase-check-fix.txt").write_text("fixed phase check\n", encoding="utf-8")
        _gate(repo, script, "run-checks", "--phase", "M9", "--scope", "phase")
        recovered_phase_status = _gate(repo, script, "status", "--phase", "M9")
        assert recovered_phase_status["status"] == "phase_review"
        (repo / "after-phase-checks.txt").write_text("requires rerun\n", encoding="utf-8")
        stale_phase_checks = _gate(
            repo,
            script,
            "prepare-final-review",
            "--phase",
            "M9",
            expected=2,
        )
        assert "changed after the latest successful checks" in str(stale_phase_checks["output"])
        _gate(repo, script, "run-checks", "--phase", "M9", "--scope", "phase")
        final_review = _gate(repo, script, "prepare-final-review", "--phase", "M9")
        final_request = root / "final-request.md"
        final_request.write_text(
            "VERDICT: REQUEST_CHANGES\n"
            f"REVIEW_NONCE: {final_review['review_nonce']}\n\n"
            "- P1: correct the final integration.\n",
            encoding="utf-8",
        )
        final_rejected = _gate(
            repo,
            script,
            "record-final-review",
            "--phase",
            "M9",
            "--thread-id",
            "final-review-thread-1",
            "--verdict-file",
            str(final_request),
        )
        assert final_rejected["status"] == "final_changes_requested"
        assert final_rejected["implementer_agent"] == "phase_implementer_escalated"
        (repo / "final-integration-fix.txt").write_text("fixed final review\n", encoding="utf-8")
        _gate(repo, script, "run-checks", "--phase", "M9", "--scope", "phase")
        final_review = _gate(repo, script, "prepare-final-review", "--phase", "M9")
        approve.write_text(
            f"VERDICT: APPROVE\nREVIEW_NONCE: {final_review['review_nonce']}\n",
            encoding="utf-8",
        )
        completed = _gate(
            repo,
            script,
            "record-final-review",
            "--phase",
            "M9",
            "--thread-id",
            "final-review-thread-2",
            "--verdict-file",
            str(approve),
        )
        assert completed["status"] == "complete"
        final_bundle = repo / str(final_review["bundle"])
        final_diff = final_bundle / "changes.diff"
        original_final_diff = final_diff.read_bytes()
        final_diff.write_bytes(b"tampered historical diff\n")
        historical_bundle_failure = _gate(
            repo,
            script,
            "assert-complete",
            "--phase",
            "M9",
            expected=2,
        )
        assert "recorded review bundle changed" in str(historical_bundle_failure["output"])
        final_diff.write_bytes(original_final_diff)

        final_verdict = final_bundle / "verdict.md"
        original_final_verdict = final_verdict.read_text(encoding="utf-8")
        final_verdict.write_text("VERDICT: APPROVE\nREVIEW_NONCE: tampered\n", encoding="utf-8")
        historical_verdict_failure = _gate(
            repo,
            script,
            "assert-complete",
            "--phase",
            "M9",
            expected=2,
        )
        assert "recorded review verdict changed" in str(historical_verdict_failure["output"])
        final_verdict.write_text(original_final_verdict, encoding="utf-8")

        (repo / "second-unit.txt").write_text("changed after final review\n", encoding="utf-8")
        changed_after_final = _gate(
            repo,
            script,
            "assert-complete",
            "--phase",
            "M9",
            expected=2,
        )
        assert "changed after final approval" in str(changed_after_final["output"])
        (repo / "second-unit.txt").write_text("second unit\n", encoding="utf-8")
        proof = _gate(repo, script, "assert-complete", "--phase", "M9")
        assert proof["completion_verified"] is True

        (repo / "spec.md").write_text("# Changed after freeze\n", encoding="utf-8")
        frozen_failure = _gate(
            repo,
            script,
            "verify",
            "--phase",
            "M9",
            expected=2,
        )
        assert "frozen file changed" in str(frozen_failure["output"])

    print("phase_gate self-test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
