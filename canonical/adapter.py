"""External adapters that capture both compiler implementations unchanged."""

from __future__ import annotations

from collections import defaultdict, deque
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import importlib.util
import os
from pathlib import Path
import random
import sys
import tempfile
from types import ModuleType
from typing import Any, Iterator

from .metrics import compute_metrics
from .qasm import CircuitIR, Operation, parse_qasm
from .serialization import finalize_integrity
from .verifier import VerificationError, verify_compiler_output


@dataclass(frozen=True)
class _MethodSpec:
    method_id: str
    implementation_directory: str
    engine: str | None
    algorithm_files: tuple[str, ...]


_METHODS = {
    "forceshuttle": _MethodSpec(
        method_id="forceshuttle",
        implementation_directory="DasAtom",
        engine="noVF2",
        algorithm_files=(
            "DasAtom.py",
            "DasAtom_fun.py",
            "mcts_mapper.py",
            "analytical_placer.py",
            "Enola/route.py",
        ),
    ),
    "dasatom": _MethodSpec(
        method_id="dasatom",
        implementation_directory="DasAtom_Origin",
        engine=None,
        algorithm_files=("DasAtom.py", "DasAtom_fun.py", "Enola/route.py"),
    ),
}


@dataclass
class _CaptureState:
    layout_qubit_count: int | None = None
    initial_grid_size: int | None = None
    final_grid_size: int | None = None
    partitions: list = field(default_factory=list)
    embeddings: list = field(default_factory=list)
    parallel_groups: list = field(default_factory=list)
    flattened_movement_batches: list = field(default_factory=list)
    router_movement_transitions: list = field(default_factory=list)
    legacy_fidelity_result: tuple | None = None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_under(path: str | None, parent: Path) -> bool:
    if not path:
        return False
    try:
        Path(path).resolve().relative_to(parent)
        return True
    except (OSError, ValueError):
        return False


@contextmanager
def _loaded_implementation(repository_root: Path, spec: _MethodSpec) -> Iterator[ModuleType]:
    """Load one implementation despite both trees using the same import names."""

    implementation_root = (repository_root / spec.implementation_directory).resolve(strict=True)
    entry = implementation_root / "DasAtom.py"
    isolated_names = ("DasAtom_fun", "mcts_mapper", "analytical_placer", "Enola", "Enola.route")
    saved = {name: sys.modules[name] for name in isolated_names if name in sys.modules}
    for name in isolated_names:
        sys.modules.pop(name, None)

    module_name = f"_canonical_{spec.method_id}_implementation"
    previous_unique = sys.modules.pop(module_name, None)
    sys.path.insert(0, str(implementation_root))
    try:
        module_spec = importlib.util.spec_from_file_location(module_name, entry)
        if module_spec is None or module_spec.loader is None:
            raise ImportError(f"Cannot load compiler implementation: {entry}")
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[module_name] = module
        module_spec.loader.exec_module(module)
        yield module
    finally:
        try:
            sys.path.remove(str(implementation_root))
        except ValueError:
            pass
        for name, module in list(sys.modules.items()):
            if name == module_name or _is_under(getattr(module, "__file__", None), implementation_root):
                sys.modules.pop(name, None)
        for name, module in saved.items():
            sys.modules[name] = module
        if previous_unique is not None:
            sys.modules[module_name] = previous_unique


