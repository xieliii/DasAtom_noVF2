# Windows Continuous ForceShuttle Optimization Handoff

更新时间：2026-08-04

适用对象：家中 Windows Ryzen 7 9700X 机器上的 Codex

## 1. 你现在接手的任务

继续优化 ForceShuttle，最终同时满足：

1. ForceShuttle 编译结果通过 canonical verifier，64/64 电路正确。
2. ForceShuttle 路径绝不调用 VF2，不使用 DasAtom fallback，不读取旧 schedule cache。
3. 不修改 fidelity 模型、QASM、verifier、seed 或物理参数来美化结果。
4. 相对当前 ForceShuttle 起点，任何电路、任何 fidelity sensitivity 场景都不下降。
5. 对冻结 DasAtom 基线，batch、per-atom 以及全部 sensitivity 的几何平均 fidelity 均不低于 1.0。
6. 改善 Windows 9700X 上的真实 external monotonic runtime，尤其改善小电路固定开销和 QFT 系列。
7. 所有最终论文数据来自同一台 Windows 9700X，并保留完整 raw、commit、环境和文件哈希。

不要立即运行最终 64 x 3。先建立 Windows 优化基线，然后边优化边运行目标集合。只有所有优化结束后，才运行一次最终 64 x 3。

## 2. 前因后果

早期论文把 ForceShuttle 描述得比代码能够严格证明的能力更强，并且 fidelity/transfer 指标存在口径不统一。Mac 端已经完成 canonical runner、collector、validator、fidelity sensitivity 和 frozen-baseline comparison，当前不再允许使用旧的手工汇总或混合指标。

冻结 DasAtom baseline 来自 Windows 9700X：

```text
run = full64-20260730T053710Z
external ZIP SHA256 = c990cef27b1cb1c77252cb7f82fc46d2d3c448fa7a4d90998967f9b938cd256f
```

该 baseline 已通过以下审计：

- 同一套 64 个 QASM 及其 SHA256。
- 相同 `configs/local64.txt`。
- 相同 `configs/fidelity_models.json`。
- CPython 3.12.10 x64。
- Python resolved packages 完全固定。
- Ryzen 7 9700X，高性能电源方案。
- clean Git commit，validator PASS、complete。
- DasAtom 在 58 个电路成功，在 6 个大电路达到 3 小时 timeout。

Windows 后来完成了一次 ForceShuttle-only 64 x 3：

```text
handoff ZIP SHA256 = 68a50e1c421958a76162f82fec1968cbc4171fa778a77afc8a1a619141a26ae8
executed commit = 62f43c2e73745e0bf3b7c92e0da7b39d293bd03b
192/192 succeeded
64/64 circuits schedule deterministic
validator PASS + complete
```

这批旧 ForceShuttle Windows 数据本身 paper-eligible，但它不是当前优化起点。它与 DasAtom 的结果是：

```text
58 comparable circuits
runtime geometric speedup (DasAtom / ForceShuttle) = 1.1074288559x
runtime median ratio = 0.8970264355x
runtime wins/ties/losses = 15/0/43
batch fidelity GM = 1.0330280393x
per-atom fidelity GM = 1.0411697624x
worst sensitivity GM = 1.0058862878x
```

解释：ForceShuttle 的运行时间几何平均更快，但在多数单独电路上稍慢；优势主要来自少量 DasAtom 极慢电路和 6 个 DasAtom timeout 电路。后续优化应重点改善多数小电路和 QFT，而不是继续扩大少量 outlier 的优势。

Mac 端随后完成了选择性 prefix failure 优化。当前正式起点是：

```text
repository = git@github.com:xieliii/DasAtom_noVF2.git
branch = fidelity-optimization-2026-08-04
start commit = feedfa980ff740de12f49a08fbb204fde8213747
```

该版本在 Mac 上已经证明：

```text
42 tests passed
64/64 canonical verifier PASS
1280 fidelity/sensitivity comparisons against the previous ForceShuttle:
  120 improved
  1160 unchanged
  0 worse
```

它只改变 6 个大规模电路的物理结果，并保留 QFT 和其他电路的旧行为。它尚未在 Windows 9700X 上做最终 64 x 3，所以 Windows 优化必须从这个 commit 开始。

## 3. 绝对禁止

不得：

