#!/usr/bin/env python3
"""Independently validate raw attempts, canonical summaries, and run provenance."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from _common import (
    REPO_ROOT,
    SCHEMA_VERSION,
    canonical_hash,
    canonical_json_bytes,
    csv_bytes,
    git_state,
    latest_execution_dirs,
    load_json,
    method_provenance_from_snapshot,
    select_logical_execution,
    sha256_file,
    source_snapshot,
    utc_now,
    validate_attempt,
    validate_hash_manifests,
    write_json_atomic,
)
from collect_canonical_results import build_collection
from run_canonical_pairwise import _validate_recorded_plan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--summary-dir", type=Path)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--json-out", type=Path)
    return parser


def _expected_map(manifest: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    configuration = manifest.get("base_configuration", {})
    return {int(circuit["index"]): circuit for circuit in configuration.get("circuits", [])}


def _check_manifest(manifest: Mapping[str, Any], run_dir: Path) -> list[str]:
    errors: list[str] = []
    configuration = manifest.get("base_configuration")
    if not isinstance(configuration, Mapping):
        return ["run_manifest.json has no base_configuration object"]
    if manifest.get("base_configuration_hash") != canonical_hash(configuration):
        errors.append("run_manifest base_configuration_hash mismatch")
    current_git = git_state()
    if not current_git.get("available"):
        errors.append("current Git state is unavailable")
    else:
        if current_git.get("commit") != configuration.get("git_commit"):
            errors.append("current Git commit differs from the run's locked commit")
        if current_git.get("dirty"):
            errors.append("current Git worktree is dirty during validation")
    recorded_snapshot = configuration.get("source_snapshot")
    current_snapshot = source_snapshot()
    if canonical_hash(recorded_snapshot) != canonical_hash(current_snapshot):
        errors.append("canonical/compiler/script source snapshot differs from the locked run inputs")
    if canonical_hash(configuration.get("host_fingerprint")) != configuration.get("host_fingerprint_hash"):
        errors.append("locked host_fingerprint_hash mismatch")
    benchmark_dir = Path(str(configuration.get("benchmark_dir", "")))
    if not benchmark_dir.is_absolute():
        benchmark_dir = REPO_ROOT / benchmark_dir
    if benchmark_dir.name != "local64":
        errors.append(f"manifest benchmark directory is not explicit local64: {benchmark_dir}")
    circuits = configuration.get("circuits", [])
    seen_names: set[str] = set()
    seen_indices: set[int] = set()
    for circuit in circuits:
        try:
            index = int(circuit["index"])
            name = str(circuit["name"])
            expected_hash = str(circuit["sha256"])
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"malformed circuit entry in run manifest: {exc}")
            continue
        if index in seen_indices:
            errors.append(f"duplicate circuit index in run manifest: {index}")
        if name in seen_names:
            errors.append(f"duplicate circuit name in run manifest: {name}")
        seen_indices.add(index)
        seen_names.add(name)
        source = benchmark_dir / name
        if not source.is_file():
            errors.append(f"canonical QASM source is missing: {source}")
        elif sha256_file(source) != expected_hash:
            errors.append(f"canonical QASM source hash changed: {source}")
    list_path = Path(str(configuration.get("benchmark_list", "")))
    if not list_path.is_absolute():
        list_path = REPO_ROOT / list_path
    if not list_path.is_file():
        errors.append(f"benchmark list is missing: {list_path}")
    elif sha256_file(list_path) != configuration.get("benchmark_list_sha256"):
        errors.append(f"benchmark list hash changed: {list_path}")
    compiler_entry = Path(str(configuration.get("compiler_entry", "")))
    if not compiler_entry.is_absolute():
        compiler_entry = REPO_ROOT / compiler_entry
    if not compiler_entry.is_file():
        errors.append(f"compiler entry is missing: {compiler_entry}")
    elif sha256_file(compiler_entry) != configuration.get("compiler_entry_sha256"):
        errors.append(f"compiler entry hash changed since run: {compiler_entry}")
    return errors


def _expected_attempt(
    status: Mapping[str, Any], manifest: Mapping[str, Any], circuits: Mapping[int, Mapping[str, Any]]
) -> dict[str, Any] | None:
    try:
        index = int(status["circuit_index"])
        circuit = circuits[index]
    except (KeyError, TypeError, ValueError):
        return None
    configuration = manifest["base_configuration"]
    method = status.get("method") if status.get("method") in configuration.get("methods", []) else "<invalid>"
    try:
        method_provenance = method_provenance_from_snapshot(method, configuration.get("source_snapshot", {}))
    except ValueError:
        method_provenance = None
    return {
        "circuit_index": index,
        "circuit_name": circuit["name"],
        "input_sha256": circuit["sha256"],
        "method": method,
        "repetition": status.get("repetition"),
        "seed": configuration.get("seed"),
        "timeout_sec": configuration.get("timeout_sec"),
        "git_commit": configuration.get("git_commit"),
        "host_fingerprint_hash": configuration.get("host_fingerprint_hash"),
        "method_provenance": method_provenance,
    }


def _check_derived(collection: Mapping[str, Any], summary_dir: Path) -> list[str]:
    errors: list[str] = []
    expected_objects = {
        "attempts.json": collection["attempts_json"],
        "summary.json": collection["summary_json"],
    }
    expected_bytes = {
        "attempts.csv": collection["attempts_csv"],
        "summary.csv": collection["summary_csv"],
    }
    for name, expected in expected_objects.items():
        path = summary_dir / name
        try:
            observed = load_json(path)
        except (OSError, ValueError) as exc:
            errors.append(f"cannot read derived {path}: {exc}")
        else:
            if canonical_hash(observed) != canonical_hash(expected):
                errors.append(f"derived file does not match raw-data reconstruction: {path}")
    for name, expected in expected_bytes.items():
        path = summary_dir / name
        try:
            observed = path.read_bytes()
        except OSError as exc:
            errors.append(f"cannot read derived {path}: {exc}")
        else:
            if observed != expected:
                errors.append(f"derived file does not match raw-data reconstruction: {path}")
    manifest_path = summary_dir / "derived_manifest.json"
    try:
        derived_manifest = load_json(manifest_path)
    except (OSError, ValueError) as exc:
        errors.append(f"cannot read {manifest_path}: {exc}")
        return errors
    for name in (*expected_objects, *expected_bytes):
        path = summary_dir / name
        if not path.is_file():
            continue
        entry = derived_manifest.get("files", {}).get(name, {})
        if entry.get("sha256") != sha256_file(path) or entry.get("size_bytes") != path.stat().st_size:
            errors.append(f"derived manifest hash/size mismatch for {path}")
    expected_collection_hash = canonical_hash(
        {"attempts": collection["attempts_json"], "summary": collection["summary_json"]}
    )
    if derived_manifest.get("collection_hash") != expected_collection_hash:
        errors.append("derived_manifest collection_hash mismatch")
    return errors


def _check_optional_fidelity(run_dir: Path, summary_dir: Path) -> list[str]:
    fidelity_dir = summary_dir / "fidelity"
    if not fidelity_dir.exists():
        return []
    from recompute_fidelity import build_fidelity

    errors: list[str] = []
    config_path = REPO_ROOT / "configs" / "fidelity_models.json"
    try:
        result = build_fidelity(run_dir, config_path)
    except Exception as exc:
        return [f"cannot reconstruct fidelity sensitivity: {type(exc).__name__}: {exc}"]
    expected_objects = {
        "fidelity_models.used.json": {
            "source_sha256": result["config_sha256"],
            "model_config": result["config"],
        },
        "fidelity_sensitivity.json": {
            key: result[key]
            for key in (
                "schema_version",
                "score_name",
                "scope",
                "config_sha256",
                "scenario_count",
                "attempt_rows",
            )
        },
        "fidelity_summary.json": {
            key: result[key]
            for key in (
                "schema_version",
                "score_name",
                "scope",
                "config_sha256",
                "scenario_count",
                "summary_rows",
            )
        },
    }
    expected_bytes = {
        "fidelity_sensitivity.csv": csv_bytes(result["attempt_rows"]),
        "fidelity_summary.csv": csv_bytes(result["summary_rows"]),
    }
    for name, expected in expected_objects.items():
        path = fidelity_dir / name
        try:
            observed = load_json(path)
        except (OSError, ValueError) as exc:
            errors.append(f"cannot read fidelity output {path}: {exc}")
        else:
            if canonical_hash(observed) != canonical_hash(expected):
                errors.append(f"fidelity output does not match raw-data reconstruction: {path}")
    for name, expected in expected_bytes.items():
        path = fidelity_dir / name
        try:
            observed = path.read_bytes()
        except OSError as exc:
            errors.append(f"cannot read fidelity output {path}: {exc}")
        else:
            if observed != expected:
                errors.append(f"fidelity output does not match raw-data reconstruction: {path}")
    manifest_path = fidelity_dir / "fidelity_manifest.json"
    try:
        manifest = load_json(manifest_path)
    except (OSError, ValueError) as exc:
        errors.append(f"cannot read {manifest_path}: {exc}")
        return errors
    for name in (*expected_objects, *expected_bytes):
        path = fidelity_dir / name
        if not path.is_file():
            continue
        entry = manifest.get("files", {}).get(name, {})
        if entry.get("sha256") != sha256_file(path) or entry.get("size_bytes") != path.stat().st_size:
            errors.append(f"fidelity manifest hash/size mismatch for {path}")
    expected_result_hash = canonical_hash(
        {
            "attempts": expected_objects["fidelity_sensitivity.json"],
            "summary": expected_objects["fidelity_summary.json"],
        }
    )
    if manifest.get("result_hash") != expected_result_hash:
        errors.append("fidelity_manifest result_hash mismatch")
    return errors


def _completeness(
    manifest: Mapping[str, Any], grouped: Mapping[tuple[int, int, str], Sequence[Path]]
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    configuration = manifest.get("base_configuration", {})
    circuits = configuration.get("circuits", [])
    methods = configuration.get("methods", [])
    repetitions = int(manifest.get("requested_repetitions", 1))
    logical_records = manifest.get("logical_attempts", {})
    for circuit in circuits:
        index = int(circuit["index"])
        for repetition in range(repetitions):
            for method in methods:
                identity = (index, repetition, method)
                logical_key = f"{index:04d}/r{repetition:03d}/{method}"
                record = logical_records.get(logical_key, {})
                if identity not in grouped and record.get("state") != "not_scheduled_threshold":
                    errors.append(f"missing logical attempt: {logical_key}")
    return not errors, errors


def _check_logical_manifest(
    manifest: Mapping[str, Any], grouped: Mapping[tuple[int, int, str], Sequence[Path]], run_dir: Path
) -> list[str]:
    errors: list[str] = []
    logical_records = manifest.get("logical_attempts", {})
    if not isinstance(logical_records, Mapping):
        return ["run_manifest logical_attempts must be an object"]
    known_keys: set[str] = set()
    for (index, repetition, method), paths in grouped.items():
        key = f"{index:04d}/r{repetition:03d}/{method}"
        known_keys.add(key)
        record = logical_records.get(key)
        if not isinstance(record, Mapping):
            errors.append(f"run_manifest has no logical record for {key}")
            continue
        try:
            selected = select_logical_execution(paths)
            status = load_json(selected / "status.json")
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
            continue
        if record.get("state") != status.get("outcome"):
            errors.append(f"run_manifest state disagrees with selected status for {key}")
        expected_path = selected.relative_to(run_dir).as_posix()
        if record.get("selected_execution") != expected_path:
            errors.append(f"run_manifest selected_execution disagrees for {key}")
        for field in ("external_elapsed_seconds", "failure_kind"):
            if record.get(field) != status.get(field):
                errors.append(f"run_manifest {field} disagrees with selected status for {key}")
    for key, record in logical_records.items():
        if key not in known_keys and not (
            isinstance(record, Mapping) and record.get("state") == "not_scheduled_threshold"
        ):
            errors.append(f"run_manifest contains an unbound logical record: {key}")
    return errors


def _check_repeat_threshold_policy(
    manifest: Mapping[str, Any], grouped: Mapping[tuple[int, int, str], Sequence[Path]]
) -> list[str]:
    errors: list[str] = []
    repetitions = int(manifest.get("requested_repetitions", 1))
    if repetitions <= 1:
        return errors
    threshold = manifest.get("repeat_threshold_sec")
    configuration = manifest.get("base_configuration", {})
    circuits = configuration.get("circuits", [])
    methods = list(configuration.get("methods", []))
    logical_records = manifest.get("logical_attempts", {})
    if threshold is not None and set(methods) != {"forceshuttle", "dasatom"}:
        errors.append("repeat threshold policy requires both forceshuttle and dasatom")
    for circuit in circuits:
        index = int(circuit["index"])
        eligible = threshold is None
        pair_max: float | None = None
        expected_skip_reason: str | None = None
        if threshold is not None:
            repetition_zero: list[Mapping[str, Any]] = []
            for method in methods:
                paths = grouped.get((index, 0, method))
                if not paths:
                    expected_skip_reason = f"repetition 0 did not succeed for {method}"
                    break
                try:
                    selected = select_logical_execution(paths)
                    status = load_json(selected / "status.json")
                except (OSError, ValueError) as exc:
                    errors.append(str(exc))
                    expected_skip_reason = f"repetition 0 did not succeed for {method}"
                    break
                if status.get("outcome") != "succeeded":
                    expected_skip_reason = f"repetition 0 did not succeed for {method}"
                    break
                repetition_zero.append(status)
            if len(repetition_zero) == len(methods):
                pair_max = max(float(status["external_elapsed_seconds"]) for status in repetition_zero)
                eligible = pair_max <= float(threshold)
                if not eligible:
                    expected_skip_reason = (
                        f"pair max external time {pair_max:.9f}s exceeds threshold {float(threshold):.9f}s"
                    )
        for repetition in range(1, repetitions):
            for method in methods:
                identity = (index, repetition, method)
                key = f"{index:04d}/r{repetition:03d}/{method}"
                record = logical_records.get(key, {})
                if eligible:
                    if identity not in grouped:
                        errors.append(f"eligible repeat is missing and cannot be threshold-skipped: {key}")
                    if record.get("state") == "not_scheduled_threshold":
                        errors.append(f"eligible repeat was falsely marked not_scheduled_threshold: {key}")
                else:
                    if identity in grouped:
                        errors.append(f"ineligible repeat was executed despite threshold policy: {key}")
                    if record.get("state") != "not_scheduled_threshold":
                        errors.append(f"ineligible repeat lacks not_scheduled_threshold record: {key}")
                    if record.get("pair_max_external_seconds") != pair_max:
                        errors.append(f"threshold evidence pair_max_external_seconds mismatch: {key}")
                    if record.get("reason") != expected_skip_reason:
                        errors.append(f"threshold evidence reason mismatch: {key}")
    return errors


def _check_plan_bounds(
    manifest: Mapping[str, Any], grouped: Mapping[tuple[int, int, str], Sequence[Path]]
) -> list[str]:
    errors = _validate_recorded_plan(manifest)
    repetitions = int(manifest.get("requested_repetitions", 0))
    if repetitions < 1:
        errors.append("requested_repetitions must be positive")
        return errors
    for index, repetition, method in grouped:
        if repetition < 0 or repetition >= repetitions:
            errors.append(
                f"attempt lies outside the locked repetition plan: {index:04d}/r{repetition:03d}/{method}"
            )
    for key in manifest.get("logical_attempts", {}):
        parts = str(key).split("/")
        try:
            repetition = int(parts[1][1:]) if len(parts) == 3 and parts[1].startswith("r") else -1
        except ValueError:
            repetition = -1
        if repetition < 0 or repetition >= repetitions:
            errors.append(f"logical record lies outside the locked repetition plan: {key}")
    return errors


def validate_run(run_dir: Path, summary_dir: Path, require_complete: bool) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    summary_dir = summary_dir.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    manifest_path = run_dir / "run_manifest.json"
    try:
        manifest = load_json(manifest_path)
    except (OSError, ValueError) as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "passed": False,
            "pilot_gate_passed": False,
            "complete": False,
            "errors": [f"cannot read {manifest_path}: {exc}"],
            "warnings": [],
        }
    errors.extend(_check_manifest(manifest, run_dir))
    circuits = _expected_map(manifest)
    grouped = latest_execution_dirs(run_dir)
    errors.extend(_check_plan_bounds(manifest, grouped))
    all_status_paths = sorted((run_dir / "attempts").glob("**/execution_*/status.json"))
    attempt_reports: list[dict[str, Any]] = []
    for status_path in all_status_paths:
        attempt_dir = status_path.parent
        try:
            status = load_json(status_path)
        except (OSError, ValueError) as exc:
            errors.append(f"cannot read {status_path}: {exc}")
            continue
        expected = _expected_attempt(status, manifest, circuits)
        if expected is None:
            errors.append(f"attempt references a circuit absent from run manifest: {attempt_dir}")
        report = validate_attempt(attempt_dir, expected=expected)
        errors.extend(report["errors"])
        warnings.extend(report["warnings"])
        attempt_reports.append(
            {
                "attempt_dir": attempt_dir.relative_to(run_dir).as_posix(),
                "outcome": status.get("outcome"),
                "failure_kind": status.get("failure_kind"),
                "passed": report["passed"],
                "errors": report["errors"],
                "warnings": report["warnings"],
            }
        )
    execution_dirs = sorted((run_dir / "attempts").glob("**/execution_*"))
    status_parents = {path.parent for path in all_status_paths}
    for directory in execution_dirs:
        if directory.is_dir() and directory not in status_parents:
            errors.append(f"execution directory has no status.json: {directory}")
    for abandoned_dir in sorted((run_dir / "attempts").glob("**/abandoned/*")):
        if not abandoned_dir.is_dir():
            continue
        abandonment_path = abandoned_dir / "abandonment.json"
        if not abandonment_path.is_file():
            errors.append(f"abandoned execution has no abandonment.json: {abandoned_dir}")
            continue
        errors.extend(validate_hash_manifests(abandoned_dir))
        try:
            abandonment = load_json(abandonment_path)
        except (OSError, ValueError) as exc:
            errors.append(f"cannot read {abandonment_path}: {exc}")
        else:
            if not abandonment.get("reason") or not abandonment.get("original_execution_name"):
                errors.append(f"malformed abandonment record: {abandonment_path}")
            warnings.append(
                f"preserved abandoned execution {abandoned_dir.relative_to(run_dir).as_posix()} "
                f"({abandonment.get('reason')})"
            )

    complete, completeness_errors = _completeness(manifest, grouped)
    errors.extend(_check_logical_manifest(manifest, grouped, run_dir))
    errors.extend(_check_repeat_threshold_policy(manifest, grouped))
    if require_complete:
        errors.extend(completeness_errors)
    else:
        warnings.extend(completeness_errors)

    collection = build_collection(run_dir)
    errors.extend(collection["errors"])
    warnings.extend(collection["warnings"])
    errors.extend(_check_derived(collection, summary_dir))
    errors.extend(_check_optional_fidelity(run_dir, summary_dir))

    selected_statuses: list[Mapping[str, Any]] = []
    for paths in grouped.values():
        try:
            selected = select_logical_execution(paths)
            selected_statuses.append(load_json(selected / "status.json"))
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
    status_counts = Counter(str(status.get("outcome")) for status in selected_statuses)
    failure_kind_counts = Counter(
        str(status.get("failure_kind"))
        for status in selected_statuses
        if status.get("failure_kind") is not None
    )
    integrity_passed = not errors
    non_timeout_failures = [
        status
        for status in selected_statuses
        if status.get("outcome") not in {"succeeded", "timeout"}
        or (status.get("outcome") == "timeout" and status.get("failure_kind") != "timeout")
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_now(),
        "run_dir": str(run_dir),
        "summary_dir": str(summary_dir),
        "passed": integrity_passed,
        "pilot_gate_passed": integrity_passed and complete and not non_timeout_failures,
        "complete": complete,
        "logical_attempt_count": len(selected_statuses),
        "execution_count": len(attempt_reports),
        "status_counts": dict(sorted(status_counts.items())),
        "failure_kind_counts": dict(sorted(failure_kind_counts.items())),
        "errors": errors,
        "warnings": warnings,
        "attempt_reports": attempt_reports,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_dir = args.run_dir.resolve()
    summary_dir = args.summary_dir.resolve() if args.summary_dir else run_dir / "derived"
    report = validate_run(run_dir, summary_dir, args.require_complete)
    if args.json_out:
        write_json_atomic(args.json_out, report)
    print(canonical_json_bytes(report).decode("utf-8"), end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
