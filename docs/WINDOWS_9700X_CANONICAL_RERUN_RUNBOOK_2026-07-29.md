# ForceShuttle Windows 9700X Canonical Rerun Runbook

更新时间：2026-08-04

适用对象：家中 Windows 实验机上的 Codex

目标分支：`fidelity-optimization-2026-08-04`

正式代码 commit：由 Mac 端最终交接消息单独提供的 40 位 Git SHA

## 0. 这是强制执行规范

Windows Codex 必须从头到尾阅读本文件后再执行。不要跳过环境冻结、测试、smoke、pilot、validator 或归档步骤。

本任务只允许：

1. 从指定 GitHub 仓库克隆指定分支和精确 commit。
2. 创建 Python 3.12 虚拟环境并安装锁定依赖。
3. 运行仓库已经提供的测试、runner、collector、validator、sensitivity 和 archive 工具。
4. 在被 `.gitignore` 忽略的 `results/` 目录下写实验输出、环境记录、本地状态和日志。
5. 读取日志、判断门禁、恢复中断运行和打包结果。

本任务禁止：

1. 修改 `DasAtom/`、`DasAtom_Origin/`、`canonical/`、`scripts/`、`tests/` 或任何其他受 Git 跟踪的文件。
2. 为了让测试通过而放宽 verifier、删除测试或手工修改 JSON/CSV/status。
3. `git commit`、`git push`、`git pull`、merge、rebase、cherry-pick；除第 5 节首次 `checkout --detach` 正式 SHA 外，不得再切换 commit。
4. 从旧目录 `D:\DasAtom_noVF2`、旧 Excel、旧 `res/`、旧 `runs/` 或论文 expected CSV 导入结果。
5. 并行运行 ForceShuttle 和 DasAtom，或同时启动两个 canonical runner。
6. 人工把没有实际跑满 10800 秒的任务标成 timeout。
7. 在正式运行途中改变 seed、Python、依赖、代码 commit、benchmark list 或硬件参数。
8. 运行 Enola、Atomique、Q-Tetris 或其他编译器。

如果任何受 Git 跟踪的文件发生改变，立即停止，不要自行修复或提交。保存 `git status --short` 并报告给 Mac 端。

## 1. 前因后果

旧论文的核心比较是 ForceShuttle 对原始 DasAtom。旧结果存在以下可审计性问题：

- 旧 runner 用 XLSX 是否存在判断成功，可能跳过旧输出。
- 旧 runner 没有为每个 attempt 保存完整 command、hash、结构化状态和 verifier 报告。
- ForceShuttle 与 DasAtom 的 transfer-like 指标存在 batch 与 per-atom 两种口径。
- 旧 fidelity 数值把这些口径混在一起，必须从新的 raw endpoint 数据重算。
- 旧运行没有为所有成功项留下独立 endpoint-layout correctness 证据。
- 毫秒级内部 runtime 需要 monotonic timer，并通过固定环境和顺序执行降低测量噪声。

本次不是为了强行复现旧论文中的漂亮数字，也不是让新结果匹配旧的 `64/64`、`58/64` 或旧 fidelity。新的 canonical rerun 必须接受真实结果，包括 runtime 变慢、指标变化、编译失败或真实 timeout。

本次只重跑：

```text
ForceShuttle
DasAtom_Origin
```

本次不重跑：

```text
Enola
Atomique
Q-Tetris
```

Enola 和 Atomique 完全 out of scope。不要寻找它们的旧目录，不要修改它们的旧状态，也不要把它们混入新 summary。

当前目标分支已经包含两类经过 Mac 端完整 64 电路验证的 ForceShuttle 修改：

1. 用确定性的无 VF2 dependency-prefix local search 减少不必要分区与 transfer。
2. 对 min-conflicts 候选代价采用等价的增量计算与交换边缓存，在不改变分区、transfer、距离或 fidelity 指标的前提下减少运行时间。

这些修改不允许 Windows 端继续调整。Windows 任务只是从冻结 commit 做同机 ForceShuttle/DasAtom 最终测量。

仓库中的两套实现仍会 import 各自目录下的 `Enola/route.py`。这里的 `route.py` 只是 ForceShuttle 和 DasAtom 内部复用的 endpoint movement batching helper。看到该文件被 import 或执行不表示运行了 Enola compiler baseline，不要删除、替换或禁用这个 helper。Atomique 在本轮代码路径中完全不涉及。

## 2. 本次能够证明和不能证明什么

本次采用的验证范围固定为：

```text
scope.name = endpoint_layout_v1
scope.continuous_motion_verified = false
scope.one_qubit_policy = counted_not_scheduled
```

本次能够验证：

1. Canonicalized 2Q interaction operation 没有丢失、重复或违反每个 qubit 上的依赖顺序。
2. 每个 partition 的 endpoint mapping 对 active 2Q qubit 完整且 injective。
3. endpoint 坐标在报告的网格范围内。
4. 每个 partition 中的 2Q operation 满足 interaction radius `Rb=2`。
5. 每个 parallel group 的 operation coverage、operand-disjoint 和采用的 `Re=4` 分组规则成立。
6. 相邻 endpoint layout 之间的 changed atoms、batch grouping 和距离指标能从公开 raw JSON 重算。
7. 运行状态、输入 QASM、代码 commit、seed、命令和结果文件 hash 一致。

本次不验证：

1. 连续时间物理轨迹无碰撞。
2. AOD/SLM 的完整控制约束。
3. 满阵列 permutation 一定存在物理 buffer path。
4. 当前 batching proxy 是硬件可直接执行的 movement waveform。
5. 1Q gate 的调度时间和 error。

因此日志或报告中的 transition 数据只能解释为 endpoint transition proxy，不能解释成完整物理 movement schedule。

