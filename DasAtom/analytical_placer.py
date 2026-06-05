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

_RB_NEIGHBOR_CACHE = {}


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
    node_list = tuple(sorted(tuple(node) for node in all_grid_nodes))
    key = (float(Rb), node_list)
    cached = _RB_NEIGHBOR_CACHE.get(key)
    if cached is not None:
        return cached

    if len(_RB_NEIGHBOR_CACHE) > 64:
        _RB_NEIGHBOR_CACHE.clear()

    rb_neighbors = {}
    for n1 in node_list:
        neighbors = {n1}
        for n2 in node_list:
            if n1 != n2 and _euclidean_dist(n1, n2) <= Rb + 1e-9:
                neighbors.add(n2)
        rb_neighbors[n1] = neighbors
    _RB_NEIGHBOR_CACHE[key] = rb_neighbors
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
                                 gate_adj, rb_neighbors, Rb, base_mapping=None):
    """
    约束传播贪心吸附：
    1. 高度数 qubit 优先分配
    2. 分配时考虑"有足够空闲 Rb 邻居"
    3. 分配后约束传播，缩小门邻居的候选集
    4. 候选集空时用 BFS 修复（从已分配门邻居出发找最近空位）
    """
    if base_mapping is None:
        num_qubits = max(sorted_qubits) + 1 if sorted_qubits else 0
        new_mapping = [-1] * num_qubits
        assigned = set()
    else:
        num_qubits = len(base_mapping)
        new_mapping = [(-1 if pos == -1 else tuple(pos)) for pos in base_mapping]
        assigned = {q for q, pos in enumerate(new_mapping) if pos != -1}
    available_nodes = set(all_grid_nodes) - {pos for pos in new_mapping if pos != -1}
    candidate_sets = {}
    for q in sorted_qubits:
        if q < len(new_mapping) and new_mapping[q] != -1:
            candidate_sets[q] = set(available_nodes) | {new_mapping[q]}
        else:
            candidate_sets[q] = set(all_grid_nodes)

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


