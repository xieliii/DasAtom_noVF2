import rustworkx as rx
import networkx as nx
import numpy as np
import random
import math
import os
import re
import json

from qiskit import qasm2, transpile, QuantumCircuit, QuantumRegister
from qiskit.converters import dag_to_circuit, circuit_to_dag
from qiskit.circuit import library
import copy
from copy import deepcopy

custom = [
    qasm2.CustomInstruction("p",num_params= 1, num_qubits=1 ,constructor=library.PhaseGate, builtin=True),
]

_RB_NEIGHBOR_CACHE = {}
_EUCLIDEAN_CACHE = {}


def _get_cached_rb_neighbors(all_nodes, rb, eps=1e-9):
    """
    Cache physical-neighbor candidate sets by (Rb, node set) to avoid
    recomputing the same geometry in repeated local-repair calls.
    """
    nodes = tuple(sorted(tuple(node) for node in all_nodes))
    key = (float(rb), nodes)
    cached = _RB_NEIGHBOR_CACHE.get(key)
    if cached is not None:
        return cached

    if len(_RB_NEIGHBOR_CACHE) > 128:
        _RB_NEIGHBOR_CACHE.clear()

    rb2 = (float(rb) * float(rb)) + eps
    neighbors = {}
    for ax, ay in nodes:
        anchor = (ax, ay)
        cand = []
        for bx, by in nodes:
            dx = ax - bx
            dy = ay - by
            if (dx * dx + dy * dy) <= rb2:
                cand.append((bx, by))
        neighbors[anchor] = cand

    _RB_NEIGHBOR_CACHE[key] = neighbors
    return neighbors


def CreateCircuitFromQASM(file, path):
    filePath = os.path.join(path,file)
    # print(filePath)
    cir = qasm2.load(filePath, custom_instructions=custom)
    gates_in_circuit = {op[0].name for op in cir.data}
    allowed_basis_gates = {'cz', 'h', 's', 't', 'rx', 'ry', 'rz'}
    # Check if there are any disallowed gates by checking the difference between sets
    if gates_in_circuit - allowed_basis_gates:
        cir = transpile(cir, basis_gates=list(allowed_basis_gates),optimization_level=0)
    return cir


def get_rx_one_mapping(graph_max, G):
    sub_graph = rx.networkx_converter(graph_max)
    big_graph = rx.networkx_converter(G)
    nx_edge_s = list(graph_max.edges())
    rx_edge_s = list(sub_graph.edge_list())
    rx_nx_s = dict()
    for i in range(len(rx_edge_s)):
        if rx_edge_s[i][0] not in rx_nx_s:
            rx_nx_s[rx_edge_s[i][0]] = nx_edge_s[i][0]
        if rx_edge_s[i][1] not in rx_nx_s:
            rx_nx_s[rx_edge_s[i][1]] = nx_edge_s[i][1]
    nx_edge_G = list(G.edges())
    rx_edge_G = list(big_graph.edge_list())
    rx_nx_G = dict()
    for i in range(len(rx_edge_G)):
        if rx_edge_G[i][0] not in rx_nx_G:
            rx_nx_G[rx_edge_G[i][0]] = nx_edge_G[i][0]
        if rx_edge_G[i][1] not in rx_nx_G:
            rx_nx_G[rx_edge_G[i][1]] = nx_edge_G[i][1]
    vf2 = rx.vf2_mapping(big_graph, sub_graph, subgraph=True, induced = False)
    item = next(vf2)
    reverse_mapping = {rx_nx_s[value]: rx_nx_G[key] for  key, value in item.items()}
    return reverse_mapping

def rx_is_subgraph_iso(G, subG):
    Grx = rx.networkx_converter(G)
    subGrx = rx.networkx_converter(subG)
    gm = rx.is_subgraph_isomorphic(Grx, subGrx, induced = False)   
    return gm

def get_layer_gates(dag):
    gate_layer_list = []
    for item in dag.layers():
        gate_layer = []
        graph_one = nx.Graph()
        for gate in item['partition']:
            c0 = gate[0]._index
            c1 = gate[1]._index
            gate_layer.append([c0, c1])
        gate_layer_list.append(gate_layer)
    return gate_layer_list

def partition_from_DAG(dag, coupling_graph):
    gate_layer_list = get_layer_gates(dag)
    num_of_gate = 0
    last_index = 0
    partition_gates = []
    for i in range(len(gate_layer_list)):
        #print(i)
        #print(last_index)
        merge_gates = sum(gate_layer_list[last_index:i+1], [])
        tmp_graph = nx.Graph()
        tmp_graph.add_edges_from(merge_gates)
        connected_components = list(nx.connected_components(tmp_graph))
        isIso = True
        for idx, component in enumerate(connected_components, 1):
            subgraph = tmp_graph.subgraph(component)
            if len(subgraph.edges()) == nx.diameter(subgraph): #path-tolopology, must sub_iso
                continue
            # print(subgraph.edges())
            if not rx_is_subgraph_iso(coupling_graph, subgraph):
                isIso = False
                break
        if isIso:
            if i == len(gate_layer_list) - 1:
                merge_gates = sum(gate_layer_list[last_index: i+1], [])
                partition_gates.append(merge_gates)
            continue
        else:
            merge_gates = sum(gate_layer_list[last_index: i], [])
            partition_gates.append(merge_gates)
            last_index = i
            if i == len(gate_layer_list) - 1:
                merge_gates = sum(gate_layer_list[last_index: i+1], [])
                partition_gates.append(merge_gates)

    return partition_gates


def _can_embed_degree_budget(gate_graph, coupling_degree_seq):
    """
    检查门图的所有高度数节点能否在耦合图中找到足够的"宿主"位置。
    
    思路：门图度数 ≥ k 的节点必须映射到耦合图度数 ≥ k 的位置。
    我们统计每个度数等级的"需求"和"供给"，如果在任何等级需求>供给就拒绝。
    
    这比简单的度数序列对比更严格：它考虑了多个高度数节点的竞争。
    
    复杂度 O(V + max_degree)
    """
    gate_degs = sorted([d for _, d in gate_graph.degree()], reverse=True)
    if not gate_degs:
        return True
    
    max_possible_deg = max(gate_degs[0], coupling_degree_seq[0] if coupling_degree_seq else 0)
    
    # 对每个度数阈值 k，统计门图中度数>=k 的节点数（demand）
    # 和耦合图中度数>=k 的节点数（supply）
    for k in range(max_possible_deg, 0, -1):
        demand = sum(1 for d in gate_degs if d >= k)
        supply = sum(1 for d in coupling_degree_seq if d >= k)
        if demand > supply:
            return False
    
    return True


