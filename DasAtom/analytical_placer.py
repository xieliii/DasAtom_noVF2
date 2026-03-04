"""
力导向解析布局模块 (analytical_placer.py)

自适应锚定版 + 约束传播吸附：
- 有门的 qubit：低锚定，门引力主导
- 没门的 qubit：高锚定，原地不动
- 前瞻力：提前为未来层预判位置
- 约束传播吸附：保证每个门的两端 qubit 在 Rb 距离内
- BFS 修复：当约束冲突时，从已分配邻居位置做 BFS 搜空位

日期：2026-03-03
"""

import math
import numpy as np
from collections import defaultdict, deque


def _euclidean_dist(p1, p2):
    """计算两个位置之间的欧几里得距离。"""
    return math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)


def _build_gate_adj(current_gates):
    """
    构建当前层的 qubit 门邻接表。
    返回 dict: {qubit_id: set of neighbor qubit_ids}
    """
    adj = defaultdict(set)
    for gate in current_gates:
        u, v = gate[0], gate[1]
        adj[u].add(v)
        adj[v].add(u)
    return dict(adj)


def _precompute_rb_neighbors(all_grid_nodes, Rb):
    """
    预计算每个格点在 Rb 距离内的邻居集合（含自身）。
    """
    rb_neighbors = {}
    node_list = list(all_grid_nodes)
    for n1 in node_list:
        neighbors = {n1}
        for n2 in node_list:
            if n1 != n2 and _euclidean_dist(n1, n2) <= Rb + 1e-9:
                neighbors.add(n2)
        rb_neighbors[n1] = neighbors
    return rb_neighbors


def _bfs_find_nearest(start_positions, available_nodes, rb_neighbors, ideal_pos):
    """
    从一组起始位置出发做 BFS，找到最近的可用格点。
    优先找 Rb 距离内的，如果没有再扩展搜索。
    
    Args:
        start_positions: BFS 起点集合
        available_nodes: 当前可用的格点集合
        rb_neighbors: 预计算的邻居表
        ideal_pos: 理想位置 (x, y)
    
    Returns:
        最佳可用格点，如果找不到返回 None
    """
    # 第一步：从 start_positions 的 Rb 邻居中找可用的
    candidates = set()
    for sp in start_positions:
        candidates |= (rb_neighbors.get(sp, set()) & available_nodes)
    
    if candidates:
        # 从候选中选离理想位置最近的
        return min(candidates, key=lambda n: (n[0]-ideal_pos[0])**2 + (n[1]-ideal_pos[1])**2)
    
    # 如果 Rb 范围内没有可用位置，扩展 BFS
    visited = set()
    queue = deque()
    for sp in start_positions:
        queue.append(sp)
        visited.add(sp)
    
    while queue:
        current = queue.popleft()
        for neighbor in rb_neighbors.get(current, set()):
            if neighbor in visited:
                continue
            visited.add(neighbor)
            if neighbor in available_nodes:
                return neighbor
            queue.append(neighbor)
    
    # 最终回退：从可用节点中选最近的
    if available_nodes:
        return min(available_nodes, key=lambda n: (n[0]-ideal_pos[0])**2 + (n[1]-ideal_pos[1])**2)
    return None


def _constraint_propagated_snap(sorted_qubits, ideal_positions, all_grid_nodes,
                                 gate_adj, rb_neighbors, Rb):
    """
    约束传播贪心吸附：
    1. 高度数 qubit 优先分配
    2. 分配时考虑"有足够空闲 Rb 邻居"
    3. 分配后约束传播，缩小门邻居的候选集
    4. 候选集空时用 BFS 修复（从已分配门邻居出发找最近空位）
    """
    num_qubits = max(sorted_qubits) + 1 if sorted_qubits else 0
    new_mapping = [-1] * num_qubits
    available_nodes = set(all_grid_nodes)
    candidate_sets = {q: set(all_grid_nodes) for q in sorted_qubits}
    assigned = set()

    for q in sorted_qubits:
        valid_candidates = candidate_sets[q] & available_nodes

        if not valid_candidates:
            # --- 约束冲突修复 ---
            # 找已分配的门邻居，从它们的位置做 BFS 找最近可用位置
            assigned_neighbors = []
            if q in gate_adj:
                for nb in gate_adj[q]:
                    if nb in assigned and new_mapping[nb] != -1:
                        assigned_neighbors.append(new_mapping[nb])
            
            if assigned_neighbors:
                best = _bfs_find_nearest(
                    assigned_neighbors, available_nodes, rb_neighbors, ideal_positions[q]
                )
            else:
                best = None
            
            if best is None and available_nodes:
                # 纯回退：选离理想位置最近的
                ideal = ideal_positions[q]
                best = min(available_nodes, 
                          key=lambda n: (n[0]-ideal[0])**2 + (n[1]-ideal[1])**2)
            
            if best is not None:
                new_mapping[q] = best
                available_nodes.discard(best)
                assigned.add(q)
            continue

        ideal = ideal_positions[q]
        q_neighbors = gate_adj.get(q, set())
        unassigned_neighbors = q_neighbors - assigned

        if len(unassigned_neighbors) > 0:
            # Hub qubit 评分：位置要有足够空闲 Rb 邻居容纳 spoke
            def _score(node):
                dist_penalty = (node[0] - ideal[0]) ** 2 + (node[1] - ideal[1]) ** 2
                free_rb = len(rb_neighbors[node] & available_nodes) - 1
                needed = len(unassigned_neighbors)
                capacity_penalty = max(0, needed - free_rb) * 10000.0
                return capacity_penalty + dist_penalty

            best_node = min(valid_candidates, key=_score)
        else:
            best_node = min(
                valid_candidates,
                key=lambda n: (n[0] - ideal[0]) ** 2 + (n[1] - ideal[1]) ** 2
            )

        new_mapping[q] = best_node
        available_nodes.remove(best_node)
        assigned.add(q)

        # --- 前向约束传播 ---
        if q in gate_adj:
            rb_disk = rb_neighbors.get(best_node, {best_node})
            for neighbor in gate_adj[q]:
                if neighbor not in assigned:
                    old_cands = candidate_sets.get(neighbor, set(all_grid_nodes))
                    candidate_sets[neighbor] = old_cands & rb_disk

    return new_mapping