def _build_hub_seed_mapping(current_gates, prev_mapping, all_grid_nodes, rb, num_qubits, future_gates=None):
    """
    Fast hub-centric layout for star / spoke-heavy partitions.
    This keeps the core idea in force-directed, but bypasses the full linear solve
    when a single high-degree hub dominates the current partition.
    """
    gate_adj = _build_gate_adj(current_gates)
    if not gate_adj:
        return None

    degrees = {q: len(neigh) for q, neigh in gate_adj.items()}
    hub, hub_deg = max(degrees.items(), key=lambda item: item[1])
    active_nodes = list(gate_adj.keys())
    edge_count = len(current_gates)
    avg_deg = (2.0 * edge_count / max(1, len(active_nodes)))
    if len(active_nodes) < 10 or hub_deg < min(len(active_nodes) - 1, 8) or avg_deg > 2.4:
        return None

    center_x = sum(n[0] for n in all_grid_nodes) / len(all_grid_nodes)
    center_y = sum(n[1] for n in all_grid_nodes) / len(all_grid_nodes)
    rb_neighbors = _precompute_rb_neighbors(all_grid_nodes, rb)

    future_touch = defaultdict(int)
    if future_gates:
        for layer in future_gates[:2]:
            for gate in layer:
                future_touch[gate[0]] += 1
                future_touch[gate[1]] += 1

    solve_qubits = set(active_nodes)
    if future_gates:
        for layer in future_gates[:1]:
            for gate in layer:
                solve_qubits.add(gate[0])
                solve_qubits.add(gate[1])

    occupied_fixed = set()
    candidates = []
    prev_hub = None if prev_mapping[hub] == -1 else tuple(prev_mapping[hub])
    for node in all_grid_nodes:
        if node in occupied_fixed:
            continue
        free_rb = len([n for n in rb_neighbors[node] if n not in occupied_fixed]) - 1
        capacity_penalty = max(0, hub_deg - free_rb) * 1000.0
        prev_cost = 0.0 if prev_hub is None else _euclidean_dist(node, prev_hub)
        center_cost = _euclidean_dist(node, (center_x, center_y))
        candidates.append((capacity_penalty, prev_cost, center_cost, node))

    if not candidates:
        return None

    candidates.sort()
    best_hub_nodes = [node for _, _, _, node in candidates[: min(6, len(candidates))]]
    best_mapping = None
    best_score = None
    for hub_node in best_hub_nodes:
        ideal_positions = {}
        ideal_positions[hub] = hub_node

        spoke_nodes = [
            n for n in rb_neighbors[hub_node]
            if n != hub_node and n not in occupied_fixed
        ]
        spoke_nodes.sort(
            key=lambda n: (
                _euclidean_dist(n, (center_x, center_y)),
                0.0 if prev_hub is None else _euclidean_dist(n, prev_hub)
            )
        )

        spoke_qubits = list(gate_adj.get(hub, set()))
        spoke_qubits.sort(
            key=lambda q: (
                future_touch.get(q, 0),
                1 if prev_mapping[q] == -1 else 0
            ),
            reverse=True
        )
        if len(spoke_nodes) < len(spoke_qubits):
            continue

        available_spokes = list(spoke_nodes)
        for q in spoke_qubits:
            prev_q = None if prev_mapping[q] == -1 else tuple(prev_mapping[q])
            if prev_q is None:
                chosen = available_spokes.pop(0)
            else:
                idx = min(range(len(available_spokes)), key=lambda j: _euclidean_dist(available_spokes[j], prev_q))
                chosen = available_spokes.pop(idx)
            ideal_positions[q] = chosen

        for q in solve_qubits:
            if q in ideal_positions:
                continue
            if prev_mapping[q] != -1:
                ideal_positions[q] = tuple(prev_mapping[q])
            else:
                ideal_positions[q] = (center_x, center_y)

        for q in range(num_qubits):
            if q in ideal_positions:
                continue
            if prev_mapping[q] != -1:
                ideal_positions[q] = tuple(prev_mapping[q])
            else:
                ideal_positions[q] = (center_x, center_y)

        sorted_qubits = sorted(
            range(num_qubits),
            key=lambda q: (
                1 if q == hub else 0,
                1 if q in solve_qubits else 0,
                degrees.get(q, 0),
                future_touch.get(q, 0),
                1 if prev_mapping[q] == -1 else 0
            ),
            reverse=True
        )
        trial = _constraint_propagated_snap(
            sorted_qubits,
            ideal_positions,
            all_grid_nodes,
            gate_adj,
            rb_neighbors,
            rb,
            base_mapping=None
        )

        violations = 0
        movement = 0.0
        for gate in current_gates:
            u, v = gate[0], gate[1]
            if _euclidean_dist(tuple(trial[u]), tuple(trial[v])) > rb + 1e-9:
                violations += 1
        for q in solve_qubits:
            if prev_mapping[q] != -1:
                movement += _euclidean_dist(tuple(trial[q]), tuple(prev_mapping[q]))
        score = (violations, round(movement, 8))
        if best_score is None or score < best_score:
            best_score = score
            best_mapping = trial
            if violations == 0:
                break

    return best_mapping


def _build_dense_seed_mapping(current_gates, prev_mapping, all_grid_nodes, rb, num_qubits, future_gates=None):
    """
    Compact central seed for small dense partitions.
    Helps force-directed start from a near-feasible cluster instead of spreading qubits out.
    """
    gate_adj = _build_gate_adj(current_gates)
    if not gate_adj:
        return None

    active_nodes = sorted(gate_adj.keys())
    edge_count = len(current_gates)
    avg_deg = (2.0 * edge_count / max(1, len(active_nodes)))
    max_deg = max((len(neigh) for neigh in gate_adj.values()), default=0)
    if not (9 <= len(active_nodes) <= 12 and avg_deg >= 4.0 and max_deg >= min(len(active_nodes) - 1, 5)):
        return None

    rb_neighbors = _precompute_rb_neighbors(all_grid_nodes, rb)
    center_x = sum(n[0] for n in all_grid_nodes) / len(all_grid_nodes)
    center_y = sum(n[1] for n in all_grid_nodes) / len(all_grid_nodes)

    future_touch = defaultdict(int)
    if future_gates:
        for layer in future_gates[:2]:
            for gate in layer:
                future_touch[gate[0]] += 1
                future_touch[gate[1]] += 1

    physical_rank = sorted(
        all_grid_nodes,
        key=lambda node: (
            -len(rb_neighbors[node]),
            _euclidean_dist(node, (center_x, center_y))
        )
    )
    seed_nodes = physical_rank[: len(active_nodes)]
    ideal_positions = {}

    logical_rank = sorted(
        active_nodes,
        key=lambda q: (
            len(gate_adj.get(q, set())),
            future_touch.get(q, 0),
            1 if prev_mapping[q] == -1 else 0
        ),
        reverse=True
    )
    seed_nodes = sorted(
        seed_nodes,
        key=lambda node: (
            len(rb_neighbors[node]),
            -_euclidean_dist(node, (center_x, center_y))
        ),
        reverse=True
    )

    available_seed_nodes = list(seed_nodes)
    for q in logical_rank:
        prev_q = None if prev_mapping[q] == -1 else tuple(prev_mapping[q])
        if prev_q is None:
            chosen = available_seed_nodes.pop(0)
        else:
            idx = min(
                range(len(available_seed_nodes)),
                key=lambda j: _euclidean_dist(available_seed_nodes[j], prev_q)
            )
            chosen = available_seed_nodes.pop(idx)
        ideal_positions[q] = chosen

    for q in range(num_qubits):
        if q in ideal_positions:
            continue
        if prev_mapping[q] != -1:
            ideal_positions[q] = tuple(prev_mapping[q])
        else:
            ideal_positions[q] = (center_x, center_y)

    sorted_qubits = sorted(
        range(num_qubits),
        key=lambda q: (
            1 if q in active_nodes else 0,
            len(gate_adj.get(q, set())),
            future_touch.get(q, 0),
            1 if prev_mapping[q] == -1 else 0
        ),
        reverse=True
    )

    return _constraint_propagated_snap(
        sorted_qubits,
        ideal_positions,
        all_grid_nodes,
        gate_adj,
        rb_neighbors,
        rb,
        base_mapping=None
    )