def layer_only_partition(dag, grid_capacity, coupling_graph=None):
    """
    容量 + 拓扑约束贪心合并：不用 VF2，用轻量级拓扑检查。
    
    合并规则（同时满足才合并）：
    1. 合并后 qubit 总数 ≤ grid_capacity
    2. 合并后门图最大度数 ≤ 实际可放置阈值（coupling_max_degree 的一半）
    3. 度数预算检查：全图度数需求不超过耦合图供给
    
    复杂度 O(n·k)，零 VF2 调用。
    """
    all_layers = get_layer_gates(dag)
    
    if not all_layers:
        return []
    
    # 耦合图度数信息
    if coupling_graph is not None:
        coupling_degree_seq = sorted(
            [d for _, d in coupling_graph.degree()],
            reverse=True
        )
        coupling_max_degree = coupling_degree_seq[0]
    else:
        coupling_degree_seq = [4] * grid_capacity
        coupling_max_degree = 4
    
    # 实际可放置阈值：放宽到接近耦合图真实上限，减少过度切分。
    # 严格正确性由后续嵌入+必要分裂兜底保证。
    practical_degree_cap = max(coupling_max_degree, 4)
    
    def _can_embed_degree_budget_from_degrees(degrees):
        gate_degs = sorted([d for d in degrees if d > 0], reverse=True)
        if not gate_degs:
            return True
        max_possible_deg = max(gate_degs[0], coupling_degree_seq[0] if coupling_degree_seq else 0)
        for k in range(max_possible_deg, 0, -1):
            demand = sum(1 for d in gate_degs if d >= k)
            supply = sum(1 for d in coupling_degree_seq if d >= k)
            if demand > supply:
                return False
        return True

    def _check_embeddability_from_stats(node_count, gate_count, degrees):
        """检查门图是否可嵌入：度数上限 + 度数预算双重检查。"""
        if gate_count <= 0:
            return True
        if node_count > len(coupling_degree_seq):
            return False
        if max(degrees, default=0) > practical_degree_cap:
            return False
        # 中大分区放宽预算检查，减少过度切分。
        # 严格正确性由后续嵌入阶段（必要时继续拆分）保证。
        if gate_count >= 24:
            return True
        return _can_embed_degree_budget_from_degrees(degrees)

    def _is_overdense_large_from_stats(gate_count, node_count, edge_count):
        """
        防止超大稠密分区：这类分区在后续嵌入阶段常被反复拆分，
        先在分区阶段提前截断可减少重试开销。
        """
        if gate_count < 96:
            return False
        if node_count < min(grid_capacity, 12) or node_count == 0:
            return False
        avg_deg = (2.0 * edge_count) / node_count
        return avg_deg >= 4.8

    def _build_partition_state(gates):
        qubits = set()
        adj = {}
        degree = {}
        edge_count = 0
        for u, v in gates:
            qubits.add(u)
            qubits.add(v)
            adj.setdefault(u, set())
            adj.setdefault(v, set())
            degree.setdefault(u, 0)
            degree.setdefault(v, 0)
            if v not in adj[u]:
                adj[u].add(v)
                adj[v].add(u)
                degree[u] += 1
                degree[v] += 1
                edge_count += 1
        return qubits, adj, degree, edge_count

    partition_gates = []
    current_gates = list(all_layers[0])
    current_qubits, current_adj, current_degree, current_edge_count = _build_partition_state(current_gates)
    
    for i in range(1, len(all_layers)):
        next_layer = list(all_layers[i])
        next_qubits = set()
        trial_degree = dict(current_degree)
        new_edges = []
        trial_edge_count = current_edge_count
        for u, v in next_layer:
            next_qubits.add(u)
            next_qubits.add(v)
            trial_degree.setdefault(u, current_degree.get(u, 0))
            trial_degree.setdefault(v, current_degree.get(v, 0))
            neighbors_u = current_adj.get(u)
            if neighbors_u is not None and v in neighbors_u:
                continue
            new_edges.append((u, v))
            trial_degree[u] += 1
            trial_degree[v] += 1
            trial_edge_count += 1
        
        merged_qubits = current_qubits | next_qubits
        
        # 条件 1：qubit 数不超容量
        if len(merged_qubits) > grid_capacity:
            partition_gates.append(current_gates)
            current_gates = next_layer
            current_qubits, current_adj, current_degree, current_edge_count = _build_partition_state(current_gates)
            continue
        
        trial_gates = current_gates + next_layer
        # 条件 2：合并后的门拓扑可嵌入耦合图
        if not _check_embeddability_from_stats(len(merged_qubits), len(trial_gates), trial_degree.values()):
            partition_gates.append(current_gates)
            current_gates = next_layer
            current_qubits, current_adj, current_degree, current_edge_count = _build_partition_state(current_gates)
            continue

        # 条件 3：避免形成超大稠密分区（会拖慢后续 noVF2 嵌入）
        if _is_overdense_large_from_stats(len(trial_gates), len(merged_qubits), trial_edge_count):
            partition_gates.append(current_gates)
            current_gates = next_layer
            current_qubits, current_adj, current_degree, current_edge_count = _build_partition_state(current_gates)
            continue

        # 两个条件都满足 → 合并
        current_gates = trial_gates
        current_qubits = merged_qubits
        current_degree = trial_degree
        current_edge_count = trial_edge_count
        for u, v in new_edges:
            current_adj.setdefault(u, set()).add(v)
            current_adj.setdefault(v, set()).add(u)
    
    partition_gates.append(current_gates)
    return partition_gates


def fast_partition(dag, grid_capacity, coupling_graph=None):
    """
    快速分层：与 partition_from_DAG 保证相同的 Rb 物理正确性，
    但通过快速短路判断跳过大部分 VF2 调用。
    
    VF2 短路规则：
      - 路径拓扑: 一定可嵌入 → O(1) 通过（与原版一致）
      - 树结构 (edges == nodes-1): 只要 max_degree ≤ 耦合图最大度数 → O(1) 通过
      - 小分量 (≤ 3 nodes): 一定可嵌入 → O(1) 通过
      - 其他 (含环 + 大): 回退到 VF2 子图匹配（保证正确性）
    """
    gate_layer_list = get_layer_gates(dag)
    
    if not gate_layer_list:
        return []
    
    # 耦合图最大度数
    if coupling_graph is not None:
        coupling_max_degree = max(dict(coupling_graph.degree()).values())
    else:
        coupling_max_degree = 12
    
    last_index = 0
    partition_gates = []
    
    for i in range(len(gate_layer_list)):
        merge_gates = sum(gate_layer_list[last_index:i+1], [])
        tmp_graph = nx.Graph()
        tmp_graph.add_edges_from(merge_gates)
        connected_components = list(nx.connected_components(tmp_graph))
        
        can_merge = True
        for component in connected_components:
            subgraph = tmp_graph.subgraph(component)
            num_nodes = len(subgraph.nodes())
            num_edges = len(subgraph.edges())
            
            if num_edges == 0:
                continue
            
            # 短路 1: 路径拓扑 — 与原版完全一致的判断
            try:
                diam = nx.diameter(subgraph)
                if num_edges == diam:
                    continue  # 路径图，一定可嵌入
            except nx.NetworkXError:
                pass
            
            # 短路 2: 小分量 (≤ 3 nodes) — 任何连通图都可嵌入 Rb=2 网格
            if num_nodes <= 3:
                continue
            
            # 短路 3: 树结构 — 无环，只要度数合法就一定可嵌入
            if num_edges == num_nodes - 1:  # 树的充要条件
                max_deg = max(dict(subgraph.degree()).values())
                if max_deg <= coupling_max_degree:
                    continue
            
            # 以上都不满足 → 含环的非平凡子图，必须用 VF2 精确检查
            if not rx_is_subgraph_iso(coupling_graph, subgraph):
                can_merge = False
                break
        
        if can_merge:
            if i == len(gate_layer_list) - 1:
                merge_gates = sum(gate_layer_list[last_index:i+1], [])
                partition_gates.append(merge_gates)
            continue
        else:
            merge_gates = sum(gate_layer_list[last_index:i], [])
            partition_gates.append(merge_gates)
            last_index = i
            if i == len(gate_layer_list) - 1:
                merge_gates = sum(gate_layer_list[last_index:i+1], [])
                partition_gates.append(merge_gates)
    
    # 边界情况
    if not partition_gates and gate_layer_list:
        partition_gates.append(sum(gate_layer_list, []))
    
    return partition_gates

def get_2q_gates_list(circ):
    gate_2q_list = []
    instruction = circ.data
    for ins in instruction:
        if ins.operation.num_qubits == 2:
            gate_2q_list.append((ins.qubits[0]._index, ins.qubits[1]._index))
    return gate_2q_list


def get_qubits_num(gate_2q_list):
    num = max(max(gate) for gate in gate_2q_list)
    num += 1
    return num

def gates_list_to_QC(gate_list):  #default all 2-q gates circuit
    Lqubit = get_qubits_num(gate_list)
    circ = QuantumCircuit(Lqubit)
    # issue: cz
    for two_qubit_gate in gate_list:
        circ.cz(two_qubit_gate[0], two_qubit_gate[1])
    
    dag = circuit_to_dag(circ)
    return circ, dag


def euclidean_distance(node1, node2):
    p1 = node1 if isinstance(node1, tuple) else tuple(node1)
    p2 = node2 if isinstance(node2, tuple) else tuple(node2)
    key = (p1, p2) if p1 <= p2 else (p2, p1)
    cached = _EUCLIDEAN_CACHE.get(key)
    if cached is not None:
        return cached
    if len(_EUCLIDEAN_CACHE) > 500000:
        _EUCLIDEAN_CACHE.clear()
    x1, y1 = p1
    x2, y2 = p2
    dist = math.hypot(x2 - x1, y2 - y1)
    _EUCLIDEAN_CACHE[key] = dist
    return dist

def generate_grid_with_Rb(n, m, Rb):
    G = nx.grid_2d_graph(n, m)  # 生成n*m的网格图
    for node1 in G.nodes():
        for node2 in G.nodes():
            if node1 != node2:
                distance = euclidean_distance(node1, node2)
                if distance <= Rb:
                    G.add_edge(node1, node2)

    return G

def extend_graph(coupling_graph, arch_size, Rb):
    coupling_graph = generate_grid_with_Rb(arch_size+1, arch_size+1, Rb)
    return coupling_graph


def map2list(mapping, num_q):
    map_list = [-1] * num_q
    for key, value in mapping.items():
        map_list[key] = value

    return map_list

