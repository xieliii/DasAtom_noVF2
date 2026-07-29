"""Independent verifier for the endpoint_layout_v1 compiler output schema."""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import math
from pathlib import Path
from typing import Any, Callable

from .metrics import compute_metrics
from .qasm import parse_qasm
from .serialization import canonical_sha256, integrity_projection, schedule_projection


_EPSILON = 1e-9


class VerificationError(RuntimeError):
    """Raised when requested verification does not pass."""

    def __init__(self, result: dict[str, Any]):
        self.result = result
        messages = "; ".join(item["message"] for item in result.get("errors", []))
        super().__init__(messages or "Canonical compiler output verification failed.")


class _Audit:
    def __init__(self) -> None:
        self.errors: list[dict[str, str]] = []
        self.warnings: list[dict[str, str]] = []
        self.checks: dict[str, dict[str, Any]] = {}

    def error(self, code: str, message: str, path: str = "") -> None:
        self.errors.append({"code": code, "message": message, "path": path})

    def warning(self, code: str, message: str, path: str = "") -> None:
        self.warnings.append({"code": code, "message": message, "path": path})

    def run(self, name: str, callback: Callable[[], dict[str, Any] | None]) -> None:
        before = len(self.errors)
        try:
            details = callback() or {}
        except Exception as exc:  # Keep validator output useful after malformed JSON.
            self.error("malformed_structure", f"{name}: {type(exc).__name__}: {exc}", name)
            details = {}
        self.checks[name] = {"passed": len(self.errors) == before, **details}

    def result(self) -> dict[str, Any]:
        return {
            "passed": not self.errors,
            "scope": "endpoint_layout_v1",
            "checks": self.checks,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def _same_pair(left: list[int] | tuple[int, ...], right: list[int] | tuple[int, ...]) -> bool:
    return len(left) == len(right) == 2 and set(left) == set(right)


def _position(value: Any) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"Expected [x, y], got {value!r}")
    result: list[int] = []
    for coordinate in value:
        if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
            raise ValueError(f"Coordinate is not numeric: {coordinate!r}")
        if not math.isfinite(float(coordinate)) or not float(coordinate).is_integer():
            raise ValueError(f"Coordinate is not a finite grid integer: {coordinate!r}")
        result.append(int(coordinate))
    return result[0], result[1]


