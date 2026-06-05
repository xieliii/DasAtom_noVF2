import argparse
import copy
import math
import os
import time

import networkx as nx

from DasAtom import SingleFileProcessor
from analytical_placer import force_directed_mapping
from DasAtom_fun import (
    _collect_active_qubits,
    _minimize_idle_movement,
    _movement_aware_repair,
    _normalize_mapping,
    _split_valid_violating_gates,
    _try_static_embedding,
    CreateCircuitFromQASM,
    compute_fidelity,
    extend_graph,
    fast_partition,
    gates_list_to_QC,
    generate_grid_with_Rb,
    get_2q_gates_list,
    get_embeddings,
    get_embeddings_vf2,
    get_qubits_num,
    get_rx_one_mapping,
    layer_only_partition,
    map2list,
    partition_from_DAG,
    rx_is_subgraph_iso,
)
from mcts_mapper import mcts_initial_mapping


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CIRCUIT_DIR = os.path.join(SCRIPT_DIR, "Data", "benchmark_circuits")


def adaptive_mcts_iterations(num_qubits, partitioned_gates):
    first_partition_size = len(partitioned_gates[0]) if partitioned_gates else 0
    fp_graph = nx.Graph()
    if partitioned_gates:
        fp_graph.add_edges_from(partitioned_gates[0])
    fp_nodes = fp_graph.number_of_nodes()
    fp_edges = fp_graph.number_of_edges()
    fp_avg_deg = (2.0 * fp_edges / fp_nodes) if fp_nodes else 0.0
    fp_max_deg = max((d for _, d in fp_graph.degree()), default=0)
    fp_hub_like = (
        fp_nodes >= 6
        and fp_avg_deg <= 2.2
        and fp_max_deg >= min(fp_nodes - 1, max(4, fp_nodes // 2))
    )

    if num_qubits <= 8:
        adaptive_iterations = int(max(8, max(1, fp_nodes) + 2))
    elif num_qubits <= 10:
        adaptive_iterations = int(
            max(12, int(1.5 * max(1, fp_nodes)) + max(3, first_partition_size // 3))
        )
    elif num_qubits <= 16:
        adaptive_iterations = int(
            max(
                48,
                min(
                    3600,
                    (num_qubits ** 2) * 5 + first_partition_size * max(1, num_qubits // 6),
                ),
            )
        )
    else:
        adaptive_iterations = int(
            max(
                80,
                min(
                    7000,
                    (num_qubits ** 2) * 6 + first_partition_size * max(1, num_qubits // 7),
                ),
            )
        )

    if fp_nodes and fp_edges == max(0, fp_nodes - 1) and fp_max_deg <= 4:
        tree_cap = max(12, int(fp_nodes * 1.5))
        if fp_max_deg <= 2:
            tree_cap = max(10, fp_nodes // 2 + 4)
        adaptive_iterations = min(adaptive_iterations, tree_cap)
    elif fp_hub_like:
        hub_cap = max(8, fp_nodes // 2 + 2)
        if num_qubits <= 14:
            hub_cap = min(hub_cap, 10)
        adaptive_iterations = min(adaptive_iterations, hub_cap)
    elif fp_nodes and fp_avg_deg <= 2.2 and fp_max_deg <= 5:
        adaptive_iterations = max(20, int(adaptive_iterations * 0.18))
    elif fp_nodes and fp_avg_deg >= 3.0:
        adaptive_iterations = min(8000, int(adaptive_iterations * 1.15))
    if num_qubits <= 10 and fp_avg_deg >= 2.8:
        dense_cap = max(4, fp_nodes // 2)
        if num_qubits <= 8:
            dense_cap = max(2, dense_cap - 2)
        adaptive_iterations = min(adaptive_iterations, dense_cap)
    if num_qubits >= 14 and fp_avg_deg >= 5.5:
        dense_large_cap = max(24, min(96, fp_nodes * 3))
        adaptive_iterations = min(adaptive_iterations, dense_large_cap)
    if num_qubits <= 6:
        small_cap = 4
        if fp_max_deg <= 2:
            small_cap = 3
        elif fp_avg_deg >= 3.0:
            small_cap = 2
        adaptive_iterations = min(adaptive_iterations, small_cap)
    if num_qubits <= 14 and fp_nodes <= 6 and first_partition_size <= 6 and fp_avg_deg <= 2.0:
        adaptive_iterations = min(adaptive_iterations, 4)
    return adaptive_iterations


def select_qasm_files(circuit_folder, requested_files=None, max_files=None):
    qasm_files = sorted(f for f in os.listdir(circuit_folder) if f.endswith(".qasm"))
    if requested_files:
        requested = []
        missing = []
        for name in requested_files:
            if name in qasm_files:
                requested.append(name)
            else:
                missing.append(name)
        if missing:
            raise FileNotFoundError(f"Missing QASM files: {missing}")
        qasm_files = requested
    if max_files is not None:
        qasm_files = qasm_files[:max_files]
    return qasm_files


def choose_partitioning(dag_object, coupling_graph, partitioning):
    grid_capacity = len(list(coupling_graph.nodes()))
    if partitioning == "fast":
        return fast_partition(dag_object, grid_capacity, coupling_graph)
    if partitioning == "vf2":
        return partition_from_DAG(dag_object, coupling_graph)
    if partitioning == "layer":
        return layer_only_partition(dag_object, grid_capacity, coupling_graph)
    raise ValueError(f"Unsupported partitioning mode: {partitioning}")


def ensure_first_partition_embeddable(first_partition, coupling_graph, grid_size, rb):
    tmp_graph = nx.Graph()
    tmp_graph.add_edges_from(first_partition)
    updated_graph = coupling_graph
    updated_grid_size = grid_size
    while tmp_graph.number_of_edges() and not rx_is_subgraph_iso(updated_graph, tmp_graph):
        updated_graph = extend_graph(updated_graph, updated_grid_size, rb)
        updated_grid_size += 1
    return updated_graph, updated_grid_size


def deterministically_complete_mapping(partial_mapping, coupling_graph):
    mapping = list(partial_mapping)
    used = {tuple(pos) for pos in mapping if pos != -1}
    available = [tuple(node) for node in coupling_graph.nodes() if tuple(node) not in used]
    available.sort()
    cursor = 0
    for idx, pos in enumerate(mapping):
        if pos == -1:
            mapping[idx] = available[cursor]
            cursor += 1
    return _normalize_mapping(mapping)


def legalize_seed_mapping(partial_mapping, first_partition, coupling_graph, num_q, rb):
    all_nodes = [tuple(node) for node in coupling_graph.nodes()]
    seed_mapping = deterministically_complete_mapping(partial_mapping, coupling_graph)
    violating, _ = _split_valid_violating_gates(first_partition, seed_mapping, rb)
    if not violating:
        return seed_mapping

    repaired, repaired_ok = _movement_aware_repair(
        first_partition,
        seed_mapping,
        seed_mapping,
        all_nodes,
        rb,
        future_gates=None,
        max_rounds=40,
    )
    if repaired_ok:
        return _normalize_mapping(repaired)

    active_qubits = _collect_active_qubits(first_partition)
    fd_embedding = force_directed_mapping(
        first_partition,
        seed_mapping,
        all_nodes,
        rb,
        num_q,
        future_gates=None,
    )
    fd_embedding = _normalize_mapping(fd_embedding)
    if active_qubits:
        fd_embedding = _minimize_idle_movement(fd_embedding, seed_mapping, all_nodes, active_qubits)
    fd_embedding, fd_ok = _movement_aware_repair(
        first_partition,
        fd_embedding,
        seed_mapping,
        all_nodes,
        rb,
        future_gates=None,
        max_rounds=40,
    )
    if fd_ok:
        return _normalize_mapping(fd_embedding)

    static_embedding = _try_static_embedding(
        [first_partition],
        coupling_graph,
        num_q,
        rb,
        prev_mapping=seed_mapping,
        max_search_steps=6000,
    )
    if static_embedding is not None:
        return _normalize_mapping(static_embedding)

    raise RuntimeError("Failed to legalize injected initial mapping for the shared first partition.")


def build_vf2_initial_mapping(first_partition, coupling_graph, num_q):
    tmp_graph = nx.Graph()
    tmp_graph.add_edges_from(first_partition)
    mapping = get_rx_one_mapping(tmp_graph, coupling_graph)
    return map2list(mapping, num_q)


def build_mcts_initial_mapping(dag_object, coupling_graph, num_q, grid_size, rb, partitioned_gates):
    iterations = adaptive_mcts_iterations(num_q, partitioned_gates)
    mcts_dict = mcts_initial_mapping(
        dag_object,
        coupling_graph,
        grid_size,
        interaction_radius=rb,
        max_iterations=iterations,
    )
    init_map = [-1] * num_q
    for q, pos in mcts_dict.items():
        if q < num_q:
            init_map[q] = pos
    return init_map, iterations


def make_processor(qasm_filename, circuit_folder, rb):
    return SingleFileProcessor(
        qasm_filename=qasm_filename,
        circuit_folder=circuit_folder,
        benchmark_name="init_mapping_swap",
        interaction_radius=rb,
        extended_radius=2 * rb,
        result_path=os.path.join(SCRIPT_DIR, "res", "tmp_init_swap"),
        embeddings_path=os.path.join(SCRIPT_DIR, "res", "tmp_init_swap", "emb"),
        partitions_path=os.path.join(SCRIPT_DIR, "res", "tmp_init_swap", "part"),
        read_embeddings=False,
        save_partitions_and_embeddings=False,
        save_circuit_results=False,
        save_benchmark_results=False,
        engine="dual",
    )


def run_arm(
    qasm_filename,
    circuit_folder,
    qasm_circuit,
    two_qubit_gates,
    partitioned_gates,
    coupling_graph,
    grid_size,
    num_q,
    rb,
    initial_mapping,
    initial_source,
    initial_mapping_time_s,
    downstream_engine,
):
    partitions = copy.deepcopy(partitioned_gates)
    embedding_graph = coupling_graph.copy()

    env_backup = os.environ.get("DASATOM_DISABLE_FIRST_PARTITION_REFINEMENT")
    if downstream_engine == "force":
        os.environ["DASATOM_DISABLE_FIRST_PARTITION_REFINEMENT"] = "1"
    try:
        embed_start = time.time()
        if downstream_engine == "baseline":
            embeddings, extend_positions = get_embeddings_vf2(
                partitions,
                embedding_graph,
                num_q,
                grid_size,
                rb,
                initial_mapping=list(initial_mapping),
            )
        else:
            embeddings, extend_positions = get_embeddings(
                partitions,
                embedding_graph,
                num_q,
                grid_size,
                rb,
                initial_mapping=list(initial_mapping),
            )
        embedding_time_s = time.time() - embed_start
    finally:
        if env_backup is None:
            os.environ.pop("DASATOM_DISABLE_FIRST_PARTITION_REFINEMENT", None)
        else:
            os.environ["DASATOM_DISABLE_FIRST_PARTITION_REFINEMENT"] = env_backup

    final_grid_size = grid_size + len(extend_positions)
    final_coupling_graph = generate_grid_with_Rb(final_grid_size, final_grid_size, rb)

    processor = make_processor(qasm_filename, circuit_folder, rb)
    processor._validate_schedule_correctness(
        qasm_circuit=qasm_circuit,
        original_two_qubit_gates=two_qubit_gates,
        partitioned_gates=partitions,
        embeddings=embeddings,
        coupling_graph=final_coupling_graph,
    )

    route_start = time.time()
    parallel_groups, movement_ops, merged_parallel = processor._compute_gates_and_movements(
        num_q,
        partitions,
        embeddings,
        final_coupling_graph,
        final_grid_size,
    )
    routing_time_s = time.time() - route_start

    idle_time, fidelity, move_fidelity, total_runtime, num_transfers, num_moves, total_move_distance = compute_fidelity(
        merged_parallel,
        movement_ops,
        num_q,
        len(two_qubit_gates),
    )

    return {
        "qasm_file": qasm_filename,
        "num_qubits": num_q,
        "num_cz_gates": len(two_qubit_gates),
        "shared_partition_count": len(partitioned_gates),
        "final_partition_count": len(partitions),
        "initial_source": initial_source,
        "downstream_engine": downstream_engine,
        "initial_mapping_time_s": initial_mapping_time_s,
        "embedding_time_s": embedding_time_s,
        "routing_time_s": routing_time_s,
        "end_to_end_time_s": initial_mapping_time_s + embedding_time_s + routing_time_s,
        "fidelity": fidelity,
        "movement_fidelity": move_fidelity,
        "idle_time": idle_time,
        "total_runtime": total_runtime,
        "num_movement_ops": len(movement_ops),
        "num_transfers": num_transfers,
        "num_moves": num_moves,
        "total_move_distance": total_move_distance,
        "num_gate_cycles": len(merged_parallel),
        "grid_size_start": grid_size,
        "grid_size_final": final_grid_size,
        "architecture_extensions": len(extend_positions),
        "parallel_group_count": len(parallel_groups),
    }


def summarize_rows(rows):
    try:
        import pandas as pd
    except ImportError:
        return None, None

    df = pd.DataFrame(rows)
    numeric_cols = [
        "initial_mapping_time_s",
        "embedding_time_s",
        "routing_time_s",
        "end_to_end_time_s",
        "fidelity",
        "movement_fidelity",
        "idle_time",
        "total_runtime",
        "num_movement_ops",
        "num_transfers",
        "num_moves",
        "total_move_distance",
        "num_gate_cycles",
        "final_partition_count",
        "architecture_extensions",
    ]
    summary = (
        df.groupby(["downstream_engine", "initial_source"])[numeric_cols]
        .agg(["mean", "median"])
        .reset_index()
    )

    pair_rows = []
    for engine in sorted(df["downstream_engine"].unique()):
        ref = df[(df["downstream_engine"] == engine) & (df["initial_source"] == ("vf2" if engine == "baseline" else "mcts"))]
        alt = df[(df["downstream_engine"] == engine) & (df["initial_source"] == ("mcts" if engine == "baseline" else "vf2"))]
        merged = ref.merge(
            alt,
            on="qasm_file",
            suffixes=("_ref", "_alt"),
        )
        if merged.empty:
            continue
        pair_rows.append(
            {
                "downstream_engine": engine,
                "circuits": len(merged),
                "ref_initial_source": "vf2" if engine == "baseline" else "mcts",
                "alt_initial_source": "mcts" if engine == "baseline" else "vf2",
                "delta_end_to_end_time_s_mean": (merged["end_to_end_time_s_alt"] - merged["end_to_end_time_s_ref"]).mean(),
                "delta_embedding_time_s_mean": (merged["embedding_time_s_alt"] - merged["embedding_time_s_ref"]).mean(),
                "delta_total_move_distance_mean": (merged["total_move_distance_alt"] - merged["total_move_distance_ref"]).mean(),
                "delta_num_transfers_mean": (merged["num_transfers_alt"] - merged["num_transfers_ref"]).mean(),
                "delta_fidelity_mean": (merged["fidelity_alt"] - merged["fidelity_ref"]).mean(),
                "alt_better_fidelity_count": int((merged["fidelity_alt"] > merged["fidelity_ref"]).sum()),
                "alt_lower_move_distance_count": int((merged["total_move_distance_alt"] < merged["total_move_distance_ref"]).sum()),
                "alt_faster_end_to_end_count": int((merged["end_to_end_time_s_alt"] < merged["end_to_end_time_s_ref"]).sum()),
            }
        )
    pair_summary = pd.DataFrame(pair_rows)
    return df, summary, pair_summary


def save_outputs(output_dir, rows, failures=None):
    os.makedirs(output_dir, exist_ok=True)
    details_path = os.path.join(output_dir, "details.csv")

    df = None
    summary = None
    pair_summary = None
    try:
        df, summary, pair_summary = summarize_rows(rows)
    except Exception:
        pass

    if df is not None:
        df.to_csv(details_path, index=False)
        summary.to_csv(os.path.join(output_dir, "summary.csv"), index=False)
        if pair_summary is not None:
            pair_summary.to_csv(os.path.join(output_dir, "pair_summary.csv"), index=False)
        try:
            with __import__("pandas").ExcelWriter(os.path.join(output_dir, "swap_initial_mapping.xlsx")) as writer:
                df.to_excel(writer, sheet_name="details", index=False)
                summary.to_excel(writer, sheet_name="summary", index=False)
                if pair_summary is not None:
                    pair_summary.to_excel(writer, sheet_name="pair_summary", index=False)
        except Exception:
            pass
    else:
        import csv

        with open(details_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

    if failures:
        try:
            import pandas as pd

            pd.DataFrame(failures).to_csv(os.path.join(output_dir, "failures.csv"), index=False)
        except Exception:
            import csv

            with open(os.path.join(output_dir, "failures.csv"), "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=failures[0].keys())
                writer.writeheader()
                writer.writerows(failures)


def print_pair_summary(output_dir):
    pair_path = os.path.join(output_dir, "pair_summary.csv")
    if not os.path.exists(pair_path):
        print(f"No pair summary found at {pair_path}")
        return
    with open(pair_path, "r", encoding="utf-8") as f:
        print(f.read())


def main():
    parser = argparse.ArgumentParser(description="Run shared-partition initial-mapping swap experiments.")
    parser.add_argument("--circuit_folder", default=DEFAULT_CIRCUIT_DIR)
    parser.add_argument("--interaction_radius", type=int, default=2)
    parser.add_argument("--partitioning", choices=["fast", "vf2", "layer"], default="fast")
    parser.add_argument("--files", nargs="*")
    parser.add_argument("--max_files", type=int)
    parser.add_argument(
        "--output_dir",
        default=os.path.join(SCRIPT_DIR, "res", "init_mapping_swap_shared_fast"),
    )
    args = parser.parse_args()

    qasm_files = select_qasm_files(args.circuit_folder, args.files, args.max_files)
    rows = []
    failures = []

    for qasm_filename in qasm_files:
        print(f"[RUN] {qasm_filename}")
        try:
            qasm_circuit = CreateCircuitFromQASM(qasm_filename, args.circuit_folder)
            two_qubit_gates = get_2q_gates_list(qasm_circuit)
            if not two_qubit_gates:
                print(f"  [SKIP] {qasm_filename}: no 2Q gates")
                continue

            _, dag_object = gates_list_to_QC(two_qubit_gates)
            num_q = get_qubits_num(two_qubit_gates)
            grid_size = math.ceil(math.sqrt(num_q))
            coupling_graph = generate_grid_with_Rb(grid_size, grid_size, args.interaction_radius)

            partitioned_gates = choose_partitioning(dag_object, coupling_graph, args.partitioning)
            if not partitioned_gates:
                print(f"  [SKIP] {qasm_filename}: no partitions")
                continue

            coupling_graph, grid_size = ensure_first_partition_embeddable(
                partitioned_gates[0],
                coupling_graph,
                grid_size,
                args.interaction_radius,
            )

            vf2_start = time.time()
            vf2_partial = build_vf2_initial_mapping(partitioned_gates[0], coupling_graph, num_q)
            vf2_init = legalize_seed_mapping(
                vf2_partial,
                partitioned_gates[0],
                coupling_graph,
                num_q,
                args.interaction_radius,
            )
            vf2_time = time.time() - vf2_start

            mcts_start = time.time()
            mcts_partial, mcts_iterations = build_mcts_initial_mapping(
                dag_object,
                coupling_graph,
                num_q,
                grid_size,
                args.interaction_radius,
                partitioned_gates,
            )
            mcts_init = legalize_seed_mapping(
                mcts_partial,
                partitioned_gates[0],
                coupling_graph,
                num_q,
                args.interaction_radius,
            )
            mcts_time = time.time() - mcts_start

            coupling_graph_run = coupling_graph
            grid_size_run = grid_size

            arms = [
                ("baseline", "vf2", vf2_init, vf2_time),
                ("baseline", "mcts", mcts_init, mcts_time),
                ("force", "mcts", mcts_init, mcts_time),
                ("force", "vf2", vf2_init, vf2_time),
            ]

            for downstream_engine, initial_source, initial_mapping, init_time in arms:
                row = run_arm(
                    qasm_filename=qasm_filename,
                    circuit_folder=args.circuit_folder,
                    qasm_circuit=qasm_circuit,
                    two_qubit_gates=two_qubit_gates,
                    partitioned_gates=partitioned_gates,
                    coupling_graph=coupling_graph_run,
                    grid_size=grid_size_run,
                    num_q=num_q,
                    rb=args.interaction_radius,
                    initial_mapping=initial_mapping,
                    initial_source=initial_source,
                    initial_mapping_time_s=init_time,
                    downstream_engine=downstream_engine,
                )
                row["partitioning"] = args.partitioning
                row["mcts_iterations"] = mcts_iterations
                rows.append(row)
                print(
                    f"  [{downstream_engine}/{initial_source}] "
                    f"time={row['end_to_end_time_s']:.3f}s "
                    f"dist={row['total_move_distance']:.2f} "
                    f"fid={row['fidelity']:.6f}"
                )
        except Exception as exc:
            print(f"  [FAIL] {qasm_filename}: {exc}")
            failures.append(
                {
                    "qasm_file": qasm_filename,
                    "partitioning": args.partitioning,
                    "error": str(exc),
                }
            )
            continue

    if not rows:
        raise RuntimeError("No experiment rows were produced.")

    save_outputs(args.output_dir, rows, failures=failures)
    print(f"\nSaved outputs to: {args.output_dir}")
    print_pair_summary(args.output_dir)


if __name__ == "__main__":
    main()