def complete_mapping(i, embeddings, indices, coupling_graph):
    cur_map = embeddings[i]
    # 找出当前步骤中所有未被占用的物理节点
    unoccupied = [value for value in coupling_graph.nodes() if value not in cur_map]
    for index in indices:
        flag = False
        
        # 策略 1: 优先保持不动 (Stayput)
        # 如果不是第一步，且该比特在上一步的位置现在是空的，那就继续呆在那，减少移动
        if i != 0:  #If pre_map is not empty
            if embeddings[i-1][index] in unoccupied:
                cur_map[index] = embeddings[i-1][index]
                flag = True
                unoccupied.remove(cur_map[index])
        
        # 策略 2: 前瞻 (Lookahead)
        # 如果策略1失败，查看该比特在未来的步骤中是否已经有了确定的位置
        # 如果未来的位置现在是空的，就直接去那等着，为未来做准备
        if i != len(embeddings) - 1 and flag == False:
            for j in range(i+1, len(embeddings)):
                if embeddings[j][index] != -1 and embeddings[j][index] in unoccupied:
                    cur_map[index] = embeddings[j][index]
                    unoccupied.remove(cur_map[index])
                    flag = True
                    break
        
        # 策略 3: 就近原则 (Nearest Neighbor)
        # 如果既不能不动，也不能去未来的家，那就找一个离"相关节点"最近的空位
        if flag == False:
            if i != 0:
                # 情况A: 有过去的位置。找离上一步位置最近的空位
                source = embeddings[i-1][index]
                node_of_shortest = dict()
                for node in unoccupied:
                    distance = nx.shortest_path_length(coupling_graph, source=source, target=node)
                    node_of_shortest[node] = distance
                min_node = min(node_of_shortest, key=node_of_shortest.get)
                cur_map[index] = min_node
                unoccupied.remove(min_node)
                flag = True
            elif i != len(embeddings) - 1:
                # 情况B: 没有过去(第一步)，但有未来。找离未来位置最近的空位
                for j in range(i+1, len(embeddings)):
                    if embeddings[j][index] != -1:
                        source = embeddings[j][index]
                        node_of_shortest = dict()
                        for node in unoccupied:
                            distance = nx.shortest_path_length(coupling_graph, source=source, target=node)
                            node_of_shortest[node] = distance
                        min_node = min(node_of_shortest, key=node_of_shortest.get)
                        cur_map[index] = min_node
                        unoccupied.remove(min_node)
                        flag = True
                        break
        
        # 策略 4: 随机 (Random Fallback)
        # 实在没办法了，随机选一个空位
        if flag == False:
            min_node = random.choice(unoccupied)
            cur_map[index] = min_node
            unoccupied.remove(min_node)
    return cur_map


def loc_to_qasm(n: int, qubit: int, loc: tuple[int, int]) -> str:
    """
    Converts a qubit location to a QASM formatted string.

    Parameters:
    n (int): The number of qubits in the quantum register.
    qubit (int): The specific qubit index.
    loc (tuple[int, int]): The location of the qubit as a tuple of two integers.

    Returns:
    str: The QASM formatted string representing the qubit location.

    Raises:
    ValueError: If the loc tuple does not have exactly two elements.
    """
    if len(loc) != 2:
        raise ValueError("Invalid loc, it must be a tuple of length 2")
    return f"Qubit(QuantumRegister({n}, 'q'), {qubit})\n({loc[0]}, {loc[1]})"

def map_to_qasm(n: int, map: list[tuple[int, int]], filename: str) -> None:
    """
    Converts a list of qubit locations to QASM format and saves it to a file.

    Parameters:
    n (int): The number of qubits in the quantum register.
    map (list[tuple[int, int]]): A list of tuples representing the locations of the qubits.
    filename (str): The name of the file to save the QASM formatted strings.

    Returns:
    None
    """
    with open(filename, 'w') as f:
        for i in range(n):
            f.write(loc_to_qasm(n, i, map[i]) + '\n')

def gate_in_layer(gate_list:list[list[int]])->list[map]:
    res = []
    for i in range(len(gate_list),-1):
        assert len(gate_list[i]) == 2
        res.append({'id':i,'q0':gate_list[i][0],'q1':gate_list[i][1]})
    return res

def check_available(graph, coupling_graph, mapping):

    for eg0, eg1 in graph.edges():
        if (mapping[eg0], mapping[eg1]) not in coupling_graph.edges():
            return False
    return True

def check_intersect(gate1, gate2, coupling_graph, mapping):
    rg1 = 1/2 * (euclidean_distance(mapping[gate1[0]], mapping[gate1[1]]))
    rg2 = 1/2 * (euclidean_distance(mapping[gate2[0]], mapping[gate2[1]]))
    dis = rg1 + rg2
    if euclidean_distance(mapping[gate1[0]], mapping[gate2[0]]) >= dis and \
        euclidean_distance(mapping[gate1[0]], mapping[gate2[1]]) >= dis and \
        euclidean_distance(mapping[gate1[1]], mapping[gate2[0]]) >= dis and \
        euclidean_distance(mapping[gate1[1]], mapping[gate2[1]]) >= dis:
        return True
    else:
        return False

def check_intersect_ver2(gate1, gate2, coupling_graph, mapping, r_re):
    if euclidean_distance(mapping[gate1[0]], mapping[gate2[0]]) > r_re and \
        euclidean_distance(mapping[gate1[0]], mapping[gate2[1]]) > r_re and \
        euclidean_distance(mapping[gate1[1]], mapping[gate2[0]]) > r_re and \
        euclidean_distance(mapping[gate1[1]], mapping[gate2[1]]) > r_re:
        return True
    else:
        return False

def get_parallel_gates(gates, coupling_graph, mapping, r_re):
    gates_list = []
    _, dag = gates_list_to_QC(gates)
    gate_layer_list = get_layer_gates(dag)

    for items in gate_layer_list:
        gates_copy = deepcopy(items)
        while(len(gates_copy) != 0):
            parallel_gates = []
            parallel_gates.append(gates_copy[0])
            for i in range(1, len(gates_copy)):
                flag = True
                for gate in parallel_gates:
                    if check_intersect_ver2(gate, gates_copy[i], coupling_graph, mapping, r_re):
                        continue
                    else:
                        flag = False
                        break
                if flag:
                    parallel_gates.append(gates_copy[i])

            for gate in parallel_gates:
                gates_copy.remove(gate)
            gates_list.append(parallel_gates)
            #print("parl:",parallel_gates)
    return gates_list

'''def set_parameters(default):
    para = {}
    if default:
        para['T_cz'] = 0.2  #us
        para['T_eff'] = 1.5e6 #us
        para['T_trans'] = 20 # us
        para['AOD_width'] = 3 #um
        para['AOD_height'] = 3 #um
        para['Move_speed'] = 0.55 #um/us
        para['F_cz'] = 0.995 

    return para'''

'''
T_cz (0.2 us): 做一个双比特门需要的时间。
T_eff (1.5e6 us): 退相干时间（Coherence Time）。
这是原子的寿命。如果在这个时间内不操作完，量子信息就丢失了。
1.5秒看似很长，但对于量子计算来说还是不够。
T_trans (20 us): 抓起或放下原子需要的时间（Shuttling overhead）。
Move_speed (0.55 um/us): 原子移动的速度。非常慢！
AOD_width/height (3 um): 网格中两个相邻点之间的物理距离。
F_cz (0.995): 做一次 CZ 门原本的保真度（99.5%）
'''
def set_parameters(T_cz = 0.2, T_eff = 1.5e6, T_trans=20, AOD_width=3,AOD_height=3,Move_speed=0.55,F_cz=0.995, F_trans = 0.9981):
    para = {}
    para['T_cz'] = T_cz  #us
    para['T_eff'] = T_eff #us
    para['T_trans'] = T_trans # us
    para['AOD_width'] = AOD_width #um
    para['AOD_height'] = AOD_height #um
    para['Move_speed'] = Move_speed #um/us
    para['F_cz'] = F_cz
    para['F_trans'] = F_trans

    return para

'''def compute_fidelity(parallel_gates, all_movements, num_q, gate_num):
    para = set_parameters(True)
    t_total = 0
    t_total += (len(parallel_gates) * para['T_cz']) # cz execution time, parallel
    t_move = 0
    for move in all_movements:
        t_total += (4 * para['T_trans']) # pick/drop/pick/drop
        t_move += (4 * para['T_trans'])
        max_dis = 0
        for each_move in move:
            x1, y1 = each_move[1][0],each_move[1][1]
            x2, y2 = each_move[2][0],each_move[2][1]
            dis = (abs(x2-x1)*para['AOD_width'])**2 + (abs(y2-y1)*para['AOD_height'])**2
            if dis > max_dis:
                max_dis = dis
        max_dis = math.sqrt(max_dis)
        t_total += (max_dis/para['Move_speed'])
        t_move += (max_dis/para['Move_speed'])

    t_idle = num_q * t_total - gate_num * para['T_cz']
    Fidelity = math.exp(-t_idle/para['T_eff']) * (para['F_cz']**gate_num)
    move_fidelity = math.exp(-t_move/para['T_eff'])
    return t_idle, Fidelity, move_fidelity'''

