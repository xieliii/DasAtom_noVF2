import math
from networkx import maximal_independent_set, Graph


def compatible_2D(a: list[int], b: list[int]) -> bool:
    """
    Checks if two 2D points are compatible based on specified rules.
    根据指定规则检查两个 2D 点是否兼容。

    Parameters:
    a (list[int]): A list of four integers representing the first point. The elements are ordered as [x_loc_before, y_loc_before, x_loc_after, y_loc_after].
    a (list[int]): 包含四个整数的列表，表示第一个点。元素顺序为 [x_loc_before, y_loc_before, x_loc_after, y_loc_after]。
    b (list[int]): A list of four integers representing the second point. The elements are ordered as [x_loc_before, y_loc_before, x_loc_after, y_loc_after].
    b (list[int]): 包含四个整数的列表，表示第二个点。元素顺序为 [x_loc_before, y_loc_before, x_loc_after, y_loc_after]。

    Returns:
    bool: True if the points are compatible, False otherwise.
    bool: 如果点兼容则返回 True，否则返回 False。
    """
    assert len(a) == 4 and len(b) == 4, "Both arguments must be lists with exactly four elements."

    # Check compatibility for the first two elements of each point
    # 检查每个点的前两个元素的兼容性
    if a[0] == b[0] and a[2] != b[2]:
        return False
    if a[2] == b[2] and a[0] != b[0]:
        return False
    if a[0] < b[0] and a[2] >= b[2]:
        return False
    if a[0] > b[0] and a[2] <= b[2]:
        return False

    # Check compatibility for the last two elements of each point
    # 检查每个点的后两个元素的兼容性
    if a[1] == b[1] and a[3] != b[3]:
        return False
    if a[3] == b[3] and a[1] != b[1]:
        return False
    if a[1] < b[1] and a[3] >= b[3]:
        return False
    if a[1] > b[1] and a[3] <= b[3]:
        return False

    return True

def maximalis_solve_sort(n: int, edges: list[tuple[int]], nodes: set[int]) -> list[int]:
    """
    Finds a maximal independent set from the given graph nodes using a sorted approach.
    使用排序方法从给定的图中查找极大独立集。

    Parameters:
    n (int): Number of nodes in the graph. The nodes were expressed by integers from 0 to n-1.
    n (int): 图中的节点数。节点由 0 到 n-1 的整数表示。
    edges (list[tuple[int]]): list of edges in the graph, where each edge is a tuple of two nodes.
    edges (list[tuple[int]]): 图中的边列表，其中每条边是两个节点的元组。
    nodes (set[int]): Set of nodes to consider for the maximal independent set.
    nodes (set[int]): 考虑用于极大独立集的节点集合。

    Returns:
    list[int]: list of nodes in the maximal independent set.
    list[int]: 极大独立集中的节点列表。
    """
    # Initialize conflict status for each node
    # 初始化每个节点的冲突状态
    is_node_conflict = [False for _ in range(n)]
    
    # Create a dictionary to store neighbors of each node
    # 创建一个字典来存储每个节点的邻居
    node_neighbors = {i: [] for i in range(n)}
    
    # Populate the neighbors dictionary
    # 填充邻居字典
    for edge in edges:
        node_neighbors[edge[0]].append(edge[1])
        node_neighbors[edge[1]].append(edge[0])
    
    result = []
    for i in nodes:
        if is_node_conflict[i]:
            continue
        else:
            result.append(i)
            for j in node_neighbors[i]:
                is_node_conflict[j] = True
    return result

def maximalis_solve(nodes:list[int], edges:list[tuple[int]])-> list[int]:
    """
    Wrapper function to find a maximal independent set using the Graph class.
    使用 Graph 类查找极大独立集的包装函数。

    Parameters:
    n (int): Number of nodes in the graph. The nodes were expressed by integers from 0 to n-1.
    n (int): 图中的节点数。节点由 0 到 n-1 的整数表示。
    edges (list[tuple[int]]): list of edges in the graph.
    edges (list[tuple[int]]): 图中的边列表。

    Returns:
    list[int]: list of nodes in the maximal independent set.
    list[int]: 极大独立集中的节点列表。
    """
    G = Graph()
    for i in nodes:
        G.add_node(i)
    for edge in edges:
        G.add_edge(edge[0], edge[1]) # add_edge会自动处理无向图的双向关系
    
    # Use a library function to find the maximal independent set
    # 使用库函数查找极大独立集
    result = maximal_independent_set(G, seed=0) # seed=0确保每次运行结果一致
    return result

