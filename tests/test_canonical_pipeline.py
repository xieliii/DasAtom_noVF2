from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any, Mapping

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import _common as common  # noqa: E402
import archive_canonical_run as archive  # noqa: E402
import collect_canonical_results as collector  # noqa: E402
import recompute_fidelity as fidelity  # noqa: E402
import run_canonical_pairwise as runner  # noqa: E402
import validate_canonical_run as validator  # noqa: E402


MINIMAL_QASM = b'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[1];\n'
FAKE_COMMIT = "1" * 40
FAKE_SOURCE_FILES = {
    f"{spec['implementation_directory']}/{relative}": hashlib.sha256(
        f"{method}:{relative}".encode("ascii")
    ).hexdigest()
    for method, spec in common.METHOD_PROVENANCE_SPECS.items()
    for relative in spec["algorithm_files"]
}
FAKE_SOURCE_SNAPSHOT = {
    "algorithm": "sha256",
    "files": FAKE_SOURCE_FILES,
    "tree_hash": common.canonical_hash(FAKE_SOURCE_FILES),
}
FAKE_HOST_FINGERPRINT = {
    "hostname": "test-host",
    "system": "TestOS",
    "machine": "x86_64",
    "python_major_minor": "3.12",
    "python_executable": "/test/python",
}
FAKE_HOST_FINGERPRINT_HASH = common.canonical_hash(FAKE_HOST_FINGERPRINT)
FAKE_VERIFICATION = {
    "schema_version": common.SCHEMA_VERSION,
    "passed": True,
    "errors": [],
    "warnings": [],
}
FAKE_METRICS = {
    "schema_version": common.SCHEMA_VERSION,
    "movement_batches": 2,
    "atom_endpoint_changes": 3,
    "transfer_rounds": 4,
    "atom_transfer_events": 5,
    "batch_critical_distance_um": 6.0,
    "endpoint_sum_distance_um": 7.0,
    "two_qubit_operation_count": 1,
    "parallel_gate_group_count": 1,
}


def _attempt_status(
    *,
    outcome: str,
    method: str = "forceshuttle",
    repetition: int = 0,
    execution: int = 0,
    input_sha256: str,
    elapsed_seconds: float = 1.0,
) -> dict[str, Any]:
    failure_kind = None if outcome == "succeeded" else outcome
    if outcome == "interrupted":
        failure_kind = "user_interrupt"
    return {
        "schema_version": common.SCHEMA_VERSION,
        "outcome": outcome,
        "failure_kind": failure_kind,
        "method": method,
        "seed": 0,
        "repetition": repetition,
        "execution": execution,
        "circuit_index": 0,
        "circuit_name": "tiny.qasm",
        "input_sha256": input_sha256,
        "started_at_utc": "2026-07-29T00:00:00.000000Z",
        "ended_at_utc": "2026-07-29T00:00:01.000000Z",
        "external_elapsed_ns": int(elapsed_seconds * 1_000_000_000),
        "external_elapsed_seconds": elapsed_seconds,
        "timeout_sec": 10.0,
        "return_code": 0 if outcome == "succeeded" else 1,
        "verification_hash": (
            common.canonical_hash(FAKE_VERIFICATION) if outcome == "succeeded" else None
        ),
        "metrics_hash": common.canonical_hash(FAKE_METRICS) if outcome == "succeeded" else None,
        "compiler_output_hash": None,
        "git_commit": FAKE_COMMIT,
        "host_fingerprint_hash": FAKE_HOST_FINGERPRINT_HASH,
        "child_pid": None,
        "child_started_epoch_ns": None,
    }


