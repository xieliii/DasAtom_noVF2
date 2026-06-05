import os
import time
import math
from openpyxl import Workbook
import warnings
from collections import Counter
from Enola.route import QuantumRouter
from DasAtom_fun import *
from mcts_mapper import mcts_initial_mapping
import argparse

class SingleFileProcessor:
    """
    A helper class responsible for processing a single QASM file. This class:
    用于处理单个 QASM 文件的辅助类。此类负责：
        - Reads the circuit from QASM.
        - 从 QASM 读取电路。
        - Computes gate lists and partitions.
        - 计算门列表和分区。
        - Retrieves/Generates embeddings.
        - 检索/生成嵌入。
        - Computes parallel gates and necessary qubit-movement operations.
        - 计算并列门和必要的量子比特移动操作。
        - Calculates fidelity and time metrics.
        - 计算保真度和时间指标。
        - Saves per-file results.
        - 保存每个文件的结果。
    """

    def __init__(
        self,
        qasm_filename: str,
        circuit_folder: str,
        benchmark_name: str,
        interaction_radius: int,
        extended_radius: int,
        result_path: str,
        embeddings_path: str,
        partitions_path: str,
        read_embeddings: bool,
        save_partitions_and_embeddings: bool,
        save_circuit_results: bool,
        save_benchmark_results: bool,
        engine: str = 'dual'
    ):
        """
        Initialize the processor with file-specific and benchmark-wide parameters.
        使用特定文件和全基准测试参数初始化处理器。

        :param qasm_filename: Name of the QASM file to process (e.g., 'circuit_14.qasm').
        :param qasm_filename: 要处理的 QASM 文件名称（例如 'circuit_14.qasm'）。
        :param circuit_folder: Directory containing the QASM file.
        :param circuit_folder: 包含 QASM 文件的目录。
        :param benchmark_name: The benchmark name (used for naming output files).
        :param benchmark_name: 基准测试名称（用于命名输出文件）。
        :param interaction_radius: The interaction radius (Rb).
        :param interaction_radius: 相互作用半径 (Rb)。
        :param extended_radius: The extended interaction radius (2 * Rb).
        :param extended_radius: 扩展相互作用半径 (2 * Rb)。
        :param result_path: Path to the parent results folder.
        :param result_path: 父结果文件夹的路径。
        :param embeddings_path: Path to the folder where embeddings are read/saved.
        :param embeddings_path: 读取/保存嵌入的文件夹路径。
        :param partitions_path: Path to the folder where partitions are read/saved.
        :param partitions_path: 读取/保存分区的文件夹路径。
        :param read_embeddings: Whether to read embeddings from existing files (instead of computing).
        :param read_embeddings: 是否从现有文件读取嵌入（而不是计算）。
        :param save_partitions_and_embeddings: Whether to save newly created partitions/embeddings to disk.
        :param save_partitions_and_embeddings: 是否将新创建的分区/嵌入保存到磁盘。
        :param save_circuit_results: Whether to save circuit-level results (xlsx).
        :param save_circuit_results: 是否保存电路级结果 (xlsx)。
        :param save_benchmark_results: Whether to save the overall benchmark-level results.
        :param save_benchmark_results: 是否保存整体基准测试级结果。
        """
        self.qasm_filename = qasm_filename
        self.circuit_folder = circuit_folder
        self.benchmark_name = benchmark_name
        self.interaction_radius = interaction_radius
        self.extended_radius = extended_radius
        self.result_path = result_path
        self.embeddings_path = embeddings_path
        self.partitions_path = partitions_path
        self.read_embeddings = read_embeddings
        self.save_partitions_and_embeddings = save_partitions_and_embeddings
        self.save_circuit_results = save_circuit_results
        self.save_benchmark_results = save_benchmark_results
        self.engine = engine

        # Used to store logs for the final XLSX per file
        # 用于存储每个文件最终 XLSX 的日志
        self.file_process_log = []

    def process_qasm_file(self):
        """
        Main entry point to process the single QASM file. This function:
        处理单个 QASM 文件的主入口点。此函数：
            1. Builds the circuit from the QASM file.
            1. 从 QASM 文件构建电路。
            2. Partitions the circuit and obtains embeddings.
            2. 对电路进行分区并获取嵌入。
            3. Generates parallel gates and qubit-movement sequences.
            3. 生成并行门和量子比特移动序列。
            4. Computes fidelity metrics.
            4. 计算保真度指标。
            5. Saves logs to a per-file Excel sheet (if configured).
            5. 将日志保存到单文件 Excel 表中（如果已配置）。

        :return: A list of metrics to be appended as a row in the main (benchmark-wide) workbook.
        :return: 一个指标列表，将作为一行附加到主（全基准测试）工作簿中。
        """
        wb = Workbook()
        ws = wb.active
        start_time = time.time()

        # 1) Create circuit from QASM, extract gate statistics and 2-qubit DAG
        # 1) 从 QASM 创建电路，提取门统计信息和 2 量子比特 DAG
        qasm_circuit = CreateCircuitFromQASM(self.qasm_filename, self.circuit_folder)
        total_gate_count = len(qasm_circuit.data)
        one_qubit_gate_count = sum(1 for ins in qasm_circuit.data if ins.operation.num_qubits == 1)
        two_qubit_gates_list = get_2q_gates_list(qasm_circuit)
        other_gate_count = total_gate_count - one_qubit_gate_count - len(two_qubit_gates_list)

        if other_gate_count > 0:
            raise ValueError(
                f"Unsupported gates (>2 qubits) in {self.qasm_filename}: {other_gate_count} gates."
            )

        if two_qubit_gates_list:
            qc_object, dag_object = gates_list_to_QC(two_qubit_gates_list)
        else:
            qc_object, dag_object = qasm_circuit, None

        # 2) Determine key architecture parameters
        # 2) 确定关键架构参数
        num_qubits, num_cz_gates, grid_size = self._compute_architecture_parameters(
            two_qubit_gates_list,
            qasm_circuit.num_qubits
        )
        full_circuit_depth = qasm_circuit.depth()
        self.file_process_log.append(["Total gates (full circuit)", total_gate_count])
        self.file_process_log.append(["1Q gates (preserved)", one_qubit_gate_count])
        self.file_process_log.append(["2Q gates (scheduled)", num_cz_gates])

        # 3) Generate coupling graph based on the interaction radius
        # 3) 基于相互作用半径生成耦合图
        coupling_graph = self._generate_coupling_graph(grid_size)

        # 4) Get or create partitions
        # 4) 获取或创建分区
        if num_cz_gates == 0:
            partitioned_gates = []
        else:
            partitioned_gates = self._retrieve_or_generate_partitions(self.qasm_filename, coupling_graph, dag_object)

        # 5) Get or create embeddings
        # 5) 获取或创建嵌入
        if num_cz_gates == 0:
            embeddings = []
        else:
            original_grid_size = grid_size
            embeddings, grid_size = self._retrieve_or_generate_embeddings(
                self.qasm_filename,
                partitioned_gates,
                coupling_graph,
                num_qubits,
                grid_size,
                dag_object
            )
            if grid_size != original_grid_size:
                coupling_graph = self._generate_coupling_graph(grid_size)

        # 6) Generate parallel gates and all movement operations
        # 6) 生成并行门和所有移动操作
        if num_cz_gates == 0:
            parallel_gates, movements_list, merged_parallel_gates = [], [], []
        else:
            strict_validate = os.environ.get("DASATOM_STRICT_VALIDATE", "0") == "1"
            if strict_validate:
                self._validate_schedule_correctness(
                    qasm_circuit=qasm_circuit,
                    original_two_qubit_gates=two_qubit_gates_list,
                    partitioned_gates=partitioned_gates,
                    embeddings=embeddings,
                    coupling_graph=coupling_graph
                )
            parallel_gates, movements_list, merged_parallel_gates = self._compute_gates_and_movements(
                num_qubits,
                partitioned_gates,
                embeddings,
                coupling_graph,
                grid_size
            )

        # 7) Compute fidelity/time metrics
        # 7) 计算保真度/时间指标
        total_time_now = time.time()
        idle_time, fidelity, move_fidelity, total_runtime, num_transfers, num_moves, total_move_distance = compute_fidelity(
            merged_parallel_gates,
            movements_list,
            num_qubits,
            num_cz_gates
        )

        # 8) Log final stats for this file
        # 8) 记录此文件的最终统计信息
        self.file_process_log.append(["Total processing time", total_time_now - start_time])
        self.file_process_log.append(["Original circuit depth", full_circuit_depth])
        self.file_process_log.append(["Fidelity", fidelity])
        self.file_process_log.append(["Idle time", idle_time])
        self.file_process_log.append(["Movement fidelity", move_fidelity])
        self.file_process_log.append(["Movement operations", len(movements_list)])
        self.file_process_log.append(["Parallel gate groups", len(merged_parallel_gates)])
        self.file_process_log.append(["Number of partitions", len(embeddings)])
        self.file_process_log.append(["Num of qubit moves (transfers)", num_transfers])
        self.file_process_log.append(["Num of final re-locations (moves)", num_moves])
        self.file_process_log.append(["Total move distance", total_move_distance])
        self.file_process_log.append(["Total run time", total_time_now - start_time])

        # 9) Optionally save a per-file XLSX
        # 9) 可选：保存单文件 XLSX
        save_file_name = os.path.join(
            self.result_path,
            f'{self.qasm_filename}_rb{self.interaction_radius:.3g}.xlsx'
        )
        for item in self.file_process_log:
            ws.append([str(v) if not isinstance(v, (int, float, str)) else v for v in item])
        if self.save_circuit_results:
            wb.save(save_file_name)

        # 10) Return the row of aggregated stats for the main (benchmark-wide) workbook
        # 10) 返回聚合统计数据的行，用于主（全基准测试）工作簿
        return [
            self.qasm_filename,
            num_qubits,
            num_cz_gates,
            full_circuit_depth,
            fidelity,
            move_fidelity,
            len(movements_list),
            num_moves * 4,           # num of transfer
            num_moves,
            total_move_distance,
            len(merged_parallel_gates),
            len(embeddings),
            (total_time_now - start_time),
            total_runtime,
            idle_time
        ]

    def _validate_schedule_correctness(
        self,
        qasm_circuit,
        original_two_qubit_gates,
        partitioned_gates,
        embeddings,
        coupling_graph
    ):
        """
        Strict correctness checks for schedule integrity:
        1) all 2Q gates are preserved (no drop / no duplicate),
        2) every embedding is complete and collision-free,
        3) every scheduled 2Q gate satisfies Rb in its partition embedding.
        """
        flattened = [tuple(g) for part in partitioned_gates for g in part]
        if len(flattened) != len(original_two_qubit_gates):
            raise RuntimeError(
                f"2Q gate count mismatch: original={len(original_two_qubit_gates)}, scheduled={len(flattened)}"
            )

        original_counter = Counter(tuple(sorted(g)) for g in original_two_qubit_gates)
        scheduled_counter = Counter(tuple(sorted(g)) for g in flattened)
        if original_counter != scheduled_counter:
            raise RuntimeError("2Q gate multiset mismatch between original circuit and scheduled partitions.")

        if len(partitioned_gates) != len(embeddings):
            raise RuntimeError(
                f"Partition/embedding length mismatch: partitions={len(partitioned_gates)}, embeddings={len(embeddings)}"
            )

        legal_nodes = {tuple(n) for n in coupling_graph.nodes()}
        for part_idx, embedding in enumerate(embeddings):
            if len(embedding) != qasm_circuit.num_qubits:
                raise RuntimeError(
                    f"Embedding length mismatch at partition {part_idx}: got={len(embedding)}, expected={qasm_circuit.num_qubits}"
                )

            occupied = set()
            for q_idx, pos in enumerate(embedding):
                if pos == -1:
                    raise RuntimeError(f"Unmapped qubit at partition {part_idx}, qubit={q_idx}.")
                pos_t = tuple(pos)
                if pos_t not in legal_nodes:
                    raise RuntimeError(f"Illegal physical location at partition {part_idx}: {pos_t}")
                if pos_t in occupied:
                    raise RuntimeError(f"Collision at partition {part_idx}: duplicated location {pos_t}")
                occupied.add(pos_t)

            for gate in partitioned_gates[part_idx]:
                u, v = gate[0], gate[1]
                dist = euclidean_distance(tuple(embedding[u]), tuple(embedding[v]))
                if dist > self.interaction_radius + 1e-9:
                    raise RuntimeError(
                        f"Rb violation at partition {part_idx}, gate={gate}, dist={dist:.6f}, Rb={self.interaction_radius}"
                    )

        one_qubit_gate_count = sum(1 for ins in qasm_circuit.data if ins.operation.num_qubits == 1)
        full_gate_count = len(qasm_circuit.data)
        if one_qubit_gate_count + len(original_two_qubit_gates) != full_gate_count:
            raise RuntimeError(
                "Full-circuit gate accounting failed: 1Q + 2Q does not match total instruction count."
            )

        self.file_process_log.append(["Strict schedule validation", "PASS"])

    def _compute_architecture_parameters(self, two_qubit_gates_list, fallback_num_qubits):
        """
        Compute the number of qubits, the number of gates, and an initial grid dimension
        for the architecture based on the QASM file's gate set.
        基于 QASM 文件的门集合，计算架构的量子比特数、门数量和初始网格尺寸。

        :param two_qubit_gates_list: The list of extracted 2-qubit gates.
        :param two_qubit_gates_list: 提取的 2 量子比特门列表。
        :param fallback_num_qubits: Number of qubits from the full circuit when no 2Q gate exists.
        :return: (num_qubits, num_cz_gates, grid_size)
        """
        num_cz_gates = len(two_qubit_gates_list)
        if two_qubit_gates_list:
            num_qubits = get_qubits_num(two_qubit_gates_list)
        else:
            num_qubits = fallback_num_qubits
        grid_size = math.ceil(math.sqrt(num_qubits))

        self.file_process_log.append(["Number of CZ gates", num_cz_gates])
        self.file_process_log.append(["Initial grid size (sqrt(num_qubits))", grid_size])
        self.file_process_log.append(["Interaction radius (Rb)", self.interaction_radius])
        self.file_process_log.append(["Extended radius (Re)", self.extended_radius])

        return num_qubits, num_cz_gates, grid_size

    def _generate_coupling_graph(self, grid_size):
        """
        Create a 2D grid-based coupling graph based on the specified grid size
        and the interaction radius.
        基于指定的网格大小和相互作用半径创建 2D 网格耦合图。

        :param grid_size: The number of rows/columns in the square grid.
        :param grid_size: 方形网格的行/列数。
        :return: A graph representing qubit coupling.
        :return: 表示量子比特耦合的图。
        """
        return generate_grid_with_Rb(grid_size, grid_size, self.interaction_radius)

    def _retrieve_or_generate_partitions(self, filename, coupling_graph, dag_object):
        """
        Retrieve precomputed partitions from JSON if read_embeddings is True,
        otherwise partition the circuit's DAG and optionally save to JSON.
        如果 read_embeddings 为 True，则从 JSON 检索预计算的分区，
        否则对电路 DAG 进行分区并可选择保存到 JSON。
        """
        if self.read_embeddings:
            return read_data(
                self.partitions_path,
                filename.removesuffix(".qasm") + '.json'
            )
        else:
            start_partition_time = time.time()
            if self.engine == 'noVF2':
                # noVF2 引擎：容量+度数贪心合并，不用 VF2
                grid_capacity = len(list(coupling_graph.nodes()))
                partitioned_gates = layer_only_partition(dag_object, grid_capacity, coupling_graph)
                self.file_process_log.append(["Partitioning method", "capacity_merge (no VF2)"])
            elif self.engine == 'dual':
                # Dual 引擎：快速贪心分层，用启发式替代 VF2
                grid_capacity = len(list(coupling_graph.nodes()))
                partitioned_gates = fast_partition(dag_object, grid_capacity, coupling_graph)
                self.file_process_log.append(["Partitioning method", "fast_partition (heuristic merge)"])
            else:
                # Baseline 引擎：保留原版的 VF2 分区
                partitioned_gates = partition_from_DAG(dag_object, coupling_graph)
                self.file_process_log.append(["Partitioning method", "VF2 partition"])
            self.file_process_log.append(["Partitioning time", time.time() - start_partition_time])

            if self.save_partitions_and_embeddings:
                write_data_json(
                    partitioned_gates,
                    self.partitions_path,
                    filename.removesuffix(".qasm") + 'part.json'
                )
            return partitioned_gates

    def _retrieve_or_generate_embeddings(
        self,
        filename,
        partitioned_gates,
        coupling_graph,
        num_qubits,
        grid_size,
        dag_object
    ):
        """
        Retrieve or compute embeddings for each partition. If read_embeddings
        is True, read from JSON. Otherwise, use MCTS for the initial mapping
        and compute remaining embeddings.
        检索或计算每个分区的嵌入。如果 read_embeddings 为 True，则从 JSON 读取。
        否则，使用 MCTS 生成初始映射，再计算后续嵌入。

        :param filename: QASM file name (string).
        :param filename: QASM 文件名（字符串）。
        :param partitioned_gates: A list of partitioned gates (from partition_from_DAG).
        :param partitioned_gates: 分区门列表（来自 partition_from_DAG）。
        :param coupling_graph: Qubit coupling graph.
        :param coupling_graph: 量子比特耦合图。
        :param num_qubits: Number of qubits in the circuit.
        :param num_qubits: 电路中的量子比特数。
        :param grid_size: Current grid dimension.
        :param grid_size: 当前网格尺寸。
        :param dag_object: DAG representation of the circuit (needed by MCTS).
        :param dag_object: 电路的 DAG 表示（MCTS 需要）。
        :return: (embeddings, potentially updated grid_size)
        :return: (嵌入, 可能更新后的网格尺寸)
        """
        if self.read_embeddings:
            embeddings = read_data(
                self.embeddings_path,
                filename.removesuffix(".qasm") + '.json'
            )
            return embeddings, grid_size
        else:
            start_embed_time = time.time()
            init_map_list = None
            verbose_init = os.environ.get("DASATOM_VERBOSE_INIT", "0") == "1"

            if self.engine in ('dual', 'noVF2'):
                # --- MCTS: 为第 0 层分区生成初始映射 ---
                # noVF2 走 MCTS-first，不再使用静态/确定性 seed 取代初始搜索。
                first_partition_size = len(partitioned_gates[0]) if partitioned_gates else 0
                fp_graph = nx.Graph()
                if partitioned_gates:
                    fp_graph.add_edges_from(partitioned_gates[0])
                fp_nodes = fp_graph.number_of_nodes()
                fp_edges = fp_graph.number_of_edges()
                fp_avg_deg = (2.0 * fp_edges / fp_nodes) if fp_nodes else 0.0
                fp_max_deg = max((d for _, d in fp_graph.degree()), default=0)
                fp_hub_like = (
                    fp_nodes >= 6 and
                    fp_avg_deg <= 2.2 and
                    fp_max_deg >= min(fp_nodes - 1, max(4, fp_nodes // 2))
                )
                if num_qubits <= 8:
                    adaptive_iterations = int(max(8, max(1, fp_nodes) + 2))
                elif num_qubits <= 10:
                    adaptive_iterations = int(max(12, int(1.5 * max(1, fp_nodes)) + max(3, first_partition_size // 3)))
                elif num_qubits <= 16:
                    adaptive_iterations = int(
                        max(
                            48,
                            min(
                                3600,
                                (num_qubits ** 2) * 5 + first_partition_size * max(1, num_qubits // 6)
                            )
                        )
                    )
                else:
                    adaptive_iterations = int(
                        max(
                            80,
                            min(
                                7000,
                                (num_qubits ** 2) * 6 + first_partition_size * max(1, num_qubits // 7)
                            )
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
                if verbose_init:
                    print(f"  [MCTS] Searching for optimal initial mapping for {filename}...")
                    print(
                        f"  [MCTS] Adaptive iterations: {adaptive_iterations} "
                        f"(qubits={num_qubits}, first_partition_gates={first_partition_size})"
                    )
                mcts_start = time.time()
                mcts_dict = mcts_initial_mapping(
                    dag_object,
                    coupling_graph,
                    grid_size,
                    interaction_radius=self.interaction_radius,
                    max_iterations=adaptive_iterations
                )
                mcts_time = time.time() - mcts_start
                if verbose_init:
                    print(f"  [MCTS] Done in {mcts_time:.2f}s, mapped {len(mcts_dict)} qubits")
                self.file_process_log.append(["Initial mapping method", "mcts_primary"])
                self.file_process_log.append(["MCTS search time", mcts_time])

                # 格式转换：MCTS 字典 {logic_qubit: (x,y)} -> 列表格式
                init_map_list = [-1] * num_qubits
                for q, pos in mcts_dict.items():
                    if q < num_qubits:
                        init_map_list[q] = pos
                embeddings, extended_positions = get_embeddings(
                    partitioned_gates,
                    coupling_graph,
                    num_qubits,
                    grid_size,
                    self.interaction_radius,
                    initial_mapping=init_map_list
                )
            else:
                # Baseline: pure VF2 embedding path (compatible with DasAtom_Origin)
                self.file_process_log.append(["MCTS search time", 0])
                embeddings, extended_positions = get_embeddings_vf2(
                    partitioned_gates,
                    coupling_graph,
                    num_qubits,
                    grid_size,
                    self.interaction_radius,
                    initial_mapping=None
                )
            self.file_process_log.append(["Embedding computation time", time.time() - start_embed_time])

            if self.save_partitions_and_embeddings:
                write_data_json(
                    embeddings,
                    self.embeddings_path,
                    filename.removesuffix(".qasm") + 'emb.json'
                )

            # If graph was extended, reflect this in the grid_size
            if extended_positions:
                self.file_process_log.append(["Graph extension count", len(extended_positions)])
                self.file_process_log.append(["Extended positions", extended_positions])
                grid_size += len(extended_positions)

            return embeddings, grid_size

    def _compute_gates_and_movements(self, num_qubits, partitioned_gates, embeddings, coupling_graph, grid_size):
        """
        Use the QuantumRouter to determine how to move qubits between partitions.
        Also compute the parallel gates for each partition based on the extended radius.
        使用 QuantumRouter 确定如何在分区之间移动量子比特。
        同时基于扩展半径计算每个分区的并行门。

        :param num_qubits: Number of qubits in the circuit.
        :param num_qubits: 电路中的量子比特数。
        :param partitioned_gates: Gates partitioned by circuit stage.
        :param partitioned_gates: 按电路阶段分区的门。
        :param embeddings: Embeddings for each partition.
        :param embeddings: 每个分区的嵌入。
        :param coupling_graph: Grid-based qubit coupling graph.
        :param coupling_graph: 基于网格的量子比特耦合图。
        :param grid_size: Dimensions of the square grid.
        :param grid_size: 方形网格的尺寸。
        :return: (list of parallel gate groups, list of all movement operations, merged list of parallel gates)
        :return: (并行门组列表, 所有移动操作列表, 合并后的并行门列表)
        """
        parallel_gate_groups = []
        movement_operations = []
        merged_parallel_gates = []

        # QuantumRouter: figure out the qubit re-locations from partition N to N+1
        # QuantumRouter: 确定从分区 N 到 N+1 的量子比特重定位
        router = QuantumRouter(
            num_qubits, embeddings, partitioned_gates, [grid_size, grid_size]
        )
        router.run()

        # Generate the parallel gates for each partition
        # 为每个分区生成并行门
        for i in range(len(partitioned_gates)):
            gates = get_parallel_gates(
                partitioned_gates[i],
                coupling_graph,
                embeddings[i],
                self.extended_radius
            )
            parallel_gate_groups.append(gates)

        # Append parallel gates and movement sequences
        # 附加并行门和移动序列
        for i in range(len(embeddings) - 1):
            # Log parallel gate group for partition i
            for g_list in parallel_gate_groups[i]:
                self.file_process_log.append([str(g) for g in g_list])
                merged_parallel_gates.append(g_list)

            # Movement from partition i to partition i+1
            for move_group in router.movement_list[i]:
                self.file_process_log.append([str(m) for m in move_group])
                movement_operations.append(move_group)

        # The last partition (which doesn't need to move to a next partition)
        # 最后一个分区（不需要移动到下一个分区）
        if len(partitioned_gates) > 0:
            self.file_process_log.append([str(embeddings[-1])])
            for g_list in parallel_gate_groups[-1]:
                self.file_process_log.append([str(g) for g in g_list])
                merged_parallel_gates.append(g_list)

        return parallel_gate_groups, movement_operations, merged_parallel_gates


class DasAtom:
    """
    Main class to handle multiple QASM files (i.e., the entire benchmark).
    处理多个 QASM 文件（即整个基准测试）的主类。
    Responsibilities:
    职责：
        - Storing benchmark-level configurations.
        - 存储基准测试级配置。
        - Iterating over all QASM files in the input directory.
        - 遍历输入目录中的所有 QASM 文件。
        - Invoking SingleFileProcessor for each QASM file.
        - 为每个 QASM 文件调用 SingleFileProcessor。
        - Maintaining a master Excel workbook of aggregated results.
        - 维护聚合结果的主 Excel 工作簿。
    """
    def __init__(
        self,
        benchmark_name: str,
        circuit_folder: str,
        interaction_radius: int = 2,
        results_folder: str = None,
        read_embeddings: bool = False,
        save_partitions_and_embeddings: bool = True,
        save_circuit_results: bool = True,
        save_benchmark_results: bool = True,
        engine: str = 'dual'
    ):
        """
        Initialize the multi-file processor with user-provided settings.
        使用用户提供的设置初始化多文件处理器。

        :param benchmark_name: Name of the benchmark (used in output naming).
        :param benchmark_name: 基准测试名称（用于输出命名）。
        :param circuit_folder: Path containing the QASM files to process.
        :param circuit_folder: 包含要处理的 QASM 文件的路径。
        :param interaction_radius: The interaction radius (Rb).
        :param interaction_radius: 相互作用半径 (Rb)。
        :param results_folder: The parent folder where results are stored (defaults to 'res/{benchmark_name}').
        :param results_folder: 存储结果的父文件夹（默认为 'res/{benchmark_name}'）。
        :param read_embeddings: If True, read existing embeddings/partitions from disk.
        :param read_embeddings: 如果为 True，从磁盘读取现有的嵌入/分区。
        :param save_partitions_and_embeddings: If True, save newly computed partitions/embeddings to JSON.
        :param save_partitions_and_embeddings: 如果为 True，将新计算的分区/嵌入保存到 JSON。
        :param save_circuit_results: If True, save per-circuit XLSX logs.
        :param save_circuit_results: 如果为 True，保存每个电路的 XLSX 日志。
        :param save_benchmark_results: If True, save a master XLSX for all circuits.
        :param save_benchmark_results: 如果为 True，为所有电路保存主 XLSX。
        :param engine: 'dual' (MCTS + force-directed) or 'baseline' (pure VF2).
        :param engine: 'dual'（MCTS + 力导向）或 'baseline'（纯 VF2）。
        """
        self.benchmark_name = benchmark_name
        self.interaction_radius = interaction_radius
        self.extended_radius = 2 * self.interaction_radius
        self.engine = engine

        assert os.path.exists(circuit_folder), f"Directory not found: {circuit_folder}"
        self.circuit_folder = circuit_folder

        # Default results folder: 'res/{engine}_benchmark'
        # 按 engine 自动分离输出目录，防止结果覆盖
        if results_folder is None:
            results_folder = f"res/{self.engine}_benchmark"
        if os.path.exists(results_folder):
            warnings.warn(
                f"The results for '{self.benchmark_name}' may be overwritten in: {results_folder}. "
                f"Consider using a different folder to preserve existing results."
            )
        self.results_folder = results_folder
        os.makedirs(self.results_folder, exist_ok=True)

        # Collect all .qasm files
        qasm_files = [f for f in os.listdir(self.circuit_folder) if f.endswith('.qasm')]
        self.qasm_files = sorted(qasm_files, key=self._extract_numeric_suffix)

        self.read_embeddings = read_embeddings
        self.save_partitions_and_embeddings = save_partitions_and_embeddings
        self.save_circuit_results = save_circuit_results
        self.save_benchmark_results = save_benchmark_results

    @staticmethod
    def _extract_numeric_suffix(filename: str):
        """
        Extract a numeric suffix from the filename for sorting.
        E.g., 'circuit_14.qasm' -> 14. If none found, return +∞ so that
        such files sort to the end.
        从文件名中提取数字后缀以便排序。
        例如，'circuit_14.qasm' -> 14。如果未找到，则返回 +∞，
        以便此类文件排在最后。

        :param filename: Filename string, e.g. 'circuit_14.qasm'.
        :param filename: 文件名字符串，例如 'circuit_14.qasm'。
        :return: An integer suffix if found, else float('inf').
        :return: 如果找到则返回整数后缀，否则返回 float('inf')。
        """
        try:
            base = filename.replace('.qasm', '')
            parts = base.split("_")[::-1]
            for part in parts:
                try:
                    return int(part)
                except ValueError:
                    continue
            return float('inf')
        except Exception:
            return float('inf')

    def modify_result_folder(self, new_folder: str):
        """
        Change the results folder if the given path does not already exist.
        Otherwise, print a warning.
        如果给定路径不存在，则更改结果文件夹。
        否则，打印警告。

        :param new_folder: The path to the new results folder.
        :param new_folder: 新结果文件夹的路径。
        """
        if not os.path.exists(new_folder):
            self.results_folder = new_folder
            os.makedirs(self.results_folder)
        else:
            print(f"Folder already exists: {new_folder}. Try using a different path.")

    def process_all_files(self, file_indices=None):
        """
        Process either all QASM files or a selected subset. Results are aggregated
        in a single Excel workbook.
        处理所有 QASM 文件或选定的子集。结果将聚合
        在一个 Excel 工作簿中。

        :param file_indices: A list of indices specifying which files to process.
        :param file_indices: 指定要处理的文件的索引列表。
        If None, process all.
        如果为 None，则处理所有文件。
        """
        # Prepare sub-folders
        # 准备子文件夹
        result_subfolder = os.path.join(self.results_folder, f"Rb{self.interaction_radius:.3g}Re{self.extended_radius:.3g}")
        embeddings_subfolder = os.path.join(result_subfolder, "embeddings")
        partitions_subfolder = os.path.join(result_subfolder, "partitions")
        os.makedirs(embeddings_subfolder, exist_ok=True)
        os.makedirs(partitions_subfolder, exist_ok=True)

        # Create a master Excel workbook for the entire benchmark
        # 为整个基准测试创建一个主 Excel 工作簿
        self.master_workbook = Workbook()
        self.master_sheet = self.master_workbook.active
        self.master_sheet.append([
            'QASM File',
            'Num Qubits',
            'Num CZ Gates',
            'Circuit Depth',
            'Fidelity',
            'Movement Fidelity',
            'Num Movement Ops',
            'Num Transferred Qubits',
            'Num Moves',
            'Total Move Distance',
            'Num Gate Cycles',
            'Num Partitions',
            'Elapsed Time (s)',
            'Total_T (from fidelity calc)',
            'Idle Time'
        ])

        # If no indices specified, process all files
        # 如果未指定索引，则处理所有文件
        if file_indices is None:
            file_indices = range(len(self.qasm_files))

        # Process each specified file
        # 处理每个指定的文件
        for idx in file_indices:
            qasm_file = self.qasm_files[idx]
            print(f"Processing: {qasm_file}")

            processor = SingleFileProcessor(
                qasm_filename=qasm_file,
                circuit_folder=self.circuit_folder,
                benchmark_name=self.benchmark_name,
                interaction_radius=self.interaction_radius,
                extended_radius=self.extended_radius,
                result_path=result_subfolder,
                embeddings_path=embeddings_subfolder,
                partitions_path=partitions_subfolder,
                read_embeddings=self.read_embeddings,
                save_partitions_and_embeddings=self.save_partitions_and_embeddings,
                save_circuit_results=self.save_circuit_results,
                save_benchmark_results=self.save_benchmark_results,
                engine=self.engine
            )

            # Returns one row of aggregated stats
            row_data = processor.process_qasm_file()
            self.master_sheet.append(row_data)

        # Optionally append global parameters at the bottom
        # 可选：在底部附加全局参数
        params_dict = set_parameters(True)
        param_log_row = []
        for key, val in params_dict.items():
            param_log_row.append(str(key))
            param_log_row.append(str(val))
        self.master_sheet.append(param_log_row)

        # Save the aggregated results if requested
        # 这请求，保存聚合结果
        if self.save_benchmark_results:
            master_file_path = os.path.join(result_subfolder, f'{self.benchmark_name}_summary.xlsx')
            self.master_workbook.save(master_file_path)


# ------------------------------------------------------------------------------
# Script entry point for command line usage
# 命令行脚本入口点
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Initialize and run the DasAtom benchmark processor.")

    parser.add_argument("benchmark_name", type=str, help="Name of the benchmark.")
    parser.add_argument("circuit_folder", type=str, help="Path to the folder containing .qasm files.")
    parser.add_argument("--interaction_radius", type=int, default= 2, help="Interaction radius (default=2).")
    parser.add_argument("--engine", type=str, choices=['baseline', 'dual', 'noVF2'], default='dual',
                        help="Engine mode: 'baseline' (pure VF2), 'dual' (MCTS + force-directed), or 'noVF2' (raw DAG layers + MCTS + force-directed). Default: dual.")
    parser.add_argument("--results_folder", type=str, help="Folder where results are stored (default: res/{engine}_benchmark).")
    parser.add_argument("--read_embeddings", action="store_true", default=False, help="Read precomputed embeddings/partitions.")
    parser.add_argument("--padused", type=bool, default=False, help="Whether to use a specialized embedding tool (not used in code).")
    parser.add_argument("--save_embeddings", action="store_true", default=True, help="Save partition/embedding JSONs (default=True).")
    parser.add_argument("--no_save_embeddings", action="store_false", dest="save_embeddings", help="Do not save partitions/embeddings.")
    parser.add_argument("--save_circuit_results", action="store_true", default=True, help="Save circuit-level XLSX logs (default=True).")
    parser.add_argument("--no_save_circuit_results", action="store_false", dest="save_circuit_results", help="Do not save circuit-level logs.")
    parser.add_argument("--save_benchmark_results", action="store_true", default=True, help="Save summary XLSX at benchmark-level (default=True).")
    parser.add_argument("--no_save_benchmark_results", action="store_false", dest="save_benchmark_results", help="Do not save summary XLSX.")

    args = parser.parse_args()

    das_atom = DasAtom(
        benchmark_name=args.benchmark_name,
        circuit_folder=args.circuit_folder,
        interaction_radius=args.interaction_radius,
        results_folder=args.results_folder,
        read_embeddings=args.read_embeddings,
        save_partitions_and_embeddings=args.save_embeddings,
        save_circuit_results=args.save_circuit_results,
        save_benchmark_results=args.save_benchmark_results,
        engine=args.engine
    )
    das_atom.process_all_files()
