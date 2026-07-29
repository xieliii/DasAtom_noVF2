from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
QASM = REPOSITORY_ROOT / "benchmarks" / "local64" / "3_regular_10.qasm"


def _run_once(tmp_path: Path, method: str, suffix: str) -> dict:
    output = tmp_path / f"{method}-{suffix}"
    env = os.environ.copy()
    env.update(
        {
            "PYTHONHASHSEED": "0",
            "DASATOM_SEED": "0",
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "RAYON_NUM_THREADS": "1",
        }
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(REPOSITORY_ROOT / "scripts" / "run_compiler_once.py"),
            "--method",
            method,
            "--qasm",
            str(QASM),
            "--output",
            str(output),
            "--seed",
            "0",
        ],
        cwd=REPOSITORY_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads((output / "compiler_output.json").read_text(encoding="utf-8"))


@pytest.mark.slow
def test_both_compilers_pass_and_forceshuttle_is_deterministic(tmp_path: Path) -> None:
    forceshuttle_a = _run_once(tmp_path, "forceshuttle", "a")
    forceshuttle_b = _run_once(tmp_path, "forceshuttle", "b")
    dasatom = _run_once(tmp_path, "dasatom", "a")

    assert forceshuttle_a["verification"]["passed"]
    assert dasatom["verification"]["passed"]
    assert forceshuttle_a["schedule_hash"] == forceshuttle_b["schedule_hash"]

    source_q5 = [
        operation["operation_id"]
        for operation in forceshuttle_a["circuit"]["two_qubit_operations"]
        if 5 in operation["qubits"]
    ]
    scheduled_q5 = [
        operation["operation_id"]
        for group in forceshuttle_a["compiler"]["parallel_groups"]
        for operation in group["operations"]
        if 5 in operation["qubits"]
    ]
    assert scheduled_q5 == source_q5
