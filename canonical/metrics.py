"""Canonical endpoint-layout metrics derived only from captured raw structures."""

from __future__ import annotations

import math
from typing import Any


def _distance_um(source: list[float], destination: list[float], pitch_x: float, pitch_y: float) -> float:
    return math.hypot(
        (float(destination[0]) - float(source[0])) * pitch_x,
        (float(destination[1]) - float(source[1])) * pitch_y,
    )


def compute_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    """Recompute unambiguous counts and distance proxies from compiler output.

    No legacy workbook field is used as an input.  Distances concern layout
    endpoints only; they are not continuous-motion trajectory validation.
    """

    compiler = payload["compiler"]
    circuit = payload["circuit"]
    config = payload["config"]
    pitch_x = float(config.get("grid_pitch_x_um", 3.0))
    pitch_y = float(config.get("grid_pitch_y_um", 3.0))

    movement_batch_count = 0
    endpoint_change_count = 0
    batch_critical_distance_um = 0.0
    endpoint_sum_distance_um = 0.0
    transition_count_with_changes = 0

    for transition in compiler.get("movement_transitions", []):
        transition_changes = 0
        for batch in transition.get("batches", []):
            movement_batch_count += 1
            distances: list[float] = []
            for move in batch.get("moves", []):
                distance = _distance_um(move["source"], move["destination"], pitch_x, pitch_y)
                distances.append(distance)
                endpoint_sum_distance_um += distance
                endpoint_change_count += 1
                transition_changes += 1
            batch_critical_distance_um += max(distances, default=0.0)
        if transition_changes:
            transition_count_with_changes += 1

    parallel_groups = compiler.get("parallel_groups", [])
    partitions = compiler.get("partitions", [])
    return {
        "metric_scope": "endpoint_layout_v1",
        "continuous_motion_verified": False,
        "two_qubit_operation_count": int(circuit["two_qubit_operation_count"]),
        "one_qubit_operation_count": int(circuit["one_qubit_operation_count"]),
        "partition_count": len(partitions),
        "parallel_gate_group_count": len(parallel_groups),
        "movement_transition_count": len(compiler.get("movement_transitions", [])),
        "movement_transition_count_with_changes": transition_count_with_changes,
        "movement_batches": movement_batch_count,
        "atom_endpoint_changes": endpoint_change_count,
        "transfer_rounds": 4 * movement_batch_count,
        "atom_transfer_events": 4 * endpoint_change_count,
        "batch_critical_distance_um": batch_critical_distance_um,
        "endpoint_sum_distance_um": endpoint_sum_distance_um,
    }