1. 在 ForceShuttle 路径调用 `rx.vf2_mapping`、`rx_is_subgraph_iso` 或任何 VF2 包装器。
2. 调用 DasAtom baseline 生成 ForceShuttle embedding。
3. 遇到搜索失败时回退到 DasAtom、VF2 或预先保存的 schedule。
4. 修改 `configs/fidelity_models.json` 来提升 score。
5. 放宽 canonical verifier、忽略 Rb violation、跳过 gate occurrence 或依赖顺序检查。
6. 手工修改 raw JSON、CSV、status、schedule hash 或 validation 文件。
7. 使用 QASM 文件名硬编码算法行为，例如 `if filename == "qft_40.qasm"`。
8. 只挑有利电路或 sensitivity 场景报告。
9. 将 Mac runtime 与 Windows DasAtom runtime 组合成论文 runtime 结论。
10. 在 dirty worktree 上启动 canonical runner。
11. 覆盖或删除旧 DasAtom baseline、旧 ForceShuttle handoff 或已归档结果。
12. 直接向 `fidelity-optimization-2026-08-04` 分支 force-push 实验代码。

## 4. 建立独立 Windows 优化工作区

使用新的 ASCII 路径，不要复用 `D:\ForceShuttleFinal`：

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoUrl = "git@github.com:xieliii/DasAtom_noVF2.git"
$StartCommit = "feedfa980ff740de12f49a08fbb204fde8213747"
$WorkRoot = "D:\ForceShuttleOptimize"
$OptimizationBranch = "windows-continuous-optimization-2026-08-04"

if (Test-Path -LiteralPath $WorkRoot) {
    throw "WorkRoot already exists. Use a new empty ASCII path."
}

git -c core.autocrlf=false clone $RepoUrl $WorkRoot
Set-Location -LiteralPath $WorkRoot
git config --local core.autocrlf false
git config --local core.eol lf
git config --local core.safecrlf true
git config --local core.longpaths true
git checkout --detach $StartCommit

if ((git rev-parse HEAD).Trim() -ne $StartCommit) {
    throw "Start commit mismatch."
}
if (@(git status --porcelain).Count -ne 0) {
    throw "Fresh checkout is dirty."
}

git switch -c $OptimizationBranch
```

SSH 失败时只允许将 URL 改成：

```text
https://github.com/xieliii/DasAtom_noVF2.git
```

不要从旧目录复制源码。只允许复制冻结 baseline 结果用于只读比较。

## 5. Python 和确定性环境

```powershell
py -3.12 -c "import struct,sys; print(sys.version); assert sys.version_info[:2] == (3,12); assert struct.calcsize('P')*8 == 64"
py -3.12 -m venv .venv-opt
$Python = Join-Path $WorkRoot ".venv-opt\Scripts\python.exe"

& $Python -m pip install --no-cache-dir -r requirements-repro.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
& $Python -m pip check
if ($LASTEXITCODE -ne 0) { throw "pip check failed." }

$env:PYTHONHASHSEED = "0"
$env:OMP_NUM_THREADS = "1"
$env:OPENBLAS_NUM_THREADS = "1"
$env:MKL_NUM_THREADS = "1"
$env:NUMEXPR_NUM_THREADS = "1"
$env:VECLIB_MAXIMUM_THREADS = "1"
$env:BLIS_NUM_THREADS = "1"
$env:RAYON_NUM_THREADS = "1"
$env:QISKIT_PARALLEL = "FALSE"
```

始终使用 `$Python`，不要依赖激活虚拟环境后的隐式 `python`。

## 6. 起点门禁

```powershell
& $Python -m py_compile `
    DasAtom\DasAtom.py `
    DasAtom\DasAtom_fun.py `
    DasAtom\mcts_mapper.py `
    DasAtom\analytical_placer.py `
    scripts\run_compiler_once.py `
    scripts\run_canonical_pairwise.py `
    scripts\collect_canonical_results.py `
    scripts\validate_canonical_run.py `
    scripts\recompute_fidelity.py `
    scripts\compare_frozen_baseline.py
if ($LASTEXITCODE -ne 0) { throw "py_compile failed." }

& $Python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "tests failed." }

git diff --check
if ($LASTEXITCODE -ne 0) { throw "git diff --check failed." }
```

预期至少 `42 passed`。如果起点测试失败，不要优化，先报告完整错误。

## 7. 建立 feedfa9 Windows 优化基线

先对起点 commit 运行 64 电路一次。该结果只用于 Windows 同机优化 A/B，不是最终论文结果：

```powershell
$BaselineRun = Join-Path $WorkRoot "results\windows-opt-baseline-feedfa9-full64"