@contextmanager
def _fixed_environment(seed: int) -> Iterator[None]:
    keys = {
        "PYTHONHASHSEED": str(seed),
        "DASATOM_SEED": str(seed),
        "DASATOM_STRICT_VALIDATE": "0",
        "DASATOM_VERBOSE_INIT": "0",
        "DASATOM_VERBOSE_MCTS": "0",
        "DASATOM_DISABLE_FIRST_PARTITION_REFINEMENT": "0",
    }
    previous = {key: os.environ.get(key) for key in keys}
    for key, value in keys.items():
        os.environ[key] = value
    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed)
    except ImportError:
        pass
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _capture_processor(module: ModuleType, state: _CaptureState) -> type:
    base = module.SingleFileProcessor

    class CapturingSingleFileProcessor(base):
        def _compute_architecture_parameters(self, *args, **kwargs):
            result = super()._compute_architecture_parameters(*args, **kwargs)
            state.layout_qubit_count = int(result[0])
            state.initial_grid_size = int(result[2])
            return result

        def _retrieve_or_generate_partitions(self, *args, **kwargs):
            result = super()._retrieve_or_generate_partitions(*args, **kwargs)
            state.partitions = deepcopy(result)
            return result

        def _retrieve_or_generate_embeddings(self, *args, **kwargs):
            result = super()._retrieve_or_generate_embeddings(*args, **kwargs)
            state.embeddings = deepcopy(result[0])
            state.final_grid_size = int(result[1])
            return result

        def _compute_gates_and_movements(self, *args, **kwargs):
            # ForceShuttle may split partitioned_gates in-place while building
            # embeddings.  The arguments at this boundary are the final schedule.
            state.partitions = deepcopy(args[1] if len(args) >= 2 else kwargs["partitioned_gates"])
            state.embeddings = deepcopy(args[2] if len(args) >= 3 else kwargs["embeddings"])
            state.final_grid_size = int(args[4] if len(args) >= 5 else kwargs["grid_size"])
            result = super()._compute_gates_and_movements(*args, **kwargs)
            state.parallel_groups = deepcopy(result[0])
            state.flattened_movement_batches = deepcopy(result[1])
            return result

    CapturingSingleFileProcessor.__name__ = f"CanonicalCapture{base.__name__}"
    return CapturingSingleFileProcessor


def _install_capture_hooks(module: ModuleType, state: _CaptureState) -> None:
    original_router = module.QuantumRouter

    class CapturingRouter(original_router):
        def run(self):
            result = super().run()
            state.router_movement_transitions = deepcopy(self.movement_list)
            return result

    module.QuantumRouter = CapturingRouter

    original_compute_fidelity = module.compute_fidelity

    def capturing_compute_fidelity(*args, **kwargs):
        result = original_compute_fidelity(*args, **kwargs)
        state.legacy_fidelity_result = tuple(result)
        return result

    module.compute_fidelity = capturing_compute_fidelity


def _pair_key(qubits: list[int] | tuple[int, int]) -> tuple[int, int]:
    u, v = (int(value) for value in qubits)
    return (u, v) if u < v else (v, u)


def _bind_parallel_occurrences(
    circuit: CircuitIR,
    raw_parallel_groups: list,
) -> tuple[list[list[list[dict[str, Any]]]], set[str]]:
    """Bind endpoint-only gates to source occurrences by per-pair source order.

    Binding intentionally does not enforce DAG order.  A compiler schedule that
    reorders different interactions on one qubit must still be serializable so
    the independent FIFO verifier can report the violation.
    """

    source = list(circuit.two_qubit_operations)
    source_by_pair: dict[tuple[int, int], deque[Operation]] = defaultdict(deque)
    for operation in source:
        source_by_pair[_pair_key(operation.qubits)].append(operation)

    used: set[str] = set()
    bound: list[list[list[dict[str, Any]]]] = []
    for partition_index, partition_groups in enumerate(raw_parallel_groups):
        bound_partition: list[list[dict[str, Any]]] = []
        for group_index, raw_group in enumerate(partition_groups):
            group_used: set[str] = set()
            bound_group: list[dict[str, Any]] = []
            for raw_gate in raw_group:
                qubits = [int(raw_gate[0]), int(raw_gate[1])]
                candidates = source_by_pair[_pair_key(qubits)]
                if not candidates:
                    raise VerificationError(
                        {
                            "passed": False,
                            "errors": [
                                {
                                    "code": "occurrence_binding",
                                    "message": (
                                        f"Cannot bind extra gate {qubits} at partition {partition_index}, "
                                        f"parallel group {group_index}."
                                    ),
                                    "path": "compiler.parallel_groups",
                                }
                            ],
                        }
                    )
                operation = candidates.popleft()
                group_used.add(operation.operation_id)
                bound_group.append({"operation_id": operation.operation_id, "qubits": qubits})
            used.update(group_used)
            bound_partition.append(bound_group)
        bound.append(bound_partition)
    return bound, used