def compute_fidelity(parallel_gates, all_movements, num_q, gate_num, para=None):
    if para is None:
        para = set_parameters()
    t_total = 0
    t_total += (len(parallel_gates) * para['T_cz']) # cz execution time, parallel
    t_move = 0
    num_trans = 0
    num_move = 0
    all_move_dis = 0
    for move in all_movements:
        t_total += (4 * para['T_trans']) # pick/drop/pick/drop
        t_move += (4 * para['T_trans'])
        num_trans += 4
        max_dis = 0
        for each_move in move:
            num_move += 1
            x1, y1 = each_move[1][0],each_move[1][1]
            x2, y2 = each_move[2][0],each_move[2][1]
            dis = (abs(x2-x1)*para['AOD_width'])**2 + (abs(y2-y1)*para['AOD_height'])**2
            if dis > max_dis:
                max_dis = dis
        max_dis = math.sqrt(max_dis)
        all_move_dis += max_dis
        t_total += (max_dis/para['Move_speed'])
        t_move += (max_dis/para['Move_speed'])

    t_idle = num_q * t_total - gate_num * para['T_cz']
    Fidelity = math.exp(-t_idle/para['T_eff']) * (para['F_cz']**gate_num) * (para['F_trans'] ** num_trans)
    move_fidelity = math.exp(-t_move/para['T_eff'])
    return t_idle, Fidelity, move_fidelity, t_total, num_trans, num_move, all_move_dis

def _gate_violates_rb(gate, embedding, rb):
    u, v = gate[0], gate[1]
    pu = embedding[u]
    pv = embedding[v]
    if pu == -1 or pv == -1:
        return True
    return euclidean_distance(tuple(pu), tuple(pv)) > rb + 1e-9


def _split_valid_violating_gates(gates, embedding, rb):
    valid = []
    violating = []
    for gate in gates:
        if _gate_violates_rb(gate, embedding, rb):
            violating.append(gate)
        else:
            valid.append(gate)
    return violating, valid


def _build_strict_single_gate_embedding(prev_mapping, gate, all_nodes, rb, num_q):
    u, v = gate[0], gate[1]
    all_nodes = [tuple(node) for node in all_nodes]

    occupied_by_others = set()
    for idx, pos in enumerate(prev_mapping):
        if idx in (u, v) or pos == -1:
            continue
        occupied_by_others.add(tuple(pos))

    candidate_nodes = [node for node in all_nodes if node not in occupied_by_others]
    prev_u = None if prev_mapping[u] == -1 else tuple(prev_mapping[u])
    prev_v = None if prev_mapping[v] == -1 else tuple(prev_mapping[v])

    best_pair = None
    best_cost = float("inf")
    for node_u in candidate_nodes:
        for node_v in candidate_nodes:
            if node_u == node_v:
                continue
            if euclidean_distance(node_u, node_v) > rb + 1e-9:
                continue
            cost = 0.0
            if prev_u is not None:
                cost += euclidean_distance(prev_u, node_u)
            if prev_v is not None:
                cost += euclidean_distance(prev_v, node_v)
            if cost < best_cost:
                best_cost = cost
                best_pair = (node_u, node_v)

    if best_pair is None:
        raise RuntimeError(f"Cannot build a valid embedding for single gate {gate} under Rb={rb}.")

    next_mapping = [(-1 if pos == -1 else tuple(pos)) for pos in prev_mapping]
    next_mapping[u], next_mapping[v] = best_pair

    used = set()
    for idx, pos in enumerate(next_mapping):
        if pos == -1:
            continue
        if pos in used:
            next_mapping[idx] = -1
            continue
        used.add(pos)

    available = [node for node in all_nodes if node not in used]
    for idx in range(num_q):
        if next_mapping[idx] != -1:
            continue
        if not available:
            raise RuntimeError("No available physical node when completing strict embedding.")
        prev = None if prev_mapping[idx] == -1 else tuple(prev_mapping[idx])
        if prev is None:
            next_mapping[idx] = available.pop(0)
        else:
            nearest_idx = min(range(len(available)), key=lambda j: euclidean_distance(prev, available[j]))
            next_mapping[idx] = available.pop(nearest_idx)

    return next_mapping


def _total_violation_metrics(gates, mapping, rb):
    violations = 0
    excess = 0.0
    for gate in gates:
        u, v = gate[0], gate[1]
        d = euclidean_distance(tuple(mapping[u]), tuple(mapping[v]))
        if d > rb + 1e-9:
            violations += 1
            excess += (d - rb)
    return violations, excess


def _collect_active_qubits(*gate_groups):
    active = set()
    for group in gate_groups:
        if not group:
            continue
        for item in group:
            if not item:
                continue
            if isinstance(item[0], int):
                active.add(item[0])
                active.add(item[1])
            else:
                for gate in item:
                    active.add(gate[0])
                    active.add(gate[1])
    return active


def _minimize_idle_movement(mapping, prev_mapping, all_nodes, active_qubits):
    """
    Keep non-active qubits close to previous positions while preserving injective mapping.
    """
    all_nodes = [tuple(node) for node in all_nodes]
    next_mapping = [tuple(pos) for pos in mapping]
    used = set()
    for q in active_qubits:
        used.add(tuple(next_mapping[q]))

    pending = []
    for q in range(len(next_mapping)):
        if q in active_qubits:
            continue
        prev_pos = prev_mapping[q]
        if prev_pos != -1 and tuple(prev_pos) not in used:
            next_mapping[q] = tuple(prev_pos)
            used.add(tuple(prev_pos))
        else:
            pending.append(q)

    available = [node for node in all_nodes if node not in used]
    for q in pending:
        if not available:
            break
        prev_pos = prev_mapping[q]
        if prev_pos == -1:
            next_mapping[q] = available.pop(0)
            continue
        target = tuple(prev_pos)
        idx = min(range(len(available)), key=lambda j: euclidean_distance(target, available[j]))
        next_mapping[q] = available.pop(idx)

    return next_mapping


def _normalize_mapping(mapping):
    normalized = []
    for pos in mapping:
        if pos == -1:
            normalized.append(-1)
        else:
            normalized.append(tuple(pos))
    return normalized


def _mapping_transition_metrics(mapping, prev_mapping):
    move_sum = 0.0
    move_max = 0.0
    churn = 0
    for q in range(len(mapping)):
        cur = mapping[q]
        prev = prev_mapping[q]
        if cur == -1 or prev == -1:
            continue
        d = euclidean_distance(tuple(cur), tuple(prev))
        move_sum += d
        move_max = max(move_max, d)
        if tuple(cur) != tuple(prev):
            churn += 1
    return move_sum, move_max, churn


def _future_gate_pressure(mapping, future_gates, rb):
    if not future_gates:
        return 0, 0.0, 0.0

    violations = 0
    excess = 0.0
    weighted_distance = 0.0
    for layer_idx, layer in enumerate(future_gates):
        weight = 0.7 ** layer_idx
        for gate in layer:
            u, v = gate[0], gate[1]
            d = euclidean_distance(tuple(mapping[u]), tuple(mapping[v]))
            weighted_distance += weight * d
            if d > rb + 1e-9:
                violations += 1
                excess += weight * (d - rb)

    return violations, excess, weighted_distance


def _embedding_objective_key(mapping, prev_mapping, future_gates, rb, fidelity_priority=False):
    move_sum, move_max, churn = _mapping_transition_metrics(mapping, prev_mapping)
    fut_viol, fut_excess, fut_dist = _future_gate_pressure(mapping, future_gates, rb)
    if fidelity_priority:
        return (
            fut_viol,
            round(fut_excess, 8),
            churn,
            round(fut_dist, 8),
            round(move_max, 8),
            round(move_sum, 8)
        )
    return (
        churn,
        round(move_max, 8),
        round(move_sum, 8),
        fut_viol,
        round(fut_excess, 8),
        round(fut_dist, 8)
    )


def _local_refine_valid_embedding(
    gates,
    mapping,
    prev_mapping,
    all_nodes,
    rb,
    future_gates=None,
    max_rounds=8,
    fidelity_priority=False
):
    """
    Improve a valid embedding with small local moves:
    keep current partition Rb-valid, reduce transition distance/churn, and
    mildly improve lookahead pressure for upcoming partitions.
    """
    current = _normalize_mapping(mapping)
    prev = _normalize_mapping(prev_mapping)
    all_nodes = [tuple(node) for node in all_nodes]

    for gate in gates:
        if _gate_violates_rb(gate, current, rb):
            return current

    active_qubits = _collect_active_qubits(gates, future_gates or [])
    if not active_qubits:
        return current

    incident = {q: [] for q in range(len(current))}
    for gate in gates:
        u, v = gate[0], gate[1]
        incident[u].append(v)
        incident[v].append(u)

    current_key = _embedding_objective_key(
        current,
        prev,
        future_gates,
        rb,
        fidelity_priority=fidelity_priority
    )
    max_rounds = min(max_rounds, 4) if len(active_qubits) > 18 else max_rounds
    for _ in range(max_rounds):
        improved = False
        for q in sorted(active_qubits):
            if q >= len(current):
                continue

            used = {tuple(current[idx]) for idx in range(len(current)) if idx != q}
            candidates = [node for node in all_nodes if node not in used]
            if not candidates:
                continue

            neighbors = incident.get(q, [])
            if neighbors:
                filtered = []
                for node in candidates:
                    ok = True
                    for nb in neighbors:
                        if euclidean_distance(node, tuple(current[nb])) > rb + 1e-9:
                            ok = False
                            break
                    if ok:
                        filtered.append(node)
                if filtered:
                    candidates = filtered

            prev_q = tuple(current[q]) if prev[q] == -1 else tuple(prev[q])
            candidates.sort(
                key=lambda n: (
                    euclidean_distance(n, prev_q),
                    euclidean_distance(n, tuple(current[q]))
                )
            )
            if tuple(current[q]) not in candidates:
                candidates.insert(0, tuple(current[q]))
            candidates = candidates[:8]

            best_node = None
            best_key = current_key
            for node in candidates:
                if node == tuple(current[q]):
                    continue
                trial = list(current)
                trial[q] = node

                valid = True
                for nb in neighbors:
                    if euclidean_distance(tuple(trial[q]), tuple(trial[nb])) > rb + 1e-9:
                        valid = False
                        break
                if not valid:
                    continue

                key = _embedding_objective_key(
                    trial,
                    prev,
                    future_gates,
                    rb,
                    fidelity_priority=fidelity_priority
                )
                if key < best_key:
                    best_key = key
                    best_node = node

            if best_node is not None:
                current[q] = best_node
                current_key = best_key
                improved = True

        if not improved:
            break

    return current


