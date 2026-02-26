"""
debug_qft8_embeddings.py — 分析 qft_8 回归案例

qft_8 是唯一一个 Dual 的移动距离比 Baseline 大的电路:
  Baseline: 24.90   Dual: 28.42   (Dual多了3.51)

分析两种方法生成的 embedding，计算各分区之间的移动距离
"""
import json
import math

def load_embeddings(path):
    with open(path) as f:
        return json.load(f)

def compute_partition_distance(emb_from, emb_to):
    """计算从一个 embedding 到下一个的总移动距离 (每个 qubit 的欧氏距离之和)"""
    total = 0
    per_qubit = []
    for i in range(len(emb_from)):
        x1, y1 = emb_from[i]
        x2, y2 = emb_to[i]
        d = math.sqrt((x2-x1)**2 + (y2-y1)**2)
        per_qubit.append((i, (x1,y1), (x2,y2), d))
        total += d
    return total, per_qubit

base_emb = load_embeddings(r'e:\coding\DasAtom_reading\DasAtom_Origin\res\baseline_benchmark\Rb2Re4\embeddings\qft_8emb.json')
dual_emb = load_embeddings(r'e:\coding\DasAtom_reading\DasAtom\res\dual_benchmark\Rb2Re4\embeddings\qft_8emb.json')

print("=" * 80)
print("qft_8 Embedding 对比分析")
print("=" * 80)

# 分区数
print(f"\nBaseline 分区数: {len(base_emb)}")
print(f"Dual 分区数: {len(dual_emb)}")

# 打印各分区的 embedding
for tag, embs in [("Baseline", base_emb), ("Dual", dual_emb)]:
    print(f"\n--- {tag} ---")
    for idx, emb in enumerate(embs):
        print(f"  Partition {idx}: {emb}")

# 计算分区间距离
print("\n\n=== 分区间移动距离分析 ===")
for tag, embs in [("Baseline", base_emb), ("Dual", dual_emb)]:
    print(f"\n--- {tag} ---")
    grand_total = 0
    for j in range(len(embs)-1):
        total, per_q = compute_partition_distance(embs[j], embs[j+1])
        grand_total += total
        print(f"  Partition {j} → {j+1}: total_dist = {total:.3f}")
        for q, src, dst, d in per_q:
            if d > 0:
                print(f"    qubit {q}: {src} → {dst}  dist={d:.3f}")
    print(f"  Grand total (sum of all qubit moves): {grand_total:.3f}")

# 但注意！compute_fidelity 用的是 all_movements (来自 QuantumRouter)
# 其中每个 movement step 取 max_dis (最远的那个qubit), 不是 sum
print("\n\n=== 注意 ===")
print("以上是 per-qubit 欧氏距离之和")
print("但实际 compute_fidelity 中，每个 movement step 取 max_dis (最远qubit的距离)")
print("所以 t_total 中的移动时间 = sum(max_dis/Move_speed)")
print("而 Total Move Distance 在 report 中 = sum(max_dis) (不是 per-qubit sum)")

# 分析 QFT-8 的门结构
print("\n\n=== qft_8 分区门列表 ===")
part_path = r'e:\coding\DasAtom_reading\DasAtom_Origin\res\baseline_benchmark\Rb2Re4\partitions\qft_8.json'
try:
    parts = load_embeddings(part_path)
    for i, p in enumerate(parts):
        print(f"  Partition {i}: {p}")
except:
    print("  (无法读取分区文件)")

part_path2 = r'e:\coding\DasAtom_reading\DasAtom\res\dual_benchmark\Rb2Re4\partitions\qft_8.json'
try:
    parts2 = load_embeddings(part_path2)
    if parts == parts2:
        print("\n  ✓ 两边的分区完全相同 (同一电路)")
    else:
        print("\n  ✗ 两边的分区不同！")
        for i, (p1, p2) in enumerate(zip(parts, parts2)):
            if p1 != p2:
                print(f"    Partition {i} differs:")
                print(f"      Baseline: {p1}")
                print(f"      Dual:     {p2}")
except:
    print("  (无法读取Dual分区文件)")

# 进一步分析：Dual 在 qft_8 中的第0层 MCTS 结果
print("\n\n=== MCTS vs VF2 初始映射对比 ===")
print(f"Baseline Layer 0 (VF2): {base_emb[0]}")
print(f"Dual     Layer 0 (MCTS): {dual_emb[0]}")

# 比较 Layer 1 的映射质量
if len(base_emb) > 1 and len(dual_emb) > 1:
    # 计算 Layer 0 → Layer 1 的 "紧凑性": 有多少 qubit 需要移动？
    print(f"\nBaseline Layer 1 (VF2): {base_emb[1]}")
    print(f"Dual     Layer 1 (Force): {dual_emb[1]}")
    
    base_moves = sum(1 for i in range(len(base_emb[0])) if base_emb[0][i] != base_emb[1][i])
    dual_moves = sum(1 for i in range(len(dual_emb[0])) if dual_emb[0][i] != dual_emb[1][i])
    print(f"\n需要移动的 qubit 数: Baseline={base_moves}, Dual={dual_moves}")