def _write_attempt(
    attempt_dir: Path,
    *,
    outcome: str,
    method: str = "forceshuttle",
    repetition: int = 0,
    execution: int = 0,
    elapsed_seconds: float = 1.0,
) -> dict[str, Any]:
    if outcome == "timeout" and elapsed_seconds < 10.0:
        elapsed_seconds = 10.0
    attempt_dir.mkdir(parents=True)
    (attempt_dir / "input.qasm").write_bytes(MINIMAL_QASM)
    input_sha256 = common.sha256_file(attempt_dir / "input.qasm")
    status = _attempt_status(
        outcome=outcome,
        method=method,
        repetition=repetition,
        execution=execution,
        input_sha256=input_sha256,
        elapsed_seconds=elapsed_seconds,
    )
    command = {
        "schema_version": common.SCHEMA_VERSION,
        "argv": ["python", "fake_compiler.py"],
        "cwd": str(REPO_ROOT),
        "method": method,
        "seed": 0,
        "repetition": repetition,
        "circuit_index": 0,
        "circuit_name": "tiny.qasm",
        "input_sha256": input_sha256,
        "timeout_sec": 10.0,
        "git_commit": FAKE_COMMIT,
        "host_fingerprint_hash": FAKE_HOST_FINGERPRINT_HASH,
    }
    common.write_json_atomic(attempt_dir / "command.json", command)
    common.write_json_atomic(attempt_dir / "status.json", status)
    (attempt_dir / "stdout.log").write_bytes(b"")
    (attempt_dir / "stderr.log").write_bytes(b"")
    if outcome == "succeeded":
        payload = {
            "schema_version": common.SCHEMA_VERSION,
            "schedule_hash": "a" * 64,
            "method": common.method_provenance_from_snapshot(method, FAKE_SOURCE_SNAPSHOT),
            "input": {"filename": "input.qasm", "qasm_sha256": input_sha256},
            "config": {"seed": 0},
            "circuit": {"layout_qubit_count": 1},
            "verification": {"passed": True},
        }
        common.write_json_atomic(
            attempt_dir / "compiler_output.json",
            payload,
        )
        common.write_json_atomic(attempt_dir / "verification.json", FAKE_VERIFICATION)
        common.write_json_atomic(attempt_dir / "metrics.json", FAKE_METRICS)
        status["compiler_output_hash"] = common.canonical_hash(payload)
        common.write_json_atomic(attempt_dir / "status.json", status)
    common.write_hash_manifests(attempt_dir)
    return status


def _expected_attempt(method: str = "forceshuttle", repetition: int = 0) -> dict[str, Any]:
    return {
        "method": method,
        "seed": 0,
        "repetition": repetition,
        "circuit_index": 0,
        "circuit_name": "tiny.qasm",
        "input_sha256": hashlib.sha256(MINIMAL_QASM).hexdigest(),
        "git_commit": FAKE_COMMIT,
        "timeout_sec": 10.0,
        "host_fingerprint_hash": FAKE_HOST_FINGERPRINT_HASH,
        "method_provenance": common.method_provenance_from_snapshot(method, FAKE_SOURCE_SNAPSHOT),
    }


def _patch_fake_recomputation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "verify_compiler_output", lambda payload, qasm_path=None: FAKE_VERIFICATION)
    monkeypatch.setattr(common, "compute_metrics", lambda payload: FAKE_METRICS)
    monkeypatch.setattr(fidelity, "compute_metrics", lambda payload: FAKE_METRICS)


