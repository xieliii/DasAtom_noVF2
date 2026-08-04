from __future__ import annotations

import importlib.util
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _load_module():
    path = REPOSITORY_ROOT / "scripts" / "compare_frozen_baseline.py"
    spec = importlib.util.spec_from_file_location("compare_frozen_baseline", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_aggregate_reports_geometric_mean_and_win_tie_loss() -> None:
    module = _load_module()
    result = module._aggregate([2.0, 1.0, 0.5])
    assert result["count"] == 3
    assert result["geometric_mean"] == 1.0
    assert result["median"] == 1.0
    assert result["wins"] == 1
    assert result["ties"] == 1
    assert result["losses"] == 1


def test_host_compatibility_ignores_python_executable_path() -> None:
    module = _load_module()
    baseline = {
        "hostname": "HOST",
        "system": "Windows",
        "machine": "AMD64",
        "python_major_minor": "3.12",
        "python_executable": "d:/old/.venv/python.exe",
    }
    candidate = {**baseline, "python_executable": "d:/new/.venv/python.exe"}
    passed, errors = module._host_compatibility(candidate, baseline)
    assert passed
    assert errors == []


def test_successful_runtime_distinguishes_timeout_from_comparable_row() -> None:
    module = _load_module()
    assert (
        module._successful_runtime(
            {"successful_attempts": "0", "external_elapsed_seconds_median": ""},
            label="timeout",
        )
        is None
    )
    assert module._successful_runtime(
        {"successful_attempts": "3", "external_elapsed_seconds_median": "1.25"},
        label="success",
    ) == 1.25
