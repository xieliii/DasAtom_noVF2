#!/usr/bin/env python3
"""Shared, dependency-light helpers for the canonical rerun scripts."""

from __future__ import annotations

import csv
import ctypes
import hashlib
import io
import json
import os
import platform
import re
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SCHEMA_VERSION = "canonical-rerun-v1"
DEFAULT_METHODS = ("forceshuttle", "dasatom")
ABLATION_METHODS = ("fs_no_mcts", "fs_no_force", "fs_no_lookahead")
METHODS = DEFAULT_METHODS
SUPPORTED_METHODS = DEFAULT_METHODS + ABLATION_METHODS
METHOD_PROVENANCE_SPECS = {
    "forceshuttle": {
        "implementation_directory": "DasAtom",
        "engine": "noVF2",
        "algorithm_files": (
            "DasAtom.py",
            "DasAtom_fun.py",
            "mcts_mapper.py",
            "analytical_placer.py",
            "Enola/route.py",
        ),
        "ablation_mode": "full",
    },
    "fs_no_mcts": {
        "implementation_directory": "DasAtom",
        "engine": "noVF2",
        "ablation_mode": "no_mcts",
        "algorithm_files": ("DasAtom.py", "DasAtom_fun.py", "mcts_mapper.py", "analytical_placer.py", "Enola/route.py"),
    },
    "fs_no_force": {
        "implementation_directory": "DasAtom",
        "engine": "noVF2",
        "ablation_mode": "no_force",
        "algorithm_files": ("DasAtom.py", "DasAtom_fun.py", "mcts_mapper.py", "analytical_placer.py", "Enola/route.py"),
    },
    "fs_no_lookahead": {
        "implementation_directory": "DasAtom",
        "engine": "noVF2",
        "ablation_mode": "no_lookahead",
        "algorithm_files": ("DasAtom.py", "DasAtom_fun.py", "mcts_mapper.py", "analytical_placer.py", "Enola/route.py"),
    },
    "dasatom": {
        "implementation_directory": "DasAtom_Origin",
        "engine": None,
        "algorithm_files": ("DasAtom.py", "DasAtom_fun.py", "Enola/route.py"),
    },
}
SUCCESS_OUTCOME = "succeeded"
TERMINAL_OUTCOMES = {SUCCESS_OUTCOME, "failed", "timeout", "invalid", "interrupted"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def load_json(path: Path | str) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _fallback_canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")


def canonical_json_bytes(value: Any) -> bytes:
    try:
        from canonical.serialization import canonical_json_bytes as implementation
    except (ImportError, AttributeError):
        return _fallback_canonical_bytes(value)
    return implementation(value)


def canonical_hash(value: Any) -> str:
    try:
        from canonical.serialization import canonical_sha256
    except (ImportError, AttributeError):
        return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    return canonical_sha256(value)


def write_bytes_atomic(path: Path | str, data: bytes) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def write_text_atomic(path: Path | str, text: str) -> None:
    write_bytes_atomic(path, text.encode("utf-8"))


def write_json_atomic(path: Path | str, value: Any) -> None:
    destination = Path(path)
    try:
        from canonical.serialization import atomic_write_json
    except (ImportError, AttributeError):
        write_bytes_atomic(destination, canonical_json_bytes(value))
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(destination, value)


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return cleaned or "unnamed"


def normalize_methods(values: Sequence[str]) -> list[str]:
    aliases = {
        "force": "forceshuttle",
        "forceshuttle": "forceshuttle",
        "das": "dasatom",
        "dasatom": "dasatom",
        "fs_no_mcts": "fs_no_mcts",
        "fs_no_force": "fs_no_force",
        "fs_no_lookahead": "fs_no_lookahead",
    }
    result: list[str] = []
    for value in values:
        key = value.strip().lower()
        if key not in aliases:
            raise ValueError(f"unknown method {value!r}; choose from {', '.join(SUPPORTED_METHODS)}")
        normalized = aliases[key]
        if normalized not in result:
            result.append(normalized)
    if not result:
        raise ValueError("at least one method is required")
    return result


def read_benchmark_list(list_path: Path | str, benchmark_dir: Path | str) -> list[dict[str, Any]]:
    list_file = Path(list_path).resolve()
    root = Path(benchmark_dir).resolve()
    if root.name != "local64":
        raise ValueError(f"benchmark directory must be the explicit flat local64 directory: {root}")
    names: list[str] = []
    for line_number, raw_line in enumerate(list_file.read_text(encoding="utf-8-sig").splitlines(), 1):
        value = raw_line.strip()
        if not value or value.startswith("#"):
            continue
        path_value = Path(value)
        if path_value.name != value or path_value.suffix.lower() != ".qasm":
            raise ValueError(f"{list_file}:{line_number}: expected a QASM basename, got {value!r}")
        names.append(value)
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"duplicate benchmark names in {list_file}: {duplicates}")
    circuits: list[dict[str, Any]] = []
    for index, name in enumerate(names):
        path = root / name
        if not path.is_file():
            raise FileNotFoundError(f"listed benchmark is missing: {path}")
        circuits.append(
            {
                "index": index,
                "name": name,
                "path": str(path),
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )
    return circuits


def git_state(repo_root: Path | str = REPO_ROOT) -> dict[str, Any]:
    root = Path(repo_root)

    def run(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args], cwd=root, check=True, capture_output=True, text=True, encoding="utf-8"
        )
        return completed.stdout.strip()

    try:
        commit = run("rev-parse", "HEAD")
        branch = run("rev-parse", "--abbrev-ref", "HEAD")
        status = run("status", "--porcelain=v1", "--untracked-files=normal")
        remote = run("config", "--get", "remote.origin.url")
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        return {"available": False, "error": str(exc), "commit": None, "branch": None, "dirty": None}
    return {
        "available": True,
        "commit": commit,
        "branch": branch,
        "dirty": bool(status),
        "status_porcelain": status.splitlines(),
        "remote_origin": remote or None,
    }


