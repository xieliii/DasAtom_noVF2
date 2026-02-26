"""
generate_bench_circuits.py — 多维度基准测试电路生成器

在 Data/benchmark_circuits/ 目录下生成 ~30 个 .qasm 文件：
  - 3 种尺寸：Small (6, 8), Medium (12, 16), Large (20, 30)
  - 5 种结构：QFT, QuantumVolume, Linear, Star, Random

所有电路会被 transpile 到 {cz, h, rx, ry, rz} 基础门集。

用法：
    python generate_bench_circuits.py
"""

import os
import random as pyrandom
import numpy as np
from qiskit import QuantumCircuit, transpile
from qiskit import qasm2
from qiskit.synthesis.qft import synth_qft_full

# ==============================================================================
# 配置
# ==============================================================================
OUTPUT_DIR = os.path.join("Data", "benchmark_circuits")
QUBIT_SIZES = [6, 8, 12, 16, 20, 30]
BASIS_GATES = ['cz', 'h', 'rx', 'ry', 'rz']
RANDOM_DEPTH = 10

os.makedirs(OUTPUT_DIR, exist_ok=True)


def save_circuit(qc: QuantumCircuit, name: str):
    """Transpile to basis gates and save as .qasm file."""
    qc_t = transpile(qc, basis_gates=BASIS_GATES, optimization_level=0)
    filepath = os.path.join(OUTPUT_DIR, f"{name}.qasm")
    with open(filepath, 'w') as f:
        qasm2.dump(qc_t, f)
    gate_count = sum(1 for inst in qc_t.data if inst.operation.num_qubits == 2)
    print(f"  {name}.qasm  ({qc_t.num_qubits}q, {gate_count} 2q-gates)")


# ==============================================================================
# 电路生成器
# ==============================================================================

def gen_qft(n: int):
    """量子傅里叶变换 — 高密度/全连接"""
    qc = synth_qft_full(n)
    save_circuit(qc, f"qft_{n}")


def gen_quantum_volume(n: int):
    """
    量子体积 — 随机全连接
    用随机应用全连接双量子比特门模拟 QuantumVolume 结构
    """
    qc = QuantumCircuit(n)
    depth = max(n, 8)  # 保证足够深度
    for _ in range(depth):
        # 随机打乱比特，两两配对做随机门
        perm = list(range(n))
        pyrandom.shuffle(perm)
        for j in range(0, n - 1, 2):
            q0, q1 = perm[j], perm[j + 1]
            # 随机单量子比特门 + CZ
            qc.rx(pyrandom.uniform(0, np.pi), q0)
            qc.ry(pyrandom.uniform(0, np.pi), q1)
            qc.cz(q0, q1)
            qc.rz(pyrandom.uniform(0, np.pi), q0)
    save_circuit(qc, f"qv_{n}")


def gen_linear(n: int):
    """线性链 — 规则/局部连接：q0-q1-q2-...-qN"""
    qc = QuantumCircuit(n)
    # 多层线性链以增加电路深度
    for layer in range(max(3, n // 2)):
        for i in range(n - 1):
            qc.h(i)
            qc.cz(i, i + 1)
    save_circuit(qc, f"linear_{n}")


def gen_star(n: int):
    """星型 — q0 连接所有其他比特"""
    qc = QuantumCircuit(n)
    # 多轮星型操作以增加门数
    for layer in range(max(3, n // 2)):
        for i in range(1, n):
            qc.h(0)
            qc.cz(0, i)
            qc.rz(pyrandom.uniform(0, np.pi), i)
    save_circuit(qc, f"star_{n}")


def gen_random(n: int):
    """随机连接 — 随机选取比特对做门"""
    qc = QuantumCircuit(n)
    for _ in range(RANDOM_DEPTH):
        num_gates = pyrandom.randint(n // 2, n)
        for _ in range(num_gates):
            q0, q1 = pyrandom.sample(range(n), 2)
            # 随机单量子比特门 + CZ
            gate_choice = pyrandom.choice(['h', 'rx', 'ry', 'rz'])
            if gate_choice == 'h':
                qc.h(q0)
            elif gate_choice == 'rx':
                qc.rx(pyrandom.uniform(0, np.pi), q0)
            elif gate_choice == 'ry':
                qc.ry(pyrandom.uniform(0, np.pi), q0)
            else:
                qc.rz(pyrandom.uniform(0, np.pi), q0)
            qc.cz(q0, q1)
    save_circuit(qc, f"random_{n}")


# ==============================================================================
# 主函数
# ==============================================================================

GENERATORS = {
    'QFT':     gen_qft,
    'QV':      gen_quantum_volume,
    'Linear':  gen_linear,
    'Star':    gen_star,
    'Random':  gen_random,
}

if __name__ == "__main__":
    print(f"Generating benchmark circuits in: {OUTPUT_DIR}")
    print(f"Qubit sizes: {QUBIT_SIZES}")
    print(f"Structures: {list(GENERATORS.keys())}")
    print()

    pyrandom.seed(42)
    np.random.seed(42)

    total = 0
    for struct_name, gen_fn in GENERATORS.items():
        print(f"[{struct_name}]")
        for n in QUBIT_SIZES:
            gen_fn(n)
            total += 1
        print()

    print(f"Done! Generated {total} circuits in {OUTPUT_DIR}")