def _build_valid_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    _patch_fake_recomputation(monkeypatch)
    run_dir = tmp_path / "run"
    benchmark_dir = tmp_path / "local64"
    benchmark_dir.mkdir()
    qasm_path = benchmark_dir / "tiny.qasm"
    qasm_path.write_bytes(MINIMAL_QASM)
    list_path = tmp_path / "list.txt"
    list_path.write_text("tiny.qasm\n", encoding="utf-8")
    compiler_entry = tmp_path / "compiler.py"
    compiler_entry.write_text("# frozen test compiler\n", encoding="utf-8")
    source_snapshot = FAKE_SOURCE_SNAPSHOT
    base_configuration = {
        "schema_version": common.SCHEMA_VERSION,
        "benchmark_dir": str(benchmark_dir),
        "benchmark_list": str(list_path),
        "benchmark_list_sha256": common.sha256_file(list_path),
        "circuits": [
            {
                "index": 0,
                "name": "tiny.qasm",
                "sha256": common.sha256_file(qasm_path),
                "size_bytes": qasm_path.stat().st_size,
            }
        ],
        "methods": ["forceshuttle"],
        "timeout_sec": 10.0,
        "seed": 0,
        "compiler_entry": str(compiler_entry),
        "compiler_entry_sha256": common.sha256_file(compiler_entry),
        "git_commit": FAKE_COMMIT,
        "source_snapshot": source_snapshot,
        "host_fingerprint": FAKE_HOST_FINGERPRINT,
        "host_fingerprint_hash": FAKE_HOST_FINGERPRINT_HASH,
    }
    attempt_dir = (
        run_dir
        / "attempts"
        / "0000__tiny"
        / "repeat_000"
        / "forceshuttle"
        / "execution_000"
    )
    status = _write_attempt(attempt_dir, outcome="succeeded")
    relative_attempt = attempt_dir.relative_to(run_dir).as_posix()
    initial_plan = {"requested_repetitions": 1, "repeat_threshold_sec": None}
    first_plan_record = {
        "sequence": 0,
        "requested_repetitions": 1,
        "repeat_threshold_sec": None,
        "recorded_at_utc": "2026-07-29T00:00:00.000000Z",
    }
    manifest = {
        "schema_version": common.SCHEMA_VERSION,
        "base_configuration": base_configuration,
        "base_configuration_hash": common.canonical_hash(base_configuration),
        "requested_repetitions": 1,
        "repeat_threshold_sec": None,
        "initial_plan": initial_plan,
        "initial_plan_hash": common.canonical_hash(initial_plan),
        "plan_history": [first_plan_record],
        "plan_history_hash": common.canonical_hash([first_plan_record]),
        "logical_attempts": {
            "0000/r000/forceshuttle": {
                "state": "succeeded",
                "selected_execution": relative_attempt,
                "external_elapsed_seconds": status["external_elapsed_seconds"],
                "failure_kind": None,
            }
        },
    }
    common.write_json_atomic(run_dir / "run_manifest.json", manifest)
    collection = collector.build_collection(run_dir)
    assert collection["passed"], collection["errors"]
    collector.write_collection(collection, run_dir / "derived")

    monkeypatch.setattr(
        validator,
        "git_state",
        lambda: {
            "available": True,
            "commit": FAKE_COMMIT,
            "dirty": False,
            "status_porcelain": [],
        },
    )
    monkeypatch.setattr(validator, "source_snapshot", lambda: source_snapshot)
    baseline = validator.validate_run(run_dir, run_dir / "derived", require_complete=True)
    assert baseline["passed"], baseline["errors"]
    return run_dir, attempt_dir


def test_method_order_alternates_ab_ba_by_circuit_and_repetition() -> None:
    assert runner.method_order(0, 0, common.METHODS) == ["forceshuttle", "dasatom"]
    assert runner.method_order(1, 0, common.METHODS) == ["dasatom", "forceshuttle"]
    assert runner.method_order(0, 1, common.METHODS) == ["dasatom", "forceshuttle"]
    assert runner.method_order(1, 1, common.METHODS) == ["forceshuttle", "dasatom"]
    assert runner.method_order(0, 0, list(reversed(common.METHODS))) == [
        "forceshuttle",
        "dasatom",
    ]


def test_source_snapshot_locks_windows_powershell_entry_points(tmp_path: Path) -> None:
    (tmp_path / "canonical").mkdir()
    (tmp_path / "canonical" / "adapter.py").write_text("# adapter\n", encoding="utf-8")
    windows_dir = tmp_path / "scripts" / "windows"
    windows_dir.mkdir(parents=True)
    (windows_dir / "canonical_pipeline.ps1").write_text("# runner\n", encoding="utf-8")
    (windows_dir / "ignored.txt").write_text("not executable\n", encoding="utf-8")

    snapshot = common.source_snapshot(tmp_path)

    assert "canonical/adapter.py" in snapshot["files"]
    assert "scripts/windows/canonical_pipeline.ps1" in snapshot["files"]
    assert "scripts/windows/ignored.txt" not in snapshot["files"]


