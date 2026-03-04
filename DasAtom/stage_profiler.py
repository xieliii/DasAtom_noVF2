"""
stage_profiler.py — 编译流水线各阶段时间戳剖析
================================================
对 Dual 引擎和 Baseline (原版 VF2) 分别运行选定电路，
在每个编译阶段打时间戳，输出详细的 Stage Breakdown 对比表。

阶段定义：
  S1: QASM 解析 + 门提取 + DAG 构建
  S2: 架构参数计算 + 耦合图生成
  S3: 分区 (partition_from_DAG)
  S4: 嵌入 (MCTS+FD 或 VF2)
  S5: 路由 + 并行门 (QuantumRouter + get_parallel_gates)
  S6: 保真度计算
"""
import sys, os, time, math
sys.stdout.reconfigure(encoding='utf-8')

# ──── 让两套 DasAtom 可以并行 import ────
def profile_dual(circuit_file):
    """带时间戳剖析 Dual 引擎"""
    sys.path.insert(0, r'e:\coding\DasAtom_reading\DasAtom')
    # 清除已有的模块缓存，确保导入正确版本
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith(('DasAtom_fun', 'DasAtom', 'mcts_mapper', 'analytical_placer', 'Enola')):
            del sys.modules[mod_name]

    from DasAtom_fun import (CreateCircuitFromQASM, get_2q_gates_list, gates_list_to_QC,
                              get_qubits_num, generate_grid_with_Rb, partition_from_DAG,
                              get_embeddings, get_parallel_gates, compute_fidelity, set_parameters)
    from mcts_mapper import mcts_initial_mapping
    from Enola.route import QuantumRouter

    circuit_folder = r'e:\coding\DasAtom_reading\DasAtom\Data\benchmark_circuits'
    stages = {}

    # S1: QASM + 门 + DAG
    t0 = time.time()
    qasm_circuit = CreateCircuitFromQASM(circuit_file, circuit_folder)
    two_qubit_gates_list = get_2q_gates_list(qasm_circuit)
    qc_object, dag_object = gates_list_to_QC(two_qubit_gates_list)
    stages['S1_parse'] = time.time() - t0

    # S2: 架构参数 + 耦合图
    t0 = time.time()
    num_cz_gates = len(two_qubit_gates_list)
    num_qubits = get_qubits_num(two_qubit_gates_list)
    grid_size = math.ceil(math.sqrt(num_qubits))
    Rb = 2
    coupling_graph = generate_grid_with_Rb(grid_size, grid_size, Rb)
    stages['S2_arch'] = time.time() - t0

    # S3: 分区
    t0 = time.time()
    partitioned_gates = partition_from_DAG(dag_object, coupling_graph)
    stages['S3_partition'] = time.time() - t0

    # S4: 嵌入 (MCTS + 力导向)
    t0 = time.time()
    adaptive_iterations = int(max(100, (num_qubits ** 2) * 10))
    t_mcts_start = time.time()
    mcts_dict = mcts_initial_mapping(dag_object, coupling_graph, grid_size,
                                      interaction_radius=Rb, max_iterations=adaptive_iterations)
    stages['S4a_mcts'] = time.time() - t_mcts_start

    init_map_list = [-1] * num_qubits
    for q, pos in mcts_dict.items():
        if q < num_qubits:
            init_map_list[q] = pos

    t_fd_start = time.time()
    embeddings, _ = get_embeddings(partitioned_gates, coupling_graph, num_qubits,
                                    grid_size, Rb, initial_mapping=init_map_list)
    stages['S4b_fd'] = time.time() - t_fd_start
    stages['S4_embed_total'] = time.time() - t0

    # S5: 路由 + 并行门
    t0 = time.time()
    router = QuantumRouter(num_qubits, embeddings, partitioned_gates, [grid_size, grid_size])
    router.run()
    t_route = time.time() - t0
    stages['S5a_route'] = t_route

    t0 = time.time()
    Re = 2 * Rb
    merged_parallel_gates = []
    movements_list = []
    for i in range(len(partitioned_gates)):
        gates = get_parallel_gates(partitioned_gates[i], coupling_graph, embeddings[i], Re)
        merged_parallel_gates.extend(gates)
    for i in range(len(embeddings) - 1):
        for move_group in router.movement_list[i]:
            movements_list.append(move_group)
    stages['S5b_parallel'] = time.time() - t0
    stages['S5_route_total'] = t_route + stages['S5b_parallel']

    # S6: 保真度
    t0 = time.time()
    idle_time, fidelity, move_fidelity, total_runtime, num_transfers, num_moves, total_move_distance = \
        compute_fidelity(merged_parallel_gates, movements_list, num_qubits, num_cz_gates)
    stages['S6_fidelity_calc'] = time.time() - t0

    stages['total'] = sum(v for k, v in stages.items() if k.startswith('S') and '_total' not in k and k.count('_') <= 1 or k in ['S4_embed_total', 'S5_route_total'])
    stages['total'] = stages['S1_parse'] + stages['S2_arch'] + stages['S3_partition'] + stages['S4_embed_total'] + stages['S5_route_total'] + stages['S6_fidelity_calc']
    stages['fidelity'] = fidelity
    stages['distance'] = total_move_distance
    stages['qubits'] = num_qubits

    return stages