## 3. 权威来源

权威 GitHub 仓库：

```text
git@github.com:xieliii/DasAtom_noVF2.git
```

权威分支：

```text
fidelity-optimization-2026-08-04
```

权威 commit 不直接写死在这份受 Git 跟踪的文件中，而是由 Mac 端在代码完成、测试通过并推送后，通过最终交接消息单独提供一个 40 位 Git SHA。原因是 commit 不能在同一次提交中包含它自身的最终 SHA；把 SHA 再写回本文件会产生另一个 commit，并使原 SHA 失效。

Windows Codex 在开始前必须同时拿到：

```text
branch = fidelity-optimization-2026-08-04
commit = Mac 端最终交接消息中的 40 位 SHA
```

若最终交接消息没有明确给出 40 位 SHA，禁止猜测、禁止直接使用当时的 branch HEAD，直接报告“正式 commit 尚未冻结”。该 SHA 只写入 PowerShell 变量和被 `.gitignore` 忽略的 `results\control\source_lock.json`，不得为了填 SHA 而修改本文件。

唯一 benchmark 来源：

```text
benchmarks\local64\
configs\local64.txt
```

不要从 `DasAtom\Data\` 或 `DasAtom_Origin\Data\` 递归搜索同名 QASM。那些目录只属于历史代码树，不是本次 canonical input。

Canonical input 的字节级固定值：

```text
configs/local64.txt SHA256:
bdcc9a9a9674f4b938cf64a6b0d5774e0472f1f5dc4ab5cf62f50fd7bd9c5682

64-QASM set SHA256:
f3f4cc03ca0c1ba3526409970f841e992778e0fc4801905fef1a8829d3cdd467
```

QASM set hash 的定义是：按 filename ordinal/ASCII 升序，对每个文件形成 `filename<TAB>lowercase_sha256<LF>`，将 64 行拼接为 ASCII bytes 后再做 SHA256。Canonical QASM 和 `configs/local64.txt` 原始为 CRLF；`.gitattributes` 将它们标为 `-text`，禁止 Git 换行归一化。

## 4. Windows 机器要求

正式机器必须是论文原实验机：

```text
CPU: AMD Ryzen 7 9700X
OS: Windows 10/11 64-bit
Python: CPython 3.12 x64
Storage: 本机 NTFS SSD/NVMe
Power: 接通交流电
```

运行期间：

- 不运行游戏、视频转码、其他编译、其他量子实验或重负载应用。
- 不从 OneDrive、微信临时目录、网络盘、中文路径或带空格的深层目录运行。
- 可以锁屏，但不能注销、关机、重启或让机器睡眠。
- 确认 Windows Update 不会在未来至少 24 小时内强制重启。
- 不需要关闭 Windows Defender。若已有企业安全策略，只记录，不要绕过。
- 不要手工调整 CPU 超频、PBO、核心亲和性或进程优先级。
- 记录当前 power plan，不在 pilot 与 full64 之间切换。

完整 64 的 `r000` 第一轮预计至少约 19 小时。历史上 DasAtom 有 6 个 3 小时 timeout，因此实际用时可能更长；通过第一轮门禁后，较快的成对结果还会追加 `r001`、`r002`，需要额外时间。Pilot 也包含较难电路，可能出现真实 timeout。不要根据预计时长人工终止仍在 timeout 上限内的 attempt。

## 5. 使用全新 ASCII 工作区

推荐路径：

```text
D:\ForceShuttleCanonical
```

不要复用任何旧 clone。如果该目录已经存在，不要自动删除，改用新的空目录，例如：

```text
D:\ForceShuttleCanonical_20260729
```

打开 64-bit PowerShell。PowerShell 7 优先，Windows PowerShell 5.1 也可。首先执行：

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoUrl = "git@github.com:xieliii/DasAtom_noVF2.git"
$Branch = "fidelity-optimization-2026-08-04"
$ExpectedCommit = "<粘贴 Mac 端最终交接消息中的 40 位 commit SHA>"
$WorkRoot = "D:\ForceShuttleCanonical"

if ($ExpectedCommit -notmatch '^[0-9a-fA-F]{40}$') {
    throw "Expected commit must be a full 40-character SHA."
}
if (Test-Path -LiteralPath $WorkRoot) {
    throw "WorkRoot already exists. Use a new empty ASCII path; do not delete it automatically."
}

git -c core.autocrlf=false clone --branch $Branch --single-branch $RepoUrl $WorkRoot
Set-Location -LiteralPath $WorkRoot
git config --local core.autocrlf false
git config --local core.eol lf
git config --local core.safecrlf true
git config --local core.longpaths true

git fetch origin $Branch
git checkout --detach $ExpectedCommit

$ActualCommit = (git rev-parse HEAD).Trim()
if ($ActualCommit -ne $ExpectedCommit) {
    throw "Commit mismatch: actual=$ActualCommit expected=$ExpectedCommit"
}
git merge-base --is-ancestor $ExpectedCommit "origin/$Branch"
if ($LASTEXITCODE -ne 0) {
    throw "Expected commit is not on origin/$Branch"
}

$Dirty = @(git status --porcelain)
if ($Dirty.Count -ne 0) {
    throw "Fresh clone is unexpectedly dirty: $($Dirty -join '; ')"
}

$ControlDir = Join-Path $WorkRoot "results\control"
New-Item -ItemType Directory -Force -Path $ControlDir | Out-Null
@{
    repository = $RepoUrl
    branch = $Branch
    commit = $ExpectedCommit.ToLowerInvariant()
    recorded_utc = [DateTime]::UtcNow.ToString("o")
} | ConvertTo-Json -Depth 4 |
    Set-Content -LiteralPath (Join-Path $ControlDir "source_lock.json") -Encoding UTF8
```