def test_timeout_is_terminal_and_writes_complete_raw_hashes(tmp_path: Path) -> None:
    qasm_path = tmp_path / "tiny.qasm"
    qasm_path.write_bytes(MINIMAL_QASM)
    compiler_entry = tmp_path / "sleeping_compiler.py"
    compiler_entry.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    circuit = {
        "index": 0,
        "name": qasm_path.name,
        "path": str(qasm_path),
        "sha256": common.sha256_file(qasm_path),
        "size_bytes": qasm_path.stat().st_size,
    }

    attempt_dir, status = runner._run_execution(
        run_dir=tmp_path / "run",
        circuit=circuit,
        repetition=0,
        method="forceshuttle",
        execution=0,
        seed=0,
        timeout_sec=0.05,
        compiler_entry=compiler_entry,
        git={"commit": FAKE_COMMIT},
    )

    assert status["outcome"] == "timeout"
    assert status["failure_kind"] == "timeout"
    assert status["ended_at_utc"] is not None
    assert status["external_elapsed_ns"] > 0
    assert (attempt_dir / "hashes.json").is_file()
    assert (attempt_dir / "files.sha256").is_file()
    report = common.validate_attempt(
        attempt_dir,
        expected={
            "method": "forceshuttle",
            "seed": 0,
            "repetition": 0,
            "circuit_index": 0,
            "circuit_name": "tiny.qasm",
            "input_sha256": circuit["sha256"],
            "git_commit": FAKE_COMMIT,
        },
    )
    assert report["passed"], report["errors"]


@pytest.mark.parametrize("outcome", ["succeeded", "timeout", "failed", "invalid"])
def test_resume_recognizes_all_immutable_terminal_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    logical_root = tmp_path / outcome
    attempt_dir = logical_root / "execution_000"
    _write_attempt(attempt_dir, outcome=outcome)
    _patch_fake_recomputation(monkeypatch)

    selected = runner._existing_completed(logical_root, _expected_attempt())

    assert selected is not None
    assert selected[0] == attempt_dir
    assert selected[1]["outcome"] == outcome


def test_resume_quarantines_running_and_interrupted_but_preserves_immutable_terminals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logical_root = tmp_path / "logical"
    outcomes = ["succeeded", "timeout", "failed", "invalid", "running", "interrupted"]
    original_dirs: dict[str, Path] = {}
    for execution, outcome in enumerate(outcomes):
        path = logical_root / f"execution_{execution:03d}"
        _write_attempt(path, outcome=outcome, execution=execution)
        original_dirs[outcome] = path
    _patch_fake_recomputation(monkeypatch)
    monkeypatch.setattr(runner, "process_matches_start", lambda pid, started: False)

    quarantined = runner._quarantine_incomplete_executions(logical_root, _expected_attempt())

    assert len(quarantined) == 2
    for outcome in ("succeeded", "timeout", "failed", "invalid"):
        assert original_dirs[outcome].is_dir()
    for outcome in ("running", "interrupted"):
        assert not original_dirs[outcome].exists()
    observed = {
        common.load_json(path / "abandonment.json")["observed_status"]["outcome"]
        for path in quarantined
    }
    assert observed == {"running", "interrupted"}
    for path in quarantined:
        assert not common.validate_hash_manifests(path)