def source_snapshot(repo_root: Path | str = REPO_ROOT) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    files: list[Path] = []
    for directory_name in ("canonical", "DasAtom", "DasAtom_Origin"):
        directory = root / directory_name
        if directory.is_dir():
            files.extend(path for path in directory.rglob("*.py") if path.is_file())
    scripts_dir = root / "scripts"
    if scripts_dir.is_dir():
        files.extend(
            path
            for path in scripts_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in {".py", ".ps1"}
        )
    mapping = {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(set(files))
    }
    return {"algorithm": "sha256", "files": mapping, "tree_hash": canonical_hash(mapping)}


def host_identity() -> dict[str, Any]:
    return {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": sys.version,
        "python_executable": sys.executable,
        "pid": os.getpid(),
    }


def host_fingerprint() -> dict[str, Any]:
    return {
        "hostname": socket.gethostname(),
        "system": platform.system(),
        "machine": platform.machine(),
        "python_major_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
        "python_executable": os.path.normcase(str(Path(sys.executable).resolve())),
    }


def method_provenance_from_snapshot(method: str, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    if method not in METHOD_PROVENANCE_SPECS:
        raise ValueError(f"unknown method for provenance: {method}")
    spec = METHOD_PROVENANCE_SPECS[method]
    source_files = snapshot.get("files", {})
    implementation = str(spec["implementation_directory"])
    algorithm_hashes: dict[str, str] = {}
    for relative in spec["algorithm_files"]:
        source_key = f"{implementation}/{relative}"
        value = source_files.get(source_key)
        if not isinstance(value, str):
            raise ValueError(f"source snapshot is missing method file {source_key}")
        algorithm_hashes[str(relative)] = value
    return {
        "id": method,
        "implementation_directory": implementation,
        "engine": spec["engine"],
        "ablation_mode": spec.get("ablation_mode"),
        "algorithm_file_sha256": algorithm_hashes,
    }


def payload_provenance_errors(
    payload: Mapping[str, Any], *, expected: Mapping[str, Any]
) -> list[str]:
    errors: list[str] = []
    method = payload.get("method")
    expected_method = expected.get("method_provenance")
    if expected_method is not None and canonical_hash(method) != canonical_hash(expected_method):
        errors.append("compiler payload method provenance differs from the locked source snapshot")
    input_record = payload.get("input", {})
    if input_record.get("filename") != "input.qasm":
        errors.append("compiler payload input.filename must be input.qasm")
    if expected.get("input_sha256") is not None and input_record.get("qasm_sha256") != expected.get("input_sha256"):
        errors.append("compiler payload input QASM hash differs from the locked attempt input")
    config = payload.get("config", {})
    if expected.get("seed") is not None and config.get("seed") != expected.get("seed"):
        errors.append("compiler payload seed differs from the locked attempt seed")
    return errors


class RunLock(AbstractContextManager["RunLock"]):
    def __init__(self, run_dir: Path, *, force_unlock: bool = False) -> None:
        self.path = run_dir / ".canonical-run.lock"
        self.force_unlock = force_unlock
        self.acquired = False

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.force_unlock:
            self.path.unlink(missing_ok=True)
        if self.path.exists():
            try:
                owner = load_json(self.path)
            except (OSError, ValueError) as exc:
                raise RuntimeError(
                    f"existing lock is unreadable; inspect it and use --force-unlock only if no run is active: "
                    f"{self.path}: {exc}"
                ) from exc
            same_host = owner.get("hostname") == socket.gethostname()
            owner_pid = owner.get("pid")
            if (
                same_host
                and isinstance(owner_pid, int)
                and process_matches_start(owner_pid, owner.get("started_epoch_ns"))
            ):
                raise RuntimeError(
                    f"run directory is locked by active PID {owner_pid} on {owner.get('hostname')}: {self.path}"
                )
            if not same_host:
                raise RuntimeError(
                    f"run directory has a lock from host {owner.get('hostname')!r}; use --force-unlock only after "
                    f"confirming no other machine is writing it: {self.path}"
                )
            stale_suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            stale_path = self.path.with_name(f"{self.path.name}.stale.{stale_suffix}")
            os.replace(self.path, stale_path)
        payload = canonical_json_bytes(
            {
                "schema_version": SCHEMA_VERSION,
                "hostname": socket.gethostname(),
                "pid": os.getpid(),
                "started_at_utc": utc_now(),
                "started_epoch_ns": time.time_ns(),
                "command": sys.argv,
                "python_executable": sys.executable,
            }
        )
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            try:
                owner = self.path.read_text(encoding="utf-8").strip()
            except OSError:
                owner = "<unreadable>"
            raise RuntimeError(
                f"run directory is locked by another invocation: {self.path}\nlock record: {owner}"
            ) from exc
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        self.acquired = True
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.acquired:
            self.path.unlink(missing_ok=True)
            self.acquired = False


def process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _windows_process_creation_epoch_ns(pid: int) -> int | None:
    if os.name != "nt":
        return None

    class FileTime(ctypes.Structure):
        _fields_ = [("low", ctypes.c_ulong), ("high", ctypes.c_ulong)]

    process_query_limited_information = 0x1000
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return None
    try:
        creation = FileTime()
        exit_time = FileTime()
        kernel = FileTime()
        user = FileTime()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
        windows_ticks = (int(creation.high) << 32) | int(creation.low)
        return windows_ticks * 100 - 11_644_473_600_000_000_000
    finally:
        kernel32.CloseHandle(handle)


def process_matches_start(pid: int, expected_epoch_ns: Any) -> bool:
    if not process_is_alive(pid):
        return False
    if os.name != "nt" or not isinstance(expected_epoch_ns, int):
        return True
    observed = _windows_process_creation_epoch_ns(pid)
    return observed is not None and abs(observed - expected_epoch_ns) <= 10_000_000_000


def _hashable_attempt_files(attempt_dir: Path) -> list[Path]:
    ignored = {"hashes.json", "files.sha256"}
    return sorted(
        path
        for path in attempt_dir.rglob("*")
        if path.is_file() and path.name not in ignored and not path.name.startswith(".tmp-")
    )


def build_hash_manifest(attempt_dir: Path) -> dict[str, Any]:
    files: dict[str, Any] = {}
    for path in _hashable_attempt_files(attempt_dir):
        relative = path.relative_to(attempt_dir).as_posix()
        files[relative] = {"sha256": sha256_file(path), "size_bytes": path.stat().st_size}
    return {"schema_version": SCHEMA_VERSION, "algorithm": "sha256", "files": files}


def write_hash_manifests(attempt_dir: Path) -> dict[str, Any]:
    manifest = build_hash_manifest(attempt_dir)
    write_json_atomic(attempt_dir / "hashes.json", manifest)
    lines = [f"{entry['sha256']}  {name}" for name, entry in manifest["files"].items()]
    write_text_atomic(attempt_dir / "files.sha256", "\n".join(lines) + ("\n" if lines else ""))
    return manifest


def validate_hash_manifests(attempt_dir: Path) -> list[str]:
    errors: list[str] = []
    hashes_path = attempt_dir / "hashes.json"
    text_path = attempt_dir / "files.sha256"
    if not hashes_path.is_file():
        return [f"missing {hashes_path}"]
    if not text_path.is_file():
        return [f"missing {text_path}"]
    try:
        recorded = load_json(hashes_path)
    except (OSError, ValueError) as exc:
        return [f"cannot read {hashes_path}: {exc}"]
    actual = build_hash_manifest(attempt_dir)
    if canonical_hash(recorded) != canonical_hash(actual):
        recorded_files = recorded.get("files", {}) if isinstance(recorded, dict) else {}
        actual_files = actual["files"]
        for name in sorted(set(recorded_files) | set(actual_files)):
            if recorded_files.get(name) != actual_files.get(name):
                errors.append(f"hash/size mismatch for {attempt_dir / name}")
        if not errors:
            errors.append(f"hash manifest metadata mismatch: {hashes_path}")
    expected_text = "".join(
        f"{entry['sha256']}  {name}\n" for name, entry in actual["files"].items()
    )
    try:
        observed_text = text_path.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"cannot read {text_path}: {exc}")
    else:
        if observed_text != expected_text:
            errors.append(f"text hash manifest mismatch: {text_path}")
    return errors