def _movement_aware_repair(gates, mapping, prev_mapping, all_nodes, rb, future_gates=None, max_rounds=32):
    """
    Local greedy repair that minimizes Rb violations while keeping movement small.
    No VF2 is used.
    """
    eps = 1e-9
    all_nodes = [tuple(node) for node in all_nodes]
    current = [tuple(pos) for pos in mapping]
    prev = [tuple(pos) for pos in prev_mapping]
    num_q = len(current)

    future_flat = []
    if future_gates:
        for layer in future_gates:
            future_flat.extend(layer)

    def _prepare_gate_metrics(gate_list):
        if not gate_list:
            return [], [[] for _ in range(num_q)], [], 0, 0.0

        pairs = []
        incident = [[] for _ in range(num_q)]
        over_list = []
        viol = 0
        excess = 0.0

        for idx, gate in enumerate(gate_list):
            u, v = gate[0], gate[1]
            pairs.append((u, v))
            incident[u].append(idx)
            if v != u:
                incident[v].append(idx)

            over = euclidean_distance(current[u], current[v]) - rb
            if over > eps:
                over_list.append(over)
                viol += 1
                excess += over
            else:
                over_list.append(0.0)

        return pairs, incident, over_list, viol, excess

    def _edge_over_with_move(pair, moving, new_pos):
        u, v = pair
        pu = new_pos if u == moving else current[u]
        pv = new_pos if v == moving else current[v]
        over = euclidean_distance(pu, pv) - rb
        return over if over > eps else 0.0

    def _trial_metrics_for_move(moving, new_pos, pairs, incident, over_list, base_viol, base_excess):
        trial_viol = base_viol
        trial_excess = base_excess
        for idx in incident[moving]:
            old_over = over_list[idx]
            new_over = _edge_over_with_move(pairs[idx], moving, new_pos)
            if old_over > eps and new_over <= eps:
                trial_viol -= 1
            elif old_over <= eps and new_over > eps:
                trial_viol += 1
            trial_excess += (new_over - old_over)
        return trial_viol, trial_excess

    def _apply_move_update_metrics(moving, new_pos, pairs, incident, over_list, cur_viol, cur_excess):
        for idx in incident[moving]:
            old_over = over_list[idx]
            new_over = _edge_over_with_move(pairs[idx], moving, new_pos)
            if old_over > eps and new_over <= eps:
                cur_viol -= 1
            elif old_over <= eps and new_over > eps:
                cur_viol += 1
            cur_excess += (new_over - old_over)
            over_list[idx] = new_over
        return cur_viol, cur_excess

    main_pairs, main_incident, main_over, cur_viol, cur_excess = _prepare_gate_metrics(gates)
    future_pairs, future_incident, future_over, cur_f_viol, cur_f_excess = _prepare_gate_metrics(future_flat)
    if cur_viol == 0:
        return current, True

    rb_neighbors = _get_cached_rb_neighbors(all_nodes, rb, eps=eps)

    for _ in range(max_rounds):
        improved = False
        violating_indices = [idx for idx, over in enumerate(main_over) if over > eps]
        if not violating_indices:
            return current, True
        violating_indices.sort(key=lambda idx: main_over[idx], reverse=True)

        occupied = set(current)
        for gate_idx in violating_indices:
            u, v = main_pairs[gate_idx]
            best_move = None
            best_key = (cur_viol, cur_excess, cur_f_viol, cur_f_excess, float("inf"))

            for moving, anchor in ((u, v), (v, u)):
                anchor_pos = current[anchor]
                for node in rb_neighbors.get(anchor_pos, all_nodes):
                    if node in occupied:
                        continue
                    trial_viol, trial_excess = _trial_metrics_for_move(
                        moving, node, main_pairs, main_incident, main_over, cur_viol, cur_excess
                    )
                    if future_pairs:
                        f_viol, f_excess = _trial_metrics_for_move(
                            moving,
                            node,
                            future_pairs,
                            future_incident,
                            future_over,
                            cur_f_viol,
                            cur_f_excess
                        )
                    else:
                        f_viol, f_excess = cur_f_viol, cur_f_excess
                    move_cost = euclidean_distance(prev[moving], node)
                    key = (trial_viol, trial_excess, f_viol, f_excess, move_cost)
                    if key < best_key:
                        best_key = key
                        best_move = (moving, node)

            if best_move is not None and (
                best_key[0] < cur_viol
                or (best_key[0] == cur_viol and best_key[1] + eps < cur_excess)
                or (best_key[0] == cur_viol and abs(best_key[1] - cur_excess) <= eps and best_key[2] < cur_f_viol)
                or (
                    best_key[0] == cur_viol
                    and abs(best_key[1] - cur_excess) <= eps
                    and best_key[2] == cur_f_viol
                    and best_key[3] + eps < cur_f_excess
                )
            ):
                moving, node = best_move
                old_pos = current[moving]
                cur_viol, cur_excess = _apply_move_update_metrics(
                    moving, node, main_pairs, main_incident, main_over, cur_viol, cur_excess
                )
                if future_pairs:
                    cur_f_viol, cur_f_excess = _apply_move_update_metrics(
                        moving, node, future_pairs, future_incident, future_over, cur_f_viol, cur_f_excess
                    )
                current[moving] = node
                occupied.remove(old_pos)
                occupied.add(node)
                cur_viol, cur_excess = best_key[0], best_key[1]
                cur_f_viol, cur_f_excess = best_key[2], best_key[3]
                improved = True
                break

        if not improved:
            break

        if cur_viol == 0:
            return current, True

    return current, cur_viol == 0


def _should_try_static_embedding(gates, num_q):
    """
    Static CSP embedding is only enabled for sparse topologies where it is
    usually fast and useful (e.g., line/star-like), avoiding dense slow cases.
    """
    if not gates:
        return False

    g = nx.Graph()
    g.add_edges_from(gates)
    n = g.number_of_nodes()
    e = g.number_of_edges()
    if n == 0:
        return False

    avg_deg = (2.0 * e) / n
    max_deg = max((d for _, d in g.degree()), default=0)
    if n > min(num_q, 18):
        return False
    if avg_deg > 2.4:
        return False
    if max_deg > 4:
        return False
    return True


def _try_static_embedding(partition_gates, coupling_graph, num_q, rb, prev_mapping=None, max_search_steps=12000):
    """
    Try to find one static embedding that satisfies all 2Q interactions.
    Backtracking CSP (no VF2). Returns mapping list or None.
    """
    logic_graph = nx.Graph()
    for layer in partition_gates:
        logic_graph.add_edges_from(layer)

    if logic_graph.number_of_edges() == 0:
        return None

    coupling_degree = dict(coupling_graph.degree())
    logic_degree = dict(logic_graph.degree())
    if max(logic_degree.values(), default=0) > max(coupling_degree.values(), default=0):
        return None

    # Skip obviously dense cases to preserve speed advantage.
    used_logic_nodes = list(logic_graph.nodes())
    if len(used_logic_nodes) > 0:
        avg_logic_deg = (2 * logic_graph.number_of_edges()) / len(used_logic_nodes)
        if avg_logic_deg > 5.5:
            return None

    phys_nodes = list(coupling_graph.nodes())
    phys_neighbors = {p: set(coupling_graph.neighbors(p)) | {p} for p in phys_nodes}

    assign = {}
    used_phys = set()
    steps = [0]
    unassigned_logic = set(used_logic_nodes)

    def candidate_positions(logic_node):
        neighbors = logic_graph.adj[logic_node]
        cand = []
        for p in phys_nodes:
            if p in used_phys:
                continue
            ok = True
            for nb in neighbors:
                if nb in assign and assign[nb] not in phys_neighbors[p]:
                    ok = False
                    break
            if ok:
                cand.append(p)
        prev_pos = None
        if prev_mapping is not None and logic_node < len(prev_mapping):
            if prev_mapping[logic_node] != -1:
                prev_pos = tuple(prev_mapping[logic_node])

        if prev_pos is not None:
            cand.sort(key=lambda p: (euclidean_distance(p, prev_pos), -coupling_degree.get(p, 0)))
        else:
            # Prefer high-degree physical positions for high-degree logical nodes.
            cand.sort(key=lambda p: (-coupling_degree.get(p, 0), euclidean_distance(p, (0, 0))))
        return cand

    def choose_next_logic():
        best_node = None
        best_cands = None
        for q in unassigned_logic:
            cands = candidate_positions(q)
            if not cands:
                return q, []
            if best_cands is None or len(cands) < len(best_cands):
                best_node, best_cands = q, cands
        return best_node, best_cands

    def dfs():
        if not unassigned_logic:
            return True
        steps[0] += 1
        if steps[0] > max_search_steps:
            return False

        node, cands = choose_next_logic()
        if not cands:
            return False
        unassigned_logic.remove(node)
        for p in cands:
            assign[node] = p
            used_phys.add(p)
            if dfs():
                return True
            used_phys.remove(p)
            del assign[node]
        unassigned_logic.add(node)
        return False

    if not dfs():
        return None

    mapping = [-1] * num_q
    for q in range(num_q):
        if q in assign:
            mapping[q] = assign[q]

    remaining_phys = [p for p in phys_nodes if p not in set(assign.values())]
    if len(remaining_phys) < sum(1 for x in mapping if x == -1):
        return None
    for q in range(num_q):
        if mapping[q] == -1:
            if prev_mapping is not None and q < len(prev_mapping) and prev_mapping[q] != -1:
                target = tuple(prev_mapping[q])
                idx = min(range(len(remaining_phys)), key=lambda j: euclidean_distance(remaining_phys[j], target))
                mapping[q] = remaining_phys.pop(idx)
            else:
                mapping[q] = remaining_phys.pop(0)

    for layer in partition_gates:
        for gate in layer:
            if _gate_violates_rb(gate, mapping, rb):
                return None
    return mapping


