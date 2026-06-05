"""
MCTS 初始映射模块 (mcts_mapper.py)

该模块实现基于蒙特卡洛树搜索 (MCTS) 的初始量子比特映射生成器。
核心思想：用 MCTS 的"构造式搜索"替代 VF2 子图匹配，
通过向前模拟多层电路的物理保真度来寻找全局最优的初始原子布局。

日期：2026-02-09
"""

import math
import os
import networkx as nx
import numpy as np
from DasAtom_fun import set_parameters, get_layer_gates


class MCTSNode:
    """
    MCTS 搜索树的节点类。
    
    每个节点代表一个部分映射状态，记录了哪些逻辑比特已被放置到物理位置，
    以及哪些比特尚待放置。
    """
    
    def __init__(self, mapping: dict, unmapped_qubits: list, parent=None):
        """
        初始化 MCTS 节点。
        
        Args:
            mapping: 当前已确定的映射字典 {logic_qubit_id: (x, y)}
            unmapped_qubits: 尚未放置的逻辑比特列表
            parent: 父节点引用
        """
        self.mapping = mapping.copy()  # 避免共享引用导致的问题
        self.unmapped_qubits = list(unmapped_qubits)
        self.parent = parent
        self.children = []
        self.visits = 0      # 访问次数 N
        self.value = 0.0     # 累积保真度评分 Q
        self.occupied_positions = set(self.mapping.values())
        self.rollout_mapping = None
        self.rollout_reward = None
        
        # 记录该节点尝试过的动作 (qubit, position) 对
        self.tried_actions = set()
    
    def is_terminal(self) -> bool:
        """判断是否所有比特都已放置（终端节点）。"""
        return len(self.unmapped_qubits) == 0
    
    def is_fully_expanded(self, available_positions: set) -> bool:
        """
        判断是否还有合法的动作未尝试。
        
        Args:
            available_positions: 当前可用的物理位置集合
        
        Returns:
            True 如果所有合法动作都已尝试
        """
        if not self.unmapped_qubits:
            return True
        
        # 计算当前节点可能的动作数量
        next_qubit = self.unmapped_qubits[0]
        possible_actions = len(available_positions)
        tried_for_qubit = len([a for a in self.tried_actions if a[0] == next_qubit])
        
        return tried_for_qubit >= possible_actions
    
    def best_child(self, c_param: float = 1.414) -> 'MCTSNode':
        """
        使用 UCB1 公式选择最佳子节点。
        
        UCB = Q/N + c * sqrt(ln(N_parent) / N)
        
        Args:
            c_param: 探索系数，默认 sqrt(2) ≈ 1.414
        
        Returns:
            UCB 值最高的子节点
        """
        best_score = float('-inf')
        best_child = None
        
        for child in self.children:
            if child.visits == 0:
                # 未访问过的节点给予高优先级
                return child
            
            # UCB1 公式
            exploitation = child.value / child.visits  # 平均保真度
            exploration = c_param * math.sqrt(
                math.log(self.visits) / child.visits
            )
            ucb_score = exploitation + exploration
            
            if ucb_score > best_score:
                best_score = ucb_score
                best_child = child
        
        return best_child
    
    def get_occupied_positions(self) -> set:
        """获取当前映射中已被占用的物理位置集合。"""
        return self.occupied_positions