如果 SSH clone 失败，只允许把 `$RepoUrl` 改为同一仓库的 HTTPS URL：

```text
https://github.com/xieliii/DasAtom_noVF2.git
```

不要改用另一个 fork 或 main 分支。

## 6. 创建 Python 3.12 环境

不要依赖 PATH 中含义不明的 `python`。使用 Windows Python Launcher 找到 3.12，然后在仓库中创建被忽略的 `.venv-repro`：

```powershell
Set-Location -LiteralPath $WorkRoot

py -3.12 -c "import platform, struct, sys; print(sys.version); print(platform.platform()); print(struct.calcsize('P') * 8); assert sys.version_info[:2] == (3, 12); assert struct.calcsize('P') * 8 == 64"
if ($LASTEXITCODE -ne 0) {
    throw "CPython 3.12 x64 is required."
}

py -3.12 -m venv .venv-repro
$Python = Join-Path $WorkRoot ".venv-repro\Scripts\python.exe"

& $Python -m pip install --no-cache-dir -r requirements-repro.txt
if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed."
}
& $Python -m pip check
if ($LASTEXITCODE -ne 0) {
    throw "pip check failed."
}
& $Python -V
```

不要激活虚拟环境。后续所有命令都使用绝对 `$Python`，防止 Codex 或新终端误用其他 Python。

## 7. 固定进程和数值库线程

本次所有 compiler attempt 必须单 worker、顺序运行。不要使用仓库中的旧 parallel runner。

在每一个用于执行实验的 PowerShell 进程中设置：

```powershell
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

Runner 会以单 worker 执行，并根据 circuit index 与 repetition 进行 ForceShuttle/DasAtom AB/BA 轮换。不要另外启动第二个 runner。

## 8. Preflight 与输入冻结

创建本地 preflight 目录：

```powershell
$Preflight = Join-Path $WorkRoot "results\preflight"
New-Item -ItemType Directory -Force -Path $Preflight | Out-Null
```

检查 CPU：

```powershell
$Cpu = Get-CimInstance Win32_Processor
$Cpu | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed |
    ConvertTo-Json -Depth 4 |
    Set-Content -LiteralPath (Join-Path $Preflight "cpu.json") -Encoding UTF8

if (($Cpu.Name -join " ") -notmatch "9700X") {
    throw "Wrong machine: expected AMD Ryzen 7 9700X."
}
```

记录 OS、内存、power plan 和磁盘：

```powershell
Get-ComputerInfo |
    Select-Object WindowsProductName, WindowsVersion, OsBuildNumber, OsArchitecture |
    ConvertTo-Json -Depth 4 |
    Set-Content -LiteralPath (Join-Path $Preflight "windows.json") -Encoding UTF8

Get-CimInstance Win32_ComputerSystem |
    Select-Object Manufacturer, Model, TotalPhysicalMemory, NumberOfLogicalProcessors |
    ConvertTo-Json -Depth 4 |
    Set-Content -LiteralPath (Join-Path $Preflight "system.json") -Encoding UTF8

powercfg /getactivescheme |
    Set-Content -LiteralPath (Join-Path $Preflight "power_plan.txt") -Encoding UTF8

Get-Volume |
    Select-Object DriveLetter, FileSystem, DriveType, Size, SizeRemaining |
    ConvertTo-Json -Depth 4 |
    Set-Content -LiteralPath (Join-Path $Preflight "volumes.json") -Encoding UTF8
```

检查 `configs\local64.txt` 与 flat benchmark 目录完全一致：

```powershell
$ListPath = Join-Path $WorkRoot "configs\local64.txt"
$BenchmarkDir = Join-Path $WorkRoot "benchmarks\local64"

$ExpectedNames = @(
    Get-Content -LiteralPath $ListPath |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ -ne "" }
)
$ActualNames = @(
    Get-ChildItem -LiteralPath $BenchmarkDir -File -Filter "*.qasm" |
    Select-Object -ExpandProperty Name |
    Sort-Object
)

if ($ExpectedNames.Count -ne 64) {
    throw "configs/local64.txt must contain exactly 64 non-empty entries."
}
if (($ExpectedNames | Sort-Object -Unique).Count -ne 64) {
    throw "configs/local64.txt contains duplicate names."
}

$Diff = @(Compare-Object ($ExpectedNames | Sort-Object) $ActualNames)
if ($ActualNames.Count -ne 64 -or $Diff.Count -ne 0) {
    $Diff | Format-Table | Out-String |
        Set-Content -LiteralPath (Join-Path $Preflight "benchmark_diff.txt") -Encoding UTF8
    throw "Benchmark list and benchmarks/local64 do not match exactly."
}

$QasmHashes = foreach ($Name in $ExpectedNames) {
    $Path = Join-Path $BenchmarkDir $Name
    $Hash = Get-FileHash -Algorithm SHA256 -LiteralPath $Path
    [PSCustomObject]@{
        circuit = $Name
        sha256 = $Hash.Hash.ToLowerInvariant()
        bytes = (Get-Item -LiteralPath $Path).Length
    }
}
$QasmHashes | Export-Csv -NoTypeInformation -Encoding UTF8 -LiteralPath (Join-Path $Preflight "qasm_sha256.csv")

$ExpectedListHash = "bdcc9a9a9674f4b938cf64a6b0d5774e0472f1f5dc4ab5cf62f50fd7bd9c5682"
$ActualListHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ListPath).Hash.ToLowerInvariant()
if ($ActualListHash -ne $ExpectedListHash) {
    throw "configs/local64.txt byte hash mismatch: actual=$ActualListHash expected=$ExpectedListHash"
}