& $Python scripts\run_canonical_pairwise.py `
    --run-dir $BaselineRun `
    --benchmark-dir "benchmarks\local64" `
    --list "configs\local64.txt" `
    --methods forceshuttle `
    --repetitions 1 `
    --timeout-sec 10800 `
    --seed 0 `
    --fail-fast

& $Python scripts\collect_canonical_results.py --run-dir $BaselineRun
& $Python scripts\validate_canonical_run.py `
    --run-dir $BaselineRun `
    --require-complete `
    --json-out (Join-Path $BaselineRun "derived\validation.json")
& $Python scripts\recompute_fidelity.py `
    --run-dir $BaselineRun `
    --config "configs\fidelity_models.json"
```

要求 64/64 succeeded、validator PASS、complete。保存该目录，不要覆盖。

## 8. 当前代码结构和已完成优化

核心路径：

```text
DasAtom/DasAtom.py
DasAtom/DasAtom_fun.py
DasAtom/mcts_mapper.py
DasAtom/analytical_placer.py
DasAtom/Enola/route.py
canonical/adapter.py
canonical/verifier.py
```

当前已有：

1. 无 VF2 dependency-safe prefix local search。
2. min-conflicts 的距离矩阵、Rb excess matrix、incident edge 和 swap-affected edge 增量评分。
3. deterministic integer-quantized candidate ordering。
4. 对合法 embedding 的 future pressure、movement churn、最大距离和总距离排序。
5. 小稠密电路的 dependency-prefix 搜索，避免重复 binary split。
6. 对至少 30 qubit、总 2Q gate 不超过 500、unique-interaction 为主的电路缩短失败搜索预算。
7. 重复交互比例较高的 QFT 类 prefix 保留完整搜索预算，避免 batch fidelity 回退。

不要重新实现这些功能。先 profile，再针对剩余热点优化。

## 9. 优化优先级

### Priority A: 不改变 schedule 的低风险优化

优先做这些，因为它们可以改善速度而不改变 fidelity：

1. 使用 `cProfile` 和 `-X importtime` 分析小电路固定启动成本。
2. 将只在特定路径需要的重量级模块改为 lazy import，但不得改变随机数初始化顺序。
3. 缓存相同 `(all_nodes, Rb)` 的 distance matrix、excess matrix、node index。
4. 在同一次 dependency-prefix 搜索中复用 nested prefix 的 unique edges 和 incident tables。
5. 避免重复执行 QASM-to-DAG、dependency layer 和相同 topology 统计。
6. 预绑定热点循环中的局部变量或使用等价的数据结构，但必须验证 schedule hash 不变。

注意：之前尝试用 streaming top-8 取代完整 candidate sort，虽然正确性通过，但 schedule 改变且没有稳定加速，因此已经撤销。不要未经证据重复该方案。

### Priority B: QFT/repeated-interaction 搜索加速

旧 Windows 数据中 QFT 是主要速度弱点：

```text
qft_30 speed ratio about 0.160x
qft_24 about 0.196x
qft_40 about 0.236x
```

可尝试：

1. 相邻 nested prefix 之间复用成功 mapping 作为 seed。
2. 复用 prefix search context，而不是每个 prefix 重建全部矩阵和索引。
3. 使用严格必要条件提前拒绝不可能 embedding 的 prefix，例如 degree-demand/supply；必要条件不得产生 false negative。
4. 为 repeated-interaction topology 构造通用 deterministic seed，不得检查 benchmark 文件名。
5. 缓存同一搜索中的失败和成功 prefix，但不能把 heuristic failure 当作数学不可嵌入证明。

禁止简单把 QFT 的 `max_steps` 从 1050 降到 420。Mac 已验证该做法使 `qft_40` batch fidelity 下降约 23%。

### Priority C: 小电路固定开销

旧 Windows 结果中 ForceShuttle 在 43/58 个可比较电路上较慢，很多差异来自进程启动、模块 import、MCTS 初始化等固定开销。

允许优化编译器本身的 import 和初始化成本。不要改变 canonical runtime 协议。若改成 persistent worker，则 ForceShuttle 和 DasAtom 都必须按同一新协议重新运行；在未准备重跑 DasAtom 前，不要采用这种方法作为论文 runtime。