def profile_baseline(circuit_file):
    """带时间戳剖析 Baseline VF2 引擎"""
    # 把 baseline 目录放在 sys.path 最前面
    sys.path.insert(0, r'e:\coding\DasAtom_reading\DasAtom_Origin')
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith(('DasAtom_fun', 'DasAtom', 'mcts_mapper', 'analytical_placer', 'Enola')):
            del sys.modules[mod_name]

    from DasAtom_fun import (CreateCircuitFromQASM, get_2q_gates_list, gates_list_to_QC,
                              get_qubits_num, generate_grid_with_Rb, partition_from_DAG,
                              get_embeddings, get_parallel_gates, compute_fidelity)
    from Enola.route import QuantumRouter

    circuit_folder = r'e:\coding\DasAtom_reading\DasAtom\Data\benchmark_circuits'
    stages = {}

    # S1: QASM + 门 + DAG
    t0 = time.time()
    qasm_circuit = CreateCircuitFromQASM(circuit_file, circuit_folder)
    two_qubit_gates_list = get_2q_gates_list(qasm_circuit)
    qc_object, dag_object = gates_list_to_QC(two_qubit_gates_list)
    stages['S1_parse'] = time.time() - t0

    # S2: 架构参数 + 耦合图
    t0 = time.time()
    num_cz_gates = len(two_qubit_gates_list)
    num_qubits = get_qubits_num(two_qubit_gates_list)
    grid_size = math.ceil(math.sqrt(num_qubits))
    Rb = 2
    coupling_graph = generate_grid_with_Rb(grid_size, grid_size, Rb)
    stages['S2_arch'] = time.time() - t0

    # S3: 分区
    t0 = time.time()
    partitioned_gates = partition_from_DAG(dag_object, coupling_graph)
    stages['S3_partition'] = time.time() - t0

    # S4: 嵌入 (纯 VF2)
    t0 = time.time()
    stages['S4a_mcts'] = 0  # Baseline 没有 MCTS
    embeddings, _ = get_embeddings(partitioned_gates, coupling_graph, num_qubits, grid_size, Rb)
    stages['S4b_fd'] = 0   # Baseline 没有力导向
    stages['S4_embed_total'] = time.time() - t0

    # S5: 路由 + 并行门
    t0 = time.time()
    router = QuantumRouter(num_qubits, embeddings, partitioned_gates, [grid_size, grid_size])
    router.run()
    t_route = time.time() - t0
    stages['S5a_route'] = t_route

    t0 = time.time()
    Re = 2 * Rb
    merged_parallel_gates = []
    movements_list = []
    for i in range(len(partitioned_gates)):
        gates = get_parallel_gates(partitioned_gates[i], coupling_graph, embeddings[i], Re)
        merged_parallel_gates.extend(gates)
    for i in range(len(embeddings) - 1):
        for move_group in router.movement_list[i]:
            movements_list.append(move_group)
    stages['S5b_parallel'] = time.time() - t0
    stages['S5_route_total'] = t_route + stages['S5b_parallel']

    # S6: 保真度
    t0 = time.time()
    idle_time, fidelity, move_fidelity, total_runtime, num_transfers, num_moves, total_move_distance = \
        compute_fidelity(merged_parallel_gates, movements_list, num_qubits, num_cz_gates)
    stages['S6_fidelity_calc'] = time.time() - t0

    stages['total'] = stages['S1_parse'] + stages['S2_arch'] + stages['S3_partition'] + stages['S4_embed_total'] + stages['S5_route_total'] + stages['S6_fidelity_calc']
    stages['fidelity'] = fidelity
    stages['distance'] = total_move_distance
    stages['qubits'] = num_qubits

    return stages


# ──────────── 主程序 ────────────
test_circuits = [
    'qft_6.qasm', 'qv_6.qasm', 'random_6.qasm',
    'qft_8.qasm', 'qv_8.qasm', 'random_8.qasm',
    'qft_12.qasm', 'qv_12.qasm', 'random_12.qasm',
    'qft_16.qasm', 'qv_16.qasm', 'random_16.qasm',
    'qft_20.qasm', 'qv_20.qasm', 'random_20.qasm',
]

