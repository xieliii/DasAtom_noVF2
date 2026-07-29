from __future__ import annotations

import importlib
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _load_forceshuttle_functions():
    sys.modules.pop("DasAtom_fun", None)
    sys.path.insert(0, str(REPOSITORY_ROOT / "DasAtom"))
    try:
        return importlib.import_module("DasAtom_fun")
    finally:
        sys.path.remove(str(REPOSITORY_ROOT / "DasAtom"))


def test_dependency_closed_split_does_not_overtake_deferred_gate() -> None:
    module = _load_forceshuttle_functions()
    gates = [[0, 1], [2, 3], [1, 4]]
    embedding = [(0, 0), (3, 0), (0, 1), (1, 1), (3, 1)]
    executable, deferred = module._split_dependency_closed_executable_gates(gates, embedding, 2.0)
    assert executable == [[2, 3]]
    assert deferred == [[0, 1], [1, 4]]
