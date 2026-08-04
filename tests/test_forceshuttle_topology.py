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


def test_min_conflicts_embedding_is_deterministic_and_does_not_call_vf2(monkeypatch) -> None:
    module = _load_forceshuttle_functions()

    def forbidden_vf2(*args, **kwargs):
        raise AssertionError("ForceShuttle noVF2 optimization must not call VF2")

    monkeypatch.setattr(module, "rx_is_subgraph_iso", forbidden_vf2)
    nodes = sorted(module.generate_grid_with_Rb(3, 3, 2.0).nodes())
    previous = nodes[:8]
    gates = [
        [0, 1],
        [2, 3],
        [4, 5],
        [6, 7],
        [0, 2],
        [1, 3],
        [4, 6],
        [5, 7],
        [0, 4],
        [3, 7],
    ]

    first = module._min_conflicts_embedding(
        gates,
        previous,
        nodes,
        2.0,
        8,
        seed=17,
        restarts=5,
        max_steps=500,
    )
    second = module._min_conflicts_embedding(
        gates,
        previous,
        nodes,
        2.0,
        8,
        seed=17,
        restarts=5,
        max_steps=500,
    )

    assert first is not None
    assert first == second
    assert len(set(first)) == len(first)
    assert all(not module._gate_violates_rb(gate, first, 2.0) for gate in gates)


def test_dependency_layer_prefix_preserves_each_qubit_order() -> None:
    module = _load_forceshuttle_functions()
    gates = [[0, 1], [2, 3], [1, 4], [3, 5], [0, 2], [4, 5]]
    layers = module._dependency_layers(gates)
    flattened = sum(layers, [])

    for qubit in range(6):
        source = [gate for gate in gates if qubit in gate]
        scheduled = [gate for gate in flattened if qubit in gate]
        assert scheduled == source