def verify_compiler_output(
    payload: Mapping[str, Any], *, qasm_path: Path | str | None = None
) -> dict[str, Any]:
    from canonical.verifier import verify_compiler_output as implementation

    return implementation(payload, qasm_path=qasm_path, raise_on_error=False)


def compute_metrics(payload: Mapping[str, Any]) -> dict[str, Any]:
    from canonical.metrics import compute_metrics as implementation

    return implementation(payload)


def attempt_identity(status: Mapping[str, Any]) -> tuple[int, int, str]:
    return (int(status["circuit_index"]), int(status["repetition"]), str(status["method"]))


def validate_attempt(
    attempt_dir: Path,
    *,
    expected: Mapping[str, Any] | None = None,
    require_success: bool = False,
) -> dict[str, Any]:
    errors = validate_hash_manifests(attempt_dir)
    warnings: list[str] = []
    status_path = attempt_dir / "status.json"
    try:
        status = load_json(status_path)
    except (OSError, ValueError) as exc:
        return {"passed": False, "errors": errors + [f"cannot read {status_path}: {exc}"], "warnings": []}

    outcome = status.get("outcome")
    if outcome not in TERMINAL_OUTCOMES:
        errors.append(f"non-terminal or unknown outcome in {status_path}: {outcome!r}")
    if require_success and outcome != SUCCESS_OUTCOME:
        errors.append(f"attempt is not successful: {attempt_dir} ({outcome})")
    required_base = ("input.qasm", "command.json", "status.json", "stdout.log", "stderr.log")
    for name in required_base:
        if not (attempt_dir / name).is_file():
            errors.append(f"missing attempt file: {attempt_dir / name}")

    input_path = attempt_dir / "input.qasm"
    if input_path.is_file():
        input_hash = sha256_file(input_path)
        if status.get("input_sha256") != input_hash:
            errors.append(f"status input SHA256 mismatch: {status_path}")
    else:
        input_hash = None

    try:
        command = load_json(attempt_dir / "command.json")
    except (OSError, ValueError) as exc:
        errors.append(f"cannot read command.json in {attempt_dir}: {exc}")
        command = {}
    for field in (
        "method",
        "seed",
        "repetition",
        "circuit_index",
        "circuit_name",
        "git_commit",
        "host_fingerprint_hash",
    ):
        if field in command and field in status and command[field] != status[field]:
            errors.append(f"command/status mismatch for {field}: {attempt_dir}")
    if command.get("timeout_sec") != status.get("timeout_sec"):
        errors.append(f"command/status mismatch for timeout_sec: {attempt_dir}")
    if command.get("input_sha256") != input_hash:
        errors.append(f"command input SHA256 mismatch: {attempt_dir / 'command.json'}")

    elapsed_seconds = status.get("external_elapsed_seconds")
    elapsed_ns = status.get("external_elapsed_ns")
    if not status.get("ended_at_utc"):
        errors.append(f"terminal status has no ended_at_utc: {status_path}")
    if not numeric(elapsed_seconds) or float(elapsed_seconds) < 0.0:
        errors.append(f"terminal status has invalid external_elapsed_seconds: {status_path}")
    if not isinstance(elapsed_ns, int) or isinstance(elapsed_ns, bool) or elapsed_ns < 0:
        errors.append(f"terminal status has invalid external_elapsed_ns: {status_path}")
    elif numeric(elapsed_seconds) and abs(elapsed_ns / 1_000_000_000.0 - float(elapsed_seconds)) > 1e-6:
        errors.append(f"terminal elapsed seconds/nanoseconds disagree: {status_path}")
    failure_kind = status.get("failure_kind")
    if outcome == SUCCESS_OUTCOME:
        if status.get("return_code") != 0 or failure_kind is not None:
            errors.append(f"succeeded status must have return_code=0 and failure_kind=null: {status_path}")
    elif outcome == "timeout":
        if failure_kind != "timeout":
            errors.append(f"timeout status must have failure_kind=timeout: {status_path}")
        timeout_sec = status.get("timeout_sec")
        if numeric(timeout_sec) and numeric(elapsed_seconds):
            tolerance = max(1e-6, float(timeout_sec) * 1e-6)
            if float(elapsed_seconds) + tolerance < float(timeout_sec):
                errors.append(f"timeout status elapsed less than configured timeout: {status_path}")
    elif outcome == "interrupted" and failure_kind != "user_interrupt":
        errors.append(f"interrupted status must have failure_kind=user_interrupt: {status_path}")
    elif outcome in {"failed", "invalid"} and not failure_kind:
        errors.append(f"{outcome} status must include failure_kind: {status_path}")

    if expected:
        expected_fields = {
            "method": expected.get("method"),
            "seed": expected.get("seed"),
            "repetition": expected.get("repetition"),
            "circuit_index": expected.get("circuit_index"),
            "circuit_name": expected.get("circuit_name"),
            "input_sha256": expected.get("input_sha256"),
            "timeout_sec": expected.get("timeout_sec"),
            "git_commit": expected.get("git_commit"),
            "host_fingerprint_hash": expected.get("host_fingerprint_hash"),
        }
        for field, value in expected_fields.items():
            if value is None:
                continue
            if status.get(field) != value:
                errors.append(f"status {field} does not match run manifest in {attempt_dir}")
            if command.get(field) != value:
                errors.append(f"command {field} does not match run manifest in {attempt_dir}")

    verification: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None
    payload: dict[str, Any] | None = None
    if outcome == SUCCESS_OUTCOME:
        for name in ("compiler_output.json", "verification.json", "metrics.json"):
            if not (attempt_dir / name).is_file():
                errors.append(f"successful attempt is missing {attempt_dir / name}")
        if not errors:
            try:
                payload = load_json(attempt_dir / "compiler_output.json")
                recorded_verification = load_json(attempt_dir / "verification.json")
                recorded_metrics = load_json(attempt_dir / "metrics.json")
                verification = verify_compiler_output(payload, qasm_path=input_path)
                metrics = compute_metrics(payload)
            except Exception as exc:  # Canonical validation failures need to be reported, not hidden.
                errors.append(f"canonical recomputation failed for {attempt_dir}: {type(exc).__name__}: {exc}")
            else:
                if not verification.get("passed", False):
                    errors.append(f"canonical verifier rejected successful attempt {attempt_dir}")
                if canonical_hash(recorded_verification) != canonical_hash(verification):
                    errors.append(f"verification.json was modified or is stale: {attempt_dir}")
                if canonical_hash(recorded_metrics) != canonical_hash(metrics):
                    errors.append(f"metrics.json was modified or is stale: {attempt_dir}")
                embedded_verification = payload.get("verification")
                if not isinstance(embedded_verification, Mapping) or not embedded_verification.get("passed", False):
                    errors.append(f"compiler_output does not retain a passing pre-integrity self-check: {attempt_dir}")
                embedded_metrics = payload.get("metrics")
                if embedded_metrics is not None and canonical_hash(embedded_metrics) != canonical_hash(metrics):
                    errors.append(f"compiler_output embedded metrics disagree with recomputation: {attempt_dir}")
                if status.get("verification_hash") != canonical_hash(verification):
                    errors.append(f"status verification hash mismatch: {attempt_dir}")
                if status.get("metrics_hash") != canonical_hash(metrics):
                    errors.append(f"status metrics hash mismatch: {attempt_dir}")
                if status.get("compiler_output_hash") != canonical_hash(payload):
                    errors.append(f"status compiler_output hash mismatch: {attempt_dir}")
                errors.extend(
                    f"{message}: {attempt_dir}"
                    for message in payload_provenance_errors(payload, expected=expected or {})
                )
    elif (attempt_dir / "compiler_output.json").is_file():
        warnings.append(f"non-successful attempt retained partial compiler_output.json: {attempt_dir}")

    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "status": status,
        "command": command,
        "compiler_output": payload,
        "verification": verification,
        "metrics": metrics,
    }