class MCTSEngine:
    """
    MCTS 搜索引擎，负责执行完整的蒙特卡洛树搜索流程。
    
    核心功能：
    1. 基于几何约束的节点扩展（替代 VF2）
    2. 通过模拟未来 5 层电路来评估映射质量
    3. 返回最优的初始映射结果
    """
    
    def __init__(self, dag, architecture_graph: nx.Graph, 
                 grid_size: int, interaction_radius: float = 2.0):
        """
        初始化 MCTS 引擎。
        
        Args:
            dag: Qiskit DAGCircuit 对象，表示逻辑电路
            architecture_graph: NetworkX 图对象，表示物理架构
                               节点为 (x, y) 坐标元组
            grid_size: 网格大小（边长）
            interaction_radius: 相互作用半径 Rb，默认 2.0
        """
        self.dag = dag
        self.arch_graph = architecture_graph
        self.grid_size = grid_size
        self.Rb = float(interaction_radius)  # 确保是浮点数
        
        # 加载物理参数
        self.params = set_parameters()
        
        # 解析电路层
        self.layers = get_layer_gates(dag)
        
        # 提取所有涉及的逻辑比特（排序以保证跨运行稳定）
        self.all_qubits = sorted(self._extract_all_qubits())
        
        # 预处理：构建第一层的连接关系（用于几何剪枝）
        self.layer0_edges = set()
        self.layer0_neighbors = {}
        if self.layers:
            for gate in self.layers[0]:
                self.layer0_edges.add((gate[0], gate[1]))
                self.layer0_edges.add((gate[1], gate[0]))  # 无向
                self.layer0_neighbors.setdefault(gate[0], set()).add(gate[1])
                self.layer0_neighbors.setdefault(gate[1], set()).add(gate[0])
        
        # 预处理：构建前15层的连接关系（用于比特选择策略）
        # 让 _select_next_qubit 能"顺藤摸瓜"，优先放将来要交互的比特
        self.lookahead_edges = set()
        for layer in self.layers[:15]:
            for gate in layer:
                self.lookahead_edges.add((gate[0], gate[1]))
                self.lookahead_edges.add((gate[1], gate[0]))
        self.lookahead_neighbors = {}
        for u, v in self.lookahead_edges:
            self.lookahead_neighbors.setdefault(u, set()).add(v)

        self.qubit_activity = {q: 0.0 for q in self.all_qubits}
        self.frontier_activity = {q: 0.0 for q in self.all_qubits}
        for layer_idx, layer in enumerate(self.layers[:10]):
            layer_weight = 0.88 ** layer_idx
            for gate in layer:
                u, v = gate[0], gate[1]
                self.qubit_activity[u] += layer_weight
                self.qubit_activity[v] += layer_weight
                if layer_idx < 4:
                    self.frontier_activity[u] += layer_weight
                    self.frontier_activity[v] += layer_weight
        
        # 预处理：计算每个逻辑比特在前15层中的连接度（度数越高 = Hub 比特）
        # 用于中心性启发式：Hub 比特应优先放在网格中心区域
        self.qubit_degree = {}
        for q in self.all_qubits:
            self.qubit_degree[q] = sum(1 for (a, b) in self.lookahead_edges if a == q)
        self.layer0_degree = {q: len(self.layer0_neighbors.get(q, set())) for q in self.all_qubits}
        self.search_qubits = self._select_search_qubits()
        self.search_qubit_set = set(self.search_qubits)
        self.rollout_qubits = [q for q in self.all_qubits if q not in self.search_qubit_set]
        self.rollout_order = sorted(
            self.rollout_qubits,
            key=lambda q: (
                self.frontier_activity.get(q, 0.0),
                self.qubit_activity.get(q, 0.0),
                self.qubit_degree.get(q, 0),
                -q
            ),
            reverse=True
        )
        if len(self.all_qubits) <= 8:
            self.rank_cap_floor = 4
            self.rank_cap_ceiling = 8
        elif len(self.all_qubits) <= 14:
            self.rank_cap_floor = 5
            self.rank_cap_ceiling = 10
        else:
            self.rank_cap_floor = 6
            self.rank_cap_ceiling = 12
        
        # 预计算物理网格的几何中心坐标
        all_nodes = list(architecture_graph.nodes())
        self.grid_center = (
            sum(n[0] for n in all_nodes) / len(all_nodes),
            sum(n[1] for n in all_nodes) / len(all_nodes)
        )
        
        # 物理节点坐标字典 (已经是 (x, y) 格式，直接使用)
        self.node_coords = self._build_node_coords()
        self.arch_nodes = tuple(self.arch_graph.nodes())
        self.arch_nodes_set = set(self.arch_nodes)
        self.center_distance = {
            node: math.hypot(node[0] - self.grid_center[0], node[1] - self.grid_center[1])
            for node in self.arch_nodes
        }
        self.node_distance = {}
        for i, a in enumerate(self.arch_nodes):
            ax, ay = a
            for b in self.arch_nodes[i:]:
                bx, by = b
                d = math.hypot(bx - ax, by - ay)
                self.node_distance[(a, b)] = d
                self.node_distance[(b, a)] = d
    
    def _extract_all_qubits(self) -> set:
        """从所有层中提取涉及的逻辑比特集合。"""
        qubits = set()
        for layer in self.layers:
            for gate in layer:
                qubits.add(gate[0])
                qubits.add(gate[1])
        return qubits
    
    def _build_node_coords(self) -> dict:
        """
        构建物理节点坐标字典。
        
        由于 architecture_graph 的节点 ID 已经是 (x, y) 元组，
        直接返回 {node: node} 映射以保持接口一致性。
        
        Returns:
            dict: {node_id: (x, y)} 格式的坐标字典
        """
        coords = {}
        for node in self.arch_graph.nodes():
            # 节点本身就是 (x, y) 元组
            coords[node] = node
        return coords

    def _select_search_qubits(self) -> list:
        """只让 MCTS 重搜索前沿核心比特，其余比特交给启发式补全。"""
        avg_qubit_degree = sum(self.qubit_degree.values()) / max(1, len(self.all_qubits))
        chain_like = (
            max(self.layer0_degree.values(), default=0) <= 2 and
            max(self.qubit_degree.values(), default=0) <= 3
        )
        hub_like = (
            (
                max(self.layer0_degree.values(), default=0) >= min(max(3, len(self.all_qubits) // 3), len(self.all_qubits) - 1) or
                max(self.qubit_degree.values(), default=0) >= min(len(self.all_qubits) - 1, max(4, len(self.all_qubits) // 2))
            ) and
            avg_qubit_degree <= 3.0
        )
        dense_small = (
            len(self.all_qubits) <= 10 and
            avg_qubit_degree >= 4.0
        )
        dense_frontier_sparse = (
            len(self.all_qubits) >= 12 and
            avg_qubit_degree >= 5.0 and
            max(self.layer0_degree.values(), default=0) <= 1
        )
        self.chain_like = chain_like
        self.hub_like = hub_like
        self.dense_small = dense_small
        self.dense_frontier_sparse = dense_frontier_sparse
        if len(self.all_qubits) <= 8 and not dense_small and not chain_like and not hub_like:
            return list(self.all_qubits)

        focus_qubits = set()
        for layer in self.layers[:4]:
            for gate in layer:
                focus_qubits.add(gate[0])
                focus_qubits.add(gate[1])

        if dense_small:
            target_size = min(len(self.all_qubits), 4 if len(self.all_qubits) <= 8 else 8)
        elif dense_frontier_sparse:
            target_size = min(len(self.all_qubits), 8 if len(self.all_qubits) <= 20 else 9)
        elif hub_like:
            target_size = min(len(self.all_qubits), 4 if len(self.all_qubits) <= 8 else 5)
        elif chain_like:
            target_size = min(len(self.all_qubits), 4 if len(self.all_qubits) <= 8 else (5 if len(self.all_qubits) <= 12 else 7))
        elif len(self.all_qubits) <= 16:
            target_size = min(len(self.all_qubits), max(6, len(focus_qubits) + 2))
        else:
            target_size = min(len(self.all_qubits), max(8, len(focus_qubits) + 2, int(len(self.all_qubits) * 0.45)))

        ranked = sorted(
            self.all_qubits,
            key=lambda q: (
                q in focus_qubits,
                self.frontier_activity.get(q, 0.0),
                self.layer0_degree.get(q, 0),
                self.qubit_activity.get(q, 0.0),
                self.qubit_degree.get(q, 0),
                -q
            ),
            reverse=True
        )

        chosen = []
        chosen_set = set()
        for q in ranked:
            if len(chosen) >= target_size:
                break
            chosen.append(q)
            chosen_set.add(q)

        for q in ranked:
            if q not in chosen_set and self.layer0_degree.get(q, 0) >= 2 and len(chosen) < min(len(self.all_qubits), target_size + 1):
                chosen.append(q)
                chosen_set.add(q)

        return chosen
    
    def _euclidean_distance(self, pos1: tuple, pos2: tuple) -> float:
        """计算两个物理位置之间的欧几里得距离。"""
        if pos1 in self.arch_nodes_set and pos2 in self.arch_nodes_set:
            return self.node_distance[(pos1, pos2)]
        x1, y1 = pos1
        x2, y2 = pos2
        return math.hypot(x2 - x1, y2 - y1)
    
    def _is_valid_placement(self, qubit: int, position: tuple, 
                            current_mapping: dict) -> bool:
        """
        检查将逻辑比特放置到指定物理位置是否满足几何约束。
        
        几何剪枝规则：如果新放置的比特与已放置的比特在 Layer 0 存在门连接，
        则它们的物理距离必须 <= Rb（相互作用半径）。
        
        Args:
            qubit: 待放置的逻辑比特 ID
            position: 目标物理位置 (x, y)
            current_mapping: 当前已有的映射 {logic_qubit: (x, y)}
        
        Returns:
            True 如果放置合法
        """
        neighbors = self.layer0_neighbors.get(qubit)
        if not neighbors:
            return True
        for mapped_qubit in neighbors:
            mapped_pos = current_mapping.get(mapped_qubit)
            if mapped_pos is None:
                continue
            distance = self._euclidean_distance(position, mapped_pos)
            if distance > self.Rb:
                return False  # 违反距离约束
        return True
    
    def _select_next_qubit(self, unmapped_qubits: list, 
                           current_mapping: dict) -> int:
        """
        选择下一个要放置的逻辑比特。
        
        策略：优先选择与已放置比特在前15层有门连接的比特，
        让 MCTS 像"顺藤摸瓜"一样，把将来要交互的比特提前聚拢。
        
        Args:
            unmapped_qubits: 未放置的比特列表
            current_mapping: 当前映射
        
        Returns:
            选中的逻辑比特 ID
        """
        if not current_mapping:
            return max(
                unmapped_qubits,
                key=lambda q: (
                    self.layer0_degree.get(q, 0),
                    self.qubit_degree.get(q, 0),
                    -q
                )
            )

        mapped_set = set(current_mapping.keys())

        def _score(qubit):
            layer0_frontier = len(self.layer0_neighbors.get(qubit, set()) & mapped_set)
            lookahead_frontier = len(self.lookahead_neighbors.get(qubit, set()) & mapped_set)
            return (
                layer0_frontier,
                lookahead_frontier,
                self.layer0_degree.get(qubit, 0),
                self.qubit_degree.get(qubit, 0),
                -qubit
            )

        return max(unmapped_qubits, key=_score)

    def _node_fully_expanded(self, node: MCTSNode, available_positions: set) -> bool:
        """按当前启发式下的有效候选数判断节点是否已扩完。"""
        if not node.unmapped_qubits:
            return True
        next_qubit = self._select_next_qubit(node.unmapped_qubits, node.mapping)
        candidate_count = len(self._rank_positions(next_qubit, available_positions, node.mapping))
        tried_for_qubit = sum(1 for action in node.tried_actions if action[0] == next_qubit)
        return tried_for_qubit >= candidate_count
    
    def _rank_positions(self, qubit: int, available: set,
                        current_mapping: dict) -> list:
        """
        中心性启发式：对空闲物理位置进行确定性排序。
        
        策略分两种情况：
        - 情况1（无邻居）：Hub 比特优先尝试网格中心区域
        - 情况2（有邻居）：优先尝试已放置的连接比特附近
        
        Args:
            qubit: 当前要放置的逻辑比特
            available: 空闲物理位置集合
            current_mapping: 当前已有的映射
        
        Returns:
            排序后的物理位置列表（高权重位置更容易排到前面）
        """
        available_list = list(available)
        
        # 查找该比特在前15层中已放置的邻居位置
        neighbor_positions = []
        for mapped_q, mapped_pos in current_mapping.items():
            if (qubit, mapped_q) in self.lookahead_edges:
                neighbor_positions.append(mapped_pos)
        
        # 计算每个位置的权重
        weights = []
        max_degree = max(self.qubit_degree.values()) if self.qubit_degree else 1
        for pos in available_list:
            if neighbor_positions:
                # === 情况2：有已放置的邻居 -> 偏好邻居附近的位置 ===
                avg_dist = sum(
                    self._euclidean_distance(pos, np) for np in neighbor_positions
                ) / len(neighbor_positions)
                w = 1.0 / (1.0 + avg_dist)
            else:
                # === 情况1：无邻居 -> Hub 比特偏好网格中心 ===
                dist_to_center = self.center_distance.get(
                    pos,
                    self._euclidean_distance(pos, self.grid_center)
                )
                degree_ratio = self.qubit_degree.get(qubit, 1) / max_degree
                w = 1.0 / (1.0 + dist_to_center * degree_ratio)
            weights.append(w)
        
        # 确定性排序：先按权重降序，再按到中心距离升序打破平局。
        scored = []
        for pos, w in zip(available_list, weights):
            center_dist = self.center_distance.get(
                pos,
                self._euclidean_distance(pos, self.grid_center)
            )
            scored.append((w, center_dist, pos))

        scored.sort(key=lambda x: (-x[0], x[1], x[2][0], x[2][1]))
        rank_cap = min(
            len(scored),
            max(
                self.rank_cap_floor,
                min(
                    self.rank_cap_ceiling,
                    4 + self.layer0_degree.get(qubit, 0) + (1 if neighbor_positions else 0) * 2
                )
            )
        )
        if not neighbor_positions and self.layer0_degree.get(qubit, 0) <= 1:
            rank_cap = min(rank_cap, max(4, self.rank_cap_floor))
        return [pos for _, _, pos in scored[:rank_cap]]
    
    def _expand(self, node: MCTSNode) -> MCTSNode:
        """
        扩展节点：基于几何约束和中心性启发式生成一个新的子节点。
        
        替代 VF2 的核心逻辑：
        1. 选择下一个要放置的逻辑比特
        2. 用中心性启发式对空闲位置进行加权排序
        3. 应用几何剪枝检查距离约束
        4. 创建合法的子节点
        
        Args:
            node: 待扩展的父节点
        
        Returns:
            新创建的子节点，如果没有合法动作则返回 None
        """
        if node.is_terminal():
            return None
        
        # 获取已占用和可用的物理位置
        occupied = node.get_occupied_positions()
        available = self.arch_nodes_set - occupied
        
        if not available:
            return None
        
        # 选择下一个要放置的逻辑比特
        next_qubit = self._select_next_qubit(
            node.unmapped_qubits, node.mapping
        )
        
        # 中心性启发式：对位置进行加权排序（替代 random.shuffle）
        # Hub 比特优先尝试网格中心，后续比特优先尝试邻居附近
        ranked_positions = self._rank_positions(
            next_qubit, available, node.mapping
        )
        
        for position in ranked_positions:
            action = (next_qubit, position)
            
            # 跳过已尝试的动作
            if action in node.tried_actions:
                continue
            
            # 几何剪枝：检查距离约束
            if not self._is_valid_placement(next_qubit, position, node.mapping):
                node.tried_actions.add(action)  # 标记为无效
                continue
            
            # 创建新的子节点
            new_mapping = node.mapping.copy()
            new_mapping[next_qubit] = position
            
            new_unmapped = [q for q in node.unmapped_qubits if q != next_qubit]
            
            child = MCTSNode(new_mapping, new_unmapped, parent=node)
            node.children.append(child)
            node.tried_actions.add(action)
            
            return child
        
        return None  # 没有合法的动作
    
    def _greedy_complete_mapping(self, partial_mapping: dict, 
                                  unmapped: list) -> dict:
        """
        贪心补全映射：快速将剩余比特放置到空闲位置。
        
        用于模拟阶段，不需要严格优化，只需快速生成完整映射。
        
        Args:
            partial_mapping: 部分映射
            unmapped: 未放置的比特列表
        
        Returns:
            完整的映射字典
        """
        mapping = partial_mapping.copy()
        occupied = set(mapping.values())
        mapped_set = set(mapping.keys())
        available = sorted(
            list(self.arch_nodes_set - occupied),
            key=lambda p: self.center_distance.get(
                p,
                self._euclidean_distance(p, self.grid_center)
            )
        )
        pending = []
        seen = set(mapped_set)
        for qubit in list(unmapped) + self.rollout_order:
            if qubit in seen:
                continue
            pending.append(qubit)
            seen.add(qubit)

        for qubit in pending:
            if available:
                mapped_frontier = self.lookahead_neighbors.get(qubit, set()) & mapped_set
                if not mapped_frontier and self.layer0_degree.get(qubit, 0) <= 1 and self.qubit_activity.get(qubit, 0.0) < 0.75:
                    pos = available.pop(0)
                    mapping[qubit] = pos
                    mapped_set.add(qubit)
                    continue
                ranked_positions = self._rank_positions(qubit, set(available), mapping)
                placed = False
                for pos in ranked_positions:
                    if not self._is_valid_placement(qubit, pos, mapping):
                        continue
                    mapping[qubit] = pos
                    available.remove(pos)
                    mapped_set.add(qubit)
                    placed = True
                    break

                if not placed and available:
                    pos = available.pop(0)
                    mapping[qubit] = pos
                    mapped_set.add(qubit)

        return mapping
    
    def _estimate_fidelity(self, mapping: dict) -> float:
        """
        估算给定映射的保真度分数。
        
        模拟逻辑：
        1. 向前看 15 层门
        2. 对每层计算物理移动代价，乘以层权重衰减因子
        3. 使用物理公式估算保真度衰减
        
        层权重衰减：越靠后的层受初始映射影响越小（因为路由会改变位置），
        所以用指数衰减 decay^layer_idx 降低后续层的代价权重。
        
        Args:
            mapping: 完整的映射 {logic_qubit: (x, y)}
        
        Returns:
            估算的保真度分数 (0, 1]
        """
        # 创建虚拟映射用于模拟（不修改原映射）
        sim_mapping = mapping.copy()
        
        # 模拟参数
        layers_to_simulate = min(15, len(self.layers))
        layer_decay = 0.9  # 层权重衰减因子：更平缓，让后续层也有足够权重
        total_move_time = 0.0  # 总移动时间（加权后）
        total_trans_count = 0.0  # 抓取/放下次数（加权后，改为 float）
        gate_count = 0           # 门数量（不衰减，用于计算基础保真度）
        
        for layer_idx in range(layers_to_simulate):
            layer = self.layers[layer_idx]
            # 当前层的权重：越靠后影响越小
            layer_weight = layer_decay ** layer_idx
            
            for gate in layer:
                q0, q1 = gate[0], gate[1]
                
                # 获取物理位置
                if q0 not in sim_mapping or q1 not in sim_mapping:
                    continue  # 跳过未映射的比特
                
                pos0 = sim_mapping[q0]
                pos1 = sim_mapping[q1]
                
                # 计算距离
                distance = self._euclidean_distance(pos0, pos1)
                
                if distance > self.Rb:
                    # 需要移动：计算移动代价
                    # 移动距离 = 当前距离 - Rb（移动到刚好能交互的位置）
                    move_distance = distance - self.Rb
                    
                    # 转换为物理距离（微米）
                    physical_distance = move_distance * self.params['AOD_width']
                    
                    # 移动时间（乘以层权重：前面层的搬运代价更重要）
                    move_time = physical_distance / self.params['Move_speed']
                    total_move_time += move_time * layer_weight
                    
                    # 抓取/放下次数（同样乘以层权重）
                    total_trans_count += 4 * layer_weight
                    
                    # 虚拟更新：假设原子移动到彼此附近
                    # 将两个原子 snap 到中点附近的最近整数网格点，保持坐标合法
                    mid_x = (pos0[0] + pos1[0]) / 2
                    mid_y = (pos0[1] + pos1[1]) / 2
                    # q0 取 floor，q1 取 ceil，确保不重叠且都在整数格点上
                    new_x0 = int(math.floor(mid_x))
                    new_y0 = int(round(mid_y))
                    new_x1 = int(math.ceil(mid_x))
                    new_y1 = int(round(mid_y))
                    # 如果 floor == ceil（两点在同一行），让 q1 往右偏移一格
                    if new_x0 == new_x1 and new_y0 == new_y1:
                        new_x1 = min(new_x1 + 1, self.grid_size - 1)
                    sim_mapping[q0] = (new_x0, new_y0)
                    sim_mapping[q1] = (new_x1, new_y1)
                
                gate_count += 1
        
        center_penalty = 0.0
        for q in self.all_qubits:
            pos = sim_mapping.get(q)
            if pos is None:
                continue
            center_penalty += self.center_distance.get(pos, self._euclidean_distance(pos, self.grid_center))

        # 计算总时间
        trans_time = total_trans_count * self.params['T_trans']
        gate_time = gate_count * self.params['T_cz']
        
        # 计算空闲时间 (简化模型)
        num_qubits = len(mapping)
        t_total = gate_time + trans_time + total_move_time
        t_idle = num_qubits * t_total - gate_count * self.params['T_cz']
        
        # 计算保真度: F = e^(-t_idle/T_eff) * F_cz^N * F_trans^M
        fidelity = math.exp(-t_idle / self.params['T_eff'])
        fidelity *= (self.params['F_cz'] ** gate_count)
        fidelity *= (self.params['F_trans'] ** total_trans_count)

        # Prefer compact layouts for active qubits so force-directed starts closer
        # to a low-movement solution in later partitions.
        return fidelity / (1.0 + 0.0025 * center_penalty)
    
    def _simulate(self, node: MCTSNode):
        """
        从给定节点进行模拟，返回保真度评分。
        
        Args:
            node: 起始节点
        
        Returns:
            模拟得到的保真度分数
        """
        if node.rollout_mapping is not None and node.rollout_reward is not None:
            return node.rollout_reward, node.rollout_mapping

        # 如果映射不完整，先贪心补全
        if not node.is_terminal():
            complete_mapping = self._greedy_complete_mapping(
                node.mapping, node.unmapped_qubits
            )
        else:
            complete_mapping = node.mapping
        
        reward = self._estimate_fidelity(complete_mapping)
        node.rollout_mapping = complete_mapping
        node.rollout_reward = reward
        return reward, complete_mapping
    
    def _backpropagate(self, node: MCTSNode, reward: float):
        """
        反向传播：更新从叶节点到根节点路径上所有节点的统计信息。
        
        Args:
            node: 起始节点（叶节点）
            reward: 模拟得到的奖励值（保真度）
        """
        current = node
        while current is not None:
            current.visits += 1
            current.value += reward
            current = current.parent
    
    def search(self, max_iterations: int = 1000) -> dict:
        """
        执行 MCTS 搜索，返回最优的初始映射。
        内置早停机制：连续 PATIENCE 次迭代没有发现更优映射则提前终止。
        
        Args:
            max_iterations: 最大迭代次数（上限）
        
        Returns:
            最优映射字典 {logic_qubit: (x, y)}
        """
        # 早停参数：动态耐心值，小电路少等，大电路多等
        search_qubits = len(self.search_qubits)
        if search_qubits <= 8:
            patience = max(8, min(16, max_iterations // 2))
        elif search_qubits <= 12:
            patience = max(12, min(24, max_iterations // 2))
        else:
            patience = max(24, min(48, int(search_qubits * 2.5)))
        if getattr(self, "dense_frontier_sparse", False):
            patience = max(5, int(patience * 0.5))

        # 创建根节点
        root = MCTSNode(
            mapping={},
            unmapped_qubits=list(self.search_qubits)
        )

        best_mapping = self._greedy_complete_mapping({}, self.search_qubits)
        best_fidelity = self._estimate_fidelity(best_mapping)
        if self.dense_small and search_qubits <= 4 and max_iterations <= 2:
            return best_mapping
        if search_qubits <= 5 and (self.chain_like or self.hub_like) and max_iterations <= 10:
            return best_mapping
        no_improve_count = 0
        actual_iterations = 0
        
        for iteration in range(max_iterations):
            node = root
            
            # === Selection ===
            # 沿着树向下选择，直到找到可扩展的节点或叶节点
            while node.children and not node.is_terminal():
                available = self.arch_nodes_set - node.get_occupied_positions()
                if self._node_fully_expanded(node, available):
                    node = node.best_child()
                else:
                    break
            
            # === Expansion ===
            if not node.is_terminal():
                child = self._expand(node)
                if child:
                    node = child
            
            # === Simulation ===
            reward, sampled_mapping = self._simulate(node)
            
            # === Backpropagation ===
            self._backpropagate(node, reward)
            
            actual_iterations = iteration + 1
            
            # === Early Stopping ===
            if reward > best_fidelity:
                best_fidelity = reward
                best_mapping = sampled_mapping
                no_improve_count = 0
            else:
                no_improve_count += 1
            
            if no_improve_count >= patience:
                if os.environ.get("DASATOM_VERBOSE_MCTS", "0") == "1":
                    print(
                        f"  [MCTS] Early stop at iteration {actual_iterations}/{max_iterations} "
                        f"(no improvement for {patience} rounds)"
                    )
                break
        
        if no_improve_count < patience:
            if os.environ.get("DASATOM_VERBOSE_MCTS", "0") == "1":
                print(f"  [MCTS] Completed all {actual_iterations} iterations")
        
        # 优先返回搜索过程中观测到的最高奖励映射。
        if best_mapping is not None:
            return best_mapping

        # 回退：返回访问次数最多的路径对应的映射。
        return self._extract_best_mapping(root)
    
    def _extract_best_mapping(self, root: MCTSNode) -> dict:
        """
        从根节点提取访问次数最多的路径对应的完整映射。
        
        Args:
            root: 根节点
        
        Returns:
            最优映射字典
        """
        node = root
        
        while node.children:
            # 选择访问次数最多的子节点
            best_child = max(node.children, key=lambda c: c.visits)
            node = best_child
        
        # 如果映射不完整，贪心补全
        if not node.is_terminal():
            return self._greedy_complete_mapping(
                node.mapping, node.unmapped_qubits
            )

        return self._greedy_complete_mapping(node.mapping, [])


def mcts_initial_mapping(dag, architecture_graph: nx.Graph, 
                         grid_size: int, interaction_radius: float = 2.0,
                         max_iterations: int = 1000) -> dict:
    """
    便捷接口：使用 MCTS 生成初始映射。
    
    Args:
        dag: Qiskit DAGCircuit 对象
        architecture_graph: NetworkX 物理架构图
        grid_size: 网格大小
        interaction_radius: 相互作用半径 Rb
        max_iterations: MCTS 迭代次数
    
    Returns:
        初始映射 {logic_qubit: (x, y)}
    
    示例:
        >>> from qiskit.converters import circuit_to_dag
        >>> dag = circuit_to_dag(circuit)
        >>> arch_graph = generate_grid_with_Rb(5, 5, 2.0)
        >>> mapping = mcts_initial_mapping(dag, arch_graph, 5)
    """
    engine = MCTSEngine(dag, architecture_graph, grid_size, interaction_radius)
    return engine.search(max_iterations)
