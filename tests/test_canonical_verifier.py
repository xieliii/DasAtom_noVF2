from __future__ import annotations

from copy import deepcopy

from canonical import compute_metrics, finalize_integrity, verify_compiler_output


def _base_payload() -> dict:
    operations = [
        {"operation_id": "q2_00000000", "source_index": 0, "name": "cz", "qubits": [0, 1]},
        {"operation_id": "q2_00000001", "source_index": 1, "name": "cz", "qubits": [2, 3]},
        {"operation_id": "q2_00000002", "source_index": 2, "name": "cz", "qubits": [1, 4]},
    ]
    partition_operations = [
        {"operation_id": operation["operation_id"], "qubits": operation["qubits"]}
        for operation in operations
    ]
    payload = {
        "schema_version": "forceshuttle.compiler_output.v1",
        "scope": {
            "name": "endpoint_layout_v1",
            "continuous_motion_verified": False,
            "one_qubit_policy": "counted_not_scheduled",
            "unused_declared_qubits_policy": "outside_2q_active_span_not_embedded",
        },
        "method": {"id": "forceshuttle", "implementation_directory": "DasAtom", "engine": "noVF2"},
        "input": {"filename": "synthetic.qasm", "qasm_sha256": "0" * 64},
        "config": {
            "seed": 0,
            "interaction_radius_grid": 2.0,
            "exclusion_radius_grid": 4.0,
            "grid_pitch_x_um": 3.0,
            "grid_pitch_y_um": 3.0,
        },
        "circuit": {
            "filename": "synthetic.qasm",
            "qasm_sha256": "0" * 64,
            "declared_qubit_count": 5,
            "layout_qubit_count": 5,
            "operation_count": 3,
            "one_qubit_operation_count": 0,
            "two_qubit_operation_count": 3,
            "nonunitary_operation_count": 0,
            "unsupported_operation_count": 0,
            "two_qubit_operations": operations,
        },
        "compiler": {
            "layout_qubit_count": 5,
            "grid_size": 7,
            "partitions": [{"partition_index": 0, "operations": partition_operations}],
            "embeddings": [
                {
                    "partition_index": 0,
                    "positions": [[0, 0], [1, 0], [6, 0], [6, 1], [1, 1]],
                }
            ],
            "parallel_groups": [
                {
                    "partition_index": 0,
                    "group_index": 0,
                    "operations": [partition_operations[0], partition_operations[1]],
                },
                {
                    "partition_index": 0,
                    "group_index": 1,
                    "operations": [partition_operations[2]],
                },
            ],
            "movement_transitions": [],
            "legacy_metrics": {},
        },
    }
    payload["metrics"] = compute_metrics(payload)
    return finalize_integrity(payload)


def _rehash(payload: dict) -> dict:
    clean = deepcopy(payload)
    clean.pop("integrity", None)
    clean.pop("schedule_hash", None)
    return finalize_integrity(clean)


def _error_codes(payload: dict) -> set[str]:
    return {error["code"] for error in verify_compiler_output(payload)["errors"]}


def test_valid_payload_passes() -> None:
    assert verify_compiler_output(_base_payload())["passed"]


def test_independent_operation_reorder_is_allowed() -> None:
    payload = _base_payload()
    payload["compiler"]["parallel_groups"][0]["operations"].reverse()
    assert verify_compiler_output(_rehash(payload))["passed"]


def test_dependent_operation_reorder_fails_fifo() -> None:
    payload = _base_payload()
    groups = payload["compiler"]["parallel_groups"]
    groups[0]["operations"] = [groups[1]["operations"][0], groups[0]["operations"][1]]
    groups[1]["operations"] = [payload["compiler"]["partitions"][0]["operations"][0]]
    assert "qubit_fifo" in _error_codes(_rehash(payload))


def test_missing_and_duplicate_occurrences_fail() -> None:
    missing = _base_payload()
    missing["compiler"]["partitions"][0]["operations"].pop()
    assert "missing_operations" in _error_codes(_rehash(missing))

    duplicate = _base_payload()
    duplicate["compiler"]["partitions"][0]["operations"].append(
        deepcopy(duplicate["compiler"]["partitions"][0]["operations"][0])
    )
    assert "duplicate_operations" in _error_codes(_rehash(duplicate))


def test_embedding_collision_and_rb_violation_fail() -> None:
    collision = _base_payload()
    collision["compiler"]["embeddings"][0]["positions"][1] = [0, 0]
    assert "embedding_collision" in _error_codes(_rehash(collision))

    rb_violation = _base_payload()
    rb_violation["compiler"]["embeddings"][0]["positions"][1] = [3, 0]
    assert "rb_violation" in _error_codes(_rehash(rb_violation))


def test_parallel_re_exclusion_violation_fails() -> None:
    payload = _base_payload()
    positions = payload["compiler"]["embeddings"][0]["positions"]
    positions[2] = [0, 1]
    positions[3] = [0, 2]
    assert "re_violation" in _error_codes(_rehash(payload))
