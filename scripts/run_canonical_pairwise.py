#!/usr/bin/env python3
"""Run ForceShuttle and DasAtom sequentially with auditable raw outputs."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Mapping, Sequence

from _common import (
    DEFAULT_METHODS,
    METHODS,
    SUPPORTED_METHODS,
    REPO_ROOT,
    SCHEMA_VERSION,
    RunLock,
    canonical_hash,
    compute_metrics,
    git_state,
    host_fingerprint,
    host_identity,
    load_json,
    normalize_methods,
    method_provenance_from_snapshot,
    payload_provenance_errors,
    process_matches_start,
    read_benchmark_list,
    relative_to_repo,
    safe_component,
    sha256_file,
    source_snapshot,
    utc_now,
    validate_attempt,
    verify_compiler_output,
    write_bytes_atomic,
    write_hash_manifests,
    write_json_atomic,
)


DETERMINISTIC_ENV = {
    "PYTHONHASHSEED": "0",
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "RAYON_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
    "BLIS_NUM_THREADS": "1",
    "QISKIT_PARALLEL": "FALSE",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=REPO_ROOT / "results" / "canonical-run")
    parser.add_argument("--benchmark-dir", type=Path, default=REPO_ROOT / "benchmarks" / "local64")
    parser.add_argument("--list", dest="list_path", type=Path, default=REPO_ROOT / "configs" / "local64.txt")
    parser.add_argument("--methods", nargs="+", default=list(DEFAULT_METHODS))
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--timeout-sec", type=float, default=10800.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--repeat-threshold-sec",
        type=float,
        default=None,
        help="Only repeat circuits whose repetition-0 pair both succeeded within this external-time threshold.",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-circuits", type=int)
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--force-unlock", action="store_true")
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if args.repetitions < 1:
        raise ValueError("--repetitions must be at least 1")
    if args.timeout_sec <= 0:
        raise ValueError("--timeout-sec must be positive")
    if args.max_circuits is not None and args.max_circuits < 1:
        raise ValueError("--max-circuits must be positive")
    if args.repeat_threshold_sec is not None:
        if args.repeat_threshold_sec <= 0:
            raise ValueError("--repeat-threshold-sec must be positive")
        if set(args.methods) != set(DEFAULT_METHODS):
            raise ValueError("--repeat-threshold-sec requires both forceshuttle and dasatom")


def method_order(circuit_index: int, repetition: int, methods: Sequence[str]) -> list[str]:
    ordered = [method for method in SUPPORTED_METHODS if method in methods]
    offset = (circuit_index + repetition) % len(ordered)
    ordered = ordered[offset:] + ordered[:offset]
    return ordered


def _base_configuration(
    args: argparse.Namespace,
    circuits: Sequence[Mapping[str, Any]],
    methods: Sequence[str],
    compiler_entry: Path,
    git: Mapping[str, Any],
) -> dict[str, Any]:
    locked_sources = source_snapshot()
    locked_host = host_fingerprint()
    return {
        "schema_version": SCHEMA_VERSION,
        "benchmark_dir": relative_to_repo(args.benchmark_dir),
        "benchmark_list": relative_to_repo(args.list_path),
        "benchmark_list_sha256": sha256_file(args.list_path),
        "circuits": [
            {key: circuit[key] for key in ("index", "name", "sha256", "size_bytes")} for circuit in circuits
        ],
        "methods": list(methods),
        "timeout_sec": args.timeout_sec,
        "seed": args.seed,
        "compiler_entry": relative_to_repo(compiler_entry),
        "compiler_entry_sha256": sha256_file(compiler_entry),
        "git_commit": git.get("commit"),
        "source_snapshot": locked_sources,
        "host_fingerprint": locked_host,
        "host_fingerprint_hash": canonical_hash(locked_host),
    }


def _manifest_compatible(recorded: Mapping[str, Any], current: Mapping[str, Any]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    recorded_config = recorded.get("base_configuration", {})
    for key, value in current.items():
        if recorded_config.get(key) != value:
            errors.append(f"resume configuration mismatch for {key}")
    return not errors, errors


def _plan_record(repetitions: int, threshold: float | None, sequence: int) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "requested_repetitions": repetitions,
        "repeat_threshold_sec": threshold,
        "recorded_at_utc": utc_now(),
    }


def _validate_recorded_plan(manifest: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    initial_plan = manifest.get("initial_plan")
    history = manifest.get("plan_history")
    if not isinstance(initial_plan, Mapping):
        return ["run manifest has no immutable initial_plan"]
    if manifest.get("initial_plan_hash") != canonical_hash(initial_plan):
        errors.append("run manifest initial_plan_hash mismatch")
    if not isinstance(history, list) or not history:
        errors.append("run manifest has no plan_history")
        return errors
    if manifest.get("plan_history_hash") != canonical_hash(history):
        errors.append("run manifest plan_history_hash mismatch")
    for index, entry in enumerate(history):
        if entry.get("sequence") != index:
            errors.append("run manifest plan_history sequence is not contiguous")
        if index and int(entry.get("requested_repetitions", 0)) < int(
            history[index - 1].get("requested_repetitions", 0)
        ):
            errors.append("run manifest plan_history decreases repetitions")
    if int(initial_plan.get("requested_repetitions", 0)) != int(
        history[0].get("requested_repetitions", -1)
    ) or initial_plan.get("repeat_threshold_sec") != history[0].get("repeat_threshold_sec"):
        errors.append("run manifest initial_plan disagrees with first plan_history entry")
    last = history[-1]
    if int(manifest.get("requested_repetitions", 0)) != int(last.get("requested_repetitions", -1)):
        errors.append("run manifest requested_repetitions disagrees with plan_history")
    if manifest.get("repeat_threshold_sec") != last.get("repeat_threshold_sec"):
        errors.append("run manifest repeat_threshold_sec disagrees with plan_history")
    return errors


def _logical_key(circuit_index: int, repetition: int, method: str) -> str:
    return f"{circuit_index:04d}/r{repetition:03d}/{method}"


def _logical_root(run_dir: Path, circuit: Mapping[str, Any], repetition: int, method: str) -> Path:
    circuit_part = f"{int(circuit['index']):04d}__{safe_component(Path(str(circuit['name'])).stem)}"
    return run_dir / "attempts" / circuit_part / f"repeat_{repetition:03d}" / method


def _execution_number(logical_root: Path) -> int:
    numbers: list[int] = []
    for path in logical_root.glob("execution_*"):
        try:
            numbers.append(int(path.name.rsplit("_", 1)[1]))
        except (IndexError, ValueError):
            continue
    for record_path in sorted((logical_root / "abandoned").glob("*/abandonment.json")):
        try:
            original_name = str(load_json(record_path)["original_execution_name"])
            numbers.append(int(original_name.rsplit("_", 1)[1]))
        except (OSError, ValueError, KeyError, IndexError) as exc:
            raise RuntimeError(f"cannot determine execution number from {record_path}: {exc}") from exc
    return max(numbers, default=-1) + 1


def _quarantine_incomplete_executions(logical_root: Path, expected: Mapping[str, Any]) -> list[Path]:
    quarantined: list[Path] = []
    for path in sorted(logical_root.glob("execution_*")):
        status_path = path / "status.json"
        status: Mapping[str, Any] | None = None
        try:
            status = load_json(status_path)
        except (OSError, ValueError):
            reason = "missing_or_unreadable_status"
        else:
            outcome = status.get("outcome")
            terminal_immutable = outcome in {"succeeded", "timeout", "failed", "invalid"}
            hashes_complete = (path / "hashes.json").is_file() and (path / "files.sha256").is_file()
            if terminal_immutable and hashes_complete:
                report = validate_attempt(path, expected=expected)
                if not report["passed"]:
                    raise RuntimeError(
                        "terminal attempt failed integrity checks; refusing to quarantine or overwrite it:\n"
                        + "\n".join(report["errors"])
                    )
                continue
            child_pid = status.get("child_pid")
            if isinstance(child_pid, int) and process_matches_start(
                child_pid, status.get("child_started_epoch_ns")
            ):
                raise RuntimeError(
                    f"incomplete attempt still has a live compiler child PID {child_pid}; refusing resume: {path}"
                )
            reason = "interrupted" if outcome == "interrupted" else "nonterminal_or_unfinalized"

        abandoned_root = logical_root / "abandoned"
        abandoned_root.mkdir(parents=True, exist_ok=True)
        timestamp = utc_now().replace(":", "").replace("-", "").replace(".", "")
        destination = abandoned_root / f"{timestamp}__{path.name}"
        suffix = 1
        while destination.exists():
            destination = abandoned_root / f"{timestamp}__{path.name}__{suffix:02d}"
            suffix += 1
        os.replace(path, destination)
        for original_name, preserved_name in (
            ("hashes.json", "original_hashes.json"),
            ("files.sha256", "original_files.sha256"),
        ):
            original = destination / original_name
            if original.exists():
                os.replace(original, destination / preserved_name)
        write_json_atomic(
            destination / "abandonment.json",
            {
                "schema_version": SCHEMA_VERSION,
                "abandoned_at_utc": utc_now(),
                "reason": reason,
                "original_logical_root": logical_root.relative_to(logical_root.parents[3]).as_posix(),
                "original_execution_name": path.name,
                "observed_status": status,
                "replacement_may_be_scheduled": True,
            },
        )
        write_hash_manifests(destination)
        quarantined.append(destination)
    return quarantined


def _existing_success(logical_root: Path, expected: Mapping[str, Any]) -> Path | None:
    successful: list[Path] = []
    for path in sorted(logical_root.glob("execution_*")):
        status_path = path / "status.json"
        if not status_path.is_file():
            continue
        try:
            status = load_json(status_path)
        except (OSError, ValueError):
            continue
        if status.get("outcome") == "succeeded":
            successful.append(path)
    if len(successful) > 1:
        raise RuntimeError(f"multiple successful executions for one logical attempt: {successful}")
    if successful:
        report = validate_attempt(successful[0], expected=expected, require_success=True)
        if not report["passed"]:
            raise RuntimeError(
                "refusing to resume past a modified/invalid successful attempt:\n" + "\n".join(report["errors"])
            )
        return successful[0]
    return None


def _existing_completed(
    logical_root: Path, expected: Mapping[str, Any]
) -> tuple[Path, dict[str, Any]] | None:
    paths = sorted(logical_root.glob("execution_*"))
    successful = _existing_success(logical_root, expected)
    if successful is not None:
        return successful, load_json(successful / "status.json")
    completed: list[Path] = []
    for path in paths:
        try:
            status = load_json(path / "status.json")
        except (OSError, ValueError):
            continue
        if status.get("outcome") in {"timeout", "failed", "invalid"}:
            completed.append(path)
    if not completed:
        return None
    selected = completed[-1]
    report = validate_attempt(selected, expected=expected)
    if not report["passed"]:
        raise RuntimeError(
            "refusing to resume past a modified/invalid terminal attempt:\n" + "\n".join(report["errors"])
        )
    return selected, report["status"]


def _terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=10)


def _copy_input(source: Path, destination: Path) -> None:
    write_bytes_atomic(destination, source.read_bytes())


def _run_execution(
    *,
    run_dir: Path,
    circuit: Mapping[str, Any],
    repetition: int,
    method: str,
    execution: int,
    seed: int,
    timeout_sec: float,
    compiler_entry: Path,
    git: Mapping[str, Any],
    host_fingerprint_hash: str | None = None,
    expected_method_provenance: Mapping[str, Any] | None = None,
) -> tuple[Path, dict[str, Any]]:
    logical_root = _logical_root(run_dir, circuit, repetition, method)
    attempt_dir = logical_root / f"execution_{execution:03d}"
    attempt_dir.mkdir(parents=True, exist_ok=False)
    input_path = attempt_dir / "input.qasm"
    output_path = attempt_dir / "compiler_output.json"
    _copy_input(Path(str(circuit["path"])), input_path)
    input_hash = sha256_file(input_path)
    if input_hash != circuit["sha256"]:
        raise RuntimeError(f"input copy hash mismatch for {circuit['name']}")

    command = [
        sys.executable,
        str(compiler_entry),
        "--method",
        method,
        "--qasm",
        str(input_path),
        "--output",
        str(output_path),
        "--seed",
        str(seed),
    ]
    command_record = {
        "schema_version": SCHEMA_VERSION,
        "argv": command,
        "cwd": str(REPO_ROOT),
        "method": method,
        "seed": seed,
        "repetition": repetition,
        "circuit_index": circuit["index"],
        "circuit_name": circuit["name"],
        "input_sha256": input_hash,
        "timeout_sec": timeout_sec,
        "environment_overrides": DETERMINISTIC_ENV,
        "git_commit": git.get("commit"),
        "host_fingerprint_hash": host_fingerprint_hash,
    }
    write_json_atomic(attempt_dir / "command.json", command_record)

    started_at = utc_now()
    running_status = {
        "schema_version": SCHEMA_VERSION,
        "outcome": "running",
        "failure_kind": None,
        "method": method,
        "seed": seed,
        "repetition": repetition,
        "execution": execution,
        "circuit_index": circuit["index"],
        "circuit_name": circuit["name"],
        "input_sha256": input_hash,
        "started_at_utc": started_at,
        "ended_at_utc": None,
        "external_elapsed_ns": None,
        "external_elapsed_seconds": None,
        "timeout_sec": timeout_sec,
        "return_code": None,
        "verification_hash": None,
        "metrics_hash": None,
        "git_commit": git.get("commit"),
        "host_fingerprint_hash": host_fingerprint_hash,
        "child_pid": None,
        "child_started_epoch_ns": None,
    }
    write_json_atomic(attempt_dir / "status.json", running_status)

    environment = os.environ.copy()
    environment.update(DETERMINISTIC_ENV)
    popen_options: dict[str, Any] = {
        "cwd": REPO_ROOT,
        "env": environment,
    }
    if os.name == "nt":
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_options["start_new_session"] = True

    process: subprocess.Popen[Any] | None = None
    timed_out = False
    interrupted = False
    runner_exception: Exception | None = None
    return_code: int | None = None
    start_ns = time.perf_counter_ns()
    with (attempt_dir / "stdout.log").open("wb") as stdout_handle, (attempt_dir / "stderr.log").open(
        "wb"
    ) as stderr_handle:
        try:
            process = subprocess.Popen(command, stdout=stdout_handle, stderr=stderr_handle, **popen_options)
            running_status["child_pid"] = process.pid
            running_status["child_started_epoch_ns"] = time.time_ns()
            write_json_atomic(attempt_dir / "status.json", running_status)
            try:
                return_code = process.wait(timeout=timeout_sec)
            except subprocess.TimeoutExpired:
                timed_out = True
                _terminate_process_tree(process)
                return_code = process.returncode
            except KeyboardInterrupt:
                interrupted = True
                _terminate_process_tree(process)
                return_code = process.returncode
        except Exception as exc:
            runner_exception = exc
            if process is not None:
                _terminate_process_tree(process)
            stderr_handle.write(("\nRUNNER EXCEPTION\n" + traceback.format_exc()).encode("utf-8"))
            stderr_handle.flush()
    elapsed_ns = time.perf_counter_ns() - start_ns

    status = dict(running_status)
    status.update(
        {
            "ended_at_utc": utc_now(),
            "external_elapsed_ns": elapsed_ns,
            "external_elapsed_seconds": elapsed_ns / 1_000_000_000.0,
            "return_code": return_code,
        }
    )
    if interrupted:
        status.update(outcome="interrupted", failure_kind="user_interrupt")
    elif timed_out:
        status.update(outcome="timeout", failure_kind="timeout")
    elif runner_exception is not None:
        status.update(outcome="failed", failure_kind="runner_exception", error=str(runner_exception))
    elif not output_path.is_file():
        if return_code == 0:
            status.update(outcome="invalid", failure_kind="missing_compiler_output")
        else:
            status.update(outcome="failed", failure_kind="compiler_nonzero_exit")
    else:
        try:
            payload = load_json(output_path)
        except (OSError, ValueError) as exc:
            status.update(outcome="invalid", failure_kind="malformed_compiler_output", error=str(exc))
        else:
            try:
                verification = verify_compiler_output(payload, qasm_path=input_path)
            except Exception as exc:
                status.update(outcome="invalid", failure_kind="verifier_failed", error=str(exc))
            else:
                write_json_atomic(attempt_dir / "verification.json", verification)
                if not verification.get("passed", False):
                    status.update(outcome="invalid", failure_kind="verifier_failed")
                elif return_code != 0:
                    status.update(
                        outcome="failed",
                        failure_kind="compiler_nonzero_exit",
                        error="compiler exited nonzero despite a passing canonical payload",
                    )
                else:
                    provenance_errors = payload_provenance_errors(
                        payload,
                        expected={
                            "method_provenance": expected_method_provenance,
                            "input_sha256": input_hash,
                            "seed": seed,
                        },
                    )
                    if provenance_errors:
                        status.update(
                            outcome="invalid",
                            failure_kind="provenance_mismatch",
                            error="; ".join(provenance_errors),
                        )
                    else:
                        try:
                            metrics = compute_metrics(payload)
                        except Exception as exc:
                            status.update(outcome="invalid", failure_kind="metrics_failed", error=str(exc))
                        else:
                            write_json_atomic(attempt_dir / "metrics.json", metrics)
                            status.update(
                                outcome="succeeded",
                                failure_kind=None,
                                verification_hash=canonical_hash(verification),
                                metrics_hash=canonical_hash(metrics),
                                compiler_output_hash=canonical_hash(payload),
                            )
    write_json_atomic(attempt_dir / "status.json", status)
    write_hash_manifests(attempt_dir)
    if interrupted:
        raise KeyboardInterrupt
    return attempt_dir, status


def _first_repeat_pair(
    run_dir: Path, circuit: Mapping[str, Any], methods: Sequence[str], seed: int
) -> tuple[bool, float | None, str]:
    elapsed: list[float] = []
    for method in methods:
        expected = {
            "method": method,
            "seed": seed,
            "repetition": 0,
            "circuit_index": circuit["index"],
            "circuit_name": circuit["name"],
            "input_sha256": circuit["sha256"],
        }
        success = _existing_success(_logical_root(run_dir, circuit, 0, method), expected)
        if success is None:
            return False, None, f"repetition 0 did not succeed for {method}"
        status = load_json(success / "status.json")
        elapsed.append(float(status["external_elapsed_seconds"]))
    return True, max(elapsed), "eligible"


def _validate_round_zero_before_extension(
    run_dir: Path,
    circuits: Sequence[Mapping[str, Any]],
    methods: Sequence[str],
    *,
    seed: int,
    timeout_sec: float,
    git_commit: str | None,
    source_lock: Mapping[str, Any],
    host_fingerprint_hash: str,
) -> list[str]:
    errors: list[str] = []
    for circuit in circuits:
        for method in methods:
            expected = {
                "method": method,
                "seed": seed,
                "repetition": 0,
                "circuit_index": circuit["index"],
                "circuit_name": circuit["name"],
                "input_sha256": circuit["sha256"],
                "timeout_sec": timeout_sec,
                "git_commit": git_commit,
                "host_fingerprint_hash": host_fingerprint_hash,
                "method_provenance": method_provenance_from_snapshot(method, source_lock),
            }
            completed = _existing_completed(_logical_root(run_dir, circuit, 0, method), expected)
            key = _logical_key(int(circuit["index"]), 0, method)
            if completed is None:
                errors.append(f"round zero is missing or incomplete: {key}")
                continue
            _, status = completed
            outcome = status.get("outcome")
            if outcome == "succeeded":
                continue
            if outcome == "timeout" and status.get("failure_kind") == "timeout":
                continue
            errors.append(
                f"round zero has a non-admissible terminal outcome: {key} "
                f"({outcome}/{status.get('failure_kind')})"
            )
    return errors


def _mark_logical(
    manifest: dict[str, Any], key: str, *, state: str, detail: Mapping[str, Any] | None = None
) -> None:
    logical = manifest.setdefault("logical_attempts", {})
    record = logical.setdefault(key, {})
    record.update(state=state, updated_at_utc=utc_now())
    if detail:
        record.update(detail)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    requested_methods = normalize_methods(args.methods)
    args.methods = [method for method in SUPPORTED_METHODS if method in requested_methods]
    _validate_args(args)
    compiler_entry = REPO_ROOT / "scripts" / "run_compiler_once.py"
    if not compiler_entry.is_file():
        raise FileNotFoundError(f"missing compiler entry point: {compiler_entry}")
    circuits = read_benchmark_list(args.list_path, args.benchmark_dir)
    if args.max_circuits is not None:
        circuits = circuits[: args.max_circuits]
    if not circuits:
        raise ValueError("benchmark list selected no circuits")
    if not args.resume and not args.dry_run and args.repetitions != 1:
        raise ValueError("a new canonical run must start with --repetitions 1; extend only after round zero")
    state = git_state()
    if state.get("dirty") and not args.allow_dirty and not args.dry_run:
        changed = "\n".join(state.get("status_porcelain", []))
        raise RuntimeError(f"refusing to run from a dirty Git worktree; commit first or use --allow-dirty:\n{changed}")

    base_configuration = _base_configuration(args, circuits, args.methods, compiler_entry, state)
    plan = [
        {
            "circuit_index": circuit["index"],
            "circuit_name": circuit["name"],
            "repetition": repetition,
            "method_order": method_order(int(circuit["index"]), repetition, args.methods),
            "seed": args.seed,
        }
        for circuit in circuits
        for repetition in range(args.repetitions)
    ]
    if args.dry_run:
        print(
            canonical_hash(
                {"base_configuration": base_configuration, "repetitions": args.repetitions, "plan": plan}
            )
        )
        for item in plan:
            print(
                f"{item['circuit_index']:04d} r{item['repetition']:03d} "
                + " -> ".join(item["method_order"])
            )
        return 0

    run_dir = args.run_dir.resolve()
    manifest_path = run_dir / "run_manifest.json"
    if run_dir.exists() and not args.resume and any(run_dir.iterdir()):
        raise RuntimeError(f"run directory already exists and is not empty; use --resume: {run_dir}")

    with RunLock(run_dir, force_unlock=args.force_unlock):
        if args.resume:
            if not manifest_path.is_file():
                raise FileNotFoundError(f"--resume requires {manifest_path}")
            manifest = load_json(manifest_path)
            compatible, errors = _manifest_compatible(manifest, base_configuration)
            errors.extend(_validate_recorded_plan(manifest))
            if not compatible:
                raise RuntimeError("cannot resume with changed canonical inputs:\n" + "\n".join(errors))
            if errors:
                raise RuntimeError("cannot resume a run with a modified plan manifest:\n" + "\n".join(errors))
            previous_repetitions = int(manifest.get("requested_repetitions", 1))
            if args.repetitions < previous_repetitions:
                raise RuntimeError("--resume cannot reduce --repetitions")
            previous_threshold = manifest.get("repeat_threshold_sec")
            higher_repeat_recorded = any(
                "/r000/" not in key for key in manifest.get("logical_attempts", {})
            )
            if previous_threshold is not None and args.repeat_threshold_sec != previous_threshold:
                raise RuntimeError("--resume cannot change or remove the established repeat threshold")
            if previous_threshold is None and higher_repeat_recorded and args.repeat_threshold_sec is not None:
                raise RuntimeError("--resume cannot add a repeat threshold after higher repetitions were recorded")
            if args.repetitions > previous_repetitions:
                round_zero_errors = _validate_round_zero_before_extension(
                    run_dir,
                    circuits,
                    args.methods,
                    seed=args.seed,
                    timeout_sec=args.timeout_sec,
                    git_commit=state.get("commit"),
                    source_lock=base_configuration["source_snapshot"],
                    host_fingerprint_hash=base_configuration["host_fingerprint_hash"],
                )
                if round_zero_errors:
                    raise RuntimeError(
                        "cannot extend repetitions before a complete, admissible round zero:\n"
                        + "\n".join(round_zero_errors)
                    )
            manifest["requested_repetitions"] = args.repetitions
            manifest["repeat_threshold_sec"] = args.repeat_threshold_sec
            if (
                args.repetitions != previous_repetitions
                or args.repeat_threshold_sec != previous_threshold
            ):
                manifest["plan_history"].append(
                    _plan_record(args.repetitions, args.repeat_threshold_sec, len(manifest["plan_history"]))
                )
                manifest["plan_history_hash"] = canonical_hash(manifest["plan_history"])
            manifest.setdefault("resume_history", []).append(
                {"at_utc": utc_now(), "argv": sys.argv, "host": host_identity()}
            )
        else:
            initial_plan = {
                "requested_repetitions": args.repetitions,
                "repeat_threshold_sec": args.repeat_threshold_sec,
            }
            first_plan_record = _plan_record(args.repetitions, args.repeat_threshold_sec, 0)
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "created_at_utc": utc_now(),
                "updated_at_utc": utc_now(),
                "base_configuration": base_configuration,
                "base_configuration_hash": canonical_hash(base_configuration),
                "requested_repetitions": args.repetitions,
                "repeat_threshold_sec": args.repeat_threshold_sec,
                "initial_plan": initial_plan,
                "initial_plan_hash": canonical_hash(initial_plan),
                "plan_history": [first_plan_record],
                "plan_history_hash": canonical_hash([first_plan_record]),
                "git": state,
                "host": host_identity(),
                "argv": sys.argv,
                "logical_attempts": {},
            }
        write_json_atomic(manifest_path, manifest)

        failed_logical = 0
        interrupted = False
        for circuit in circuits:
            for repetition in range(args.repetitions):
                if repetition > 0 and args.repeat_threshold_sec is not None:
                    pair_ok, pair_max, reason = _first_repeat_pair(run_dir, circuit, args.methods, args.seed)
                    eligible = pair_ok and pair_max is not None and pair_max <= args.repeat_threshold_sec
                    if not eligible:
                        threshold_reason = reason
                        if pair_ok and pair_max is not None:
                            threshold_reason = (
                                f"pair max external time {pair_max:.9f}s exceeds threshold "
                                f"{args.repeat_threshold_sec:.9f}s"
                            )
                        for method in args.methods:
                            key = _logical_key(int(circuit["index"]), repetition, method)
                            _mark_logical(
                                manifest,
                                key,
                                state="not_scheduled_threshold",
                                detail={"reason": threshold_reason, "pair_max_external_seconds": pair_max},
                            )
                        manifest["updated_at_utc"] = utc_now()
                        write_json_atomic(manifest_path, manifest)
                        continue
                for method in method_order(int(circuit["index"]), repetition, args.methods):
                    key = _logical_key(int(circuit["index"]), repetition, method)
                    expected = {
                        "method": method,
                        "seed": args.seed,
                        "repetition": repetition,
                        "circuit_index": circuit["index"],
                        "circuit_name": circuit["name"],
                        "input_sha256": circuit["sha256"],
                        "timeout_sec": args.timeout_sec,
                        "git_commit": state.get("commit"),
                        "host_fingerprint_hash": base_configuration["host_fingerprint_hash"],
                        "method_provenance": method_provenance_from_snapshot(
                            method, base_configuration["source_snapshot"]
                        ),
                    }
                    logical_root = _logical_root(run_dir, circuit, repetition, method)
                    if args.resume:
                        quarantined = _quarantine_incomplete_executions(logical_root, expected)
                        for abandoned in quarantined:
                            print(f"QUARANTINE incomplete execution: {abandoned}", flush=True)
                    completed = _existing_completed(logical_root, expected) if args.resume else None
                    if completed is not None:
                        selected, selected_status = completed
                        outcome = selected_status["outcome"]
                        print(f"SKIP verified {outcome} {key}: {selected}", flush=True)
                        _mark_logical(
                            manifest,
                            key,
                            state=outcome,
                            detail={
                                "selected_execution": selected.relative_to(run_dir).as_posix(),
                                "external_elapsed_seconds": selected_status.get("external_elapsed_seconds"),
                                "failure_kind": selected_status.get("failure_kind"),
                            },
                        )
                        continue
                    execution = _execution_number(logical_root)
                    print(
                        f"RUN {key} execution={execution:03d} timeout={args.timeout_sec:g}s seed={args.seed}",
                        flush=True,
                    )
                    _mark_logical(manifest, key, state="running")
                    manifest["updated_at_utc"] = utc_now()
                    write_json_atomic(manifest_path, manifest)
                    try:
                        attempt_dir, status = _run_execution(
                            run_dir=run_dir,
                            circuit=circuit,
                            repetition=repetition,
                            method=method,
                            execution=execution,
                            seed=args.seed,
                            timeout_sec=args.timeout_sec,
                            compiler_entry=compiler_entry,
                            git=state,
                            host_fingerprint_hash=base_configuration["host_fingerprint_hash"],
                            expected_method_provenance=method_provenance_from_snapshot(
                                method, base_configuration["source_snapshot"]
                            ),
                        )
                    except KeyboardInterrupt:
                        interrupted = True
                        _mark_logical(manifest, key, state="interrupted")
                        manifest["updated_at_utc"] = utc_now()
                        write_json_atomic(manifest_path, manifest)
                        break
                    outcome = status["outcome"]
                    _mark_logical(
                        manifest,
                        key,
                        state=outcome,
                        detail={
                            "selected_execution": attempt_dir.relative_to(run_dir).as_posix(),
                            "external_elapsed_seconds": status["external_elapsed_seconds"],
                            "failure_kind": status.get("failure_kind"),
                        },
                    )
                    manifest["updated_at_utc"] = utc_now()
                    write_json_atomic(manifest_path, manifest)
                    print(
                        f"DONE {key} outcome={outcome} elapsed={status['external_elapsed_seconds']:.3f}s",
                        flush=True,
                    )
                    if outcome != "succeeded":
                        failed_logical += 1
                        if args.fail_fast:
                            interrupted = True
                            break
                if interrupted:
                    break
            if interrupted:
                break

        manifest["updated_at_utc"] = utc_now()
        manifest["runner_finished_at_utc"] = utc_now()
        manifest["runner_exit_reason"] = "interrupted_or_fail_fast" if interrupted else "plan_finished"
        manifest["failed_logical_in_this_invocation"] = failed_logical
        manifest["unsuccessful_terminal_logical_total"] = sum(
            1
            for record in manifest.get("logical_attempts", {}).values()
            if record.get("state") in {"timeout", "failed", "invalid", "interrupted"}
        )
        write_json_atomic(manifest_path, manifest)
    if interrupted:
        return 130
    return 1 if manifest["unsuccessful_terminal_logical_total"] else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