print("=" * 100)
print("  🔬 Stage Profiler: Dual (MCTS+FD) vs Baseline (VF2)")
print("=" * 100)

all_results = []
for circuit in test_circuits:
    name = circuit.replace('.qasm', '')
    print(f"\n{'─'*50} {name} {'─'*20}")

    # Baseline
    try:
        base = profile_baseline(circuit)
        print(f"  [Baseline] Total={base['total']:.4f}s  Fidelity={base['fidelity']:.6f}  Dist={base['distance']:.1f}")
    except Exception as e:
        print(f"  [Baseline] FAILED: {e}")
        base = None

    # Dual
    try:
        dual = profile_dual(circuit)
        print(f"  [Dual]     Total={dual['total']:.4f}s  Fidelity={dual['fidelity']:.6f}  Dist={dual['distance']:.1f}")
    except Exception as e:
        print(f"  [Dual]     FAILED: {e}")
        dual = None

    if base and dual:
        all_results.append((name, base, dual))

# ──────────── 输出阶段对比表 ────────────
print("\n\n" + "=" * 130)
print("  📊 Stage Breakdown (秒)")
print("=" * 130)

stage_keys = [
    ('S1_parse', 'S1 解析'),
    ('S2_arch', 'S2 架构'),
    ('S3_partition', 'S3 分区'),
    ('S4a_mcts', 'S4a MCTS'),
    ('S4b_fd', 'S4b 力导向'),
    ('S4_embed_total', 'S4 嵌入合计'),
    ('S5a_route', 'S5a 路由'),
    ('S5b_parallel', 'S5b 并行门'),
    ('S5_route_total', 'S5 路由合计'),
    ('S6_fidelity_calc', 'S6 保真度'),
    ('total', '总计'),
]

print(f"\n{'Circuit':15s}", end='')
for key, label in stage_keys:
    print(f" | {label:>10s}", end='')
print(f" |  Engine")
print("─" * 180)

for name, base, dual in all_results:
    # Baseline 行
    print(f"{name:15s}", end='')
    for key, label in stage_keys:
        val = base.get(key, 0)
        print(f" | {val:10.4f}", end='')
    print(f" |  Baseline")

    # Dual 行
    print(f"{'':15s}", end='')
    for key, label in stage_keys:
        val = dual.get(key, 0)
        print(f" | {val:10.4f}", end='')
    print(f" |  Dual")

    # Diff 行 (Dual - Baseline)
    print(f"{'':15s}", end='')
    for key, label in stage_keys:
        diff = dual.get(key, 0) - base.get(key, 0)
        if abs(diff) < 0.0001:
            print(f" | {'─':>10s}", end='')
        else:
            marker = '⬆' if diff > 0 else '⬇'
            print(f" | {marker}{diff:+.4f}", end='')
    print(f" |  Δ(Dual-Base)")
    print()

# ──────────── 汇总：各阶段平均时间占比 ────────────
print("\n" + "=" * 100)
print("  📈 平均时间占比分析")
print("=" * 100)

avg_base = {}
avg_dual = {}
for key, _ in stage_keys:
    vals_b = [b.get(key, 0) for _, b, _ in all_results]
    vals_d = [d.get(key, 0) for _, _, d in all_results]
    avg_base[key] = sum(vals_b) / len(vals_b) if vals_b else 0
    avg_dual[key] = sum(vals_d) / len(vals_d) if vals_d else 0

print(f"\n{'Stage':20s} | {'Baseline':>10s} | {'占比':>6s} | {'Dual':>10s} | {'占比':>6s} | {'差异':>10s}")
print("─" * 80)
for key, label in stage_keys:
    b = avg_base[key]
    d = avg_dual[key]
    bp = b / avg_base['total'] * 100 if avg_base['total'] > 0 else 0
    dp = d / avg_dual['total'] * 100 if avg_dual['total'] > 0 else 0
    diff = d - b
    print(f"{label:20s} | {b:10.4f} | {bp:5.1f}% | {d:10.4f} | {dp:5.1f}% | {diff:+10.4f}")

# 大电路聚焦
print(f"\n\n  🔬 大电路 (≥16Q) 阶段分析:")
large = [(n, b, d) for n, b, d in all_results if b['qubits'] >= 16]
if large:
    for key, label in [('S3_partition', 'S3 分区'), ('S4_embed_total', 'S4 嵌入'), ('S5_route_total', 'S5 路由')]:
        avg_b = sum(b.get(key, 0) for _, b, _ in large) / len(large)
        avg_d = sum(d.get(key, 0) for _, _, d in large) / len(large)
        print(f"    {label:15s}: Baseline={avg_b:.4f}s  Dual={avg_d:.4f}s  Δ={avg_d-avg_b:+.4f}s")
