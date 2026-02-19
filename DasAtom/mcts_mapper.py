"""
MCTS 初始映射模块 (mcts_mapper.py)

该模块实现基于蒙特卡洛树搜索 (MCTS) 的初始量子比特映射生成器。
核心思想：用 MCTS 的"构造式搜索"替代 VF2 子图匹配，
通过向前模拟多层电路的物理保真度来寻找全局最优的初始原子布局。

日期：2026-02-09
"""

import math
import random
import networkx as nx
import numpy as np
from copy import deepcopy
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
        return set(self.mapping.values())


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
        
        # 提取所有涉及的逻辑比特
        self.all_qubits = self._extract_all_qubits()
        
        # 预处理：构建第一层的连接关系（用于几何剪枝）
        self.layer0_edges = set()
        if self.layers:
            for gate in self.layers[0]:
                self.layer0_edges.add((gate[0], gate[1]))
                self.layer0_edges.add((gate[1], gate[0]))  # 无向
        
        # 预处理：构建前5层的连接关系（用于比特选择策略）
        # 让 _select_next_qubit 能"顺藤摸瓜"，优先放将来要交互的比特
        self.lookahead_edges = set()
        for layer in self.layers[:5]:
            for gate in layer:
                self.lookahead_edges.add((gate[0], gate[1]))
                self.lookahead_edges.add((gate[1], gate[0]))
        
        # 预处理：计算每个逻辑比特在前5层中的连接度（度数越高 = Hub 比特）
        # 用于中心性启发式：Hub 比特应优先放在网格中心区域
        self.qubit_degree = {}
        for q in self.all_qubits:
            self.qubit_degree[q] = sum(1 for (a, b) in self.lookahead_edges if a == q)
        
        # 预计算物理网格的几何中心坐标
        all_nodes = list(architecture_graph.nodes())
        self.grid_center = (
            sum(n[0] for n in all_nodes) / len(all_nodes),
            sum(n[1] for n in all_nodes) / len(all_nodes)
        )
        
        # 物理节点坐标字典 (已经是 (x, y) 格式，直接使用)
        self.node_coords = self._build_node_coords()
    
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
    
    def _euclidean_distance(self, pos1: tuple, pos2: tuple) -> float:
        """计算两个物理位置之间的欧几里得距离。"""
        x1, y1 = pos1
        x2, y2 = pos2
        return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
    
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
        for mapped_qubit, mapped_pos in current_mapping.items():
            # 检查是否在 Layer 0 有连接
            if (qubit, mapped_qubit) in self.layer0_edges:
                # 计算物理距离
                distance = self._euclidean_distance(position, mapped_pos)
                if distance > self.Rb:
                    return False  # 违反距离约束
        return True
    
    def _select_next_qubit(self, unmapped_qubits: list, 
                           current_mapping: dict) -> int:
        """
        选择下一个要放置的逻辑比特。
        
        策略：优先选择与已放置比特在前5层有门连接的比特，
        让 MCTS 像"顺藤摸瓜"一样，把将来要交互的比特提前聚拢。
        
        Args:
            unmapped_qubits: 未放置的比特列表
            current_mapping: 当前映射
        
        Returns:
            选中的逻辑比特 ID
        """
        if not current_mapping:
            # 没有已放置的比特，选择第一层第一个门的第一个比特
            if self.layers and self.layers[0]:
                return self.layers[0][0][0]
            return unmapped_qubits[0]
        
        # 第一优先级：寻找与已放置比特在 Layer 0 有连接的（剪枝价值最高）
        mapped_set = set(current_mapping.keys())
        for qubit in unmapped_qubits:
            for mapped_qubit in mapped_set:
                if (qubit, mapped_qubit) in self.layer0_edges:
                    return qubit
        
        # 第二优先级：寻找与已放置比特在前5层有连接的（前瞻聚拢）
        for qubit in unmapped_qubits:
            for mapped_qubit in mapped_set:
                if (qubit, mapped_qubit) in self.lookahead_edges:
                    return qubit
        
        # 都没有连接关系，返回第一个未放置的
        return unmapped_qubits[0]
    
    def _rank_positions(self, qubit: int, available: set,
                        current_mapping: dict) -> list:
        """
        中心性启发式：对空闲物理位置进行加权随机排序。
        
        策略分两种情况：
        - 情况1（无邻居）：Hub 比特优先尝试网格中心区域
        - 情况2（有邻居）：优先尝试已放置的连接比特附近
        
        使用加权随机采样保留多样性，避免搜索过于贪心。
        
        Args:
            qubit: 当前要放置的逻辑比特
            available: 空闲物理位置集合
            current_mapping: 当前已有的映射
        
        Returns:
            排序后的物理位置列表（高权重位置更容易排到前面）
        """
        available_list = list(available)
        
        # 查找该比特在前5层中已放置的邻居位置
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
                # 计算到所有已放置邻居的平均距离，距离越近权重越高
                avg_dist = sum(
                    self._euclidean_distance(pos, np) for np in neighbor_positions
                ) / len(neighbor_positions)
                w = 1.0 / (1.0 + avg_dist)
            else:
                # === 情况1：无邻居 -> Hub 比特偏好网格中心 ===
                # 连接度越高的比特，中心吸引力越强
                dist_to_center = self._euclidean_distance(pos, self.grid_center)
                degree_ratio = self.qubit_degree.get(qubit, 1) / max_degree
                # 度数作为"中心引力"的放大器：度越高，距离惩罚越大
                w = 1.0 / (1.0 + dist_to_center * degree_ratio)
            weights.append(w)
        
        # 加权随机排序：Efraimidis-Spirakis 加权无放回抽样算法
        # 给每个位置生成 key = random()^(1/weight)，按 key 降序排列
        # 权重高的位置更容易排到前面，但仍保留随机性
        scored = []
        for pos, w in zip(available_list, weights):
            r = random.random()
            if r == 0:
                r = 1e-10  # 防止 log(0)
            key = r ** (1.0 / max(w, 1e-10))
            scored.append((key, pos))
        
        scored.sort(key=lambda x: x[0], reverse=True)
        return [pos for _, pos in scored]
    
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
        available = set(self.arch_graph.nodes()) - occupied
        
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
        available = list(set(self.arch_graph.nodes()) - occupied)
        random.shuffle(available)
        
        for qubit in unmapped:
            if available:
                # 尝试找一个满足 Layer 0 约束的位置
                placed = False
                for i, pos in enumerate(available):
                    if self._is_valid_placement(qubit, pos, mapping):
                        mapping[qubit] = pos
                        available.pop(i)
                        placed = True
                        break
                
                # 如果找不到满足约束的，随便选一个
                if not placed and available:
                    mapping[qubit] = available.pop()
        
        return mapping
    
    def _estimate_fidelity(self, mapping: dict) -> float:
        """
        估算给定映射的保真度分数。
        
        模拟逻辑：
        1. 向前看 5 层门
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
        sim_mapping = deepcopy(mapping)
        
        # 模拟参数
        layers_to_simulate = min(5, len(self.layers))
        layer_decay = 0.8  # 层权重衰减因子：Layer0=1.0, Layer1=0.8, Layer2=0.64...
        
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
        
        return fidelity
    
    def _simulate(self, node: MCTSNode) -> float:
        """
        从给定节点进行模拟，返回保真度评分。
        
        Args:
            node: 起始节点
        
        Returns:
            模拟得到的保真度分数
        """
        # 如果映射不完整，先贪心补全
        if not node.is_terminal():
            complete_mapping = self._greedy_complete_mapping(
                node.mapping, node.unmapped_qubits
            )
        else:
            complete_mapping = node.mapping
        
        # 估算保真度
        return self._estimate_fidelity(complete_mapping)
    
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
        
        Args:
            max_iterations: 最大迭代次数
        
        Returns:
            最优映射字典 {logic_qubit: (x, y)}
        """
        # 创建根节点
        root = MCTSNode(
            mapping={},
            unmapped_qubits=list(self.all_qubits)
        )
        
        for iteration in range(max_iterations):
            node = root
            
            # === Selection ===
            # 沿着树向下选择，直到找到可扩展的节点或叶节点
            while node.children and not node.is_terminal():
                available = set(self.arch_graph.nodes()) - node.get_occupied_positions()
                if node.is_fully_expanded(available):
                    node = node.best_child()
                else:
                    break
            
            # === Expansion ===
            if not node.is_terminal():
                child = self._expand(node)
                if child:
                    node = child
            
            # === Simulation ===
            reward = self._simulate(node)
            
            # === Backpropagation ===
            self._backpropagate(node, reward)
        
        # 返回访问次数最多的路径对应的映射
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
        
        return node.mapping


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
