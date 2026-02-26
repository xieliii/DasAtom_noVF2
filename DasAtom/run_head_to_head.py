"""
run_head_to_head.py — DasAtom 双引擎 vs 原版 自动化对比基准测试

功能：
1. 自动生成测试电路（如果不存在）
2. Baseline: 用原版代码 (DasAtom_Origin/) 跑所有电路
3. Dual:     用改版代码 (DasAtom/) 跑所有电路
4. 读取两边的 _summary.xlsx，聚合对比数据
5. 在终端打印 Markdown 表格

用法：
    python run_head_to_head.py [--interaction_radius 2] [--skip_gen] [--skip_run]

依赖：pandas, openpyxl
"""

import os
import sys
import time
import shutil
import argparse
import subprocess

# Use the same Python executable that launched this script
PYTHON = sys.executable

# 目录定义
DUAL_DIR = os.path.dirname(os.path.abspath(__file__))                        # DasAtom/
ORIGIN_DIR = os.path.join(os.path.dirname(DUAL_DIR), "DasAtom_Origin")       # DasAtom_Origin/
CIRCUIT_DIR = os.path.join(DUAL_DIR, "Data", "benchmark_circuits")
BENCHMARK_NAME = "h2h_bench"


def ensure_circuits_in_origin():
    """
    确保原版代码目录下也有测试电路文件。
    通过软链接或复制 Data/benchmark_circuits/ 到 DasAtom_Origin/Data/benchmark_circuits/
    """
    origin_circuit_dir = os.path.join(ORIGIN_DIR, "Data", "benchmark_circuits")
    if os.path.exists(origin_circuit_dir):
        return  # 已经存在

    os.makedirs(origin_circuit_dir, exist_ok=True)
    # 复制所有 .qasm 文件
    for f in os.listdir(CIRCUIT_DIR):
        if f.endswith('.qasm'):
            src = os.path.join(CIRCUIT_DIR, f)
            dst = os.path.join(origin_circuit_dir, f)
            shutil.copy2(src, dst)
    print(f"  Copied {len(os.listdir(origin_circuit_dir))} circuits to DasAtom_Origin")


def run_baseline(interaction_radius: int):
    """用原版代码 (DasAtom_Origin/) 跑 baseline"""
    print(f"\n{'='*60}")
    print(f"  Running [BASELINE] — Original DasAtom (pure VF2)")
    print(f"  Working dir: {ORIGIN_DIR}")
    print(f"{'='*60}\n")

    results_folder = os.path.join(ORIGIN_DIR, "res", "baseline_benchmark")

    cmd = [
        PYTHON, "DasAtom.py",
        BENCHMARK_NAME,
        os.path.join("Data", "benchmark_circuits"),
        "--interaction_radius", str(interaction_radius),
        "--results_folder", results_folder,
    ]

    start = time.time()
    result = subprocess.run(cmd, cwd=ORIGIN_DIR)
    elapsed = time.time() - start

    if result.returncode != 0:
        print(f"  [WARNING] Baseline exited with code {result.returncode}")
    else:
        print(f"\n  [BASELINE] completed in {elapsed:.1f}s")

    return elapsed


def run_dual(interaction_radius: int):
    """用改版代码 (DasAtom/) 跑 dual 引擎"""
    print(f"\n{'='*60}")
    print(f"  Running [DUAL] — MCTS + Force-Directed")
    print(f"  Working dir: {DUAL_DIR}")
    print(f"{'='*60}\n")

    results_folder = os.path.join(DUAL_DIR, "res", "dual_benchmark")

    cmd = [
        PYTHON, "DasAtom.py",
        BENCHMARK_NAME,
        os.path.join("Data", "benchmark_circuits"),
        "--interaction_radius", str(interaction_radius),
        "--engine", "dual",
        "--results_folder", results_folder,
    ]

    start = time.time()
    result = subprocess.run(cmd, cwd=DUAL_DIR)
    elapsed = time.time() - start

    if result.returncode != 0:
        print(f"  [WARNING] Dual exited with code {result.returncode}")
    else:
        print(f"\n  [DUAL] completed in {elapsed:.1f}s")

    return elapsed


def load_summary(engine: str, interaction_radius: int):
    """Load the _summary.xlsx from a given engine's result folder."""
    import pandas as pd

    if engine == "baseline":
        base_dir = ORIGIN_DIR
    else:
        base_dir = DUAL_DIR

    rb = interaction_radius
    re = 2 * rb
    results_dir = os.path.join(base_dir, "res", f"{engine}_benchmark")
    subfolder = os.path.join(results_dir, f"Rb{rb}Re{re}")
    summary_file = os.path.join(subfolder, f"{BENCHMARK_NAME}_summary.xlsx")

    if not os.path.exists(summary_file):
        print(f"  [ERROR] Summary file not found: {summary_file}")
        return None

    df = pd.read_excel(summary_file)
    return df