$OrdinalNames = [string[]]$ActualNames
[Array]::Sort($OrdinalNames, [StringComparer]::Ordinal)
$SetLines = foreach ($Name in $OrdinalNames) {
    $Path = Join-Path $BenchmarkDir $Name
    $Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
    "$Name`t$Hash`n"
}
$SetBytes = [Text.Encoding]::ASCII.GetBytes(($SetLines -join ""))
$Sha256 = [Security.Cryptography.SHA256]::Create()
try {
    $SetHashBytes = $Sha256.ComputeHash($SetBytes)
}
finally {
    $Sha256.Dispose()
}
$ActualSetHash = -join ($SetHashBytes | ForEach-Object { $_.ToString("x2") })
$ExpectedSetHash = "f3f4cc03ca0c1ba3526409970f841e992778e0fc4801905fef1a8829d3cdd467"
if ($ActualSetHash -ne $ExpectedSetHash) {
    throw "Canonical QASM set hash mismatch: actual=$ActualSetHash expected=$ExpectedSetHash"
}

@{
    local64_list_sha256 = $ActualListHash
    qasm_set_sha256 = $ActualSetHash
    qasm_count = $ActualNames.Count
    qasm_set_hash_rule = "filename<TAB>lowercase_sha256<LF>, filename ordinal ascending, outer SHA256"
} | ConvertTo-Json -Depth 4 |
    Set-Content -LiteralPath (Join-Path $Preflight "canonical_input_hashes.json") -Encoding UTF8

Get-FileHash -Algorithm SHA256 -LiteralPath `
    "configs\local64.txt", `
    "configs\pilot12.txt", `
    "configs\smoke1.txt", `
    "configs\fidelity_models.json", `
    "requirements.txt", `
    "requirements-repro.txt", `
    "scripts\windows\canonical_pipeline.ps1" |
    Select-Object Path, Hash |
    Export-Csv -NoTypeInformation -Encoding UTF8 -LiteralPath (Join-Path $Preflight "config_sha256.csv")
```

捕获统一环境信息和解析后的依赖：

```powershell
& $Python scripts\capture_environment.py --output (Join-Path $Preflight "environment.json")
if ($LASTEXITCODE -ne 0) {
    throw "Environment capture failed."
}
& $Python -m pip freeze |
    Set-Content -LiteralPath (Join-Path $Preflight "requirements.resolved.txt") -Encoding UTF8
if ($LASTEXITCODE -ne 0) {
    throw "pip freeze failed."
}
```

再次验证 Git：

```powershell
$ActualCommit = (git rev-parse HEAD).Trim()
if ($ActualCommit -ne $ExpectedCommit) {
    throw "Commit changed during preflight."
}
git diff --check
if ($LASTEXITCODE -ne 0) {
    throw "git diff --check failed."
}
$Dirty = @(git status --porcelain)
if ($Dirty.Count -ne 0) {
    throw "Tracked or unignored files changed: $($Dirty -join '; ')"
}
```

`results/` 和 `.venv-repro/` 应被 `.gitignore` 忽略，因此 clean status 是硬门禁。

## 9. 代码级测试门禁

这一步只验证已经冻结的代码。Windows Codex 不得修改代码来处理失败。

```powershell
$PyFiles = @(
    Get-ChildItem -LiteralPath "canonical", "scripts", "DasAtom", "DasAtom_Origin" `
        -Recurse -File -Filter "*.py" |
    Select-Object -ExpandProperty FullName
)
& $Python -m py_compile @PyFiles
if ($LASTEXITCODE -ne 0) {
    throw "py_compile failed. Do not edit code; report the failure."
}

& $Python -m pytest -q 2>&1 |
    Tee-Object -FilePath (Join-Path $Preflight "pytest.log")
if ($LASTEXITCODE -ne 0) {
    throw "pytest failed. Do not edit code; report the failure."
}

git diff --check
if ($LASTEXITCODE -ne 0) {
    throw "git diff --check failed."
}

