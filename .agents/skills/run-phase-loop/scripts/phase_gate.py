from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
PHASE_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,62}[A-Za-z0-9])?")
CHECK_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
WINDOWS_RESERVED_STEMS = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
VERDICTS = {"VERDICT: APPROVE", "VERDICT: REQUEST_CHANGES"}
RUNS_RELATIVE = Path(".phase-runs")
BASELINE_REF_ROOT = "refs/codex-phase-runs"


class GateError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json_print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _run(
    repo: Path,
    argv: list[str],
    *,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and result.returncode != 0:
        command = " ".join(argv)
        raise GateError(f"command failed ({result.returncode}): {command}\n{result.stdout}")
    return result


def _repo_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise GateError(f"not inside a Git repository: {result.stdout.strip()}")
    return Path(result.stdout.strip()).resolve()


def _phase_name(value: str) -> str:
    reserved_stem = value.split(".", 1)[0].upper()
    if PHASE_PATTERN.fullmatch(value) is None or reserved_stem in WINDOWS_RESERVED_STEMS:
        raise GateError(f"invalid phase or unit name: {value!r}")
    return value


def _inside_repo(repo: Path, value: str | Path, *, require_file: bool = False) -> Path:
    candidate = (repo / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    try:
        candidate.relative_to(repo)
    except ValueError as exc:
        raise GateError(f"path escapes repository: {value}") from exc
    if require_file and not candidate.is_file():
        raise GateError(f"required file does not exist: {candidate.relative_to(repo)}")
    return candidate


def _relative(repo: Path, value: str | Path, *, require_file: bool = False) -> str:
    return _inside_repo(repo, value, require_file=require_file).relative_to(repo).as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _state_sha256(state: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in state.items() if key != "state_sha256"}
    payload = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _phase_dir(repo: Path, phase: str) -> Path:
    return repo / RUNS_RELATIVE / _phase_name(phase)


def _state_path(repo: Path, phase: str) -> Path:
    return _phase_dir(repo, phase) / "state.json"


def _load_state(repo: Path, phase: str) -> dict[str, Any]:
    path = _state_path(repo, phase)
    if not path.is_file():
        raise GateError(f"phase state does not exist: {phase}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError(f"cannot read phase state: {path}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise GateError("unsupported or malformed phase state")
    if value.get("phase") != phase:
        raise GateError("phase state identity mismatch")
    expected_seal = value.get("state_sha256")
    if not isinstance(expected_seal, str) or expected_seal != _state_sha256(value):
        raise GateError("phase state integrity seal is invalid")
    return value


def _write_state(repo: Path, state: dict[str, Any]) -> None:
    directory = _phase_dir(repo, str(state["phase"]))
    directory.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _now()
    state["state_sha256"] = _state_sha256(state)
    target = directory / "state.json"
    temporary = directory / "state.json.tmp"
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def _load_manifest(repo: Path, manifest_relative: str) -> dict[str, Any]:
    path = _inside_repo(repo, manifest_relative, require_file=True)
    try:
        manifest = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise GateError(f"cannot load manifest: {manifest_relative}") from exc
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise GateError("manifest schema_version must be 1")
    phase = manifest.get("phase")
    if not isinstance(phase, str):
        raise GateError("manifest phase must be a string")
    _phase_name(phase)
    units = manifest.get("units")
    if not isinstance(units, list) or not units or not all(isinstance(item, str) for item in units):
        raise GateError("manifest units must be a non-empty string list")
    for unit in units:
        _phase_name(unit)
    if len({unit.casefold() for unit in units}) != len(units):
        raise GateError("manifest units must be unique, including case-insensitive filesystems")
    for key in ("spec_files", "acceptance_files"):
        values = manifest.get(key)
        if not isinstance(values, list) or not values or not all(
            isinstance(item, str) for item in values
        ):
            raise GateError(f"manifest {key} must be a non-empty string list")
        for item in values:
            _relative(repo, item, require_file=True)
    checks = manifest.get("checks")
    if not isinstance(checks, list) or not checks:
        raise GateError("manifest checks must be non-empty")
    names: set[str] = set()
    for check in checks:
        if not isinstance(check, dict):
            raise GateError("each check must be a table")
        name = check.get("name")
        scope = check.get("scope")
        argv = check.get("argv")
        if not isinstance(name, str) or CHECK_NAME_PATTERN.fullmatch(name) is None:
            raise GateError(
                "check names must use only ASCII letters, digits, underscores, or hyphens"
            )
        if name.upper() in WINDOWS_RESERVED_STEMS:
            raise GateError(f"check name is reserved on Windows: {name}")
        normalized_name = name.casefold()
        if normalized_name in names:
            raise GateError("check names must be unique, including case-insensitive filesystems")
        names.add(normalized_name)
        if scope not in {"unit", "phase", "all"}:
            raise GateError(f"invalid check scope for {name}")
        if not isinstance(argv, list) or not argv or not all(
            isinstance(item, str) and item for item in argv
        ):
            raise GateError(f"check argv must be a non-empty string list: {name}")
    threshold = manifest.get("escalate_after_rejections", 2)
    if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 1:
        raise GateError("escalate_after_rejections must be a positive integer")
    for key in (
        "reviewer_agent",
        "final_reviewer_agent",
        "implementer_agent",
        "escalated_implementer_agent",
    ):
        if not isinstance(manifest.get(key), str) or not manifest[key]:
            raise GateError(f"manifest {key} must be a non-empty string")
    return manifest


def _manifest_for_state(repo: Path, state: dict[str, Any]) -> dict[str, Any]:
    manifest = _load_manifest(repo, str(state["manifest"]))
    if manifest["phase"] != state["phase"]:
        raise GateError("manifest phase no longer matches state")
    return manifest


def _verify_frozen(repo: Path, state: dict[str, Any]) -> None:
    frozen = state.get("frozen_files")
    if not isinstance(frozen, dict) or not frozen:
        raise GateError("state has no frozen file inventory")
    for relative, expected in frozen.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise GateError("malformed frozen file inventory")
        path = _inside_repo(repo, relative, require_file=True)
        actual = _sha256(path)
        if actual != expected:
            raise GateError(f"frozen file changed: {relative}")


def _verify_frozen_at_head(repo: Path, relatives: list[str]) -> None:
    for relative in relatives:
        committed = _run(repo, ["git", "rev-parse", "--verify", f"HEAD:{relative}"], check=False)
        if committed.returncode != 0:
            raise GateError(f"frozen file is not tracked at HEAD: {relative}")
        actual = _run(
            repo,
            ["git", "hash-object", f"--path={relative}", "--", relative],
        ).stdout.strip()
        if actual != committed.stdout.strip():
            raise GateError(f"frozen file content does not match HEAD: {relative}")


def _snapshot(repo: Path) -> dict[str, str]:
    tracked_runs = _run(
        repo,
        ["git", "ls-files", "--", RUNS_RELATIVE.as_posix()],
    ).stdout.strip()
    if tracked_runs:
        raise GateError(".phase-runs must remain untracked")
    ignored_probe = (RUNS_RELATIVE / ".phase-gate-ignore-probe").as_posix()
    ignored = _run(
        repo,
        ["git", "check-ignore", "-q", "--", ignored_probe],
        check=False,
    )
    if ignored.returncode != 0:
        raise GateError(".phase-runs must be ignored before snapshotting")
    with tempfile.TemporaryDirectory(prefix="phase-gate-") as temporary:
        index = Path(temporary) / "index"
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(index)
        _run(repo, ["git", "read-tree", "HEAD"], env=env)
        _run(repo, ["git", "add", "-A", "--", "."], env=env)
        tree = _run(repo, ["git", "write-tree"], env=env).stdout.strip()
    return {"tree": tree}


def _baseline_ref(phase: str, label: str) -> str:
    phase_key = hashlib.sha256(phase.encode("utf-8")).hexdigest()[:20]
    label_key = hashlib.sha256(label.encode("utf-8")).hexdigest()[:20]
    return f"{BASELINE_REF_ROOT}/{phase_key}/{label_key}"


def _durable_snapshot(repo: Path, phase: str, label: str) -> dict[str, str]:
    snapshot = _snapshot(repo)
    head = _run(repo, ["git", "rev-parse", "HEAD"]).stdout.strip()
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "Codex Phase Gate"
    env["GIT_AUTHOR_EMAIL"] = "phase-gate@invalid.local"
    env["GIT_COMMITTER_NAME"] = "Codex Phase Gate"
    env["GIT_COMMITTER_EMAIL"] = "phase-gate@invalid.local"
    commit = _run(
        repo,
        ["git", "commit-tree", snapshot["tree"], "-p", head, "-m", f"phase gate {label}"],
        env=env,
    ).stdout.strip()
    reference = _baseline_ref(phase, label)
    _run(repo, ["git", "update-ref", reference, commit])
    return {"commit": commit, "tree": snapshot["tree"], "ref": reference}


def _verify_durable_snapshot(repo: Path, value: object) -> None:
    if not isinstance(value, dict):
        raise GateError("durable baseline is malformed")
    reference = value.get("ref")
    commit = value.get("commit")
    tree = value.get("tree")
    if not all(isinstance(item, str) and item for item in (reference, commit, tree)):
        raise GateError("durable baseline identity is malformed")
    if not str(reference).startswith(f"{BASELINE_REF_ROOT}/"):
        raise GateError("durable baseline ref is outside the phase namespace")
    actual_commit = _run(repo, ["git", "rev-parse", "--verify", str(reference)]).stdout.strip()
    if actual_commit != commit:
        raise GateError(f"durable baseline ref changed: {reference}")
    actual_tree = _run(repo, ["git", "show", "-s", "--format=%T", str(commit)]).stdout.strip()
    if actual_tree != tree:
        raise GateError(f"durable baseline tree changed: {reference}")


def _verify_durable_baselines(repo: Path, state: dict[str, Any]) -> None:
    _verify_durable_snapshot(repo, state.get("phase_base"))
    refreshes = state.get("baseline_refreshes", [])
    if not isinstance(refreshes, list):
        raise GateError("baseline refresh history is malformed")
    previous_current: object = None
    for index, refresh in enumerate(refreshes):
        if not isinstance(refresh, dict):
            raise GateError("baseline refresh record is malformed")
        if index and refresh.get("previous") != previous_current:
            raise GateError("baseline refresh history chain is broken")
        if not isinstance(refresh.get("reason"), str) or not isinstance(
            refresh.get("refreshed_at"), str
        ):
            raise GateError("baseline refresh metadata is malformed")
        _verify_durable_snapshot(repo, refresh.get("previous"))
        _verify_durable_snapshot(repo, refresh.get("current"))
        previous_current = refresh.get("current")
    if refreshes and previous_current != state.get("phase_base"):
        raise GateError("phase baseline does not match refresh history")
    for unit in state.get("units", []):
        record = state.get("unit_records", {}).get(unit, {})
        if "base" in record:
            _verify_durable_snapshot(repo, record["base"])


def _current_unit(state: dict[str, Any]) -> str:
    index = state.get("current_unit_index")
    units = state.get("units")
    if isinstance(index, bool) or not isinstance(index, int):
        raise GateError("state current unit index is malformed")
    if not isinstance(units, list) or not 0 <= index < len(units):
        raise GateError("state current unit is out of range")
    unit = units[index]
    if not isinstance(unit, str):
        raise GateError("state unit is malformed")
    return unit


def _check_key(scope: str, unit: str | None = None) -> str:
    return f"unit:{unit}" if scope == "unit" else "phase"


def _require_status(state: dict[str, Any], *allowed: str) -> None:
    status = state.get("status")
    if status not in allowed:
        choices = ", ".join(allowed)
        raise GateError(f"state {status!r} does not allow this command; expected {choices}")


def _command_init(repo: Path, args: argparse.Namespace) -> None:
    manifest_relative = _relative(repo, args.manifest, require_file=True)
    manifest = _load_manifest(repo, manifest_relative)
    phase = str(manifest["phase"])
    state_path = _state_path(repo, phase)
    if state_path.exists():
        raise GateError(f"phase state already exists: {phase}")
    status = _run(repo, ["git", "status", "--porcelain"]).stdout.strip()
    if status:
        raise GateError("design baseline must be committed and working tree must be clean")
    frozen_paths = [manifest_relative, *manifest["spec_files"], *manifest["acceptance_files"]]
    normalized = [_relative(repo, item, require_file=True) for item in frozen_paths]
    if len(set(normalized)) != len(normalized):
        raise GateError("frozen file inventory contains duplicates")
    _verify_frozen_at_head(repo, normalized)
    frozen = {item: _sha256(repo / item) for item in normalized}
    phase_snapshot = _durable_snapshot(repo, phase, "phase-base")
    units = list(manifest["units"])
    state: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "phase": phase,
        "manifest": manifest_relative,
        "frozen_files": frozen,
        "phase_base": phase_snapshot,
        "expected_transition_tree": phase_snapshot["tree"],
        "units": units,
        "current_unit_index": 0,
        "status": "ready_for_unit",
        "unit_records": {
            unit: {"status": "pending", "reviews": [], "rejections": 0} for unit in units
        },
        "phase_reviews": [],
        "reviewer_thread_ids": [],
        "latest_checks": {},
        "baseline_refreshes": [],
        "created_at": _now(),
    }
    _write_state(repo, state)
    _json_print(_public_status(state, manifest))


def _public_status(state: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    unit = None if state["status"] == "complete" else _current_unit(state)
    unit_record = state["unit_records"].get(unit, {}) if unit else {}
    threshold = int(manifest.get("escalate_after_rejections", 2))
    rejections = int(unit_record.get("rejections", 0)) if unit else 0
    return {
        "exists": True,
        "phase": state["phase"],
        "status": state["status"],
        "current_unit": unit,
        "completed_units": [
            name
            for name, value in state["unit_records"].items()
            if value.get("status") == "approved"
        ],
        "rejections": rejections,
        "escalation_required": bool(unit and rejections >= threshold),
        "implementer_agent": (
            manifest["escalated_implementer_agent"]
            if state["status"] in {"phase_checks_failed", "final_changes_requested"}
            or (unit and rejections >= threshold)
            else manifest["implementer_agent"]
        ),
        "reviewer_agent": manifest["reviewer_agent"],
        "final_reviewer_agent": manifest["final_reviewer_agent"],
        "baseline_refreshes": len(state.get("baseline_refreshes", [])),
        "updated_at": state.get("updated_at"),
    }


def _command_status(repo: Path, args: argparse.Namespace) -> None:
    phase = _phase_name(args.phase)
    if not _state_path(repo, phase).is_file():
        _json_print({"exists": False, "phase": phase})
        return
    state = _load_state(repo, phase)
    manifest = _manifest_for_state(repo, state)
    _json_print(_public_status(state, manifest))


def _command_verify(repo: Path, args: argparse.Namespace) -> None:
    state = _load_state(repo, _phase_name(args.phase))
    manifest = _manifest_for_state(repo, state)
    _verify_frozen(repo, state)
    _verify_durable_baselines(repo, state)
    _verify_review_history(repo, state)
    result = _public_status(state, manifest)
    result["frozen_files_verified"] = len(state["frozen_files"])
    _json_print(result)


def _command_refresh_baseline(repo: Path, args: argparse.Namespace) -> None:
    state = _load_state(repo, _phase_name(args.phase))
    manifest = _manifest_for_state(repo, state)
    _verify_frozen(repo, state)
    _verify_durable_baselines(repo, state)
    _verify_review_history(repo, state)
    _require_status(state, "ready_for_unit")

    units = state.get("units")
    records = state.get("unit_records")
    if state.get("current_unit_index") != 0 or not isinstance(units, list):
        raise GateError("baseline refresh is allowed only before the first unit")
    if not isinstance(records, dict) or set(records) != set(units):
        raise GateError("unit record inventory is malformed")
    for unit in units:
        record = records.get(unit)
        if record != {"status": "pending", "reviews": [], "rejections": 0}:
            raise GateError("baseline refresh is allowed only before any unit history exists")
    if state.get("phase_reviews") != [] or state.get("reviewer_thread_ids") != []:
        raise GateError("baseline refresh is allowed only before any review history exists")
    if state.get("latest_checks") != {}:
        raise GateError("baseline refresh is allowed only before any check history exists")

    reason = args.reason.strip()
    if not reason or len(reason) > 256 or any(ord(character) < 32 for character in reason):
        raise GateError("baseline refresh reason must be one printable line of 1-256 characters")
    if _run(repo, ["git", "status", "--porcelain"]).stdout.strip():
        raise GateError("baseline refresh requires a clean committed working tree")
    frozen_paths = list(state["frozen_files"])
    _verify_frozen_at_head(repo, frozen_paths)
    current_tree = _snapshot(repo)["tree"]
    if current_tree == state.get("expected_transition_tree"):
        raise GateError("baseline refresh requires a committed tree change")

    previous = state["phase_base"]
    refreshes = state.setdefault("baseline_refreshes", [])
    refreshed = _durable_snapshot(
        repo,
        str(state["phase"]),
        f"phase-base-refresh-{len(refreshes) + 1}",
    )
    refreshes.append(
        {
            "previous": previous,
            "current": refreshed,
            "reason": reason,
            "refreshed_at": _now(),
        }
    )
    state["phase_base"] = refreshed
    state["expected_transition_tree"] = refreshed["tree"]
    _write_state(repo, state)
    result = _public_status(state, manifest)
    result["baseline_refreshed"] = True
    result["baseline_refresh_reason"] = reason
    _json_print(result)


def _command_start_unit(repo: Path, args: argparse.Namespace) -> None:
    state = _load_state(repo, _phase_name(args.phase))
    manifest = _manifest_for_state(repo, state)
    _verify_frozen(repo, state)
    _verify_durable_baselines(repo, state)
    _verify_review_history(repo, state)
    _require_status(state, "ready_for_unit", "changes_requested")
    unit = _current_unit(state)
    if args.unit != unit:
        raise GateError(f"expected current unit {unit}, got {args.unit}")
    current = _snapshot(repo)
    if current["tree"] != state.get("expected_transition_tree"):
        raise GateError("Git-reviewable tree changed before the unit transition was opened")
    record = state["unit_records"][unit]
    if "base" not in record:
        record["base"] = _durable_snapshot(
            repo,
            str(state["phase"]),
            f"unit-{state['current_unit_index']}-{unit}-base",
        )
        record["started_at"] = _now()
    record["status"] = "implementing"
    state["status"] = "implementing"
    state.pop("expected_transition_tree", None)
    state["latest_checks"].pop(_check_key("unit", unit), None)
    _write_state(repo, state)
    _json_print(_public_status(state, manifest))


def _selected_checks(manifest: dict[str, Any], scope: str) -> list[dict[str, Any]]:
    return [check for check in manifest["checks"] if check["scope"] in {scope, "all"}]


def _command_run_checks(repo: Path, args: argparse.Namespace) -> None:
    state = _load_state(repo, _phase_name(args.phase))
    manifest = _manifest_for_state(repo, state)
    _verify_frozen(repo, state)
    _verify_durable_baselines(repo, state)
    _verify_review_history(repo, state)
    if args.scope == "unit":
        _require_status(state, "implementing")
        unit = _current_unit(state)
        target = unit
        log_namespace = Path("units") / unit
    else:
        _require_status(state, "phase_review", "phase_checks_failed", "final_changes_requested")
        unit = None
        target = "phase"
        log_namespace = Path("phase")
        if state["status"] == "phase_review" and "expected_transition_tree" in state:
            current = _snapshot(repo)
            if current["tree"] != state.get("expected_transition_tree"):
                raise GateError("Git-reviewable tree changed before initial phase checks")
    checks = _selected_checks(manifest, args.scope)
    if not checks:
        raise GateError(f"no checks selected for scope: {args.scope}")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    log_dir = _phase_dir(repo, state["phase"]) / "checks" / log_namespace / run_id
    log_dir.mkdir(parents=True, exist_ok=False)
    results: list[dict[str, Any]] = []
    for check in checks:
        argv = list(check["argv"])
        before = _snapshot(repo)
        result = _run(repo, argv, check=False)
        after = _snapshot(repo)
        mutated_reviewable_tree = before["tree"] != after["tree"]
        log_relative = (log_dir / f"{check['name']}.log").relative_to(repo).as_posix()
        (repo / log_relative).write_text(result.stdout, encoding="utf-8")
        log_sha256 = _sha256(repo / log_relative)
        results.append(
            {
                "name": check["name"],
                "argv": argv,
                "exit_code": result.returncode,
                "log": log_relative,
                "log_sha256": log_sha256,
                "tree_before": before["tree"],
                "tree_after": after["tree"],
                "mutated_reviewable_tree": mutated_reviewable_tree,
            }
        )
    logs_intact = all(_check_log_matches(repo, item) for item in results)
    snapshot = _snapshot(repo)
    evidence = {
        "scope": args.scope,
        "target": target,
        "run_id": run_id,
        "completed_at": _now(),
        "logs_intact": logs_intact,
        "all_passed": all(
            item["exit_code"] == 0 and not item["mutated_reviewable_tree"]
            for item in results
        )
        and logs_intact,
        "snapshot": snapshot,
        "results": results,
    }
    state["latest_checks"][_check_key(args.scope, unit)] = evidence
    if args.scope == "phase":
        state.pop("expected_transition_tree", None)
        if evidence["all_passed"] and state["status"] == "phase_checks_failed":
            state["status"] = "phase_review"
        elif not evidence["all_passed"] and state["status"] == "phase_review":
            state["status"] = "phase_checks_failed"
    _write_state(repo, state)
    _json_print(evidence)
    if not evidence["all_passed"]:
        raise GateError("one or more required checks failed")


def _copy_frozen(repo: Path, state: dict[str, Any], bundle: Path) -> None:
    for relative in state["frozen_files"]:
        source = repo / relative
        target = bundle / "design" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def _bundle_inventory(bundle: Path) -> dict[str, str]:
    inventory: dict[str, str] = {}
    for path in sorted(bundle.rglob("*")):
        relative = path.relative_to(bundle)
        if path.is_file() and relative != Path("verdict.md"):
            inventory[relative.as_posix()] = _sha256(path)
    return inventory


def _bundle_sha256(inventory: dict[str, str]) -> str:
    payload = json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _check_log_matches(repo: Path, result: object) -> bool:
    if not isinstance(result, dict):
        return False
    relative = result.get("log")
    expected = result.get("log_sha256")
    if not isinstance(relative, str) or not isinstance(expected, str):
        return False
    path = _inside_repo(repo, relative)
    return path.is_file() and _sha256(path) == expected


def _verify_check_logs(repo: Path, evidence: dict[str, Any]) -> None:
    results = evidence.get("results")
    if not isinstance(results, list) or not results:
        raise GateError("check evidence has no results")
    if not all(_check_log_matches(repo, result) for result in results):
        raise GateError("recorded check log changed after execution")


def _diff(repo: Path, base_tree: str, current_tree: str) -> bytes:
    argv = [
        "git",
        "diff",
        "--binary",
        "--no-ext-diff",
        "--no-textconv",
        "--find-renames",
        base_tree,
        current_tree,
    ]
    result = subprocess.run(
        argv,
        cwd=repo,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace")
        raise GateError(f"command failed ({result.returncode}): {' '.join(argv)}\n{detail}")
    return result.stdout


def _prepare_bundle(
    repo: Path,
    state: dict[str, Any],
    manifest: dict[str, Any],
    *,
    kind: str,
) -> dict[str, Any]:
    unit = _current_unit(state) if kind == "unit" else None
    key = _check_key("unit", unit) if kind == "unit" else _check_key("phase")
    evidence = state["latest_checks"].get(key)
    if not isinstance(evidence, dict) or not evidence.get("all_passed"):
        raise GateError("latest required checks have not passed")
    _verify_check_logs(repo, evidence)
    current = _snapshot(repo)
    if current["tree"] != evidence.get("snapshot", {}).get("tree"):
        raise GateError("working tree changed after the latest successful checks")
    if kind == "unit":
        record = state["unit_records"][unit]
        if current["tree"] == record.get("last_rejected_tree"):
            raise GateError("working tree has not changed since REQUEST_CHANGES")
        attempt = len(record["reviews"]) + 1
        base = record["base"]
        bundle = (
            _phase_dir(repo, state["phase"])
            / "reviews"
            / "units"
            / unit
            / f"attempt-{attempt:03d}"
        )
        reviewer = manifest["reviewer_agent"]
    else:
        if current["tree"] == state.get("last_final_rejected_tree"):
            raise GateError("working tree has not changed since final REQUEST_CHANGES")
        attempt = len(state["phase_reviews"]) + 1
        base = state["phase_base"]
        bundle = _phase_dir(repo, state["phase"]) / "reviews" / "final" / f"attempt-{attempt:03d}"
        reviewer = manifest["final_reviewer_agent"]
    if bundle.exists():
        shutil.rmtree(bundle)
    review_nonce = secrets.token_hex(16)
    try:
        bundle.mkdir(parents=True)
        _copy_frozen(repo, state, bundle)
        (bundle / "changes.diff").write_bytes(
            _diff(repo, str(base["tree"]), current["tree"])
        )
        (bundle / "tests.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        for result in evidence["results"]:
            source = repo / result["log"]
            shutil.copyfile(source, bundle / Path(result["log"]).name)
        request_lines = [
            f"# {state['phase']} {'final' if unit is None else unit} review",
            "",
            f"- Review kind: {kind}",
            f"- Attempt: {attempt}",
            f"- Base tree: {base['tree']}",
            f"- Reviewed tree: {current['tree']}",
            f"- Reviewer agent: {reviewer}",
            f"- Review nonce: {review_nonce}",
            "",
            "Review only this bundle. Apply the configured reviewer contract and echo the nonce.",
        ]
        (bundle / "review-request.md").write_text(
            "\n".join(request_lines) + "\n",
            encoding="utf-8",
        )
    except Exception:
        shutil.rmtree(bundle, ignore_errors=True)
        raise
    inventory = _bundle_inventory(bundle)
    return {
        "kind": kind,
        "unit": unit,
        "attempt": attempt,
        "bundle": bundle.relative_to(repo).as_posix(),
        "reviewer_agent": reviewer,
        "review_nonce": review_nonce,
        "base": base,
        "reviewed_snapshot": current,
        "bundle_files": inventory,
        "bundle_sha256": _bundle_sha256(inventory),
        "prepared_at": _now(),
    }


def _command_prepare_review(repo: Path, args: argparse.Namespace) -> None:
    state = _load_state(repo, _phase_name(args.phase))
    manifest = _manifest_for_state(repo, state)
    _verify_frozen(repo, state)
    _verify_durable_baselines(repo, state)
    _verify_review_history(repo, state)
    _require_status(state, "implementing")
    review = _prepare_bundle(repo, state, manifest, kind="unit")
    state["current_review"] = review
    state["status"] = "awaiting_review"
    _write_state(repo, state)
    _json_print(review)


def _command_prepare_final_review(repo: Path, args: argparse.Namespace) -> None:
    state = _load_state(repo, _phase_name(args.phase))
    manifest = _manifest_for_state(repo, state)
    _verify_frozen(repo, state)
    _verify_durable_baselines(repo, state)
    _verify_review_history(repo, state)
    _require_status(state, "phase_review", "final_changes_requested")
    review = _prepare_bundle(repo, state, manifest, kind="final")
    state["current_review"] = review
    state["status"] = "awaiting_final_review"
    _write_state(repo, state)
    _json_print(review)


def _read_verdict(path_value: str, expected_nonce: str) -> tuple[str, str]:
    path = Path(path_value).resolve()
    if not path.is_file():
        raise GateError(f"verdict file does not exist: {path}")
    text = path.read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0] not in VERDICTS:
        raise GateError("verdict file must start with an exact VERDICT header")
    if len(lines) < 2 or lines[1] != f"REVIEW_NONCE: {expected_nonce}":
        raise GateError("verdict does not attest the prepared review nonce")
    verdict = lines[0].removeprefix("VERDICT: ")
    if verdict == "REQUEST_CHANGES" and len(lines) < 3:
        raise GateError("REQUEST_CHANGES must include at least one substantive finding")
    return verdict, text


def _verify_review_artifact(repo: Path, entry: dict[str, Any]) -> None:
    bundle = _inside_repo(repo, str(entry.get("bundle", "")))
    if not bundle.is_dir():
        raise GateError(f"recorded review bundle is missing: {entry.get('bundle')}")
    inventory = _bundle_inventory(bundle)
    if inventory != entry.get("bundle_files"):
        raise GateError(f"recorded review bundle changed: {entry.get('bundle')}")
    if _bundle_sha256(inventory) != entry.get("bundle_sha256"):
        raise GateError(f"recorded review bundle digest is invalid: {entry.get('bundle')}")
    verdict_path = bundle / "verdict.md"
    if not verdict_path.is_file():
        raise GateError(f"recorded review verdict is missing: {entry.get('bundle')}")
    if _sha256(verdict_path) != entry.get("verdict_sha256"):
        raise GateError(f"recorded review verdict changed: {entry.get('bundle')}")
    verdict, _ = _read_verdict(str(verdict_path), str(entry.get("review_nonce", "")))
    if verdict != entry.get("verdict"):
        raise GateError(f"recorded review verdict identity mismatch: {entry.get('bundle')}")


def _verify_review_history(repo: Path, state: dict[str, Any]) -> None:
    entries: list[dict[str, Any]] = []
    for unit in state["units"]:
        reviews = state["unit_records"][unit].get("reviews", [])
        if not isinstance(reviews, list):
            raise GateError(f"malformed review history for unit: {unit}")
        entries.extend(reviews)
    phase_reviews = state.get("phase_reviews", [])
    if not isinstance(phase_reviews, list):
        raise GateError("malformed final review history")
    entries.extend(phase_reviews)
    recorded_ids: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise GateError("malformed review history entry")
        _verify_review_artifact(repo, entry)
        thread_id = entry.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            raise GateError("recorded review has no thread ID")
        recorded_ids.append(thread_id)
    if len(set(recorded_ids)) != len(recorded_ids):
        raise GateError("recorded review history reuses a thread ID")
    if recorded_ids != state.get("reviewer_thread_ids"):
        raise GateError("reviewer thread inventory does not match review history")


def _record_review(
    repo: Path,
    state: dict[str, Any],
    args: argparse.Namespace,
    *,
    kind: str,
) -> None:
    thread_id = args.thread_id.strip()
    if not thread_id or len(thread_id) > 200:
        raise GateError("reviewer thread ID must be non-empty and at most 200 characters")
    if thread_id in state["reviewer_thread_ids"]:
        raise GateError("reviewer thread ID has already been used")
    current = state.get("current_review")
    if not isinstance(current, dict) or current.get("kind") != kind:
        raise GateError("current review identity does not match command")
    bundle = _inside_repo(repo, current["bundle"])
    actual_inventory = _bundle_inventory(bundle)
    if actual_inventory != current.get("bundle_files"):
        raise GateError("review bundle contents changed after preparation")
    if _bundle_sha256(actual_inventory) != current.get("bundle_sha256"):
        raise GateError("review bundle digest is invalid")
    snapshot = _snapshot(repo)
    if snapshot["tree"] != current["reviewed_snapshot"]["tree"]:
        raise GateError("working tree changed while review was in progress")
    verdict, text = _read_verdict(args.verdict_file, str(current["review_nonce"]))
    verdict_target = bundle / "verdict.md"
    if verdict_target.exists():
        existing_verdict, existing_text = _read_verdict(
            str(verdict_target), str(current["review_nonce"])
        )
        if existing_verdict != verdict or existing_text != text:
            raise GateError("orphan review verdict does not match the supplied verdict")
    else:
        verdict_target.write_text(text, encoding="utf-8")
    entry = {
        **current,
        "thread_id": thread_id,
        "verdict": verdict,
        "verdict_sha256": _sha256(verdict_target),
        "recorded_at": _now(),
    }
    state["reviewer_thread_ids"].append(thread_id)
    if kind == "unit":
        unit = _current_unit(state)
        record = state["unit_records"][unit]
        record["reviews"].append(entry)
        if verdict == "APPROVE":
            record["status"] = "approved"
            record["approved_tree"] = current["reviewed_snapshot"]["tree"]
            record.pop("last_rejected_tree", None)
            state["status"] = "unit_approved"
        else:
            record["status"] = "changes_requested"
            record["rejections"] += 1
            record["last_rejected_tree"] = current["reviewed_snapshot"]["tree"]
            state["expected_transition_tree"] = current["reviewed_snapshot"]["tree"]
            state["status"] = "changes_requested"
    else:
        state["phase_reviews"].append(entry)
        if verdict == "APPROVE":
            state["approved_tree"] = current["reviewed_snapshot"]["tree"]
            state.pop("last_final_rejected_tree", None)
            state["status"] = "complete"
        else:
            state["last_final_rejected_tree"] = current["reviewed_snapshot"]["tree"]
            state["status"] = "final_changes_requested"
    state.pop("current_review", None)
    _write_state(repo, state)


def _command_record_review(repo: Path, args: argparse.Namespace) -> None:
    state = _load_state(repo, _phase_name(args.phase))
    manifest = _manifest_for_state(repo, state)
    _verify_frozen(repo, state)
    _verify_durable_baselines(repo, state)
    _verify_review_history(repo, state)
    _require_status(state, "awaiting_review")
    _record_review(repo, state, args, kind="unit")
    _json_print(_public_status(state, manifest))


def _command_record_final_review(repo: Path, args: argparse.Namespace) -> None:
    state = _load_state(repo, _phase_name(args.phase))
    manifest = _manifest_for_state(repo, state)
    _verify_frozen(repo, state)
    _verify_durable_baselines(repo, state)
    _verify_review_history(repo, state)
    _require_status(state, "awaiting_final_review")
    _record_review(repo, state, args, kind="final")
    _json_print(_public_status(state, manifest))


def _command_advance(repo: Path, args: argparse.Namespace) -> None:
    state = _load_state(repo, _phase_name(args.phase))
    manifest = _manifest_for_state(repo, state)
    _verify_frozen(repo, state)
    _verify_durable_baselines(repo, state)
    _verify_review_history(repo, state)
    _require_status(state, "unit_approved")
    unit = _current_unit(state)
    record = state["unit_records"][unit]
    if _snapshot(repo)["tree"] != record.get("approved_tree"):
        raise GateError("working tree changed after unit approval")
    if state["current_unit_index"] + 1 < len(state["units"]):
        state["current_unit_index"] += 1
        state["status"] = "ready_for_unit"
    else:
        state["status"] = "phase_review"
    state["expected_transition_tree"] = record["approved_tree"]
    _write_state(repo, state)
    _json_print(_public_status(state, manifest))


def _command_assert_complete(repo: Path, args: argparse.Namespace) -> None:
    state = _load_state(repo, _phase_name(args.phase))
    manifest = _manifest_for_state(repo, state)
    _verify_frozen(repo, state)
    _verify_durable_baselines(repo, state)
    _verify_review_history(repo, state)
    _require_status(state, "complete")
    if any(record.get("status") != "approved" for record in state["unit_records"].values()):
        raise GateError("not every unit is approved")
    if not state["phase_reviews"] or state["phase_reviews"][-1].get("verdict") != "APPROVE":
        raise GateError("latest final review is not approved")
    if _snapshot(repo)["tree"] != state.get("approved_tree"):
        raise GateError("working tree changed after final approval")
    result = _public_status(state, manifest)
    result["completion_verified"] = True
    result["approved_tree"] = state["approved_tree"]
    _json_print(result)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deterministic Codex phase review gate")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--manifest", required=True)

    refresh = subparsers.add_parser("refresh-baseline")
    refresh.add_argument("--phase", required=True)
    refresh.add_argument("--reason", required=True)

    for name in ("status", "verify", "prepare-review", "prepare-final-review", "advance"):
        command = subparsers.add_parser(name)
        command.add_argument("--phase", required=True)

    start = subparsers.add_parser("start-unit")
    start.add_argument("--phase", required=True)
    start.add_argument("--unit", required=True)

    checks = subparsers.add_parser("run-checks")
    checks.add_argument("--phase", required=True)
    checks.add_argument("--scope", choices=("unit", "phase"), required=True)

    for name in ("record-review", "record-final-review"):
        command = subparsers.add_parser(name)
        command.add_argument("--phase", required=True)
        command.add_argument("--thread-id", required=True)
        command.add_argument("--verdict-file", required=True)

    complete = subparsers.add_parser("assert-complete")
    complete.add_argument("--phase", required=True)
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    repo = _repo_root()
    commands = {
        "init": _command_init,
        "status": _command_status,
        "verify": _command_verify,
        "refresh-baseline": _command_refresh_baseline,
        "start-unit": _command_start_unit,
        "run-checks": _command_run_checks,
        "prepare-review": _command_prepare_review,
        "record-review": _command_record_review,
        "advance": _command_advance,
        "prepare-final-review": _command_prepare_final_review,
        "record-final-review": _command_record_final_review,
        "assert-complete": _command_assert_complete,
    }
    try:
        commands[args.command](repo, args)
    except GateError as exc:
        print(f"phase gate error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