def get_movements(current_map: list, next_map: list, window_size=None) -> map:
    """
    Determines the movements of qubits between two maps.
    确定量子比特在两个映射之间的移动。

    Parameters:
    current_map (list): list of current positions of qubits.
    current_map (list): 量子比特当前位置的列表。
    next_map (list): list of next positions of qubits.
    next_map (list): 量子比特下一个位置的列表。
    window_size (optional): Size of the window for movement calculations.
    window_size (可选): 用于移动计算的窗口大小。

    Returns:
    map: A dictionary with qubit movements.
    map: 包含量子比特移动的字典。
    """
    movements = {}
    # Determine movements of qubits
    # 确定量子比特的移动
    for qubit, current_position in enumerate(current_map):
        next_position = next_map[qubit]
        if current_position != next_position:
            move_details = current_position + next_position
            movements[qubit] = move_details
    return movements

class QuantumRouter:
    def __init__(self, num_qubits: int, embeddings: list[list[list[int]]], gate_list: list[list[int]], arch_size: list[int], routing_strategy: str = "maximalis") -> None:
        """
        Initialize the QuantumRouter object with the given parameters.
        使用给定参数初始化 QuantumRouter 对象。
        
        Parameters:
        num_qubits (int): Number of qubits.
        num_qubits (int): 量子比特数量。
        embeddings (list[list[list[int]]]): Embeddings for the qubits.
        embeddings (list[list[list[int]]]): 量子比特的嵌入。
        gate_list (list[list[int]]): list of two-qubit gates.
        gate_list (list[list[int]]): 双量子比特门列表。
        arch_size (list[int]): Architecture size as [x, y].
        arch_size (list[int]): 架构尺寸 [x, y]。
        routing_strategy (str): Strategy used for routing.
        routing_strategy (str): 路由使用的策略。
        """
        self.num_qubits = num_qubits
        self.validate_embeddings(embeddings)
        self.embeddings = embeddings
        
        assert len(embeddings) == len(gate_list), "The number of embeddings should match the number of two-qubit gates in gate_list."
        self.gate_list = gate_list
        
        self.validate_architecture_size(arch_size)
        self.arch_size = arch_size
        self.routing_strategy = routing_strategy
        self.movement_list = []

    def validate_embeddings(self, embeddings: list[list[list[int]]]) -> None:
        """
        Validate the embeddings to ensure they contain locations for all qubits.
        验证嵌入以确保它们包含所有量子比特的位置。
        
        Parameters:
        embeddings (list[list[list[int]]]): Embeddings for the qubits.
        embeddings (list[list[list[int]]]): 量子比特的嵌入。
        """
        for embedding in embeddings:
            assert len(embedding) == self.num_qubits, f"Each embedding must contain locations for all {self.num_qubits} qubits."
            for loc in embedding:
                assert len(loc) == 2, "Each location must be a list containing exactly two coordinates: [x, y]."

    def validate_architecture_size(self, arch_size: list[int]) -> None:
        """
        Validate the architecture size to ensure it can accommodate all qubits.
        验证架构尺寸以确保它可以容纳所有量子比特。
        
        Parameters:
        arch_size (list[int]): Architecture size as [x, y].
        arch_size (list[int]): 架构尺寸 [x, y]。
        """
        assert len(arch_size) == 2, "Architecture size should be specified as a list with two elements: [x, y]."
        assert arch_size[0] * arch_size[1] >= self.num_qubits, (
            f"The product of the architecture dimensions x and y must be at least {self.num_qubits} to accommodate all qubits; "
            f"currently, it is {arch_size[0] * arch_size[1]}."
        )

    def process_all_embeddings(self) -> None:
        """
        Process all embeddings to resolve movements and update the program.
        处理所有嵌入以解决移动并更新程序。
        """
        for current_pos in range(len(self.embeddings) - 1):
            movements = self.resolve_movements(current_pos)
            if len(movements) == 0:
                # 警告：相邻分区嵌入完全相同，可能存在布局问题
                import warnings
                warnings.warn(f"Zero movements between embedding {current_pos} and {current_pos+1}, "
                              "this may indicate a layout issue")
                self.movement_list.append([])
            else:
                self.movement_list.append(movements)

    def solve_violations(self, movements, violations, sorted_keys):
        """
        Resolves violations in qubit movements based on the routing strategy.
        根据路由策略解决量子比特移动中的冲突。

        Parameters:
        movements (dict): Dictionary of qubit movements.
        movements (dict): 量子比特移动字典。
        violations (list): list of violations to be resolved.
        violations (list): 待解决的冲突列表。
        sorted_keys (list): list of qubit keys sorted based on priority.
        sorted_keys (list): 基于优先级排序的量子比特键列表。

        Returns:
        tuple: remaining movements, unresolved violations and movement sequence to finish movement this time
        tuple: 剩余的移动，未解决的冲突 以及 本次完成移动的移动序列
        
        """
        if self.routing_strategy == "maximalis":
            resolution_order = maximalis_solve(sorted_keys, violations)
        else:
            resolution_order = maximalis_solve_sort(self.num_q, violations, sorted_keys)
        # print(f'Resolution Order: {resolution_order}')
        move_sequence =[]
        for qubit in resolution_order:
            sorted_keys.remove(qubit)

            move = movements[qubit]
            # print(self.momvents)
            move_sequence.append([qubit,(move[0],move[1]),(move[2],move[3])])
            # print(f'Move qubit {qubit} from ({move[0]}, {move[1]}) to ({move[2]}, {move[3]})')
            # Remove resolved violations
            # 移除已解决的冲突
            violations = [v for v in violations if qubit not in v]
            del movements[qubit]
        
        return movements, violations, move_sequence

    def resolve_movements(self, current_pos: int) -> list[int, tuple[int, int], tuple[int, int]]:
        """
        Resolve movements between the current and next embeddings.
        解决当前嵌入和下一个嵌入之间的移动。
        
        Parameters:
        current_pos (int): The current position in the embeddings list.
        current_pos (int): 嵌入列表中的当前位置。
        
        Returns:
        str: The program for the resolved movements.
        str: 已解决移动的程序。
        movements = {
    0: [0, 0, 1, 0],  # Q0: (0,0) → (1,0)
    1: [1, 0, 0, 0],  # Q1: (1,0) → (0,0)
    2: [0, 1, 1, 1],  # Q2: (0,1) → (1,1)
    3: [1, 1, 0, 1]   # Q3: (1,1) → (0,1)
}
        """
        next_pos = current_pos + 1
        movements = get_movements(self.embeddings[current_pos], self.embeddings[next_pos])
        sorted_movements = sorted(movements.keys(), key=lambda k: math.dist(movements[k][:2], movements[k][2:]))
        violations = self.check_violations(sorted_movements, movements)
        move_sequences = self.handle_violations(violations, movements, sorted_movements, current_pos)
        return move_sequences

    def handle_violations(self, violations: list[tuple[int, int]], remained_mov_map: dict[int, tuple[int, int, int, int]], sorted_movements: list[int], current_pos: int) -> list[int, tuple[int, int], tuple[int, int]]:
        """
        Handle violations and return the movement sequence accordingly.
        处理冲突并相应地返回移动序列。
        
        Parameters:
        violations (list[tuple[int, int]]): list of violations.
        violations (list[tuple[int, int]]): 冲突列表。
        movements (dict[int, tuple[int, int, int, int]]): Movements between embeddings.
        movements (dict[int, tuple[int, int, int, int]]): 嵌入之间的移动。
        sorted_movements (list[int]): Sorted list of movements.
        sorted_movements (list[int]): 排序后的移动列表。
        current_pos (int): The current position in the embeddings list.
        current_pos (int): 嵌入列表中的当前位置。
        
        Returns:
        list[int, tuple[int, int], tuple[int, int]]: movement sequences.
        list[int, tuple[int, int], tuple[int, int]]: 移动序列。
        """
        movement_sequence =[]
        while remained_mov_map:
            remained_mov_map, violations, movement = self.solve_violations(remained_mov_map, violations, sorted_movements)
            movement_sequence.append(movement)

        return movement_sequence

    def check_violations(self, sorted_movements: list[int], remained_mov_map: dict[int, tuple[int, int, int, int]]) -> list[tuple[int, int]]:
        """
        Check for violations between movements.
        检查移动之间的冲突。
        
        Parameters:
        sorted_movements (list[int]): Sorted list of movements.
        sorted_movements (list[int]): 排序后的移动列表。
        movements (dict[int, tuple[int, int, int, int]]): Movements between embeddings.
        movements (dict[int, tuple[int, int, int, int]]): 嵌入之间的移动。
        
        Returns:
        list[tuple[int, int]]: list of violations.
        list[tuple[int, int]]: 冲突列表。
        """
        violations = []
        for i in range(len(sorted_movements)):
            for j in range(i + 1, len(sorted_movements)):
                if not compatible_2D(remained_mov_map[sorted_movements[i]], remained_mov_map[sorted_movements[j]]):
                    violations.append((sorted_movements[i], sorted_movements[j]))
        return violations

    def run(self) -> None:
        """
        Run the QuantumRouter to initialize, process embeddings.
        运行 QuantumRouter 以初始化、处理嵌入。
        """
        self.movement_list = []
        self.process_all_embeddings()