$Dirty = @(git status --porcelain)
if ($Dirty.Count -ne 0) {
    throw "Repository became dirty during tests: $($Dirty -join '; ')"
}
```

测试必须覆盖 runner order、timeout、resume、raw parsing、endpoint verifier、metrics identity、collector 和 tamper detection。只看到“若干测试通过”不够，必须保留 `results\preflight\pytest.log` 中的完整最终摘要。

## 10. 单电路端到端 smoke

Smoke 使用：

```text
configs\smoke1.txt
3_regular_10.qasm
```

Smoke 不产生论文结果。它必须验证：runner -> compiler raw -> independent verifier -> metrics -> collector -> validator -> fidelity sensitivity。

```powershell
$SmokeId = "smoke-" + [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$SmokeDir = Join-Path $WorkRoot ("results\" + $SmokeId)

& $Python scripts\run_canonical_pairwise.py `
    --run-dir $SmokeDir `
    --benchmark-dir "benchmarks\local64" `
    --list "configs\smoke1.txt" `
    --methods forceshuttle dasatom `
    --repetitions 1 `
    --timeout-sec 600 `
    --seed 0
if ($LASTEXITCODE -ne 0) {
    throw "Smoke runner failed. Stop before pilot."
}

& $Python scripts\collect_canonical_results.py --run-dir $SmokeDir
if ($LASTEXITCODE -ne 0) {
    throw "Smoke collector failed."
}

$SmokeValidation = Join-Path $SmokeDir "derived\validation.json"
& $Python scripts\validate_canonical_run.py `
    --run-dir $SmokeDir `
    --require-complete `
    --json-out $SmokeValidation
if ($LASTEXITCODE -ne 0) {
    throw "Smoke validator failed."
}

$SmokeGate = Get-Content -Raw -LiteralPath $SmokeValidation | ConvertFrom-Json
if (-not $SmokeGate.passed -or -not $SmokeGate.complete -or -not $SmokeGate.pilot_gate_passed) {
    throw "Smoke validation gate did not pass."
}

& $Python scripts\recompute_fidelity.py `
    --run-dir $SmokeDir `
    --config "configs\fidelity_models.json"
if ($LASTEXITCODE -ne 0) {
    throw "Smoke fidelity sensitivity failed."
}
```

人工检查每个 method attempt 至少包含：

```text
input.qasm
command.json
compiler_output.json
verification.json
metrics.json
status.json
stdout.log
stderr.log
hashes.json
files.sha256
```

`verification.json` 必须满足 `passed=true` 且 `checks.schema_scope.passed=true`。对应的 `compiler_output.json` 必须明确包含：

```text
scope.name = endpoint_layout_v1
scope.continuous_motion_verified = false
scope.one_qubit_policy = counted_not_scheduled
```

如果缺少任何 raw 文件，禁止进入 pilot。

## 11. Pilot、full64 与重复测量门禁

Pilot 使用 `configs\pilot12.txt`，共 12 个电路 × 2 个方法 × 1 repetition。每个 method/circuit 的 timeout 为真实 10800 秒。

Pilot 中 timeout 是允许出现的算法结果。下列情况不允许：

- `compiler_nonzero_exit`
- `missing_compiler_output`
- `malformed_compiler_output`
- `verifier_failed`
- `metrics_failed`
- `runner_exception`
- `user_interrupt`，除非随后使用同一 run-dir 正确 resume 并完成

Validator 输出的 `pilot_gate_passed` 定义为：raw/hash/重算/summary 全部一致，并且所有未成功 attempt 仅为诚实记录的 timeout。

门禁规则固定为：

```text
pilot_gate_passed = true  -> 归档并报告 pilot，停止并等待用户明确批准
pilot_gate_passed = false -> 归档 pilot，立即停止，不运行 full64
用户明确批准 full64      -> 才允许以 -Resume -ApproveFull64 继续
```

不要因为 runner 在存在 timeout 时返回非零就立刻停止。Runner exit code 需要记录，但 pilot 是否技术上合格由 collector 后的 validator `pilot_gate_passed` 决定。即使 `pilot_gate_passed=true`，也不能自动进入 full64；Windows Codex 必须先把统计、validation JSON、pilot ZIP 和 SHA256 报告给用户，并等待用户明确回复同意。

Full64 分成两段，不能合并或倒序：

1. 先对 64 个电路各执行 `r000`，即 64 × 2 = 128 个逻辑 attempt。
2. Collector 和 validator 对 `r000` 做完整门禁。
3. 只有 `r000` 的 `pilot_gate_passed=true` 时，才在同一个 run-dir 使用 `--resume --repetitions 3 --repeat-threshold-sec 600`。
4. 只有 `r000` 中 ForceShuttle 与 DasAtom 都成功，且两者外部 wall time 的较大值不超过 600 秒的电路，才成对追加 `r001` 和 `r002`。
5. `r000` 中任一方法 timeout、失败或超过 600 秒，该电路的两种方法都不追加重复，manifest 必须记录 `not_scheduled_threshold`。

重复测量用于给较快电路提供 runtime 中位数并检查相同 seed 的 schedule hash 是否稳定。它不能给 timeout 额外重试机会，也不能只重复其中一种方法。追加重复后必须重新 collector、validator 和 fidelity sensitivity；最终论文数据只能来自最后一次重建的 `derived\`。

## 12. 使用受 Git 跟踪的后台监督脚本

权威监督脚本已经随代码提供：

```text
scripts\windows\canonical_pipeline.ps1
```

Windows Codex 不得从 Markdown 手抄、重建或修改这个脚本，也不得在 `results/` 中创建另一个功能不同的版本。若本文件与脚本实现出现差异，以冻结 commit 中的受跟踪脚本为准，并向 Mac 端报告文档差异。

启动前验证它确实来自正式 commit：

```powershell
git ls-files --error-unmatch "scripts/windows/canonical_pipeline.ps1"
if ($LASTEXITCODE -ne 0) {
    throw "Tracked Windows supervisor is missing."
}
$Supervisor = Join-Path $WorkRoot "scripts\windows\canonical_pipeline.ps1"
```

该脚本负责：

1. 从 `results\control\source_lock.json` 读取正式 commit，并拒绝 dirty worktree。
2. 单实例锁、单 worker、ForceShuttle/DasAtom 顺序执行和真实 10800 秒 timeout。
3. Pilot runner、collector、validator、fidelity sensitivity、provenance 和 ZIP 归档。
4. Pilot PASS 后写入 `phase=awaiting_full64_approval` 并正常退出，不自动运行 full64。
5. 收到用户明确批准后，重新校验 Pilot raw/derived、ZIP SHA256 和 gate，再进入 full64。
6. Full64 先跑 `r000` 门禁，再只对双方均成功且 pair max wall time 不超过 600 秒的电路追加 `r001`、`r002`。
7. 保存 `pipeline_state.json`、archive 路径和 SHA256，并支持 phase-aware resume。
8. 归档前把 preflight、source lock、当时的 pipeline state 和 clean Git 状态复制到 run provenance。

参数语义固定为：

```text
无参数
  新建状态并只运行 Pilot；PASS 后停在 awaiting_full64_approval。

-Resume
  恢复尚未获批的 Pilot，或重新显示已归档 Pilot 的等待状态。

-Resume -ApproveFull64
  仅在用户明确批准后进入或恢复 full64。脚本要求已有完整 Pilot archive、
  SHA256 匹配且重新 validator PASS。

-ForceUnlock
  只在确认 supervisor、runner 和 compiler child 都已停止，但锁文件残留时使用。
```

本地可变控制材料只存在于被忽略的 `results\control\`，包括 `source_lock.json`、`pipeline_state.json` 和日志。不得 `git add` 这些文件。

## 13. 后台启动

完成 preflight、pytest 和 smoke 后，后台启动监督脚本：

```powershell
$ControlScript = Join-Path $WorkRoot "scripts\windows\canonical_pipeline.ps1"
$ControlOut = Join-Path $WorkRoot "results\control\background.stdout.log"
$ControlErr = Join-Path $WorkRoot "results\control\background.stderr.log"

$Process = Start-Process `
    -FilePath "powershell.exe" `
    -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $ControlScript
    ) `
    -WorkingDirectory $WorkRoot `
    -RedirectStandardOutput $ControlOut `
    -RedirectStandardError $ControlErr `
    -PassThru

@{
    pid = $Process.Id
    started_utc = [DateTime]::UtcNow.ToString("o")
    script = $ControlScript
    stdout = $ControlOut
    stderr = $ControlErr
} | ConvertTo-Json |
    Set-Content -LiteralPath (Join-Path $WorkRoot "results\control\background_process.json") -Encoding UTF8

Write-Host "Canonical pipeline started as PID $($Process.Id)"
```

第一次启动只运行到 Pilot 归档。若 Pilot gate 通过，后台进程会正常退出，并把 `pipeline_state.json` 的 phase 写成：

```text
awaiting_full64_approval
```

Windows Codex 此时必须向用户报告 Pilot 的 `status_counts`、`failure_kind_counts`、validator 结果、ZIP 路径和 SHA256。没有用户在本任务中明确回复同意前，不得传 `-ApproveFull64`，也不得直接手工运行 full64 runner。

用户明确批准后，使用第二个后台进程继续：

```powershell
$ControlScript = Join-Path $WorkRoot "scripts\windows\canonical_pipeline.ps1"
$FullOut = Join-Path $WorkRoot "results\control\full64.stdout.log"
$FullErr = Join-Path $WorkRoot "results\control\full64.stderr.log"

$Process = Start-Process `
    -FilePath "powershell.exe" `
    -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $ControlScript,
        "-Resume",
        "-ApproveFull64"
    ) `
    -WorkingDirectory $WorkRoot `
    -RedirectStandardOutput $FullOut `
    -RedirectStandardError $FullErr `
    -PassThru