def flatten_mapping(value: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, Mapping):
            result.update(flatten_mapping(item, name))
        elif isinstance(item, (list, tuple)):
            result[name] = json.dumps(item, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        else:
            result[name] = item
    return result


def csv_bytes(rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str] | None = None) -> bytes:
    if fieldnames is None:
        names: set[str] = set()
        for row in rows:
            names.update(row)
        fieldnames = sorted(names)
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(fieldnames), extrasaction="raise", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({name: row.get(name) for name in fieldnames})
    return stream.getvalue().encode("utf-8")


def latest_execution_dirs(run_dir: Path) -> dict[tuple[int, int, str], list[Path]]:
    grouped: dict[tuple[int, int, str], list[Path]] = {}
    for status_path in sorted((run_dir / "attempts").glob("**/execution_*/status.json")):
        try:
            status = load_json(status_path)
            identity = attempt_identity(status)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        grouped.setdefault(identity, []).append(status_path.parent)
    for paths in grouped.values():
        paths.sort(key=lambda path: path.name)
    return grouped


def select_logical_execution(paths: Sequence[Path]) -> Path:
    successful: list[Path] = []
    for path in paths:
        try:
            if load_json(path / "status.json").get("outcome") == SUCCESS_OUTCOME:
                successful.append(path)
        except (OSError, ValueError):
            pass
    if len(successful) > 1:
        raise ValueError(f"multiple successful executions for one logical attempt: {successful}")
    return successful[0] if successful else paths[-1]


def relative_to_repo(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def median(values: Iterable[float]) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("median requires at least one value")
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0
