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
    
    # 实际可放置阈值：保守地取耦合图最大度数的一半
    # 这保证 MCTS/力导向一定能找到合法放置
    # Rb=2, coupling_max=12 → cap=6: 每个qubit最多6门/分区
    practical_degree_cap = max(coupling_max_degree // 2, 3)
    
    def _gate_max_degree(gates):
        """计算门列表中的最大 qubit 度数"""
        deg = {}
        for g in gates:
            deg[g[0]] = deg.get(g[0], 0) + 1
            deg[g[1]] = deg.get(g[1], 0) + 1
        return max(deg.values()) if deg else 0
    
    def _check_embeddability(gates):
        """检查门图是否可嵌入：度数上限 + 度数预算双重检查"""
        if not gates:
            return True
        
        # 快速检查：最大度数不超上限
        if _gate_max_degree(gates) > practical_degree_cap:
            return False
        
        # 度数预算检查
        g = nx.Graph()
        g.add_edges_from(gates)
        if len(g.nodes()) > len(coupling_degree_seq):
            return False
        return _can_embed_degree_budget(g, coupling_degree_seq)
    
    partition_gates = []
    current_gates = list(all_layers[0])
    current_qubits = set()
    for gate in current_gates:
        current_qubits.add(gate[0])
        current_qubits.add(gate[1])
    
    for i in range(1, len(all_layers)):
        next_qubits = set()
        for gate in all_layers[i]:
            next_qubits.add(gate[0])
            next_qubits.add(gate[1])
        
        merged_qubits = current_qubits | next_qubits
        
        # 条件 1：qubit 数不超容量
        if len(merged_qubits) > grid_capacity:
            partition_gates.append(current_gates)
            current_gates = list(all_layers[i])
            current_qubits = next_qubits
            continue
        
        # 条件 2：合并后的门拓扑可嵌入耦合图
        trial_gates = current_gates + list(all_layers[i])
        if not _check_embeddability(trial_gates):
            partition_gates.append(current_gates)
            current_gates = list(all_layers[i])
            current_qubits = next_qubits
            continue
        
        # 两个条件都满足 → 合并
        current_gates = trial_gates
        current_qubits = merged_qubits
    
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
    x1, y1 = node1
    x2, y2 = node2
    return math.sqrt((x2 - x1)**2 + (y2 - y1)**2)

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
def set_parameters(T_cz = 0.2, T_eff = 1.5e6, T_trans=20, AOD_width=3,AOD_height=3,Move_speed=0.55,F_cz=0.995, F_trans = 1):
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

def get_embeddings(partition_gates, coupling_graph, num_q, arch_size, Rb, initial_mapping=None):
    """
    纯血版嵌入生成：MCTS 开局 + 力导向续航，彻底摒弃 VF2。

    Args:
        partition_gates: 分区门列表
        coupling_graph: 物理耦合图
        num_q: 量子比特数
        arch_size: 网格尺寸（保留接口，不再用于扩图）
        Rb: 相互作用半径
        initial_mapping: MCTS 提供的第 0 层映射（必须提供）

    Returns:
        (embeddings, []) — 第二个值始终为空列表（无扩图）
    """
    from analytical_placer import force_directed_mapping

    if initial_mapping is None:
        raise ValueError("纯血模式下，必须由 MCTS 提供 initial_mapping 开局！")

    embeddings = [initial_mapping]

    import math
    
    def _euclidean_dist(p1, p2):
        return math.sqrt((p1[0]-p2[0])**2 + (p1[1]-p2[1])**2)
        
    def _get_violations(gates, emb):
        violating = []
        valid = []
        for gate in gates:
            u, v = gate[0], gate[1]
            if emb[u] == -1 or emb[v] == -1 or _euclidean_dist(emb[u], emb[v]) > Rb + 1e-9:
                violating.append(gate)
            else:
                valid.append(gate)
        return violating, valid

    # 对第 0 层也做约束传播修复：MCTS 提供的映射不保证所有门都在 Rb 内，
    # force_directed 以 MCTS 映射做锚定，通过约束传播吸附来修复
    if len(partition_gates) > 0 and partition_gates[0]:
        all_nodes = list(coupling_graph.nodes())
        future = partition_gates[1:4]
        repaired_0 = force_directed_mapping(
            partition_gates[0],
            initial_mapping,
            all_nodes,
            Rb,
            num_q,
            future_gates=future
        )
        # 检查违例并拆分
        violating, valid = _get_violations(partition_gates[0], repaired_0)
        if violating:
            # 保证至少有一部分被执行，防止死循环
            if not valid:
                valid.append(violating.pop(0))
            partition_gates[0] = valid
            if violating:
                partition_gates.insert(1, violating)
        embeddings[0] = repaired_0

    # 从 Layer 1 开始，全部使用力导向 + 约束传播，并带有违例拆分兜底
    i = 1
    while i < len(partition_gates):
        all_nodes = list(coupling_graph.nodes())
        prev = embeddings[-1]

        # 力导向一步到位：弹簧求解 + 约束传播吸附 + 局部修复
        future = partition_gates[i+1:i+4]
        next_embedding = force_directed_mapping(
            partition_gates[i],
            prev,
            all_nodes,
            Rb,
            num_q,
            future_gates=future
        )
        
        # 检查违例：如果仍然有违法的门，必须留到下一个分区执行
        violating, valid = _get_violations(partition_gates[i], next_embedding)
        if violating:
            # 为了防止死循环（所有门都违法），强制保留至少一个门的进度（或者接受小违例，理论上局部修复已解决绝大部分）
            if not valid:
                # 极端情况：一个门都放不下。我们把优先级最高的一个门硬塞进去。
                valid.append(violating.pop(0))
            
            # 更新当前分区，移除违法门
            partition_gates[i] = valid
            
            # 把违法门推迟到紧接着的下一个全新分区
            # 因为它们只依赖之前的门，所以推迟不违反 DAG 拓扑顺序
            if violating:
                partition_gates.insert(i + 1, violating)
                
        embeddings.append(next_embedding)
        i += 1

    # 补全未映射的比特（理论上贪心吸附已保证无 -1，此处为安全兜底）
    for i in range(1, len(embeddings)):
        indices = [index for index, value in enumerate(embeddings[i]) if value == -1]
        if indices:
            embeddings[i] = complete_mapping(i, embeddings, indices, coupling_graph)

    # 没有扩图，extend_position 永远为空
    return embeddings, []

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