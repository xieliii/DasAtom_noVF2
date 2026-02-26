"""
力导向解析布局模块 (analytical_placer.py) — 纯血版

该模块实现基于二次型弹簧模型（Quadratic Placement）的中间层映射。
核心思想：把原子当作质点，用弹簧连接（门引力 + 惯性力），
解一个线性方程组得到理想浮点坐标，再用 **贪心度数吸附** 到合法网格点。

两步走：
1. 全局解析 (Global Placement): 解 Ax=b 得浮点坐标
2. 贪心吸附 (Greedy Snap): 按门度数降序，优先保障核心比特就近落座

设计原则：
- 不检查 Rb 合法性 — 排成什么样就什么样
- 不回退 VF2 — 信任力导向的趋势
- 剩下的瑕疵交给 QuantumRouter 去搬运

日期：2026-02-23
"""

import numpy as np


def force_directed_mapping(current_gates, prev_mapping, all_grid_nodes,
                           Rb, num_qubits):
    """
    纯粹的力导向映射：解二次型方程 + 贪心度数吸附。

    物理模型：
    - 门引力 E_gate = Σ w_gate * [(xi-xj)² + (yi-yj)²]  让有门的原子互相拉近
    - 惯性力 E_anchor = Σ w_anchor * [(xi-xi_old)² + (yi-yi_old)²]  把原子拴在上一层位置

    令 ∂E/∂x = 0 → 得到线性方程组 Ax = b，一步求解。

    Args:
        current_gates: 当前层的门列表 [[q0, q1], [q2, q3], ...]
        prev_mapping: 上一层的映射列表 [(x,y), (x,y), -1, ...] (长度 num_qubits)
        all_grid_nodes: 物理网格的所有合法节点列表 [(0,0), (0,1), ...]
        Rb: 相互作用半径（保留接口，但不再用于合法性检查）
        num_qubits: 逻辑量子比特总数

    Returns:
        new_mapping: 列表格式 [(x,y), ...] 长度 num_qubits，所有比特都有位置，无 -1
    """
    w_gate = 1.0
    w_anchor = 0.5

    # ========================================
    # 第一步：构建线性方程组 A·X = Bx, A·Y = By
    # ========================================
    A = np.zeros((num_qubits, num_qubits))
    Bx = np.zeros(num_qubits)
    By = np.zeros(num_qubits)

    # 计算网格中心（用于游离比特的微弱向心力）
    center_x = sum(n[0] for n in all_grid_nodes) / len(all_grid_nodes)
    center_y = sum(n[1] for n in all_grid_nodes) / len(all_grid_nodes)

    # --- 惯性力 (Anchor Force) ---
    for i in range(num_qubits):
        if prev_mapping[i] != -1:
            old_x, old_y = prev_mapping[i]
            A[i, i] += w_anchor
            Bx[i] += w_anchor * old_x
            By[i] += w_anchor * old_y
        else:
            # 游离比特：加微弱向心力拉向网格中心，防止矩阵奇异
            epsilon = 0.01
            A[i, i] += epsilon
            Bx[i] += epsilon * center_x
            By[i] += epsilon * center_y

    # --- 门引力 (Gate Force) + 统计度数 ---
    degrees = {i: 0 for i in range(num_qubits)}
    for gate in current_gates:
        u, v = gate[0], gate[1]
        degrees[u] += 1
        degrees[v] += 1
        # 拉普拉斯矩阵构造
        A[u, u] += w_gate
        A[v, v] += w_gate
        A[u, v] -= w_gate
        A[v, u] -= w_gate

    # ========================================
    # 第二步：求解方程，得到浮点理想坐标
    # ========================================
    try:
        X_opt = np.linalg.solve(A, Bx)
        Y_opt = np.linalg.solve(A, By)
    except np.linalg.LinAlgError:
        # 矩阵奇异 → 加正则化重试
        A += np.eye(num_qubits) * 1e-4
        X_opt = np.linalg.solve(A, Bx)
        Y_opt = np.linalg.solve(A, By)

    # ========================================
    # 第三步：贪心度数吸附 (Greedy Snap)
    # ========================================
    # 按门度数降序排列：当前层参与门越多的比特，越优先选座！
    # 这样核心比特能抢到离理想位置最近的格点，边缘比特随便放。
    new_mapping = [-1] * num_qubits
    available_nodes = set(all_grid_nodes)

    sorted_qubits = sorted(
        range(num_qubits),
        key=lambda q: degrees[q],
        reverse=True
    )

    for q in sorted_qubits:
        ideal_pos = (X_opt[q], Y_opt[q])
        # 找离理想位置最近的空位
        best_node = min(
            available_nodes,
            key=lambda n: (n[0] - ideal_pos[0]) ** 2 + (n[1] - ideal_pos[1]) ** 2
        )
        new_mapping[q] = best_node
        available_nodes.remove(best_node)

    # 不返回 is_valid — 排成什么样就什么样，交给 Router！
    return new_mapping
