# Windows 9700X ForceShuttle-Only Final Run

更新时间：2026-08-04

适用对象：家中 Windows 9700X 实验机上的 Codex

## 1. 任务结论

这次不要重新运行 DasAtom。只运行优化后的 ForceShuttle：

```text
64 circuits x 3 repetitions x ForceShuttle only
```

DasAtom 使用 2026-07-30 已完成的冻结 canonical baseline。旧 baseline 已由 Mac 端审计：

- `DasAtom_Origin/` 的 4 个受审计文件与当前仓库逐字节一致。
- `canonical/` 的 6 个文件逐字节一致。
- canonical runner、collector、validator、fidelity 和 archive 脚本逐字节一致。
- `configs/local64.txt` SHA256 仍为
  `bdcc9a9a9674f4b938cf64a6b0d5774e0472f1f5dc4ab5cf62f50fd7bd9c5682`。
- 64 个 QASM 的文件名、大小和 SHA256 与旧 run manifest 一致。
- `configs/fidelity_models.json` SHA256 仍为
  `7058f3fde42158c3b2004efab1c792e12f28bc85a5fcb1bb0227dcabd05c0ec6`。
- 旧 baseline 来自 clean commit，使用 CPython 3.12.10 x64 和固定单线程环境。
- 旧 Windows 回传 ZIP 的外部 SHA256 为
  `c990cef27b1cb1c77252cb7f82fc46d2d3c448fa7a4d90998967f9b938cd256f`。

因此旧 DasAtom raw schedule 和 fidelity 可以直接复用。新的 ForceShuttle runtime 仍必须在同一台 Ryzen 7 9700X 上重新测量。

## 2. 严格禁止

Windows Codex 不得：

1. 修改任何受 Git 跟踪的文件。
2. 使用 VF2、DasAtom fallback 或旧 ForceShuttle schedule cache。
3. 从旧工作目录复制 ForceShuttle 输出。
4. 修改 fidelity 参数、seed、timeout、QASM 或 verifier。
5. 启动 DasAtom、Enola、Atomique 或 Q-Tetris。
6. 并行启动多个 runner。
7. 手工编辑 raw JSON、CSV、status 或 verification 文件。

若 `git status --porcelain` 非空，立即停止并报告。

## 3. 冻结源码

Mac 端会在交接消息中提供：

```text
repository = git@github.com:xieliii/DasAtom_noVF2.git
branch = fidelity-optimization-2026-08-04
commit = <Mac 端提供的完整 40 位 SHA>
```

必须使用交接消息中的完整 SHA，不得直接猜测 branch HEAD。

在新的 ASCII 路径中执行：

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoUrl = "git@github.com:xieliii/DasAtom_noVF2.git"
$Branch = "fidelity-optimization-2026-08-04"
$ExpectedCommit = "<粘贴 Mac 端提供的 40 位 SHA>"
$WorkRoot = "D:\ForceShuttleFinal"

if ($ExpectedCommit -notmatch '^[0-9a-fA-F]{40}$') {
    throw "ExpectedCommit must be a full SHA."
}
if (Test-Path -LiteralPath $WorkRoot) {
    throw "WorkRoot already exists. Select a new empty ASCII path."
}

git -c core.autocrlf=false clone --branch $Branch --single-branch $RepoUrl $WorkRoot
Set-Location -LiteralPath $WorkRoot
git config --local core.autocrlf false
git config --local core.eol lf
git config --local core.safecrlf true
git config --local core.longpaths true
git checkout --detach $ExpectedCommit

if ((git rev-parse HEAD).Trim() -ne $ExpectedCommit) {
    throw "Commit mismatch."
}
if (@(git status --porcelain).Count -ne 0) {
    throw "Fresh clone is dirty."
}
```

SSH 失败时，只允许把 URL 改成：

```text
https://github.com/xieliii/DasAtom_noVF2.git
```

## 4. Python 环境

```powershell
Set-Location -LiteralPath $WorkRoot

py -3.12 -c "import struct,sys; print(sys.version); assert sys.version_info[:2] == (3,12); assert struct.calcsize('P')*8 == 64"
py -3.12 -m venv .venv-repro
$Python = Join-Path $WorkRoot ".venv-repro\Scripts\python.exe"

& $Python -m pip install --no-cache-dir -r requirements-repro.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
& $Python -m pip check
if ($LASTEXITCODE -ne 0) { throw "pip check failed." }
```

不要激活虚拟环境。后续始终使用 `$Python`。

## 5. 环境与测试

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

$Preflight = Join-Path $WorkRoot "results\preflight"
New-Item -ItemType Directory -Force -Path $Preflight | Out-Null

& $Python scripts\capture_environment.py --output (Join-Path $Preflight "environment.json")
if ($LASTEXITCODE -ne 0) { throw "Environment capture failed." }

Get-CimInstance Win32_Processor |
    Select-Object Name, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed |
    ConvertTo-Json -Depth 4 |
    Set-Content -LiteralPath (Join-Path $Preflight "cpu.json") -Encoding UTF8

powercfg /getactivescheme |
    Set-Content -LiteralPath (Join-Path $Preflight "power_plan.txt") -Encoding UTF8

& $Python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Tests failed." }

if (@(git status --porcelain).Count -ne 0) {
    throw "Tests changed tracked files."
}
```