def get_initial_mapping_no_vf2(first_partition_gates, coupling_graph, num_q, rb):
    """
    Quick no-VF2 seed mapping for easy/sparse first partitions.
    Returns a complete mapping list or None.
    """
    if not first_partition_gates:
        return None

    g = nx.Graph()
    g.add_edges_from(first_partition_gates)
    n = g.number_of_nodes()
    e = g.number_of_edges()
    if n == 0 or e == 0:
        return None

    max_deg = max((d for _, d in g.degree()), default=0)
    avg_deg = (2.0 * e) / n

    # Sparse small partition: static CSP is usually very fast.
    if n <= min(num_q, 14) and avg_deg <= 2.3 and max_deg <= 4:
        return _try_static_embedding(
            [first_partition_gates],
            coupling_graph,
            num_q,
            rb,
            prev_mapping=None,
            max_search_steps=1500
        )

    # Generic low-degree first partition: try a light static seed
    # to reduce later partition splits without heavy search.
    if n <= min(num_q, 14) and avg_deg <= 3.2 and max_deg <= 3:
        return _try_static_embedding(
            [first_partition_gates],
            coupling_graph,
            num_q,
            rb,
            prev_mapping=None,
            max_search_steps=600
        )

    # Tree-like partition: still cheap enough to try once before MCTS.
    coupling_max_deg = max((d for _, d in coupling_graph.degree()), default=0)
    if nx.is_tree(g) and n <= min(num_q, 22) and max_deg <= coupling_max_deg:
        return _try_static_embedding(
            [first_partition_gates],
            coupling_graph,
            num_q,
            rb,
            prev_mapping=None,
            max_search_steps=2600
        )

    return None


def get_embeddings_vf2(partition_gates, coupling_graph, num_q, arch_size, Rb, initial_mapping=None):
    embeddings = []
    begin_index = 0
    extend_position = []
    if initial_mapping:
        embeddings.append(initial_mapping)
        begin_index = 1
    for i in range(begin_index, len(partition_gates)):
        tmp_graph = nx.Graph()
        tmp_graph.add_edges_from(partition_gates[i])
        if not rx_is_subgraph_iso(coupling_graph, tmp_graph):
            coupling_graph = extend_graph(coupling_graph, arch_size, Rb)
            extend_position.append(i)
        next_embedding = get_rx_one_mapping(tmp_graph, coupling_graph)
        next_embedding = map2list(next_embedding, num_q)
        embeddings.append(next_embedding)

    for i in range(begin_index, len(embeddings)):
        indices = [index for index, value in enumerate(embeddings[i]) if value == -1]
        if indices:
            embeddings[i] = complete_mapping(i, embeddings, indices, coupling_graph)

    return embeddings, extend_position


