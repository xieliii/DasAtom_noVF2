# Frozen DasAtom Baseline Reuse Audit

日期：2026-08-04

## 结论

2026-07-30 在 Ryzen 7 9700X 上产生的 canonical DasAtom baseline 可以继续用于：

- DasAtom endpoint schedule 与 correctness 证据
- batch/per-atom fidelity 和 sensitivity 重算
- 同一台机器上的冻结 baseline runtime 对比

新的 Windows 实验只需运行优化后的 ForceShuttle。无需重新等待 DasAtom 的 3 小时 timeout。

该选择的限制是：新 ForceShuttle 与旧 DasAtom 并非同一批 AB/BA 交错测量。因此论文必须称其为 `same-machine frozen canonical baseline`，不能称为 simultaneous 或 interleaved measurement。

## Baseline 身份

```text
repository: https://github.com/xieliii/DasAtom_noVF2.git
branch: canonical-rerun-2026-07-29
commit: 8ad527116176a16e688dc0690680745fb72f3e8b
run: full64-20260730T053710Z
external ZIP SHA256: c990cef27b1cb1c77252cb7f82fc46d2d3c448fa7a4d90998967f9b938cd256f
```

归档状态：

```text
archive complete: true
archive paper_eligible: true
archive validation passed: true
derived validation passed: true
derived validation complete: true
logical attempts: 352
succeeded: 346
timeout: 6
```

唯一 archive warning 是一个已保留的 DasAtom 非终态 abandoned execution；最终 canonical execution 和完整性验证均通过。

## 代码一致性

将旧 run manifest 中记录的 SHA256 与当前优化分支文件逐一比较：

```text
DasAtom_Origin/: 4 files checked, 0 mismatches
canonical/: 6 files checked, 0 mismatches
canonical experiment scripts: 9 files checked, 0 mismatches
```

ForceShuttle 的 `DasAtom/` 已发生预期修改，不属于 baseline 复用条件。DasAtom baseline 执行路径仍使用未修改的 `DasAtom_Origin/`。

## 输入一致性

```text
configs/local64.txt SHA256:
bdcc9a9a9674f4b938cf64a6b0d5774e0472f1f5dc4ab5cf62f50fd7bd9c5682

64-QASM set SHA256:
f3f4cc03ca0c1ba3526409970f841e992778e0fc4801905fef1a8829d3cdd467
```

旧 run manifest 中的 64 个 filename、size 和 SHA256 与当前 `benchmarks/local64/` 一致。

## Fidelity 模型一致性

```text
configs/fidelity_models.json SHA256:
7058f3fde42158c3b2004efab1c792e12f28bc85a5fcb1bb0227dcabd05c0ec6
```

该值与旧 baseline 的 `fidelity_models.used.json.source_sha256` 一致。默认参数和全部 sensitivity 场景未改变。

## 环境证据

旧 baseline：

```text
CPU: AMD Ryzen 7 9700X
OS: Windows 11 x64
Python: CPython 3.12.10 x64
Git worktree at archive: clean
PYTHONHASHSEED: 0
OMP/BLAS/MKL/NUMEXPR/RAYON threads: 1
QISKIT_PARALLEL: FALSE
```

旧 resolved environment 包括：

```text
networkx 3.6.1
numpy 2.4.6
qiskit 2.4.1
rustworkx 0.17.1
scipy 1.17.1
```

新的 ForceShuttle-only Windows run 必须通过同一 `requirements-repro.txt` 创建 Python 3.12 环境并保存新的 resolved environment。

## 使用条件

只有以下条件全部成立，旧 DasAtom runtime 才能进入最终论文比较：

1. 新 ForceShuttle 在同一台 Ryzen 7 9700X 上运行。
2. 新 run 使用冻结 commit、相同 64 QASM、seed 0 和相同线程环境。
3. 新 ForceShuttle 每个电路运行 3 次，报告 external monotonic runtime median。
4. 新 run validator PASS、complete，且每个电路 3 次 schedule hash 一致。
5. 论文明确披露 frozen-baseline、非交错运行设计。

如果新 Windows 环境的 Python 依赖或 power plan 与旧 baseline 明显不同，runtime 复用条件失效，应补跑 DasAtom 成功完成的 58 个电路；旧 timeout 电路仍可保留原 timeout 结论。