@{
    pid = $Process.Id
    started_utc = [DateTime]::UtcNow.ToString("o")
    mode = "approved_full64"
    script = $ControlScript
    stdout = $FullOut
    stderr = $FullErr
} | ConvertTo-Json |
    Set-Content -LiteralPath (Join-Path $WorkRoot "results\control\background_process.json") -Encoding UTF8
```

锁屏不会停止该进程。注销、关机、重启或睡眠会中断。

检查状态时只读：

```powershell
Get-Content -Tail 100 -LiteralPath "results\control\background.stdout.log"
Get-Content -Tail 100 -LiteralPath "results\control\background.stderr.log"
Get-Content -Raw -LiteralPath "results\control\pipeline_state.json"
Get-Process -Id ((Get-Content -Raw "results\control\background_process.json" | ConvertFrom-Json).pid) -ErrorAction SilentlyContinue
```

不要为了“看起来卡住”而结束进程。一个 DasAtom attempt 可以合法运行接近 3 小时且日志很少。

## 14. 中断与 resume

Runner 的 `--resume` 只复用同一个 run-dir 和相同逻辑 attempt。它不以旧 XLSX 判断成功。对 `succeeded`、`timeout`、`failed`、`invalid`，只要 terminal `status.json`、raw 文件和 hash 校验完整，就必须原样跳过，不能覆盖或再给一次机会；只有 `interrupted` 或缺失 terminal status 的未完成 attempt 才能创建新的 execution。若希望重试一个完整的 compiler/verifier failure，必须新建 run-dir，并把它当作另一轮实验，不能混入当前 canonical run。

如果 Codex App 重启但后台 PID 仍存在，只重新读取状态，不启动第二个进程。

如果 Windows 或 PowerShell 进程确实中断：

1. 确认没有仍在运行的 canonical runner 或 compiler child。
2. 不删除 `results\pilot-*`、`results\full64-*` 或 `pipeline_state.json`。
3. 使用原仓库、原 commit、原 venv、原 seed 和原 run-dir。
4. 若中断发生在 Pilot 或 `awaiting_full64_approval` 且用户尚未批准，只使用 `-Resume`。
5. 若用户已经明确批准且 full64 已开始，使用 `-Resume -ApproveFull64`；这只是恢复已经批准的阶段，不是绕过 Pilot 门禁。

检查残留进程：

```powershell
Get-CimInstance Win32_Process |
    Where-Object {
        $_.CommandLine -match "run_canonical_pairwise|run_compiler_once|DasAtom.py"
    } |
    Select-Object ProcessId, ParentProcessId, Name, CommandLine
```

若没有残留进程，但 `pipeline.lock` 或 runner lock 因异常退出残留，先读取 state 决定恢复参数：

```powershell
$State = Get-Content -Raw -LiteralPath "results\control\pipeline_state.json" | ConvertFrom-Json
$ResumeArguments = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "scripts\windows\canonical_pipeline.ps1",
    "-Resume"
)
if ($null -ne $State.PSObject.Properties["full64_approval_recorded_utc"]) {
    $ResumeArguments += "-ApproveFull64"
}
$ResumeArguments += "-ForceUnlock"

$Process = Start-Process `
    -FilePath "powershell.exe" `
    -ArgumentList $ResumeArguments `
    -WorkingDirectory $WorkRoot `
    -RedirectStandardOutput "results\control\resume.stdout.log" `
    -RedirectStandardError "results\control\resume.stderr.log" `
    -PassThru
