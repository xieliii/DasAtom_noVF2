#!/usr/bin/env python3
"""Capture a portable, non-secret environment record for a canonical run."""

from __future__ import annotations

import argparse
import importlib.metadata
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

from _common import (
    REPO_ROOT,
    SCHEMA_VERSION,
    git_state,
    host_identity,
    read_benchmark_list,
    sha256_file,
    utc_now,
    write_json_atomic,
)


DETERMINISTIC_VARIABLES = (
    "PYTHONHASHSEED",
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "RAYON_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "BLIS_NUM_THREADS",
    "QISKIT_PARALLEL",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("environment.json"))
    parser.add_argument("--benchmark-dir", type=Path, default=REPO_ROOT / "benchmarks" / "local64")
    parser.add_argument("--list", dest="list_path", type=Path, default=REPO_ROOT / "configs" / "local64.txt")
    return parser


def _run(command: list[str], timeout: float = 20.0) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "error": str(exc), "command": command}
    return {
        "available": True,
        "command": command,
        "return_code": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def _hardware_details() -> dict[str, Any]:
    result: dict[str, Any] = {
        "logical_cpu_count": os.cpu_count(),
        "processor": platform.processor(),
        "machine": platform.machine(),
    }
    try:
        import psutil
    except ImportError:
        result["psutil_available"] = False
    else:
        memory = psutil.virtual_memory()
        result.update(
            psutil_available=True,
            physical_cpu_count=psutil.cpu_count(logical=False),
            memory_total_bytes=memory.total,
        )
        frequency = psutil.cpu_freq()
        if frequency:
            result["cpu_frequency_mhz"] = {
                "current": frequency.current,
                "min": frequency.min,
                "max": frequency.max,
            }
    if os.name == "nt":
        result["windows_cim"] = _run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                (
                    "$cpu=Get-CimInstance Win32_Processor | Select-Object Name,Manufacturer,"
                    "NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed;"
                    "$cs=Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model,TotalPhysicalMemory;"
                    "@{cpu=$cpu;computer=$cs}|ConvertTo-Json -Depth 4 -Compress"
                ),
            ]
        )
        result["active_power_scheme"] = _run(["powercfg.exe", "/getactivescheme"])
    elif sys.platform == "darwin":
        result["mac_hardware"] = _run(["system_profiler", "SPHardwareDataType", "-json"])
    elif sys.platform.startswith("linux"):
        result["linux_cpuinfo_sha256"] = sha256_file("/proc/cpuinfo") if Path("/proc/cpuinfo").is_file() else None
        result["linux_meminfo"] = _run(["sed", "-n", "1,8p", "/proc/meminfo"])
    return result


def _packages() -> dict[str, Any]:
    distributions = sorted(
        (
            {
                "name": distribution.metadata.get("Name", distribution.metadata.get("Summary", "unknown")),
                "version": distribution.version,
            }
            for distribution in importlib.metadata.distributions()
        ),
        key=lambda item: (str(item["name"]).lower(), str(item["version"])),
    )
    return {
        "installed_distributions": distributions,
        "pip_freeze": _run([sys.executable, "-m", "pip", "freeze", "--all"], timeout=60.0),
    }


def _tracked_input_hashes(circuits: list[dict[str, Any]]) -> dict[str, Any]:
    important = [
        REPO_ROOT / "requirements.txt",
        REPO_ROOT / "requirements-repro.txt",
        REPO_ROOT / "configs" / "fidelity_models.json",
        REPO_ROOT / "scripts" / "run_compiler_once.py",
        REPO_ROOT / "scripts" / "run_canonical_pairwise.py",
    ]
    code_files = sorted(
        path
        for root in (REPO_ROOT / "canonical", REPO_ROOT / "DasAtom", REPO_ROOT / "DasAtom_Origin")
        for path in root.rglob("*.py")
        if path.is_file()
    )
    code_files.extend(
        path
        for path in (REPO_ROOT / "scripts").rglob("*")
        if path.is_file() and path.suffix.lower() in {".py", ".ps1"}
    )
    files: dict[str, str] = {}
    for path in [*important, *code_files]:
        if path.is_file():
            files[path.relative_to(REPO_ROOT).as_posix()] = sha256_file(path)
    return {
        "files": files,
        "benchmark_qasm": {str(circuit["name"]): circuit["sha256"] for circuit in circuits},
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    circuits = read_benchmark_list(args.list_path, args.benchmark_dir)
    record = {
        "schema_version": SCHEMA_VERSION,
        "captured_at_utc": utc_now(),
        "command": sys.argv,
        "host": host_identity(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "platform": platform.platform(),
            "architecture": platform.architecture(),
        },
        "hardware": _hardware_details(),
        "python": {
            "version": sys.version,
            "version_info": list(sys.version_info),
            "executable": sys.executable,
            "implementation": platform.python_implementation(),
        },
        "packages": _packages(),
        "git": git_state(),
        "deterministic_environment": {name: os.environ.get(name) for name in DETERMINISTIC_VARIABLES},
        "inputs": _tracked_input_hashes(circuits),
        "notes": {
            "secrets_captured": False,
            "wall_clock_runtime_source": "runner time.perf_counter_ns",
            "canonical_scope": "endpoint_layout_v1",
        },
    }
    write_json_atomic(args.output, record)
    print(f"wrote environment record to {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