CPU 名称必须包含 `AMD Ryzen 7 9700X`。机器接通电源，禁止休眠，不要同时运行其他编译或高负载任务。

## 6. 运行 ForceShuttle 三次

Runner 要求新 run 先建立不可变的 round-0 计划，再扩展 repetition。先运行一次 ForceShuttle，不要加入 `dasatom`：

```powershell
$Stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$RunDir = Join-Path $WorkRoot ("results\forceshuttle-final-" + $Stamp)

& $Python scripts\run_canonical_pairwise.py `
    --run-dir $RunDir `
    --benchmark-dir "benchmarks\local64" `
    --list "configs\local64.txt" `
    --methods forceshuttle `
    --repetitions 1 `
    --timeout-sec 10800 `
    --seed 0 `
    --fail-fast

if ($LASTEXITCODE -ne 0) {
    throw "ForceShuttle runner failed. Do not edit outputs; report the run directory."
}
```

round-0 完整成功后，使用同一个 `$RunDir` 扩展到 3 次：

```powershell
& $Python scripts\run_canonical_pairwise.py `
    --run-dir $RunDir `
    --benchmark-dir "benchmarks\local64" `
    --list "configs\local64.txt" `
    --methods forceshuttle `
    --repetitions 3 `
    --timeout-sec 10800 `
    --seed 0 `
    --resume

if ($LASTEXITCODE -ne 0) {
    throw "ForceShuttle repetition extension failed."
}
```

若进程被正常重启或终端中断，但没有相关 Python runner 仍在运行，重复执行上面的 `--resume --repetitions 3` 命令。不要新建第二个 run directory 来补缺失项。

## 7. 收集、验证与 fidelity

```powershell
& $Python scripts\collect_canonical_results.py --run-dir $RunDir
if ($LASTEXITCODE -ne 0) { throw "Collector failed." }

& $Python scripts\validate_canonical_run.py `
    --run-dir $RunDir `
    --require-complete `
    --json-out (Join-Path $RunDir "derived\validation.json")
if ($LASTEXITCODE -ne 0) { throw "Validator failed." }

& $Python scripts\recompute_fidelity.py `
    --run-dir $RunDir `
    --config "configs\fidelity_models.json"
if ($LASTEXITCODE -ne 0) { throw "Fidelity recomputation failed." }

if (@(git status --porcelain).Count -ne 0) {
    throw "Repository became dirty after the run."
}
```

必须确认：

```text
logical attempts = 192
status counts = succeeded: 192
validator passed = true
validator complete = true
```

还要确认同一电路的 3 次 `schedule_hash` 完全一致。runtime 可以不同，schedule 不允许不同。

## 8. 归档

```powershell
$Provenance = Join-Path $RunDir "provenance"
New-Item -ItemType Directory -Force -Path $Provenance | Out-Null
Copy-Item -Path (Join-Path $Preflight "*") -Destination $Provenance -Recurse -Force

@{
    repository = $RepoUrl
    branch = $Branch
    commit = (git rev-parse HEAD).Trim()
    status_porcelain = @(git status --porcelain)
    captured_utc = [DateTime]::UtcNow.ToString("o")
} | ConvertTo-Json -Depth 5 |
    Set-Content -LiteralPath (Join-Path $Provenance "git_state_at_archive.json") -Encoding UTF8

$ArchiveDir = Join-Path $WorkRoot "archives"
New-Item -ItemType Directory -Force -Path $ArchiveDir | Out-Null
$Archive = Join-Path $ArchiveDir ((Split-Path $RunDir -Leaf) + ".zip")

& $Python scripts\archive_canonical_run.py `
    --run-dir $RunDir `
    --output $Archive `
    --force
if ($LASTEXITCODE -ne 0) { throw "Archive failed." }

$ArchiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Archive).Hash.ToLowerInvariant()
"$ArchiveHash  $(Split-Path $Archive -Leaf)" |
    Set-Content -LiteralPath ($Archive + ".sha256") -Encoding ASCII

Write-Host "ARCHIVE=$Archive"
Write-Host "SHA256=$ArchiveHash"
```

## 9. 回传内容

回传给 Mac 端：

1. `forceshuttle-final-*.zip`
2. 对应 `.zip.sha256`
3. PowerShell 最终显示的完整 commit SHA
4. `derived\validation.json`
5. `derived\summary.csv`
6. `derived\fidelity\fidelity_summary.csv`
7. 任何失败、timeout 或 warning 的原始文本

Mac 端会把新 ForceShuttle 与旧 Windows DasAtom canonical baseline 按 circuit name 对齐，重新计算：

- batch 与 per-atom fidelity ratio
- sensitivity 范围
- runtime geometric mean、median、wins/losses
- 每个电路的 schedule determinism
- 最终论文表格与图

论文 runtime 方法部分必须说明：DasAtom 是同一台 Ryzen 7 9700X 上的冻结 canonical baseline，但两种方法不是同一批 AB/BA 交错运行。这比混合 Mac/Windows 数据严谨，但弱于重新做完整 pairwise；不要把它写成 simultaneous 或 interleaved measurement。