### Priority D: mapping/fidelity 改进

只有 A-C 充分完成后才进行。任何新 mapping heuristic 必须满足：

- 当前分区全部 Rb-valid。
- dependency order 不变。
- 1280 sensitivity 无单项回退。
- 运行时间也有实际改善。

不要用更多候选搜索换取很小 fidelity 提升，导致整体速度优势消失。

## 10. 每个候选的 Git 纪律

Canonical runner 拒绝 dirty worktree。因此每个候选必须：

1. 只修改必要文件。
2. 运行 `py_compile`、`pytest`、`git diff --check`。
3. 提交为一个有明确说明的 Git commit。
4. 在 clean commit 上运行实验。
5. 记录 commit SHA 和结果目录。

建议提交格式：

```text
Optimize <specific hotspot> without schedule changes
Accelerate repeated-prefix context reuse
Reduce ForceShuttle import-time overhead
```

候选失败时，不要删除证据。可以从最后一个通过门禁的 commit 新建下一条 candidate branch，或使用 `git revert`。不要 force-push 已共享的结果 commit。

## 11. 分级实验流程

### Gate 1: 代码测试

每次改动后：

```powershell
& $Python -m py_compile DasAtom\DasAtom.py DasAtom\DasAtom_fun.py DasAtom\mcts_mapper.py DasAtom\analytical_placer.py
& $Python -m pytest -q
git diff --check
```

### Gate 2: 单电路 profiler

至少 profile：

```text
qv_8.qasm       small/startup
qft_30.qasm     repeated-interaction hotspot
random_40.qasm  unique-interaction hotspot
```

示例：

```powershell
& $Python -m cProfile `
    -o (Join-Path $WorkRoot "profiles\qft30.prof") `
    scripts\run_compiler_once.py `
    --method forceshuttle `
    --qasm "benchmarks\local64\qft_30.qasm" `
    --output (Join-Path $WorkRoot "profiles\qft30.json") `
    --seed 0
```

检查 `verification_passed=true`。使用 `pstats` 比较 cumulative time，不要只看一次 wall time。

### Gate 3: 目标回归集合

仓库已有：

```text
configs/fast_failure_regressions.txt
configs/fidelity_regressions.txt
configs/speed_regressions.txt
```

还应维护一个 QFT 集合，包括：

```text
qft_10.qasm
qft_12.qasm
qft_14.qasm
qft_16.qasm
qft_18.qasm
qft_20.qasm
qft_24.qasm
qft_30.qasm
qft_40.qasm
```

目标集合先跑 1 repetition。候选必须：

- 全部 verifier PASS。
- 与 Windows `feedfa9` baseline 对比，没有 fidelity sensitivity 回退。
- 修改目标电路的 runtime 有可重复改善。
- 未修改目标电路的 schedule hash 应保持一致。

### Gate 4: full64 one repetition

候选通过目标集合后，运行 64 x 1：

```powershell
$CandidateSha = (git rev-parse HEAD).Trim()
$CandidateRun = Join-Path $WorkRoot ("results\candidate-" + $CandidateSha.Substring(0,12) + "-full64-r1")

& $Python scripts\run_canonical_pairwise.py `
    --run-dir $CandidateRun `
    --benchmark-dir "benchmarks\local64" `
    --list "configs\local64.txt" `
    --methods forceshuttle `
    --repetitions 1 `
    --timeout-sec 10800 `
    --seed 0 `
    --fail-fast

& $Python scripts\collect_canonical_results.py --run-dir $CandidateRun
& $Python scripts\validate_canonical_run.py `
    --run-dir $CandidateRun `
    --require-complete `
    --json-out (Join-Path $CandidateRun "derived\validation.json")
& $Python scripts\recompute_fidelity.py `
    --run-dir $CandidateRun `
    --config "configs\fidelity_models.json"
