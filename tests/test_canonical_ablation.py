from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from canonical.adapter import compile_single_qasm
from canonical.verifier import verify_compiler_output


ROOT = Path(__file__).resolve().parents[1]
QASM = ROOT / "benchmarks" / "local64" / "3_regular_10.qasm"
PHYSICAL_FIELDS = ("layout_qubit_count", "grid_size", "partitions", "embeddings", "parallel_groups", "movement_transitions")
FROZEN_SMALL_PHYSICAL_HASH = "9a84da82b38336d701f7f97b56b824e473daf0f719399d4b565c7a3f09baf83c"


@pytest.fixture(scope="module")
def outputs():
    return {method: compile_single_qasm(method, QASM, repository_root=ROOT, seed=0) for method in ("forceshuttle", "fs_no_mcts", "fs_no_force", "fs_no_lookahead")}


def test_all_ablation_methods_verify_and_are_deterministic(outputs):
    for method, payload in outputs.items():
        assert payload["verification"]["passed"], method
        again = compile_single_qasm(method, QASM, repository_root=ROOT, seed=0)
        assert payload["schedule_hash"] == again["schedule_hash"]


def test_full_mode_preserves_frozen_small_schedule(outputs):
    candidate = outputs["forceshuttle"]
    projection = {field: candidate["compiler"][field] for field in PHYSICAL_FIELDS}
    projection["metrics"] = candidate["metrics"]
    encoded = (json.dumps(projection, sort_keys=True, separators=(",", ":")) + "\n").encode()
    assert hashlib.sha256(encoded).hexdigest() == FROZEN_SMALL_PHYSICAL_HASH


def test_disabled_component_diagnostics(outputs):
    assert outputs["forceshuttle"]["compiler"]["ablation_diagnostics"]["mcts_call_count"] > 0
    assert outputs["fs_no_mcts"]["compiler"]["ablation_diagnostics"]["mcts_call_count"] == 0
    assert outputs["fs_no_force"]["compiler"]["ablation_diagnostics"]["force_directed_call_count"] == 0
    no_lookahead = outputs["fs_no_lookahead"]["compiler"]["ablation_diagnostics"]
    assert no_lookahead["future_lookahead_enabled"] is False
    assert no_lookahead["future_lookahead_query_count"] == 0


@pytest.mark.parametrize("method", ("forceshuttle", "fs_no_mcts", "fs_no_force", "fs_no_lookahead"))
def test_all_force_variants_exclude_vf2(monkeypatch, method):
    import canonical.adapter as adapter

    original = adapter._install_capture_hooks

    def install_forbidden_vf2(module, state):
        original(module, state)

        def forbidden(*args, **kwargs):
            raise AssertionError("canonical ForceShuttle ablations must not call VF2")

        module.rx_is_subgraph_iso = forbidden
        module.get_rx_one_mapping = forbidden
        module.SingleFileProcessor.__init__.__globals__["rx_is_subgraph_iso"] = forbidden
        module.SingleFileProcessor.__init__.__globals__["get_rx_one_mapping"] = forbidden

    monkeypatch.setattr(adapter, "_install_capture_hooks", install_forbidden_vf2)
    assert compile_single_qasm(method, QASM, repository_root=ROOT, seed=0)["verification"]["passed"]


@pytest.mark.parametrize("field,value", [("ablation_mode", "full"), ("mcts_call_count", 99)])
def test_tampered_diagnostics_fail_verification(outputs, field, value):
    payload = copy.deepcopy(outputs["fs_no_mcts"])
    payload["compiler"]["ablation_diagnostics"][field] = value
    assert not verify_compiler_output(payload, qasm_path=QASM)["passed"]