def _circuit_operations(payload: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    source = payload["circuit"]["two_qubit_operations"]
    by_id: dict[str, dict[str, Any]] = {}
    ordered: list[str] = []
    for operation in source:
        operation_id = operation["operation_id"]
        if operation_id in by_id:
            raise ValueError(f"Duplicate source operation_id {operation_id}")
        qubits = operation["qubits"]
        if len(qubits) != 2 or qubits[0] == qubits[1]:
            raise ValueError(f"Invalid two-qubit operation {operation_id}: {qubits}")
        by_id[operation_id] = operation
        ordered.append(operation_id)
    return by_id, ordered


def verify_compiler_output(
    payload: dict[str, Any],
    *,
    qasm_path: str | Path | None = None,
    raise_on_error: bool = False,
) -> dict[str, Any]:
    """Verify captured endpoint layouts, operation order, groups and movements.

    Passing ``qasm_path`` additionally binds the embedded circuit facts and hash
    to an explicit source file.  The function never imports either compiler.
    """

    audit = _Audit()

    def check_schema() -> dict[str, Any]:
        if payload.get("schema_version") != "forceshuttle.compiler_output.v1":
            audit.error("schema_version", "Unsupported or missing schema_version.", "schema_version")
        scope = payload.get("scope", {})
        if scope.get("name") != "endpoint_layout_v1":
            audit.error("scope", "scope.name must be endpoint_layout_v1.", "scope.name")
        if scope.get("continuous_motion_verified") is not False:
            audit.error(
                "scope_overclaim",
                "continuous_motion_verified must be false for endpoint_layout_v1.",
                "scope.continuous_motion_verified",
            )
        if scope.get("one_qubit_policy") != "counted_not_scheduled":
            audit.error("one_qubit_scope", "1Q policy must be counted_not_scheduled.", "scope.one_qubit_policy")
        method = payload.get("method", {}).get("id")
        if method not in {"forceshuttle", "dasatom"}:
            audit.error("method", f"Unknown method {method!r}.", "method.id")
        return {"method": method}

    audit.run("schema_scope", check_schema)

    def check_qasm() -> dict[str, Any]:
        if qasm_path is None:
            return {"external_source_checked": False}
        parsed = parse_qasm(qasm_path).to_dict()
        embedded = payload["circuit"]
        fields = (
            "qasm_sha256",
            "declared_qubit_count",
            "layout_qubit_count",
            "operation_count",
            "one_qubit_operation_count",
            "two_qubit_operation_count",
            "nonunitary_operation_count",
            "unsupported_operation_count",
            "two_qubit_operations",
        )
        for field in fields:
            if embedded.get(field) != parsed.get(field):
                audit.error("qasm_mismatch", f"Embedded circuit field differs from QASM: {field}.", f"circuit.{field}")
        input_hash = payload.get("input", {}).get("qasm_sha256")
        if input_hash != parsed["qasm_sha256"]:
            audit.error("qasm_hash", "input.qasm_sha256 differs from the explicit QASM.", "input.qasm_sha256")
        return {"external_source_checked": True, "qasm_sha256": parsed["qasm_sha256"]}

    audit.run("qasm_source", check_qasm)

    source_by_id: dict[str, dict[str, Any]] = {}
    source_order: list[str] = []

    def check_occurrences() -> dict[str, Any]:
        nonlocal source_by_id, source_order
        source_by_id, source_order = _circuit_operations(payload)
        circuit = payload["circuit"]
        if circuit["two_qubit_operation_count"] != len(source_order):
            audit.error(
                "source_count",
                "two_qubit_operation_count does not match occurrence list length.",
                "circuit.two_qubit_operation_count",
            )
        layout_count = int(circuit["layout_qubit_count"])
        expected_layout_count = max(
            (max(int(q) for q in operation["qubits"]) + 1 for operation in source_by_id.values()),
            default=0,
        )
        if layout_count != expected_layout_count:
            audit.error(
                "layout_span",
                f"layout_qubit_count={layout_count}, expected active 2Q span {expected_layout_count}.",
                "circuit.layout_qubit_count",
            )
        if int(payload["compiler"]["layout_qubit_count"]) != layout_count:
            audit.error("compiler_layout_span", "Compiler and circuit layout spans differ.", "compiler.layout_qubit_count")
        if int(circuit["unsupported_operation_count"]) != 0:
            audit.error("unsupported_operations", "Source contains operations with more than two qubits.", "circuit")
        return {"two_qubit_occurrences": len(source_order), "layout_qubit_count": layout_count}

    audit.run("operation_occurrences", check_occurrences)

    partition_ids: dict[int, list[str]] = {}
    embedding_by_partition: dict[int, list[list[int]]] = {}

    def check_partitions() -> dict[str, Any]:
        compiler = payload["compiler"]
        partitions = compiler["partitions"]
        embeddings = compiler["embeddings"]
        if len(partitions) != len(embeddings):
            audit.error("partition_embedding_count", "Partition and embedding counts differ.", "compiler")
        expected_indices = list(range(len(partitions)))
        actual_indices = [int(partition["partition_index"]) for partition in partitions]
        if actual_indices != expected_indices:
            audit.error("partition_indices", "Partition indices must be contiguous and ordered.", "compiler.partitions")
        embedding_indices = [int(embedding["partition_index"]) for embedding in embeddings]
        if embedding_indices != expected_indices:
            audit.error("embedding_indices", "Embedding indices must be contiguous and ordered.", "compiler.embeddings")

        scheduled_ids: list[str] = []
        for partition in partitions:
            index = int(partition["partition_index"])
            ids: list[str] = []
            for operation in partition["operations"]:
                operation_id = operation["operation_id"]
                ids.append(operation_id)
                scheduled_ids.append(operation_id)
                source = source_by_id.get(operation_id)
                if source is None:
                    audit.error("unknown_operation", f"Unknown operation_id {operation_id}.", "compiler.partitions")
                elif not _same_pair(operation["qubits"], source["qubits"]):
                    audit.error("operation_endpoints", f"Endpoint mismatch for {operation_id}.", "compiler.partitions")
            partition_ids[index] = ids

        counts = Counter(scheduled_ids)
        missing = [operation_id for operation_id in source_order if counts[operation_id] == 0]
        duplicates = sorted(operation_id for operation_id, count in counts.items() if count > 1)
        extras = sorted(operation_id for operation_id in counts if operation_id not in source_by_id)
        if missing:
            audit.error("missing_operations", f"Missing 2Q occurrences: {missing[:8]}.", "compiler.partitions")
        if duplicates:
            audit.error("duplicate_operations", f"Duplicated 2Q occurrences: {duplicates[:8]}.", "compiler.partitions")
        if extras:
            audit.error("extra_operations", f"Unknown 2Q occurrences: {extras[:8]}.", "compiler.partitions")
        for embedding in embeddings:
            embedding_by_partition[int(embedding["partition_index"])] = embedding["positions"]
        return {"partition_count": len(partitions), "scheduled_occurrences": len(scheduled_ids)}

    audit.run("partitions_embeddings", check_partitions)

    scheduled_group_ids: list[str] = []
    groups_by_partition: dict[int, list[dict[str, Any]]] = defaultdict(list)

    def check_parallel_groups() -> dict[str, Any]:
        groups = payload["compiler"]["parallel_groups"]
        prior_key = (-1, -1)
        for group in groups:
            partition_index = int(group["partition_index"])
            group_index = int(group["group_index"])
            key = (partition_index, group_index)
            if key <= prior_key:
                audit.error("parallel_group_order", "Parallel groups must be ordered by partition/group index.", "compiler.parallel_groups")
            prior_key = key
            groups_by_partition[partition_index].append(group)
            seen_qubits: set[int] = set()
            for operation in group["operations"]:
                operation_id = operation["operation_id"]
                scheduled_group_ids.append(operation_id)
                source = source_by_id.get(operation_id)
                if source is None:
                    audit.error("parallel_unknown_operation", f"Unknown operation_id {operation_id}.", "compiler.parallel_groups")
                    continue
                if not _same_pair(operation["qubits"], source["qubits"]):
                    audit.error("parallel_endpoints", f"Endpoint mismatch for {operation_id}.", "compiler.parallel_groups")
                operands = {int(q) for q in operation["qubits"]}
                if seen_qubits & operands:
                    audit.error(
                        "parallel_operand_conflict",
                        f"Parallel group {key} contains gates sharing a logical qubit.",
                        "compiler.parallel_groups",
                    )
                seen_qubits.update(operands)

        for partition_index in range(len(partition_ids)):
            partition_groups = groups_by_partition.get(partition_index, [])
            indices = [int(group["group_index"]) for group in partition_groups]
            if indices != list(range(len(indices))):
                audit.error("parallel_group_indices", f"Non-contiguous group indices in partition {partition_index}.", "compiler.parallel_groups")
            group_ids = [op["operation_id"] for group in partition_groups for op in group["operations"]]
            if Counter(group_ids) != Counter(partition_ids.get(partition_index, [])):
                audit.error(
                    "parallel_partition_coverage",
                    f"Parallel groups do not exactly cover partition {partition_index}.",
                    "compiler.parallel_groups",
                )
        if Counter(scheduled_group_ids) != Counter(source_order):
            audit.error("parallel_global_coverage", "Parallel groups do not exactly cover all 2Q occurrences.", "compiler.parallel_groups")
        return {"parallel_group_count": len(groups), "grouped_occurrences": len(scheduled_group_ids)}

    audit.run("parallel_groups", check_parallel_groups)

    def check_fifo() -> dict[str, Any]:
        source_per_qubit: dict[int, list[str]] = defaultdict(list)
        scheduled_per_qubit: dict[int, list[str]] = defaultdict(list)
        for operation_id in source_order:
            for qubit in source_by_id[operation_id]["qubits"]:
                source_per_qubit[int(qubit)].append(operation_id)
        for operation_id in scheduled_group_ids:
            source = source_by_id.get(operation_id)
            if source is None:
                continue
            for qubit in source["qubits"]:
                scheduled_per_qubit[int(qubit)].append(operation_id)
        all_qubits = sorted(set(source_per_qubit) | set(scheduled_per_qubit))
        for qubit in all_qubits:
            if scheduled_per_qubit[qubit] != source_per_qubit[qubit]:
                audit.error(
                    "qubit_fifo",
                    f"2Q dependency order differs on logical qubit {qubit}.",
                    "compiler.parallel_groups",
                )
        return {"qubits_with_two_qubit_dependencies": len(all_qubits)}

    audit.run("per_qubit_fifo", check_fifo)

    def check_geometry() -> dict[str, Any]:
        compiler = payload["compiler"]
        layout_count = int(compiler["layout_qubit_count"])
        grid_size = int(compiler["grid_size"])
        rb = float(payload["config"]["interaction_radius_grid"])
        re_radius = float(payload["config"]["exclusion_radius_grid"])
        if grid_size <= 0:
            audit.error("grid_size", "grid_size must be positive.", "compiler.grid_size")

        normalized: dict[int, list[tuple[int, int]]] = {}
        for partition_index, positions in embedding_by_partition.items():
            if len(positions) != layout_count:
                audit.error(
                    "embedding_length",
                    f"Partition {partition_index} embedding has {len(positions)} entries, expected {layout_count}.",
                    "compiler.embeddings",
                )
                continue
            parsed_positions: list[tuple[int, int]] = []
            for qubit, value in enumerate(positions):
                try:
                    position = _position(value)
                except ValueError as exc:
                    audit.error("illegal_coordinate", f"Partition {partition_index}, q{qubit}: {exc}", "compiler.embeddings")
                    continue
                if not (0 <= position[0] < grid_size and 0 <= position[1] < grid_size):
                    audit.error(
                        "coordinate_bounds",
                        f"Partition {partition_index}, q{qubit}: {position} outside {grid_size}x{grid_size} grid.",
                        "compiler.embeddings",
                    )
                parsed_positions.append(position)
            if len(parsed_positions) == layout_count and len(set(parsed_positions)) != layout_count:
                audit.error("embedding_collision", f"Partition {partition_index} is not injective.", "compiler.embeddings")
            normalized[partition_index] = parsed_positions

        for partition in compiler["partitions"]:
            partition_index = int(partition["partition_index"])
            positions = normalized.get(partition_index, [])
            if len(positions) != layout_count:
                continue
            for operation in partition["operations"]:
                u, v = (int(q) for q in operation["qubits"])
                distance = math.dist(positions[u], positions[v])
                if distance > rb + _EPSILON:
                    audit.error(
                        "rb_violation",
                        f"Partition {partition_index}, {operation['operation_id']} distance {distance:.12g} > Rb {rb}.",
                        "compiler.partitions",
                    )

        for group in payload["compiler"]["parallel_groups"]:
            partition_index = int(group["partition_index"])
            positions = normalized.get(partition_index, [])
            if len(positions) != layout_count:
                continue
            operations = group["operations"]
            for left_index in range(len(operations)):
                for right_index in range(left_index + 1, len(operations)):
                    left = operations[left_index]["qubits"]
                    right = operations[right_index]["qubits"]
                    for left_qubit in left:
                        for right_qubit in right:
                            distance = math.dist(positions[int(left_qubit)], positions[int(right_qubit)])
                            if distance <= re_radius + _EPSILON:
                                audit.error(
                                    "re_violation",
                                    f"Parallel group ({partition_index}, {group['group_index']}) has endpoint distance "
                                    f"{distance:.12g} <= Re {re_radius}.",
                                    "compiler.parallel_groups",
                                )
        return {"grid_size": grid_size, "interaction_radius_grid": rb, "exclusion_radius_grid": re_radius}

    audit.run("embedding_geometry", check_geometry)

    def check_movements() -> dict[str, Any]:
        transitions = payload["compiler"]["movement_transitions"]
        expected_transition_count = max(len(embedding_by_partition) - 1, 0)
        if len(transitions) != expected_transition_count:
            audit.error(
                "movement_transition_count",
                f"Got {len(transitions)} movement transitions, expected {expected_transition_count}.",
                "compiler.movement_transitions",
            )
        total_changes = 0
        total_batches = 0
        for index, transition in enumerate(transitions):
            if int(transition["from_partition"]) != index or int(transition["to_partition"]) != index + 1:
                audit.error("movement_transition_indices", f"Invalid movement transition at index {index}.", "compiler.movement_transitions")
                continue
            current = embedding_by_partition.get(index, [])
            following = embedding_by_partition.get(index + 1, [])
            if len(current) != len(following):
                audit.error("movement_embedding_length", f"Adjacent embedding lengths differ at {index}.", "compiler.movement_transitions")
                continue
            expected = {
                qubit: (_position(current[qubit]), _position(following[qubit]))
                for qubit in range(len(current))
                if _position(current[qubit]) != _position(following[qubit])
            }
            actual: dict[int, tuple[tuple[int, int], tuple[int, int]]] = {}
            batches = transition["batches"]
            for batch_index, batch in enumerate(batches):
                total_batches += 1
                if int(batch["batch_index"]) != batch_index:
                    audit.error("movement_batch_index", f"Invalid batch index in transition {index}.", "compiler.movement_transitions")
                if not batch["moves"]:
                    audit.error("empty_movement_batch", f"Empty movement batch in transition {index}.", "compiler.movement_transitions")
                batch_qubits: set[int] = set()
                for move in batch["moves"]:
                    qubit = int(move["qubit"])
                    if qubit in batch_qubits:
                        audit.error("batch_duplicate_qubit", f"q{qubit} repeated in movement batch.", "compiler.movement_transitions")
                    batch_qubits.add(qubit)
                    if qubit in actual:
                        audit.error("transition_duplicate_qubit", f"q{qubit} moves more than once in transition {index}.", "compiler.movement_transitions")
                    try:
                        actual[qubit] = (_position(move["source"]), _position(move["destination"]))
                    except ValueError as exc:
                        audit.error("movement_coordinate", f"Transition {index}, q{qubit}: {exc}", "compiler.movement_transitions")
            if actual != expected:
                missing = sorted(set(expected) - set(actual))
                extra = sorted(set(actual) - set(expected))
                wrong = sorted(q for q in set(actual) & set(expected) if actual[q] != expected[q])
                audit.error(
                    "movement_endpoint_mismatch",
                    f"Transition {index} endpoint changes differ: missing={missing}, extra={extra}, wrong={wrong}.",
                    "compiler.movement_transitions",
                )
            total_changes += len(actual)
        return {"movement_batches": total_batches, "atom_endpoint_changes": total_changes}

    audit.run("movement_endpoints", check_movements)

    def check_metrics() -> dict[str, Any]:
        recomputed = compute_metrics(payload)
        if payload.get("metrics") != recomputed:
            audit.error("metrics_mismatch", "Stored canonical metrics differ from raw-structure recomputation.", "metrics")
        legacy = payload["compiler"].get("legacy_metrics", {})
        exact_pairs = {
            "movement_batches": recomputed["movement_batches"],
            "atom_endpoint_changes": recomputed["atom_endpoint_changes"],
            "parallel_gate_group_count": recomputed["parallel_gate_group_count"],
            "partition_count": recomputed["partition_count"],
            "legacy_transfer_rounds": recomputed["transfer_rounds"],
            "legacy_workbook_atom_transfer_events": recomputed["atom_transfer_events"],
        }
        for field, expected in exact_pairs.items():
            if field in legacy and legacy[field] != expected:
                audit.error("legacy_raw_mismatch", f"legacy_metrics.{field} != canonical raw count.", f"compiler.legacy_metrics.{field}")
        distance = legacy.get("batch_critical_distance_um")
        if distance is not None and not math.isclose(
            float(distance), float(recomputed["batch_critical_distance_um"]), rel_tol=1e-12, abs_tol=1e-9
        ):
            audit.error("legacy_distance_mismatch", "Legacy critical distance differs from raw batches.", "compiler.legacy_metrics")
        return {"recomputed": recomputed}

    audit.run("canonical_metrics", check_metrics)

    def check_integrity() -> dict[str, Any]:
        integrity = payload.get("integrity")
        if integrity is None:
            audit.warning("integrity_absent", "Integrity is absent during pre-finalization verification.", "integrity")
            return {"checked": False}
        if integrity.get("algorithm") != "sha256":
            audit.error("integrity_algorithm", "Integrity algorithm must be sha256.", "integrity.algorithm")
        expected_payload_hash = canonical_sha256(integrity_projection(payload))
        if integrity.get("canonical_payload_sha256") != expected_payload_hash:
            audit.error("payload_hash", "canonical_payload_sha256 mismatch.", "integrity.canonical_payload_sha256")
        expected_schedule_hash = canonical_sha256(schedule_projection(payload))
        if integrity.get("schedule_sha256") != expected_schedule_hash:
            audit.error("schedule_hash", "schedule_sha256 mismatch.", "integrity.schedule_sha256")
        if payload.get("schedule_hash") != expected_schedule_hash:
            audit.error("top_level_schedule_hash", "Top-level schedule_hash mismatch.", "schedule_hash")
        return {"checked": True, "schedule_sha256": expected_schedule_hash}

    audit.run("integrity", check_integrity)

    result = audit.result()
    if raise_on_error and not result["passed"]:
        raise VerificationError(result)
    return result