def test_first_repeat_pair_requires_both_successes_and_uses_pair_max(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    circuit = {
        "index": 0,
        "name": "tiny.qasm",
        "sha256": "b" * 64,
    }
    attempts: dict[str, Path] = {}
    for method, elapsed in (("forceshuttle", 3.0), ("dasatom", 7.0)):
        path = tmp_path / method
        path.mkdir()
        common.write_json_atomic(path / "status.json", {"external_elapsed_seconds": elapsed})
        attempts[method] = path

    monkeypatch.setattr(
        runner,
        "_existing_success",
        lambda logical_root, expected: attempts.get(str(expected["method"])),
    )
    pair_ok, pair_max, reason = runner._first_repeat_pair(
        tmp_path, circuit, common.METHODS, seed=0
    )
    assert pair_ok is True
    assert pair_max == 7.0
    assert reason == "eligible"

    attempts.pop("dasatom")
    pair_ok, pair_max, reason = runner._first_repeat_pair(
        tmp_path, circuit, common.METHODS, seed=0
    )
    assert pair_ok is False
    assert pair_max is None
    assert "dasatom" in reason


def test_validator_rejects_false_threshold_skip_for_an_eligible_pair(tmp_path: Path) -> None:
    grouped: dict[tuple[int, int, str], list[Path]] = {}
    logical_records: dict[str, dict[str, Any]] = {}
    for method in common.METHODS:
        attempt_dir = (
            tmp_path
            / "attempts"
            / "0000__tiny"
            / "repeat_000"
            / method
            / "execution_000"
        )
        attempt_dir.mkdir(parents=True)
        common.write_json_atomic(
            attempt_dir / "status.json",
            {
                "circuit_index": 0,
                "repetition": 0,
                "method": method,
                "outcome": "succeeded",
                "external_elapsed_seconds": 1.0,
                "failure_kind": None,
            },
        )
        grouped[(0, 0, method)] = [attempt_dir]
        logical_records[f"0000/r000/{method}"] = {"state": "succeeded"}
        logical_records[f"0000/r001/{method}"] = {
            "state": "not_scheduled_threshold",
            "reason": "manually hidden",
            "pair_max_external_seconds": 1.0,
        }
    manifest = {
        "base_configuration": {
            "circuits": [{"index": 0, "name": "tiny.qasm"}],
            "methods": list(common.METHODS),
        },
        "requested_repetitions": 2,
        "repeat_threshold_sec": 10.0,
        "logical_attempts": logical_records,
    }

    errors = validator._check_repeat_threshold_policy(manifest, grouped)

    assert any("eligible repeat is missing" in error for error in errors)
    assert any("falsely marked" in error for error in errors)


def test_validator_rejects_requested_repetition_plan_downgrade() -> None:
    initial_plan = {"requested_repetitions": 3, "repeat_threshold_sec": 10.0}
    first_plan_record = {
        "sequence": 0,
        "requested_repetitions": 3,
        "repeat_threshold_sec": 10.0,
        "recorded_at_utc": "2026-07-29T00:00:00.000000Z",
    }
    manifest = {
        "requested_repetitions": 1,
        "repeat_threshold_sec": 10.0,
        "initial_plan": initial_plan,
        "initial_plan_hash": common.canonical_hash(initial_plan),
        "plan_history": [first_plan_record],
        "plan_history_hash": common.canonical_hash([first_plan_record]),
        "logical_attempts": {},
    }

    errors = validator._check_plan_bounds(manifest, {})

    assert any("requested_repetitions disagrees with plan_history" in error for error in errors)


@pytest.mark.parametrize(
    ("pair_ok", "pair_max", "expected_repeat_one"),
    [(False, None, False), (True, 10.001, False), (True, 10.0, True)],
)
def test_repeat_threshold_schedules_only_an_eligible_successful_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pair_ok: bool,
    pair_max: float | None,
    expected_repeat_one: bool,
) -> None:
    benchmark_dir = tmp_path / "local64"
    benchmark_dir.mkdir()
    (benchmark_dir / "tiny.qasm").write_bytes(MINIMAL_QASM)
    list_path = tmp_path / "list.txt"
    list_path.write_text("tiny.qasm\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    calls: list[tuple[int, str]] = []

    monkeypatch.setattr(
        runner,
        "git_state",
        lambda: {
            "available": True,
            "commit": FAKE_COMMIT,
            "dirty": False,
            "status_porcelain": [],
        },
    )
    _patch_fake_recomputation(monkeypatch)
    monkeypatch.setattr(runner, "source_snapshot", lambda: FAKE_SOURCE_SNAPSHOT)
    monkeypatch.setattr(runner, "host_fingerprint", lambda: FAKE_HOST_FINGERPRINT)
    monkeypatch.setattr(runner, "host_identity", lambda: {"hostname": "test-host"})
    monkeypatch.setattr(
        runner,
        "_first_repeat_pair",
        lambda run_dir, circuit, methods, seed: (pair_ok, pair_max, "test eligibility"),
    )

    def fake_run_execution(**kwargs: Any) -> tuple[Path, dict[str, Any]]:
        repetition = int(kwargs["repetition"])
        method = str(kwargs["method"])
        calls.append((repetition, method))
        attempt_dir = runner._logical_root(
            kwargs["run_dir"], kwargs["circuit"], repetition, method
        ) / f"execution_{int(kwargs['execution']):03d}"
        status = _write_attempt(
            attempt_dir,
            outcome="succeeded",
            method=method,
            repetition=repetition,
            execution=int(kwargs["execution"]),
        )
        return attempt_dir, status

    monkeypatch.setattr(runner, "_run_execution", fake_run_execution)

    initial_result = runner.main(
        [
            "--run-dir",
            str(run_dir),
            "--benchmark-dir",
            str(benchmark_dir),
            "--list",
            str(list_path),
            "--repetitions",
            "1",
            "--timeout-sec",
            "10",
            "--repeat-threshold-sec",
            "10",
        ]
    )
    result = runner.main(
        [
            "--run-dir",
            str(run_dir),
            "--benchmark-dir",
            str(benchmark_dir),
            "--list",
            str(list_path),
            "--repetitions",
            "2",
            "--timeout-sec",
            "10",
            "--repeat-threshold-sec",
            "10",
            "--resume",
        ]
    )

    assert initial_result == 0
    assert result == 0
    repeat_one_calls = [call for call in calls if call[0] == 1]
    assert bool(repeat_one_calls) is expected_repeat_one
    assert len(repeat_one_calls) == (2 if expected_repeat_one else 0)
    manifest = common.load_json(run_dir / "run_manifest.json")
    repeat_one_states = {
        manifest["logical_attempts"][f"0000/r001/{method}"]["state"] for method in common.METHODS
    }
    assert repeat_one_states == ({"succeeded"} if expected_repeat_one else {"not_scheduled_threshold"})


@pytest.mark.parametrize("target", ["metrics", "status", "qasm"])
def test_raw_tampering_fails_collection_or_manifest_validation_even_after_rehash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    run_dir, attempt_dir = _build_valid_run(tmp_path, monkeypatch)
    if target == "metrics":
        metrics = common.load_json(attempt_dir / "metrics.json")
        metrics["movement_batches"] = 999
        common.write_json_atomic(attempt_dir / "metrics.json", metrics)
    elif target == "status":
        status = common.load_json(attempt_dir / "status.json")
        status.update(outcome="timeout", failure_kind="timeout")
        common.write_json_atomic(attempt_dir / "status.json", status)
    else:
        (attempt_dir / "input.qasm").write_bytes(MINIMAL_QASM + b"// modified\n")

    unrehash_collection = collector.build_collection(run_dir)
    assert not unrehash_collection["passed"]

    # Simulate a deliberate edit that also refreshes the local file hashes.
    common.write_hash_manifests(attempt_dir)

    collection = collector.build_collection(run_dir)
    report = validator.validate_run(run_dir, run_dir / "derived", require_complete=True)

    if target in {"metrics", "qasm"}:
        assert not collection["passed"]
    assert not report["passed"]
    assert report["errors"]


@pytest.mark.parametrize(
    ("target", "expected_error"),
    [
        ("provenance", "method provenance differs"),
        ("host", "host_fingerprint_hash does not match run manifest"),
        ("missing_command_method", "command method does not match run manifest"),
        ("timeout", "timeout_sec does not match run manifest"),
    ],
)
def test_locked_provenance_and_command_fields_cannot_be_rehashed_away(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    expected_error: str,
) -> None:
    run_dir, attempt_dir = _build_valid_run(tmp_path, monkeypatch)
    status = common.load_json(attempt_dir / "status.json")
    command = common.load_json(attempt_dir / "command.json")
    if target == "provenance":
        payload = common.load_json(attempt_dir / "compiler_output.json")
        payload["method"]["implementation_directory"] = "ModifiedImplementation"
        common.write_json_atomic(attempt_dir / "compiler_output.json", payload)
        status["compiler_output_hash"] = common.canonical_hash(payload)
    elif target == "host":
        status["host_fingerprint_hash"] = "0" * 64
        command["host_fingerprint_hash"] = "0" * 64
    elif target == "missing_command_method":
        command.pop("method")
    else:
        status["timeout_sec"] = 11.0
        command["timeout_sec"] = 11.0
    common.write_json_atomic(attempt_dir / "status.json", status)
    common.write_json_atomic(attempt_dir / "command.json", command)
    common.write_hash_manifests(attempt_dir)

    report = validator.validate_run(run_dir, run_dir / "derived", require_complete=True)

    assert not report["passed"]
    assert any(expected_error in error for error in report["errors"])


def test_summary_tampering_fails_validator_even_if_derived_manifest_is_rehashed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir, _ = _build_valid_run(tmp_path, monkeypatch)
    summary_path = run_dir / "derived" / "summary.json"
    summary = common.load_json(summary_path)
    summary["successful_attempt_count"] = 999
    common.write_json_atomic(summary_path, summary)
    derived_manifest_path = run_dir / "derived" / "derived_manifest.json"
    derived_manifest = common.load_json(derived_manifest_path)
    derived_manifest["files"]["summary.json"] = {
        "sha256": common.sha256_file(summary_path),
        "size_bytes": summary_path.stat().st_size,
    }
    common.write_json_atomic(derived_manifest_path, derived_manifest)

    report = validator.validate_run(run_dir, run_dir / "derived", require_complete=True)

    assert not report["passed"]
    assert any("raw-data reconstruction" in error for error in report["errors"])


@pytest.mark.parametrize("target", ["summary_json", "summary_csv", "manifest"])
def test_fidelity_output_tampering_fails_optional_recomputation_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    run_dir, _ = _build_valid_run(tmp_path, monkeypatch)
    config_path = REPO_ROOT / "configs" / "fidelity_models.json"
    result = fidelity.build_fidelity(run_dir, config_path)
    fidelity_dir = run_dir / "derived" / "fidelity"
    fidelity.write_fidelity(result, fidelity_dir)
    baseline = validator.validate_run(run_dir, run_dir / "derived", require_complete=True)
    assert baseline["passed"], baseline["errors"]

    if target == "summary_json":
        path = fidelity_dir / "fidelity_summary.json"
        payload = common.load_json(path)
        payload["scenario_count"] = 999
        common.write_json_atomic(path, payload)
    elif target == "summary_csv":
        path = fidelity_dir / "fidelity_summary.csv"
        path.write_bytes(path.read_bytes() + b"tampered\n")
    else:
        path = fidelity_dir / "fidelity_manifest.json"
        payload = common.load_json(path)
        payload["result_hash"] = "0" * 64
        common.write_json_atomic(path, payload)

    report = validator.validate_run(run_dir, run_dir / "derived", require_complete=True)

    assert not report["passed"]
    assert any("fidelity" in error for error in report["errors"])


def test_archive_requires_complete_validation_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    observed: dict[str, Any] = {}

    def fake_validate(run_path: Path, summary_path: Path, require_complete: bool) -> Mapping[str, Any]:
        observed["require_complete"] = require_complete
        return {"passed": False, "complete": False, "pilot_gate_passed": False}

    monkeypatch.setattr(archive, "validate_run", fake_validate)

    with pytest.raises(RuntimeError, match="refusing to archive"):
        archive.main(["--run-dir", str(run_dir), "--output", str(tmp_path / "run.zip")])

    assert observed["require_complete"] is True
    assert not (tmp_path / "run.zip").exists()