```

`-ForceUnlock` 不是常规参数。若任何相关 Python 进程仍在运行，禁止使用它。若 state 尚无 `full64_approval_recorded_utc`，绝不能由 Windows Codex 自行添加 `-ApproveFull64`；必须先取得用户的明确批准。

若中断原因是断电或 Windows 重启，恢复前还必须：

```powershell
$WorkRoot = "D:\ForceShuttleCanonical"
Set-Location -LiteralPath $WorkRoot
$Python = Join-Path $WorkRoot ".venv-repro\Scripts\python.exe"
$SourceLock = Get-Content -Raw -LiteralPath "results\control\source_lock.json" | ConvertFrom-Json
$ExpectedCommit = [string]$SourceLock.commit

$RecoveryStamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$RecoveryDir = Join-Path $WorkRoot ("results\control\recovery-" + $RecoveryStamp)
New-Item -ItemType Directory -Force -Path $RecoveryDir | Out-Null

Get-CimInstance Win32_Processor |
    Select-Object Name, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed |
    ConvertTo-Json -Depth 4 |
    Set-Content -LiteralPath (Join-Path $RecoveryDir "cpu.json") -Encoding UTF8
powercfg /getactivescheme |
    Set-Content -LiteralPath (Join-Path $RecoveryDir "power_plan.txt") -Encoding UTF8
$RecoveryCommit = (git rev-parse HEAD).Trim()
$RecoveryCommit | Set-Content -LiteralPath (Join-Path $RecoveryDir "git_commit.txt") -Encoding ASCII
$RecoveryDirty = @(git status --porcelain)
$RecoveryDirty | Set-Content -LiteralPath (Join-Path $RecoveryDir "git_status.txt") -Encoding UTF8
& $Python -m pip check 2>&1 | Tee-Object -FilePath (Join-Path $RecoveryDir "pip_check.txt")
if ($LASTEXITCODE -ne 0) {
    throw "pip check failed after recovery."
}
if ($RecoveryCommit -ne $ExpectedCommit -or $RecoveryDirty.Count -ne 0) {
    throw "Source changed after recovery."
}
```

确认仍是 9700X、commit 未变、Git status 为空、`pip check` 通过且 power plan 与 preflight 一致。记录中断的大致时间和原因到该 recovery 目录并随最终结果回传。若 Windows build、Python 环境、power plan 或代码状态发生变化，先停止并报告，不要静默继续。

## 15. Timeout 与失败分类

每次 attempt 的 `status.json` 使用：

```text
outcome:
  succeeded
  failed
  timeout
  invalid
  interrupted
```

`failure_kind` 可能为：

```text
timeout
compiler_nonzero_exit
missing_compiler_output
malformed_compiler_output
verifier_failed
metrics_failed
provenance_mismatch
runner_exception
user_interrupt
```

只有真实外部 process timeout 才能得到 `failure_kind=timeout`。Runner 必须终止整个 Windows process tree，并记录实际 wall time、timeout 配置和终止状态。

对于 pilot/full64：

- `timeout` 是可接受、必须报告的算法结果。
- 其他 failure kind 都阻止 canonical gate 通过。
- 不得把 compiler crash 改成 timeout。
- 不得把 missing raw 改成 failed-success 或手工补 JSON。

## 16. Collector、validator 与 fidelity sensitivity

Collector：

```powershell
& $Python scripts\collect_canonical_results.py --run-dir <RUN_DIR>
```

默认输出：

```text
<RUN_DIR>\derived\
```

Collector 只能从新 attempt raw 输出生成汇总，不读取旧 expected CSV。

Validator：

```powershell
& $Python scripts\validate_canonical_run.py `
    --run-dir <RUN_DIR> `
    --json-out <RUN_DIR>\derived\validation.json
```

Validator JSON 顶层必须至少包含：

```text
passed
pilot_gate_passed
complete
status_counts
failure_kind_counts
logical_attempt_count
execution_count
errors
warnings
attempt_reports
```

`passed` 表示 raw、hash、独立重算和 summary 一致。`pilot_gate_passed` 进一步要求所有非成功 attempt 只能是 timeout。

Fidelity sensitivity：

```powershell
& $Python scripts\recompute_fidelity.py `
    --run-dir <RUN_DIR> `
    --config configs\fidelity_models.json
```

配置会变化：

- transfer fidelity
- transfer duration
- coherence time
- movement speed
- transfer semantics：batch 与 per-atom

必须保留两种 transfer 语义，不能只挑对 ForceShuttle 更有利的一种。

原始指标必须分别报告：

```text
movement_batches
atom_endpoint_changes
transfer_rounds
atom_transfer_events
batch_critical_distance_um
endpoint_sum_distance_um
```

任何 estimated success probability 都是 model-based sensitivity，不是已测硬件 fidelity。

## 17. 归档与 SHA256

正式归档命令：

```powershell
New-Item -ItemType Directory -Force -Path "archives" | Out-Null

& $Python scripts\archive_canonical_run.py `
    --run-dir <RUN_DIR> `
    --output "archives\<RUN_ID>.zip" `
    --force
if ($LASTEXITCODE -ne 0) {
    throw "Archive failed."
}

$ArchiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath "archives\<RUN_ID>.zip").Hash.ToLowerInvariant()
"$ArchiveHash  <RUN_ID>.zip" |
    Set-Content -LiteralPath "archives\<RUN_ID>.zip.sha256" -Encoding ASCII
```

Archive 默认要求 run 完整。正式 Pilot 和 full64 禁止使用 `--allow-incomplete`。只有在 Mac 端明确要求收集一个无法完成的诊断现场时才可使用该参数；这类 ZIP 的 `archive_manifest.json` 会标记 `complete=false`、`paper_eligible=false`，绝不能作为论文结果。

