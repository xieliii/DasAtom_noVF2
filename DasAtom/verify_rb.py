"""
Rb 正确性验证脚本：检查每个分区的嵌入是否满足 Rb 约束。
即：每个门 (u,v) 的两个 qubit 的物理位置距离 ≤ Rb。
"""
import os
import sys
import math
import json
from DasAtom_fun import (
    CreateCircuitFromQASM, get_2q_gates_list, gates_list_to_QC,
    get_qubits_num, generate_grid_with_Rb, layer_only_partition,
    get_layer_gates
)
from mcts_mapper import mcts_initial_mapping
from DasAtom_fun import get_embeddings

def euclidean_dist(p1, p2):
    return math.sqrt((p1[0]-p2[0])**2 + (p1[1]-p2[1])**2)

def verify_rb_correctness(circuit_folder, Rb=2):
    """对每个 .qasm 文件验证 Rb 正确性。"""
    qasm_files = sorted([f for f in os.listdir(circuit_folder) if f.endswith('.qasm')])
    
    total_gates = 0
    total_violations = 0
    total_partitions = 0
    
    for qf in qasm_files:
        print(f"\n{'='*60}")
        print(f"Verifying: {qf}")
        print(f"{'='*60}")
        
        # 构建电路
        circ = CreateCircuitFromQASM(qf, circuit_folder)
        gates_2q = get_2q_gates_list(circ)
        if not gates_2q:
            print(f"  SKIP: no 2-qubit gates")
            continue
        
        num_q = get_qubits_num(gates_2q)
        qc, dag = gates_list_to_QC(gates_2q)
        grid_size = math.ceil(math.sqrt(num_q))
        coupling_graph = generate_grid_with_Rb(grid_size, grid_size, Rb)
        grid_capacity = len(list(coupling_graph.nodes()))
        
        # 分区
        partitioned_gates = layer_only_partition(dag, grid_capacity, coupling_graph)
        print(f"  Qubits: {num_q}, Grid: {grid_size}x{grid_size}, Partitions: {len(partitioned_gates)}")
        
        # MCTS 初始映射
        adaptive_iter = int(max(100, (num_q ** 2) * 10))
        mcts_dict = mcts_initial_mapping(dag, coupling_graph, grid_size,
                                         interaction_radius=Rb,
                                         max_iterations=adaptive_iter)
        init_map = [-1] * num_q
        for q, pos in mcts_dict.items():
            if q < num_q:
                init_map[q] = pos
        
        # 嵌入
        embeddings, _ = get_embeddings(partitioned_gates, coupling_graph, num_q, grid_size, Rb,
                                       initial_mapping=init_map)
        
        # 验证每个分区
        file_violations = 0
        file_gates = 0
        
        for part_idx, (gates, emb) in enumerate(zip(partitioned_gates, embeddings)):
            part_violations = 0
            for gate in gates:
                u, v = gate[0], gate[1]
                pos_u = emb[u]
                pos_v = emb[v]
                
                if pos_u == -1 or pos_v == -1:
                    print(f"  [PART {part_idx}] UNMAPPED: gate ({u},{v}) — pos_u={pos_u}, pos_v={pos_v}")
                    part_violations += 1
                    continue
                
                dist = euclidean_dist(pos_u, pos_v)
                if dist > Rb + 1e-9:
                    part_violations += 1
                    print(f"  [PART {part_idx}] VIOLATION: gate ({u},{v}) dist={dist:.3f} > Rb={Rb}")
                
                file_gates += 1
            
            file_violations += part_violations
        
        status = "✓ PASS" if file_violations == 0 else f"✗ FAIL ({file_violations} violations)"
        print(f"  Result: {status} ({file_gates} gates checked)")
        
        total_gates += file_gates
        total_violations += file_violations
        total_partitions += len(partitioned_gates)
    
    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"  Files: {len(qasm_files)}")
    print(f"  Total partitions: {total_partitions}")
    print(f"  Total gates checked: {total_gates}")
    print(f"  Total Rb violations: {total_violations}")
    if total_violations == 0:
        print(f"  ✓ ALL GATES SATISFY Rb CONSTRAINT")
    else:
        print(f"  ✗ {total_violations} VIOLATIONS FOUND")
    
    return total_violations

if __name__ == "__main__":
    folder = sys.argv[1] if len(sys.argv) > 1 else "benchmarks/star_like"
    rb = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    violations = verify_rb_correctness(folder, rb)
    sys.exit(1 if violations > 0 else 0)