def generate_report(df_base, df_dual):
    """Generate a Markdown comparison report."""
    col_file = 'QASM File'
    col_qubits = 'Num Qubits'
    col_dist = 'Total Move Distance'
    col_fid = 'Fidelity'
    col_time = 'Elapsed Time (s)'

    # Merge on QASM File
    merged = df_base[[col_file, col_qubits, col_dist, col_fid, col_time]].merge(
        df_dual[[col_file, col_dist, col_fid, col_time]],
        on=col_file,
        suffixes=('_base', '_dual')
    )

    dist_base_col = f'{col_dist}_base'
    dist_dual_col = f'{col_dist}_dual'
    fid_base_col = f'{col_fid}_base'
    fid_dual_col = f'{col_fid}_dual'
    time_base_col = f'{col_time}_base'
    time_dual_col = f'{col_time}_dual'

    merged['Dist_Imp(%)'] = ((merged[dist_base_col] - merged[dist_dual_col])
                              / merged[dist_base_col].replace(0, float('nan')) * 100)

    # Print Markdown table
    print("\n\n## Head-to-Head: Original VF2 vs MCTS+Force-Directed\n")
    print(f"| Circuit | Qubits | Dist_Base | Dist_Dual | Dist_Imp(%) | "
          f"Fidelity_Base | Fidelity_Dual | Time_Base(s) | Time_Dual(s) |")
    print(f"|---------|--------|-----------|-----------|-------------|"
          f"--------------|---------------|--------------|--------------|")

    for _, row in merged.iterrows():
        circuit = row[col_file].replace('.qasm', '')
        qubits = int(row[col_qubits])
        d_b = row[dist_base_col]
        d_d = row[dist_dual_col]
        d_imp = row['Dist_Imp(%)']
        f_b = row[fid_base_col]
        f_d = row[fid_dual_col]
        t_b = row[time_base_col]
        t_d = row[time_dual_col]

        imp_str = f"{d_imp:+.1f}%" if not (d_imp != d_imp) else "N/A"

        print(f"| {circuit:<18} | {qubits:>6} | {d_b:>9.2f} | {d_d:>9.2f} | {imp_str:>11} | "
              f"{f_b:>12.6f} | {f_d:>13.6f} | {t_b:>12.3f} | {t_d:>12.3f} |")

    # Summary averages
    print()
    valid = merged.dropna(subset=['Dist_Imp(%)'])
    if len(valid) > 0:
        avg_imp = valid['Dist_Imp(%)'].mean()
        avg_fid_base = merged[fid_base_col].mean()
        avg_fid_dual = merged[fid_dual_col].mean()
        avg_time_base = merged[time_base_col].mean()
        avg_time_dual = merged[time_dual_col].mean()

        print(f"### Summary")
        print(f"- **Average distance reduction**: {avg_imp:+.1f}%")
        print(f"- **Average fidelity**: baseline={avg_fid_base:.6f}, dual={avg_fid_dual:.6f}")
        print(f"- **Average compile time**: baseline={avg_time_base:.3f}s, dual={avg_time_dual:.3f}s")
        print(f"- **Circuits tested**: {len(merged)}")


def main():
    parser = argparse.ArgumentParser(description="Run head-to-head benchmark: Original VF2 vs MCTS+Force-Directed")
    parser.add_argument("--interaction_radius", type=int, default=2, help="Interaction radius (default: 2)")
    parser.add_argument("--skip_gen", action="store_true", help="Skip circuit generation")
    parser.add_argument("--skip_run", action="store_true", help="Skip engine runs, only generate report")
    args = parser.parse_args()

    # Step 0: Verify DasAtom_Origin exists
    if not os.path.exists(os.path.join(ORIGIN_DIR, "DasAtom.py")):
        print(f"[ERROR] Original DasAtom not found at: {ORIGIN_DIR}")
        print(f"Clone it first: git clone https://github.com/Huangyunqi/DasAtom.git {ORIGIN_DIR}")
        sys.exit(1)

    # Step 1: Generate circuits
    if not args.skip_gen and not args.skip_run:
        print("=" * 60)
        print("  Step 1: Generating benchmark circuits")
        print("=" * 60)
        result = subprocess.run([PYTHON, "generate_bench_circuits.py"], cwd=DUAL_DIR)
        if result.returncode != 0:
            print("[ERROR] Circuit generation failed!")
            sys.exit(1)

    # Ensure circuits exist in DasAtom_Origin
    if not args.skip_run:
        ensure_circuits_in_origin()

    # Step 2: Run both engines
    if not args.skip_run:
        t_baseline = run_baseline(args.interaction_radius)
        t_dual = run_dual(args.interaction_radius)

        print(f"\n{'='*60}")
        print(f"  Total wall time: baseline={t_baseline:.1f}s, dual={t_dual:.1f}s")
        print(f"{'='*60}")

    # Step 3: Load results and generate report
    print("\n" + "=" * 60)
    print("  Step 3: Generating comparison report")
    print("=" * 60)

    try:
        import pandas as pd
    except ImportError:
        print("[ERROR] pandas is required. Install: pip install pandas")
        sys.exit(1)

    df_base = load_summary("baseline", args.interaction_radius)
    df_dual = load_summary("dual", args.interaction_radius)

    if df_base is None or df_dual is None:
        print("[ERROR] Could not load one or both summary files.")
        sys.exit(1)

    generate_report(df_base, df_dual)


if __name__ == "__main__":
    main()