def _bind_partitions(
    raw_partitions: list,
    bound_groups: list[list[list[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if len(raw_partitions) != len(bound_groups):
        raise ValueError("Captured partitions and parallel-group partitions differ in length.")
    for partition_index, raw_partition in enumerate(raw_partitions):
        queues: dict[tuple[int, int], deque[dict[str, Any]]] = defaultdict(deque)
        for group in bound_groups[partition_index]:
            for operation in group:
                queues[_pair_key(operation["qubits"])].append(operation)
        operations: list[dict[str, Any]] = []
        for raw_gate in raw_partition:
            qubits = [int(raw_gate[0]), int(raw_gate[1])]
            queue = queues[_pair_key(qubits)]
            if not queue:
                raise ValueError(f"Partition {partition_index} is not covered by captured parallel groups.")
            bound = queue.popleft()
            operations.append({"operation_id": bound["operation_id"], "qubits": qubits})
        if any(queue for queue in queues.values()):
            raise ValueError(f"Parallel groups contain extra operations in partition {partition_index}.")
        result.append({"partition_index": partition_index, "operations": operations})
    return result


def _normalize_embeddings(raw_embeddings: list) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for partition_index, embedding in enumerate(raw_embeddings):
        positions = [[int(position[0]), int(position[1])] for position in embedding]
        result.append({"partition_index": partition_index, "positions": positions})
    return result


def _normalize_parallel_groups(bound_groups: list[list[list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for partition_index, partition_groups in enumerate(bound_groups):
        for group_index, operations in enumerate(partition_groups):
            result.append(
                {
                    "partition_index": partition_index,
                    "group_index": group_index,
                    "operations": operations,
                }
            )
    return result


def _normalize_movements(raw_transitions: list, partition_count: int) -> list[dict[str, Any]]:
    if len(raw_transitions) != max(partition_count - 1, 0):
        raise ValueError(
            f"Router captured {len(raw_transitions)} transitions for {partition_count} embeddings."
        )
    transitions: list[dict[str, Any]] = []
    for transition_index, raw_batches in enumerate(raw_transitions):
        batches: list[dict[str, Any]] = []
        for batch_index, raw_batch in enumerate(raw_batches):
            moves = [
                {
                    "qubit": int(move[0]),
                    "source": [int(move[1][0]), int(move[1][1])],
                    "destination": [int(move[2][0]), int(move[2][1])],
                }
                for move in raw_batch
            ]
            batches.append({"batch_index": batch_index, "moves": moves})
        transitions.append(
            {
                "from_partition": transition_index,
                "to_partition": transition_index + 1,
                "batches": batches,
            }
        )
    return transitions


def _legacy_metrics(row: list, fidelity_result: tuple | None) -> dict[str, Any]:
    if fidelity_result is None or len(fidelity_result) != 7:
        raise RuntimeError("Compiler fidelity instrumentation did not return the expected 7 fields.")
    idle_time, fidelity, movement_fidelity, physical_runtime, transfer_rounds, endpoint_changes, distance = fidelity_result
    return {
        "reported_layout_qubit_count": int(row[1]),
        "reported_two_qubit_gate_count": int(row[2]),
        "reported_circuit_depth": int(row[3]),
        "fidelity": float(fidelity),
        "movement_fidelity": float(movement_fidelity),
        "idle_time_us": float(idle_time),
        "total_physical_runtime_us": float(physical_runtime),
        "movement_batches": int(row[6]),
        "atom_endpoint_changes": int(endpoint_changes),
        "legacy_transfer_rounds": int(transfer_rounds),
        "legacy_workbook_atom_transfer_events": int(row[7]),
        "batch_critical_distance_um": float(distance),
        "parallel_gate_group_count": int(row[10]),
        "partition_count": int(row[11]),
        "wall_elapsed_seconds_omitted_for_determinism": True,
    }


def _algorithm_hashes(repository_root: Path, spec: _MethodSpec) -> dict[str, str]:
    implementation_root = repository_root / spec.implementation_directory
    return {
        relative: _file_sha256(implementation_root / relative)
        for relative in sorted(spec.algorithm_files)
    }


def compile_single_qasm(
    method: str,
    qasm_path: str | Path,
    *,
    repository_root: str | Path | None = None,
    seed: int = 0,
    interaction_radius: float = 2.0,
    exclusion_radius: float = 4.0,
    grid_pitch_x_um: float = 3.0,
    grid_pitch_y_um: float = 3.0,
) -> dict[str, Any]:
    """Run one explicit QASM through ForceShuttle or original DasAtom.

    ``forceshuttle`` is fixed to ``DasAtom/SingleFileProcessor(engine='noVF2')``.
    ``dasatom`` is fixed to ``DasAtom_Origin/SingleFileProcessor``.
    """

    normalized_method = method.strip().lower()
    if normalized_method not in _METHODS:
        raise ValueError(f"method must be one of {sorted(_METHODS)}, got {method!r}")
    spec = _METHODS[normalized_method]
    root = Path(repository_root or Path(__file__).resolve().parents[1]).expanduser().resolve(strict=True)
    qasm = Path(qasm_path).expanduser().resolve(strict=True)
    circuit = parse_qasm(qasm)
    if circuit.two_qubit_operation_count == 0:
        raise ValueError("Canonical pairwise rerun requires at least one two-qubit operation.")
    if circuit.unsupported_operation_count:
        raise ValueError("Canonical endpoint-layout scope does not support operations on more than two qubits.")

    state = _CaptureState()
    with _fixed_environment(int(seed)), _loaded_implementation(root, spec) as module:
        _install_capture_hooks(module, state)
        processor_class = _capture_processor(module, state)
        with tempfile.TemporaryDirectory(prefix=f"canonical-{normalized_method}-") as temporary:
            temporary_root = Path(temporary)
            kwargs = {
                "qasm_filename": qasm.name,
                "circuit_folder": str(qasm.parent),
                "benchmark_name": f"canonical_{normalized_method}",
                "interaction_radius": interaction_radius,
                "extended_radius": exclusion_radius,
                "result_path": str(temporary_root / "legacy"),
                "embeddings_path": str(temporary_root / "embeddings"),
                "partitions_path": str(temporary_root / "partitions"),
                "read_embeddings": False,
                "save_partitions_and_embeddings": False,
                "save_circuit_results": False,
                "save_benchmark_results": False,
            }
            if spec.engine is not None:
                kwargs["engine"] = spec.engine
            processor = processor_class(**kwargs)
            row = processor.process_qasm_file()

    if state.layout_qubit_count != circuit.layout_qubit_count:
        raise RuntimeError(
            "Independent QASM active span differs from compiler layout span: "
            f"parser={circuit.layout_qubit_count}, compiler={state.layout_qubit_count}."
        )
    if len(state.partitions) != len(state.embeddings):
        raise RuntimeError("Compiler returned different partition and embedding counts.")
    bound_groups, used_ids = _bind_parallel_occurrences(circuit, state.parallel_groups)
    expected_ids = {operation.operation_id for operation in circuit.two_qubit_operations}
    if used_ids != expected_ids:
        missing = sorted(expected_ids - used_ids)
        extra = sorted(used_ids - expected_ids)
        raise RuntimeError(f"Occurrence binding is incomplete: missing={missing[:8]}, extra={extra[:8]}")

    partitions = _bind_partitions(state.partitions, bound_groups)
    embeddings = _normalize_embeddings(state.embeddings)
    parallel_groups = _normalize_parallel_groups(bound_groups)
    movement_transitions = _normalize_movements(state.router_movement_transitions, len(embeddings))

    payload: dict[str, Any] = {
        "schema_version": "forceshuttle.compiler_output.v1",
        "scope": {
            "name": "endpoint_layout_v1",
            "continuous_motion_verified": False,
            "one_qubit_policy": "counted_not_scheduled",
            "unused_declared_qubits_policy": "outside_2q_active_span_not_embedded",
        },
        "method": {
            "id": spec.method_id,
            "implementation_directory": spec.implementation_directory,
            "engine": spec.engine,
            "algorithm_file_sha256": _algorithm_hashes(root, spec),
        },
        "input": {"filename": qasm.name, "qasm_sha256": circuit.qasm_sha256},
        "config": {
            "seed": int(seed),
            "interaction_radius_grid": float(interaction_radius),
            "exclusion_radius_grid": float(exclusion_radius),
            "grid_pitch_x_um": float(grid_pitch_x_um),
            "grid_pitch_y_um": float(grid_pitch_y_um),
        },
        "circuit": circuit.to_dict(),
        "compiler": {
            "layout_qubit_count": int(state.layout_qubit_count),
            "grid_size": int(state.final_grid_size or state.initial_grid_size or 0),
            "partitions": partitions,
            "embeddings": embeddings,
            "parallel_groups": parallel_groups,
            "movement_transitions": movement_transitions,
            "legacy_metrics": _legacy_metrics(row, state.legacy_fidelity_result),
        },
    }
    payload["metrics"] = compute_metrics(payload)
    payload = finalize_integrity(payload)
    payload["verification"] = verify_compiler_output(payload, qasm_path=qasm)
    return payload