def get_embeddings(partition_gates, coupling_graph, num_q, arch_size, Rb, initial_mapping=None):
    """
    noVF2/dual 严格嵌入：MCTS 开局 + 力导向续航。
    正确性优先：绝不将违反 Rb 的门保留在当前分区执行。
    """
    from analytical_placer import force_directed_mapping

    if initial_mapping is None:
        raise ValueError("纯血模式下，必须由 MCTS 提供 initial_mapping 开局！")

    if not partition_gates:
        return [], []

    all_nodes = [tuple(node) for node in coupling_graph.nodes()]
    extend_position = []
    current_arch_size = arch_size

    prev_mapping = [(-1 if pos == -1 else tuple(pos)) for pos in initial_mapping]
    embeddings = []
    micro_fast_mode = num_q <= 12
    disable_first_partition_refine = (
        os.environ.get("DASATOM_DISABLE_FIRST_PARTITION_REFINEMENT", "0") == "1"
    )

    i = 0
    while i < len(partition_gates):
        gates = partition_gates[i]
        future = partition_gates[i + 1:i + 4]
        if not gates:
            embeddings.append(list(prev_mapping))
            i += 1
            continue

        gate_graph = nx.Graph()
        gate_graph.add_edges_from(gates)
        gate_nodes = gate_graph.number_of_nodes()
        gate_edges = gate_graph.number_of_edges()
        avg_deg = (2.0 * gate_edges / gate_nodes) if gate_nodes else 0.0
        max_deg = max((d for _, d in gate_graph.degree()), default=0)
        dense_small_quick_split = (
            micro_fast_mode and num_q <= 8 and gate_nodes >= min(num_q, 8) and avg_deg >= 4.0
        )
        dense_repair_preferred = (
            micro_fast_mode and
            gate_nodes >= min(num_q, 10) and
            avg_deg >= 6.8 and
            max_deg >= gate_nodes - 1
        )

        # Fast path: keep previous embedding if current partition is already executable.
        # For the first partition, still give force-directed one chance to improve
        # the MCTS layout for future partitions before accepting it as-is.
        violating_prev, _ = _split_valid_violating_gates(gates, prev_mapping, Rb)
        if not violating_prev and not (
            i == 0 and len(gates) >= 8 and not disable_first_partition_refine
        ):
            embeddings.append(list(prev_mapping))
            i += 1
            continue

        if (
            not violating_prev
            and i == 0
            and len(gates) >= 8
            and not disable_first_partition_refine
        ):
            fidelity_priority = (gate_nodes >= min(num_q, 10) and avg_deg >= 3.2)
            if (not fidelity_priority) and (len(gates) < 16 or max_deg <= 2):
                embeddings.append(list(prev_mapping))
                i += 1
                continue
            active_qubits = _collect_active_qubits(gates, future)
            fd_seed = force_directed_mapping(
                gates,
                prev_mapping,
                all_nodes,
                Rb,
                num_q,
                future_gates=future
            )
            fd_seed = _normalize_mapping(fd_seed)
            if active_qubits:
                fd_seed = _minimize_idle_movement(fd_seed, prev_mapping, all_nodes, active_qubits)
            fd_seed, fd_seed_ok = _movement_aware_repair(
                gates,
                fd_seed,
                prev_mapping,
                all_nodes,
                Rb,
                future_gates=future,
                max_rounds=18
            )
            if fd_seed_ok:
                fd_seed = _normalize_mapping(fd_seed)
                if _embedding_objective_key(fd_seed, prev_mapping, future, Rb, fidelity_priority=fidelity_priority) < \
                   _embedding_objective_key(prev_mapping, prev_mapping, future, Rb, fidelity_priority=fidelity_priority):
                    embeddings.append(fd_seed)
                    prev_mapping = fd_seed
                    i += 1
                    continue
            embeddings.append(list(prev_mapping))
            i += 1
            continue

        if micro_fast_mode:
            next_embedding, fast_ok = _movement_aware_repair(
                gates,
                prev_mapping,
                prev_mapping,
                all_nodes,
                Rb,
                future_gates=None,
                max_rounds=8
            )
            next_embedding = _normalize_mapping(next_embedding)
            violating, valid = _split_valid_violating_gates(gates, next_embedding, Rb)
            if (not fast_ok) and dense_small_quick_split and violating and len(violating) <= max(3, len(gates) // 8):
                partition_gates[i] = valid
                partition_gates.insert(i + 1, violating)
                embeddings.append(next_embedding)
                prev_mapping = next_embedding
                i += 1
                continue
            if (not fast_ok) and dense_repair_preferred and valid and len(valid) >= int(0.75 * len(gates)):
                partition_gates[i] = valid
                partition_gates.insert(i + 1, violating)
                embeddings.append(next_embedding)
                prev_mapping = next_embedding
                i += 1
                continue
            if not fast_ok:
                next_embedding = force_directed_mapping(
                    gates,
                    prev_mapping,
                    all_nodes,
                    Rb,
                    num_q,
                    future_gates=None
                )
                next_embedding = _normalize_mapping(next_embedding)
                fd_violating, _ = _split_valid_violating_gates(gates, next_embedding, Rb)
                if not fd_violating:
                    fast_ok = True
                else:
                    next_embedding, fast_ok = _movement_aware_repair(
                        gates,
                        next_embedding,
                        prev_mapping,
                        all_nodes,
                        Rb,
                        future_gates=None,
                        max_rounds=10
                    )
            next_embedding = _normalize_mapping(next_embedding)
            violating, valid = _split_valid_violating_gates(gates, next_embedding, Rb)

            if (not fast_ok) and violating and not valid:
                if len(gates) == 1:
                    next_embedding = _build_strict_single_gate_embedding(prev_mapping, gates[0], all_nodes, Rb, num_q)
                    re_violating, _ = _split_valid_violating_gates(gates, next_embedding, Rb)
                    if re_violating:
                        raise RuntimeError(f"Strict fallback failed for gate {gates[0]} under Rb={Rb}.")
                    embeddings.append(next_embedding)
                    prev_mapping = next_embedding
                    i += 1
                    continue
                split_at = max(1, len(gates) // 2)
                partition_gates[i] = list(gates[:split_at])
                partition_gates.insert(i + 1, list(gates[split_at:]))
                continue

            if violating:
                partition_gates[i] = valid
                partition_gates.insert(i + 1, violating)

            embeddings.append(next_embedding)
            prev_mapping = next_embedding
            i += 1
            continue

        candidate_embeddings = []
        candidate_keys = set()
        fidelity_priority = (gate_nodes >= min(num_q, 10) and avg_deg >= 3.4)
        small_fast_mode = (num_q <= 8 and len(gates) <= 36) or (num_q <= 10 and len(gates) <= 28)
        dense_speed_mode = (gate_nodes >= min(num_q, 8) and avg_deg >= 3.2 and len(gates) >= 16)
        hub_speed_mode = (gate_nodes >= 12 and avg_deg <= 2.4 and max_deg >= min(gate_nodes - 1, 8))
        tree_speed_mode = (gate_nodes >= 6 and gate_edges == max(0, gate_nodes - 1) and avg_deg <= 2.1)
        chain_speed_mode = (gate_nodes >= 6 and avg_deg <= 2.05 and max_deg <= 2)
        speed_guard = small_fast_mode or dense_speed_mode or hub_speed_mode or tree_speed_mode or chain_speed_mode
        max_candidates = 3 if speed_guard else (5 if fidelity_priority else 4)
        search_future = None if speed_guard else future
        do_local_refine = (not speed_guard) and (fidelity_priority or not (num_q >= 16 and len(gates) >= 64))
        repair_rounds = 16 if speed_guard else (32 if len(gates) >= 60 else 22)

        def _push_candidate(cand):
            cand = _normalize_mapping(cand)
            key = tuple(cand)
            if key in candidate_keys:
                return
            candidate_keys.add(key)
            candidate_embeddings.append(cand)

        # Candidate 1: local repair from previous embedding.
        local_rounds = min(10, max(5, len(gates) // 3)) if speed_guard else (28 if len(gates) >= 60 else min(16, max(8, len(gates) // 2)))
        repaired, repaired_ok = _movement_aware_repair(
            gates,
            prev_mapping,
            prev_mapping,
            all_nodes,
            Rb,
            future_gates=search_future,
            max_rounds=local_rounds
        )
        if repaired_ok:
            repaired = _normalize_mapping(repaired)
            if speed_guard:
                embeddings.append(repaired)
                prev_mapping = repaired
                i += 1
                continue
            _push_candidate(repaired)

        # Candidate 2: force-directed mapping + idle stabilization + repair.
        # Only triggered when local repair failed or lookahead pressure is high.
        need_force_search = (not repaired_ok) and (len(candidate_embeddings) < max_candidates)
        if repaired_ok and len(candidate_embeddings) < max_candidates:
            fut_viol, fut_excess, _ = _future_gate_pressure(repaired, future, Rb)
            if fut_viol > max(2, len(gates) // 3) and fut_excess > 1e-9:
                need_force_search = True
        if (not speed_guard) and fidelity_priority and len(gates) >= 18 and len(candidate_embeddings) < max_candidates:
            need_force_search = True

        if need_force_search and len(candidate_embeddings) < max_candidates:
            active_qubits = _collect_active_qubits(gates, future) if (not speed_guard) else None
            fd_embedding = force_directed_mapping(
                gates,
                prev_mapping,
                all_nodes,
                Rb,
                num_q,
                future_gates=search_future
            )
            fd_embedding = _normalize_mapping(fd_embedding)
            if active_qubits:
                fd_embedding = _minimize_idle_movement(fd_embedding, prev_mapping, all_nodes, active_qubits)
            fd_violating, _ = _split_valid_violating_gates(gates, fd_embedding, Rb)
            if speed_guard and not fd_violating:
                embeddings.append(fd_embedding)
                prev_mapping = fd_embedding
                i += 1
                continue
            fd_embedding, fd_ok = _movement_aware_repair(
                gates,
                fd_embedding,
                prev_mapping,
                all_nodes,
                Rb,
                future_gates=search_future,
                max_rounds=repair_rounds
            )
            if fd_ok:
                _push_candidate(fd_embedding)

            # Secondary force-directed attempt without lookahead.
            # Keep it for small/medium partitions; enable for fidelity-priority dense cases.
            if (
                (not speed_guard)
                and len(candidate_embeddings) < max_candidates
                and (len(gates) <= 35 or num_q <= 12 or (fidelity_priority and len(gates) <= 80))
            ):
                fd_now_embedding = force_directed_mapping(
                    gates,
                    prev_mapping,
                    all_nodes,
                    Rb,
                    num_q,
                    future_gates=None
                )
                fd_now_embedding = _normalize_mapping(fd_now_embedding)
                if active_qubits:
                    fd_now_embedding = _minimize_idle_movement(fd_now_embedding, prev_mapping, all_nodes, active_qubits)
                fd_now_embedding, fd_now_ok = _movement_aware_repair(
                    gates,
                    fd_now_embedding,
                    prev_mapping,
                    all_nodes,
                    Rb,
                    future_gates=search_future,
                    max_rounds=repair_rounds
                )
                if fd_now_ok:
                    _push_candidate(fd_now_embedding)

            # Tertiary candidate(s): shuffled gate order for force-directed diversification.
            diversify = (fidelity_priority and len(gates) >= 20) or ((not fidelity_priority) and num_q >= 14 and len(gates) >= 30)
            if diversify and (not speed_guard) and len(candidate_embeddings) < max_candidates:
                base_seed = (i + 1) * 131 + num_q * 17 + len(gates)
                seeds = [base_seed] if fidelity_priority else [base_seed, base_seed + 97]
                for seed in seeds:
                    if len(candidate_embeddings) >= max_candidates:
                        break
                    shuffled_gates = list(gates)
                    rng = random.Random(seed)
                    rng.shuffle(shuffled_gates)
                    fd_shuffle_embedding = force_directed_mapping(
                        shuffled_gates,
                        prev_mapping,
                        all_nodes,
                        Rb,
                        num_q,
                        future_gates=future
                    )
                    fd_shuffle_embedding = _normalize_mapping(fd_shuffle_embedding)
                    if active_qubits:
                        fd_shuffle_embedding = _minimize_idle_movement(
                            fd_shuffle_embedding,
                            prev_mapping,
                            all_nodes,
                            active_qubits
                        )
                    fd_shuffle_embedding, fd_shuffle_ok = _movement_aware_repair(
                        gates,
                        fd_shuffle_embedding,
                        prev_mapping,
                        all_nodes,
                        Rb,
                        future_gates=search_future,
                        max_rounds=repair_rounds
                    )
                    if not fd_shuffle_ok:
                        continue
                    _push_candidate(fd_shuffle_embedding)

        # Candidate 3: static CSP embedding for sparse hard partitions (still no VF2).
        if (not candidate_embeddings) and _should_try_static_embedding(gates, num_q):
            search_budget = 2500
            static_embedding = _try_static_embedding(
                [gates], coupling_graph, num_q, Rb, prev_mapping=prev_mapping, max_search_steps=search_budget
            )
            if static_embedding is not None:
                _push_candidate(static_embedding)
        unique_candidates = candidate_embeddings

        if unique_candidates:
            next_embedding = min(
                unique_candidates,
                key=lambda emb: _embedding_objective_key(
                    emb,
                    prev_mapping,
                    future,
                    Rb,
                    fidelity_priority=fidelity_priority
                )
            )
        else:
            next_embedding = list(prev_mapping)

        if do_local_refine:
            next_embedding = _local_refine_valid_embedding(
                gates,
                next_embedding,
                prev_mapping,
                all_nodes,
                Rb,
                future_gates=future,
                fidelity_priority=fidelity_priority
            )
            next_embedding = _normalize_mapping(next_embedding)

        violating, valid = _split_valid_violating_gates(gates, next_embedding, Rb)

        # Before splitting partitions, try one stronger static attempt.
        # For sparse near-regular graphs, always attempt one whole-partition static
        # embedding before splitting. They are often globally embeddable and
        # splitting hurts fidelity via extra transfers and idle time.
        if violating and not valid and _should_try_static_embedding(gates, num_q):
            static_embedding = _try_static_embedding(
                [gates], coupling_graph, num_q, Rb, prev_mapping=prev_mapping, max_search_steps=(3200 if speed_guard else 5000)
            )
            if static_embedding is not None:
                next_embedding = _normalize_mapping(static_embedding)
                if do_local_refine:
                    next_embedding = _local_refine_valid_embedding(
                        gates,
                        next_embedding,
                        prev_mapping,
                        all_nodes,
                        Rb,
                        future_gates=future,
                        fidelity_priority=fidelity_priority
                    )
                violating, valid = _split_valid_violating_gates(gates, next_embedding, Rb)

        # Last chance before splitting: extend architecture and retry hard partitions.
        allow_extension_fallback = (
            (not speed_guard) and len(gates) >= 28 and num_q >= 14 and avg_deg < 3.6
        )
        if violating and not valid and allow_extension_fallback:
            extend_attempts = 2 if (num_q >= 18 and not fidelity_priority and len(gates) >= 56) else 1
            for _ in range(extend_attempts):
                coupling_graph = extend_graph(coupling_graph, current_arch_size, Rb)
                current_arch_size += 1
                all_nodes = [tuple(node) for node in coupling_graph.nodes()]
                extend_position.append(i)

                active_qubits = _collect_active_qubits(gates, future)
                ext_embedding = force_directed_mapping(
                    gates,
                    prev_mapping,
                    all_nodes,
                    Rb,
                    num_q,
                    future_gates=future
                )
                ext_embedding = _normalize_mapping(ext_embedding)
                if active_qubits:
                    ext_embedding = _minimize_idle_movement(ext_embedding, prev_mapping, all_nodes, active_qubits)
                ext_embedding, ext_ok = _movement_aware_repair(
                    gates,
                    ext_embedding,
                    prev_mapping,
                    all_nodes,
                    Rb,
                    future_gates=future,
                    max_rounds=40
                )
                if ext_ok:
                    if do_local_refine:
                        ext_embedding = _local_refine_valid_embedding(
                            gates,
                            ext_embedding,
                            prev_mapping,
                            all_nodes,
                            Rb,
                            future_gates=future,
                            fidelity_priority=False
                        )
                    next_embedding = _normalize_mapping(ext_embedding)
                    violating, valid = _split_valid_violating_gates(gates, next_embedding, Rb)
                    if not violating or valid:
                        break

        if violating and not valid:
            if len(gates) == 1:
                next_embedding = _build_strict_single_gate_embedding(prev_mapping, gates[0], all_nodes, Rb, num_q)
                re_violating, _ = _split_valid_violating_gates(gates, next_embedding, Rb)
                if re_violating:
                    raise RuntimeError(f"Strict fallback failed for gate {gates[0]} under Rb={Rb}.")
                embeddings.append(next_embedding)
                prev_mapping = next_embedding
                i += 1
                continue

            split_at = max(1, len(gates) // 2)
            partition_gates[i] = list(gates[:split_at])
            partition_gates.insert(i + 1, list(gates[split_at:]))
            continue

        if violating:
            partition_gates[i] = valid
            partition_gates.insert(i + 1, violating)

        embeddings.append(next_embedding)
        prev_mapping = next_embedding
        i += 1

    if len(embeddings) != len(partition_gates):
        raise RuntimeError(
            f"Embedding count mismatch after strict scheduling: embeddings={len(embeddings)}, "
            f"partitions={len(partition_gates)}"
        )

    for emb_idx in range(len(embeddings)):
        indices = [index for index, value in enumerate(embeddings[emb_idx]) if value == -1]
        if indices:
            embeddings[emb_idx] = complete_mapping(emb_idx, embeddings, indices, coupling_graph)

    strict_validate = os.environ.get("DASATOM_STRICT_VALIDATE", "0") == "1"
    if strict_validate:
        for part_idx, gates in enumerate(partition_gates):
            emb = embeddings[part_idx]
            if any(pos == -1 for pos in emb):
                raise RuntimeError(f"Incomplete embedding at partition {part_idx}.")
            for gate in gates:
                if _gate_violates_rb(gate, emb, Rb):
                    raise RuntimeError(f"Rb violation at partition {part_idx}, gate={gate}.")

    return embeddings, extend_position

def qasm_to_map(filename):

    with open(filename, 'r') as file:
        lines = file.readlines()
    
    # 确保行数为偶数
    if len(lines) % 2 != 0:
        raise ValueError("文件内容的行数应为偶数，以便正确配对比特和映射位置。")
    qubit_pattern = re.compile(r"Qubit\(QuantumRegister\((\d+), 'q'\), (\d+)\)")
    match = qubit_pattern.search(lines[0].strip())
    num_q = int(match.group(1))
    mapping = [-1]*num_q
    # 遍历每对行
    for i in range(0, len(lines), 2):
        # 读取比特行和映射位置行
        qubit_line = lines[i].strip()
        position_line = lines[i+1].strip()
        
        # 解析比特索引
        match = qubit_pattern.search(qubit_line)
        if match:
            num_q = int(match.group(1))
            bit_index = int(match.group(2))
        else:
            raise ValueError(f"无法从比特行提取索引: {qubit_line}")
        # 解析映射位置
        try:
            position = eval(position_line)
        except Exception as e:
            raise ValueError(f"解析映射位置时出错: {position_line}") from e
        
        # 扩展列表到足够的长度
        mapping[bit_index] = position
    
    return mapping

def compute_fidelity_tetris(cycle_file, qasm_file, path):

    with open(path+cycle_file, 'r') as cyc_file:
        cyc_lines = cyc_file.readlines()

    last_line = cyc_lines[-1].strip()
    last_gate = list(map(int, last_line.split()))

    cnot_count = 0
    swap_count = 0
    circ = CreateCircuitFromQASM(qasm_file, path)
    for instruction, qargs, cargs in circ.data:
        if instruction.name == 'cx':  # CNOT 门
            cnot_count += 1
        elif instruction.name == 'swap':  # SWAP 门
            swap_count += 1

    num_q = len(circ.qubits)
    gate_num = cnot_count + 3*swap_count
    
    para = set_parameters(True)
    gate_cycle = (last_gate[0]+1)/2
    t_total = gate_cycle*para['T_cz']
    t_idle = num_q * t_total - gate_num * para['T_cz']

    Fidelity =math.exp(-t_idle/para['T_eff']) * (para['F_cz']**gate_num)
    # print(Fidelity)
    return Fidelity, swap_count, gate_cycle

def write_data(data, path, file_name):
    with open(os.path.join(path,file_name), 'w') as file:
        for sublist in data:
        # 将每个子列表转换为 JSON 格式的字符串，并写入文件
            file.write(json.dumps(sublist) + '\n')
def write_data_json(data, path, file_name):
    with open(os.path.join(path,file_name), 'w') as file:
        file.write(json.dumps(data) + '\n')

def read_data(path, file_name):
    with open(os.path.join(path,file_name), 'r') as file:
    # 逐行读取文件
        data = [json.loads(line) for line in file]

# 输出读取的数据
    return data

def get_circuit_from_json(num_qubits: int):
    """
    Load a quantum circuit from a JSON file based on the number of qubits.

    Args:
        num_qubits (int): The number of qubits for the desired circuit.

    Returns:
        QuantumCircuit: The loaded quantum circuit.

    Raises:
        ValueError: If no circuit configuration is found for the specified number of qubits.
    """
    # Path to the JSON file containing the circuits
    json_file_path = "./Enola/graphs.json"
    with open(json_file_path, "r") as file:
        data = json.load(file)
    circuit_data = data.get(str(num_qubits))
    from qiskit.qasm2.export import dump
    # Check if the circuit configuration exists
    if circuit_data:
        # Add gates to the circuit based on the data
        index = 1
        for circuits in circuit_data: 
            for gate in circuits:
                quantum_circuit = QuantumCircuit(num_qubits)
                quantum_circuit.cz(gate[0], gate[1])
            dump(quantum_circuit, 'Data/3_regular_cz/circuits/3_regular_{}_{}.qasm'.format(num_qubits, index))
            index += 1
        #return quantum_circuit
    else:
        # Raise an error if no configuration is found
        raise ValueError(f"No circuit configuration for {num_qubits} qubits in graphs.json")