```

不得因为单次 runtime 波动直接接受候选。对发生变化或接近门槛的电路补跑 3 次。

## 12. 候选接受门槛

相对 Windows `feedfa9` baseline：

### 必须满足

1. 64/64 succeeded，validator PASS、complete。
2. 无 VF2、无 fallback、无 cache schedule。
3. 所有发生变化的电路 schedule 三次 deterministic。
4. 1280 个 ForceShuttle fidelity/sensitivity 值：`0 worse`。
5. 未受算法路径影响的电路 physical metrics 和 schedule hash 不变。
6. 目标热点的三次 runtime median 改善，不以单次偶然值判断。
7. full64 runtime geometric mean 不下降。

### 期望改善

1. 对冻结 DasAtom 的 58 电路 runtime geometric speedup 高于旧值 `1.1074x`。
2. runtime median ratio 从 `0.8970x` 提升到至少 `1.0x`，或明显接近 1.0。
3. wins 从 `15/58` 明显增加。
4. 保持 batch fidelity GM 不低于约 `1.033x`。
5. 保持 per-atom fidelity GM 不低于约 `1.041x`。
6. 最差 sensitivity GM 保持大于 1.0。

不要通过牺牲 fidelity 或 correctness 达成速度目标。

## 13. 与冻结 DasAtom 的迭代比较

在优化阶段，candidate 只有 1 repetition 时可进行 diagnostic comparison：

```powershell
& $Python scripts\compare_frozen_baseline.py `
    --candidate-run-dir $CandidateRun `
    --baseline-run-dir "<冻结 DasAtom full64-20260730T053710Z 的目录>" `
    --output-dir (Join-Path $CandidateRun "derived\frozen_baseline") `
    --diagnostic-allow-one-repetition
```

因为 candidate 和 baseline 都来自同一台 Windows 主机，不应使用 `--allow-host-mismatch`。Diagnostic one-repetition 输出不是 paper-eligible，仅用于筛选候选。

最终 64 x 3 后，不加任何 diagnostic 参数：

```powershell
& $Python scripts\compare_frozen_baseline.py `
    --candidate-run-dir $FinalRun `
    --baseline-run-dir "<冻结 DasAtom full64-20260730T053710Z 的目录>" `
    --output-dir (Join-Path $FinalRun "derived\frozen_baseline")
```

最终必须显示：

```text
comparison passed: True
paper eligible: True
matched circuits: 58
```

## 14. 最终运行

只有确认不再继续改代码后：

1. Push Windows optimization branch。
2. 记录完整 40 位 final SHA。
3. 从 clean commit 建立新的最终 run directory。
4. 先 `--repetitions 1`，再同目录 `--resume --repetitions 3`。
5. collector、validator、fidelity、frozen-baseline comparison 全部执行。
6. 保存 environment、CPU、power plan、pip freeze、git state。
7. 使用 `archive_canonical_run.py` 归档并生成 ZIP SHA256。

不要将优化期间的不同 commit 结果拼成一个 final run。

## 15. 最终报告必须回答

创建 `WINDOWS_OPTIMIZATION_REPORT.md`，至少包含：

1. 起始 SHA `feedfa9...` 和最终 SHA。
2. 每个保留优化的原因、修改文件和 commit。
3. 每个被淘汰方案及淘汰原因。
4. profiler before/after。
5. 42+ tests 的最终输出。
6. final 64 x 3 success、timeout、failure 数量。
7. schedule determinism。
8. 相对 `feedfa9` 的 runtime 和 fidelity 差异。
9. 相对冻结 DasAtom 的 58 电路：
   - runtime GM、median、wins/ties/losses
   - batch fidelity GM
   - per-atom fidelity GM
   - worst sensitivity GM
10. DasAtom timeout 的 6 个电路和 ForceShuttle runtime。
11. 已知限制，包括 continuous motion 尚未验证、fidelity 是模型 proxy。
12. final archive SHA256。

## 16. 回传给 Mac

回传：

1. final canonical ZIP
2. ZIP `.sha256`
3. `WINDOWS_OPTIMIZATION_REPORT.md`
4. final full 40 位 commit SHA
5. final branch 名称
6. `derived/validation.json`
7. `derived/summary.csv`
8. `derived/fidelity/fidelity_summary.csv`
9. `derived/frozen_baseline/frozen_baseline_comparison.json`
10. profiler before/after 和所有 warning/error

Mac Codex 将独立复核 ZIP 哈希、commit、source snapshot、QASM、validator、fidelity、runtime 和论文表格。Windows Codex 不要直接修改论文数字。

## 17. 当前决策

暂停让用户运行 `feedfa9` 最终测试。你现在负责从 `feedfa9` 继续优化，并在优化全部完成后只给用户一个最终 SHA。不要在每次小改动后要求用户手工介入；自行完成 profile、实现、目标回归、full64 one-repetition 和候选淘汰。只有需要外部 baseline 路径、权限或出现无法恢复的环境问题时才询问用户。