def force_directed_mapping(current_gates, prev_mapping, all_grid_nodes,
                           Rb, num_qubits, future_gates=None):
    """
    自适应锚定力导向映射 + 约束传播吸附。
    """
    w_gate = 3.0
    w_active = 1.0
    w_idle = 5.5
    w_future_anchor = 1.4

    active_qubits = set()
    for gate in current_gates:
        active_qubits.add(gate[0])
        active_qubits.add(gate[1])

    hub_seed = _build_hub_seed_mapping(
        current_gates,
        prev_mapping,
        all_grid_nodes,
        Rb,
        num_qubits,
        future_gates=future_gates
    )
    if hub_seed is not None:
        return hub_seed

    dense_seed = _build_dense_seed_mapping(
        current_gates,
        prev_mapping,
        all_grid_nodes,
        Rb,
        num_qubits,
        future_gates=future_gates
    )
    dense_mode = dense_seed is not None
    anchor_mapping = [(-1 if pos == -1 else tuple(pos)) for pos in prev_mapping]
    if dense_mode:
        for q in active_qubits:
            anchor_mapping[q] = tuple(dense_seed[q])
        if future_gates:
            dense_future = set()
            for layer in future_gates[:1]:
                for gate in layer:
                    dense_future.add(gate[0])
                    dense_future.add(gate[1])
            for q in dense_future:
                if dense_seed[q] != -1:
                    anchor_mapping[q] = tuple(dense_seed[q])
        w_gate = 3.6
        w_active = 0.7
        w_future_anchor = 1.1

    future_active_scores = defaultdict(float)
    future_active_qubits = set()
    if future_gates:
        for layer_offset, layer in enumerate(future_gates[:3]):
            layer_weight = 0.72 ** layer_offset
            for gate in layer:
                u, v = gate[0], gate[1]
                future_active_qubits.add(u)
                future_active_qubits.add(v)
                future_active_scores[u] += layer_weight
                future_active_scores[v] += layer_weight
                if u in active_qubits:
                    future_active_scores[v] += 0.5 * layer_weight
                if v in active_qubits:
                    future_active_scores[u] += 0.5 * layer_weight

    selected_future_qubits = set()
    if future_active_qubits:
        future_only = list(future_active_qubits - active_qubits)
        future_cap = max(3, min(8, len(active_qubits) // 2 + 2))
        future_only.sort(
            key=lambda q: (
                future_active_scores.get(q, 0.0),
                1 if prev_mapping[q] == -1 else 0
            ),
            reverse=True
        )
        selected_future_qubits = set(future_only[:future_cap])

    solve_active_qubits = set(active_qubits)

    solve_qubits = sorted(
        solve_active_qubits | selected_future_qubits | {q for q in range(num_qubits) if prev_mapping[q] == -1}
    )
    if not solve_qubits:
        return [(-1 if pos == -1 else tuple(pos)) for pos in prev_mapping]
    solve_index = {q: idx for idx, q in enumerate(solve_qubits)}

    A = np.zeros((len(solve_qubits), len(solve_qubits)))
    Bx = np.zeros(len(solve_qubits))
    By = np.zeros(len(solve_qubits))

    center_x = sum(n[0] for n in all_grid_nodes) / len(all_grid_nodes)
    center_y = sum(n[1] for n in all_grid_nodes) / len(all_grid_nodes)

    # --- 自适应锚定 ---
    for q in solve_qubits:
        idx = solve_index[q]
        if anchor_mapping[q] != -1:
            old_x, old_y = anchor_mapping[q]
            if q in active_qubits:
                w_anc = w_active
            elif q in future_active_qubits:
                w_anc = w_future_anchor
            else:
                w_anc = w_idle
            A[idx, idx] += w_anc
            Bx[idx] += w_anc * old_x
            By[idx] += w_anc * old_y
        else:
            epsilon = 0.05
            A[idx, idx] += epsilon
            Bx[idx] += epsilon * center_x
            By[idx] += epsilon * center_y

    # --- 当前层门引力 ---
    degrees = {i: 0 for i in range(num_qubits)}
    for gate in current_gates:
        u, v = gate[0], gate[1]
        degrees[u] += 1
        degrees[v] += 1
        if u in solve_index and v in solve_index:
            iu = solve_index[u]
            iv = solve_index[v]
            A[iu, iu] += w_gate
            A[iv, iv] += w_gate
            A[iu, iv] -= w_gate
            A[iv, iu] -= w_gate
        elif u in solve_index and anchor_mapping[v] != -1:
            iu = solve_index[u]
            A[iu, iu] += w_gate
            Bx[iu] += w_gate * anchor_mapping[v][0]
            By[iu] += w_gate * anchor_mapping[v][1]
        elif v in solve_index and anchor_mapping[u] != -1:
            iv = solve_index[v]
            A[iv, iv] += w_gate
            Bx[iv] += w_gate * anchor_mapping[u][0]
            By[iv] += w_gate * anchor_mapping[u][1]

    # --- 前瞻门引力 ---
    if future_gates:
        future_decay = 0.5
        for layer_offset, future_layer in enumerate(future_gates):
            w_future = w_gate * (future_decay ** (layer_offset + 1))
            for gate in future_layer:
                u, v = gate[0], gate[1]
                degrees[u] += 1
                degrees[v] += 1
                if u in solve_index and v in solve_index:
                    iu = solve_index[u]
                    iv = solve_index[v]
                    A[iu, iu] += w_future
                    A[iv, iv] += w_future
                    A[iu, iv] -= w_future
                    A[iv, iu] -= w_future
                elif u in solve_index and anchor_mapping[v] != -1:
                    iu = solve_index[u]
                    A[iu, iu] += w_future
                    Bx[iu] += w_future * anchor_mapping[v][0]
                    By[iu] += w_future * anchor_mapping[v][1]
                elif v in solve_index and anchor_mapping[u] != -1:
                    iv = solve_index[v]
                    A[iv, iv] += w_future
                    Bx[iv] += w_future * anchor_mapping[u][0]
                    By[iv] += w_future * anchor_mapping[u][1]

    # --- 求解 ---
    try:
        X_opt = np.linalg.solve(A, Bx)
        Y_opt = np.linalg.solve(A, By)
    except np.linalg.LinAlgError:
        A += np.eye(len(solve_qubits)) * 1e-4
        X_opt = np.linalg.solve(A, Bx)
        Y_opt = np.linalg.solve(A, By)

    # --- 约束传播吸附 ---
    snap_gates = list(current_gates)
    if future_gates:
        for layer in future_gates[:1]:
            snap_gates.extend(layer)
    gate_adj = _build_gate_adj(snap_gates)
    rb_neighbors = _precompute_rb_neighbors(all_grid_nodes, Rb)
    ideal_positions = {}
    for q in solve_qubits:
        idx = solve_index[q]
        ideal_positions[q] = (X_opt[idx], Y_opt[idx])
    for q in range(num_qubits):
        if q not in ideal_positions:
            if anchor_mapping[q] != -1:
                ideal_positions[q] = tuple(anchor_mapping[q])
            else:
                ideal_positions[q] = (center_x, center_y)

    # 排序：当前层门度数高的优先（hub qubit 先抢高度数位置）
    # 二级排序：有门的 qubit 优先于无门的
    sorted_qubits = sorted(
        solve_qubits,
        key=lambda q: (
            1 if q in active_qubits else 0,
            degrees[q],
            future_active_scores.get(q, 0.0),
            1 if prev_mapping[q] == -1 else 0
        ),
        reverse=True
    )

    base_mapping = [(-1 if pos == -1 else tuple(pos)) for pos in anchor_mapping]
    for q in solve_qubits:
        base_mapping[q] = -1
    new_mapping = _constraint_propagated_snap(
        sorted_qubits, ideal_positions, all_grid_nodes,
        gate_adj, rb_neighbors, Rb, base_mapping=base_mapping
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