def force_directed_mapping(current_gates, prev_mapping, all_grid_nodes,
                           Rb, num_qubits, future_gates=None):
    """
    自适应锚定力导向映射 + 约束传播吸附。
    """
    w_gate = 3.0
    w_active = 1.0
    w_idle = 5.0

    active_qubits = set()
    for gate in current_gates:
        active_qubits.add(gate[0])
        active_qubits.add(gate[1])

    A = np.zeros((num_qubits, num_qubits))
    Bx = np.zeros(num_qubits)
    By = np.zeros(num_qubits)

    center_x = sum(n[0] for n in all_grid_nodes) / len(all_grid_nodes)
    center_y = sum(n[1] for n in all_grid_nodes) / len(all_grid_nodes)

    # --- 自适应锚定 ---
    for i in range(num_qubits):
        if prev_mapping[i] != -1:
            old_x, old_y = prev_mapping[i]
            w_anc = w_active if i in active_qubits else w_idle
            A[i, i] += w_anc
            Bx[i] += w_anc * old_x
            By[i] += w_anc * old_y
        else:
            epsilon = 0.05
            A[i, i] += epsilon
            Bx[i] += epsilon * center_x
            By[i] += epsilon * center_y

    # --- 当前层门引力 ---
    degrees = {i: 0 for i in range(num_qubits)}
    for gate in current_gates:
        u, v = gate[0], gate[1]
        degrees[u] += 1
        degrees[v] += 1
        A[u, u] += w_gate
        A[v, v] += w_gate
        A[u, v] -= w_gate
        A[v, u] -= w_gate

    # --- 前瞻门引力 ---
    if future_gates:
        future_decay = 0.5
        for layer_offset, future_layer in enumerate(future_gates):
            w_future = w_gate * (future_decay ** (layer_offset + 1))
            for gate in future_layer:
                u, v = gate[0], gate[1]
                degrees[u] += 1
                degrees[v] += 1
                A[u, u] += w_future
                A[v, v] += w_future
                A[u, v] -= w_future
                A[v, u] -= w_future

    # --- 求解 ---
    try:
        X_opt = np.linalg.solve(A, Bx)
        Y_opt = np.linalg.solve(A, By)
    except np.linalg.LinAlgError:
        A += np.eye(num_qubits) * 1e-4
        X_opt = np.linalg.solve(A, Bx)
        Y_opt = np.linalg.solve(A, By)

    # --- 约束传播吸附 ---
    gate_adj = _build_gate_adj(current_gates)
    rb_neighbors = _precompute_rb_neighbors(all_grid_nodes, Rb)
    ideal_positions = {q: (X_opt[q], Y_opt[q]) for q in range(num_qubits)}

    # 排序：当前层门度数高的优先（hub qubit 先抢高度数位置）
    # 二级排序：有门的 qubit 优先于无门的
    sorted_qubits = sorted(
        range(num_qubits),
        key=lambda q: (1 if q in gate_adj else 0, degrees[q]),
        reverse=True
    )

    new_mapping = _constraint_propagated_snap(
        sorted_qubits, ideal_positions, all_grid_nodes,
        gate_adj, rb_neighbors, Rb
    )

    # --- 局部修复 (Local Repair) ---
    def _local_repair(mapping):
        available_nodes = set(all_grid_nodes) - set(mapping)
        for gate in current_gates:
            u, v = gate[0], gate[1]
            pu, pv = mapping[u], mapping[v]
            if pu == -1 or pv == -1 or _euclidean_dist(pu, pv) > Rb + 1e-9:
                # 尝试移动 u 到 pv 的 Rb 邻居中
                if pv != -1:
                    cands_u = rb_neighbors.get(pv, set()) & available_nodes
                    if cands_u:
                        best_u = min(cands_u, key=lambda n: (n[0]-ideal_positions[u][0])**2 + (n[1]-ideal_positions[u][1])**2)
                        available_nodes.add(pu)
                        mapping[u] = best_u
                        available_nodes.remove(best_u)
                        continue
                # 尝试移动 v 到 pu 的 Rb 邻居中
                if pu != -1:
                    cands_v = rb_neighbors.get(pu, set()) & available_nodes
                    if cands_v:
                        best_v = min(cands_v, key=lambda n: (n[0]-ideal_positions[v][0])**2 + (n[1]-ideal_positions[v][1])**2)
                        available_nodes.add(pv)
                        mapping[v] = best_v
                        available_nodes.remove(best_v)
                        continue
        return mapping

    new_mapping = _local_repair(new_mapping)

    return new_mapping