归档必须包含：

- exact Git commit 和 clean status 记录
- environment 和 resolved dependency versions
- benchmark/config hashes
- run manifest
- 所有 attempt 的 command、status、stdout、stderr
- 所有 `compiler_output.json`
- 所有 verifier 和 metrics 文件
- collector derived outputs
- validator JSON
- fidelity sensitivity outputs
- 每个 attempt 的 `files.sha256`，以及顶层 `archive_manifest.json`

归档不得包含：

- `.git/`
- `.venv-repro/`
- 旧 `DasAtom/res/`
- 其他旧结果目录
- 用户个人密钥或 Git credential

不要把大型 `results/` 或 ZIP `git add` 到代码仓库。

## 18. 回传给 Mac 端的内容

不要只发送截图、均值表或几个 XLSX。回传分两次：

Pilot 结束并等待批准时至少回传：

1. `archives\pilot-*.zip`
2. pilot ZIP 的 `.sha256`
3. `results\control\pipeline_state.json`
4. `results\control\background.stdout.log`
5. `results\control\background.stderr.log`
6. 最终报告模板中的 Machine、Source、Environment、Preflight、Tests、Smoke 和 Pilot 部分

用户批准并完成 full64 后再回传：

1. `archives\full64-*.zip`
2. full64 ZIP 的 `.sha256`
3. 更新后的 `pipeline_state.json`
4. `results\control\full64.stdout.log` 和 `full64.stderr.log`
5. 填写完整的最终报告

若文件太大，可上传到用户指定的私有网盘或私有 GitHub Release。不要提交到源码分支，也不要公开泄露双盲投稿身份。

Mac 端收到后会：

1. 再次验证 ZIP SHA256。
2. 解压并运行 canonical validator。
3. 审查所有 failure/timeout/verifier 状态。
4. 重算 runtime、endpoint movement 和 fidelity sensitivity。
5. 根据真实新数据修改论文摘要、Evaluation、图表、Threats to Validity 和结论。

## 19. Windows Codex 最终报告模板

```text
ForceShuttle canonical rerun report

Machine:
- CPU:
- cores/logical processors:
- RAM:
- Windows edition/build:
- power plan:
- storage drive/filesystem:

Source:
- repository:
- branch:
- exact commit:
- git status clean before run: yes/no
- git status clean after run: yes/no
- core.autocrlf: false/other

Environment:
- Python:
- Python bitness:
- requirements resolved file:
- pip check: PASS/FAIL
- PYTHONHASHSEED:
- thread variables:

Preflight:
- 64 list entries: PASS/FAIL
- 64 unique QASM files: PASS/FAIL
- list/files exact match: PASS/FAIL
- qasm hash manifest path:

Tests:
- py_compile: PASS/FAIL
- pytest: PASS/FAIL
- test count/summary:
- git diff --check: PASS/FAIL

Smoke:
- run directory:
- ForceShuttle outcome:
- DasAtom outcome:
- verifier PASS for both: yes/no
- complete: yes/no
- validation JSON:

Pilot:
- run directory:
- runner exit code:
- logical attempts:
- status_counts:
- failure_kind_counts:
- validator passed:
- pilot_gate_passed:
- pipeline phase after pilot:
- pilot archive:
- pilot archive SHA256:

Full64:
- explicit user approval received: yes/no
- full64_approval_recorded_utc:
- run directory:
- round-0 runner exit code:
- round-0 validator passed:
- round-0 gate passed:
- fast-repeat runner exit code:
- circuits eligible for r001/r002:
- logical repeats marked not_scheduled_threshold:
- logical attempts:
- execution count:
- status_counts:
- failure_kind_counts:
- ForceShuttle succeeded/timeout/failed:
- DasAtom succeeded/timeout/failed:
- validator passed:
- canonical gate passed:
- validation JSON:
- derived summary directory:
- fidelity sensitivity directory:
- full archive:
- full archive SHA256:

Integrity:
- manual status edits: no
- old result reuse: no
- Enola/Atomique executed: no
- code modified on Windows: no
- commit/push performed on Windows: no

Known limitations:
- endpoint_layout_v1 only
- continuous_motion_verified=false
- one_qubit_policy=counted_not_scheduled
- other observed limitations:

Files uploaded / transfer location:
```

## 20. 判断完成

只有同时满足以下条件，Windows 任务才算完成：

- 精确 commit 与 Ryzen 7 9700X 环境已记录。
- 所有代码测试通过且 worktree 始终 clean。
- Smoke 两种方法均完整成功并通过 verifier。
- Pilot 已执行、收集、验证、做 sensitivity 并归档。
- Pilot gate PASS 后 phase 先变为 `awaiting_full64_approval`，后台进程退出并向用户回报；若 pilot gate FAIL，则 full64 没有启动。
- 只有用户明确批准后才记录 `full64_approval_recorded_utc` 并启动 full64；没有批准时停在 Pilot 是正确状态，不是任务失败。
- Full64 `r000` 已先独立通过门禁，之后只对双方均成功且 pair max wall time 不超过 600 秒的电路成对追加 `r001`、`r002`。
- Full64 所有已调度 attempt 都有 terminal status；所有未调度重复都有 `not_scheduled_threshold`，无未知或半写入状态。
- Full64 collector/validator 已运行。
- 只有 timeout 或 succeeded 时 canonical gate 才通过。
- Pilot/full ZIP 与 SHA256 已生成。
- 所有要求的文件和最终报告已回传。

等待用户批准 full64 时，监督脚本必须已经正常退出，不能留下实验进程。用户批准后的长时间 full64 使用新的后台进程、状态文件和 resume 机制完成。
