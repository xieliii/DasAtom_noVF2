param(
    [switch]$Resume,
    [switch]$ApproveFull64,
    [switch]$ForceUnlock
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location -LiteralPath $RepoRoot

$Python = Join-Path $RepoRoot ".venv-repro\Scripts\python.exe"
$SourceLockPath = Join-Path $RepoRoot "results\control\source_lock.json"
$StatePath = Join-Path $RepoRoot "results\control\pipeline_state.json"
$LockPath = Join-Path $RepoRoot "results\control\pipeline.lock"
$LogPath = Join-Path $RepoRoot "results\control\pipeline.log"
$ArchiveDir = Join-Path $RepoRoot "archives"

if (-not (Test-Path -LiteralPath $SourceLockPath)) {
    throw "Missing source lock: $SourceLockPath"
}
$SourceLock = Get-Content -Raw -LiteralPath $SourceLockPath | ConvertFrom-Json
$ExpectedCommit = ([string]$SourceLock.commit).ToLowerInvariant()
if ($ExpectedCommit -notmatch '^[0-9a-f]{40}$') {
    throw "source_lock.json does not contain a valid 40-character commit SHA."
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Missing Python environment: $Python"
}
if ($ApproveFull64 -and -not $Resume) {
    throw "-ApproveFull64 is valid only with -Resume after an archived pilot and explicit user approval."
}

$env:PYTHONHASHSEED = "0"
$env:OMP_NUM_THREADS = "1"
$env:OPENBLAS_NUM_THREADS = "1"
$env:MKL_NUM_THREADS = "1"
$env:NUMEXPR_NUM_THREADS = "1"
$env:VECLIB_MAXIMUM_THREADS = "1"
$env:BLIS_NUM_THREADS = "1"
$env:RAYON_NUM_THREADS = "1"
$env:QISKIT_PARALLEL = "FALSE"

New-Item -ItemType Directory -Force -Path (Split-Path $StatePath -Parent), $ArchiveDir | Out-Null

if (Test-Path -LiteralPath $LockPath) {
    if (-not $ForceUnlock) {
        throw "Pipeline lock exists: $LockPath. Confirm no runner is alive, then use -Resume -ForceUnlock."
    }
    Remove-Item -LiteralPath $LockPath -Force
}

@{
    pid = $PID
    host = $env:COMPUTERNAME
    started_utc = [DateTime]::UtcNow.ToString("o")
} | ConvertTo-Json | Set-Content -LiteralPath $LockPath -Encoding UTF8

function Save-State([object]$State) {
    $State.updated_utc = [DateTime]::UtcNow.ToString("o")
    $State | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $StatePath -Encoding UTF8
}

function Sync-Provenance([string]$RunDir) {
    $ProvenanceDir = Join-Path $RunDir "provenance"
    New-Item -ItemType Directory -Force -Path $ProvenanceDir | Out-Null
    Copy-Item -Path (Join-Path $RepoRoot "results\preflight\*") `
        -Destination $ProvenanceDir -Recurse -Force
    Copy-Item -LiteralPath $SourceLockPath `
        -Destination (Join-Path $ProvenanceDir "source_lock.json") -Force
    Copy-Item -LiteralPath $StatePath `
        -Destination (Join-Path $ProvenanceDir "pipeline_state_at_archive.json") -Force

    $Status = @(git status --porcelain)
    if ($Status.Count -ne 0) {
        throw "Repository became dirty before archive: $($Status -join '; ')"
    }
    @{
        commit = (git rev-parse HEAD).Trim()
        branch_or_detached = (git rev-parse --abbrev-ref HEAD).Trim()
        status_porcelain = $Status
        captured_utc = [DateTime]::UtcNow.ToString("o")
    } | ConvertTo-Json -Depth 4 |
        Set-Content -LiteralPath (Join-Path $ProvenanceDir "git_state_at_archive.json") -Encoding UTF8
}

try {
    $ActualCommit = (git rev-parse HEAD).Trim()
    if ($ActualCommit -ne $ExpectedCommit) {
        throw "Commit mismatch: actual=$ActualCommit expected=$ExpectedCommit"
    }
    $Dirty = @(git status --porcelain)
    if ($Dirty.Count -ne 0) {
        throw "Repository is dirty: $($Dirty -join '; ')"
    }

    if ($Resume) {
        if (-not (Test-Path -LiteralPath $StatePath)) {
            throw "Cannot resume: missing $StatePath"
        }
        $State = Get-Content -Raw -LiteralPath $StatePath | ConvertFrom-Json
        if ($State.commit -ne $ExpectedCommit) {
            throw "Resume state commit does not match current commit."
        }
    }
    else {
        if (Test-Path -LiteralPath $StatePath) {
            throw "Pipeline state already exists. Use -Resume or choose a new fresh workspace."
        }
        $Stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
        $State = [PSCustomObject]@{
            schema = "forceshuttle_pipeline_state_v1"
            commit = $ExpectedCommit
            seed = 0
            timeout_sec = 10800
            pilot_dir = (Join-Path $RepoRoot ("results\pilot-" + $Stamp))
            full_dir = (Join-Path $RepoRoot ("results\full64-" + $Stamp))
            phase = "created"
            updated_utc = [DateTime]::UtcNow.ToString("o")
        }
        Save-State $State
    }

    $RunnerLockArgs = @()
    if ($ForceUnlock) {
        $RunnerLockArgs = @("--force-unlock")
    }
    $PilotArchiveVerified = $false
    $PilotGate = $null
    $PilotArchive = $null
    if ($Resume) {
        $PilotArchiveProperty = $State.PSObject.Properties["pilot_archive"]
        $PilotHashProperty = $State.PSObject.Properties["pilot_archive_sha256"]
        if ($null -ne $PilotArchiveProperty -and $null -ne $PilotHashProperty) {
            $CandidateArchive = [string]$PilotArchiveProperty.Value
            $CandidateHash = ([string]$PilotHashProperty.Value).ToLowerInvariant()
            $CandidateValidation = Join-Path ([string]$State.pilot_dir) "derived\validation.json"
            if ((Test-Path -LiteralPath $CandidateArchive) -and
                (Test-Path -LiteralPath $CandidateValidation)) {
                $ObservedHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $CandidateArchive).Hash.ToLowerInvariant()
                $ObservedGate = Get-Content -Raw -LiteralPath $CandidateValidation | ConvertFrom-Json
                $RevalidationText = (& $Python scripts\validate_canonical_run.py `
                    --run-dir ([string]$State.pilot_dir) `
                    --require-complete | Out-String)
                $RevalidationExit = $LASTEXITCODE
                $RevalidatedGate = $RevalidationText | ConvertFrom-Json
                if ($ObservedHash -eq $CandidateHash -and
                    $RevalidationExit -eq 0 -and
                    $ObservedGate.passed -and $ObservedGate.complete -and
                    $RevalidatedGate.passed -and $RevalidatedGate.complete -and
                    ([bool]$ObservedGate.pilot_gate_passed -eq [bool]$RevalidatedGate.pilot_gate_passed)) {
                    $StoredGateProperty = $State.PSObject.Properties["pilot_gate_passed"]
                    if ($null -eq $StoredGateProperty -or
                        ([bool]$StoredGateProperty.Value -ne [bool]$ObservedGate.pilot_gate_passed)) {
                        throw "Stored pilot gate and validation.json disagree. Stop for manual audit."
                    }
                    $PilotArchiveVerified = $true
                    $PilotArchive = $CandidateArchive
                    $PilotGate = $RevalidatedGate
                }
            }
        }
    }

    if ($ApproveFull64 -and -not $PilotArchiveVerified) {
        throw "-ApproveFull64 requires an existing pilot archive with matching SHA256 and a complete validated pilot. Resume without approval first."
    }

    if (-not $PilotArchiveVerified) {
        $PilotResumeArgs = @()
        if ($Resume -and (Test-Path -LiteralPath $State.pilot_dir)) {
            $PilotResumeArgs = @("--resume")
        }

        $State.phase = "pilot_running"
        Save-State $State
        & $Python scripts\run_canonical_pairwise.py `
            --run-dir $State.pilot_dir `
            --benchmark-dir "benchmarks\local64" `
            --list "configs\pilot12.txt" `
            --methods forceshuttle dasatom `
            --repetitions 1 `
            --timeout-sec 10800 `
            --seed 0 `
            @PilotResumeArgs `
            @RunnerLockArgs
        $PilotRunnerExit = $LASTEXITCODE
        $State | Add-Member -NotePropertyName pilot_runner_exit -NotePropertyValue $PilotRunnerExit -Force
        $State.phase = "pilot_collecting"
        Save-State $State

        & $Python scripts\collect_canonical_results.py --run-dir $State.pilot_dir
        if ($LASTEXITCODE -ne 0) {
            throw "Pilot collector failed."
        }

        $PilotValidation = Join-Path $State.pilot_dir "derived\validation.json"
        & $Python scripts\validate_canonical_run.py `
            --run-dir $State.pilot_dir `
            --require-complete `
            --json-out $PilotValidation
        $PilotValidatorExit = $LASTEXITCODE
        if (-not (Test-Path -LiteralPath $PilotValidation)) {
            throw "Pilot validator did not produce validation.json."
        }
        $PilotGate = Get-Content -Raw -LiteralPath $PilotValidation | ConvertFrom-Json

        $State | Add-Member -NotePropertyName pilot_validator_exit -NotePropertyValue $PilotValidatorExit -Force
        $State | Add-Member -NotePropertyName pilot_gate_passed -NotePropertyValue ([bool]$PilotGate.pilot_gate_passed) -Force
        $State.phase = "pilot_validated"
        Save-State $State

        if ($PilotGate.passed) {
            & $Python scripts\recompute_fidelity.py `
                --run-dir $State.pilot_dir `
                --config "configs\fidelity_models.json"
            if ($LASTEXITCODE -ne 0) {
                throw "Pilot fidelity sensitivity failed."
            }
        }

        $State.phase = "pilot_archiving"
        Save-State $State
        Sync-Provenance $State.pilot_dir
        $PilotArchive = Join-Path $ArchiveDir ((Split-Path $State.pilot_dir -Leaf) + ".zip")
        & $Python scripts\archive_canonical_run.py `
            --run-dir $State.pilot_dir `
            --output $PilotArchive `
            --force
        if ($LASTEXITCODE -ne 0) {
            throw "Pilot archive failed."
        }
        $PilotArchiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $PilotArchive).Hash.ToLowerInvariant()
        "$PilotArchiveHash  $(Split-Path $PilotArchive -Leaf)" |
            Set-Content -LiteralPath ($PilotArchive + ".sha256") -Encoding ASCII
        $State | Add-Member -NotePropertyName pilot_archive -NotePropertyValue $PilotArchive -Force
        $State | Add-Member -NotePropertyName pilot_archive_sha256 -NotePropertyValue $PilotArchiveHash -Force
        Save-State $State
    }
    else {
        Write-Host "Verified existing Pilot archive and SHA256; Pilot execution and re-archive are skipped."
    }

    if (-not $PilotGate.pilot_gate_passed) {
        $State.phase = "stopped_after_pilot_gate"
        Save-State $State
        throw "Pilot gate failed. Full64 is forbidden. Inspect validation.json and archive."
    }

    if (-not $ApproveFull64) {
        $ExistingApproval = $State.PSObject.Properties["full64_approval_recorded_utc"]
        if ($null -ne $ExistingApproval) {
            throw "Full64 was already approved and started. Resume it with -Resume -ApproveFull64."
        }
        $State.phase = "awaiting_full64_approval"
        Save-State $State
        Write-Host "Pilot gate PASS and archive complete. Stop now, report pilot results and SHA256, and wait for explicit user approval."
        Write-Host "After approval, resume with: -Resume -ApproveFull64"
        return
    }

    if ($null -eq $State.PSObject.Properties["full64_approval_recorded_utc"]) {
        $State | Add-Member -NotePropertyName full64_approval_recorded_utc `
            -NotePropertyValue ([DateTime]::UtcNow.ToString("o"))
        Save-State $State
    }

    $FullManifestPath = Join-Path $State.full_dir "run_manifest.json"
    $RepeatAlreadyRequested = $false
    if (Test-Path -LiteralPath $FullManifestPath) {
        $ExistingFullManifest = Get-Content -Raw -LiteralPath $FullManifestPath | ConvertFrom-Json
        $RepeatAlreadyRequested = ([int]$ExistingFullManifest.requested_repetitions -ge 3)
    }

    if (-not $RepeatAlreadyRequested) {
        $FullRound0ResumeArgs = @()
        if (Test-Path -LiteralPath $FullManifestPath) {
            $FullRound0ResumeArgs = @("--resume")
        }

        $State.phase = "full64_round0_running"
        Save-State $State
        & $Python scripts\run_canonical_pairwise.py `
            --run-dir $State.full_dir `
            --benchmark-dir "benchmarks\local64" `
            --list "configs\local64.txt" `
            --methods forceshuttle dasatom `
            --repetitions 1 `
            --timeout-sec 10800 `
            --seed 0 `
            @FullRound0ResumeArgs `
            @RunnerLockArgs
        $FullRound0RunnerExit = $LASTEXITCODE
        $State | Add-Member -NotePropertyName full_round0_runner_exit -NotePropertyValue $FullRound0RunnerExit -Force
        $State.phase = "full64_round0_collecting"
        Save-State $State

        & $Python scripts\collect_canonical_results.py --run-dir $State.full_dir
        if ($LASTEXITCODE -ne 0) {
            throw "Full64 round-0 collector failed."
        }

        $FullRound0Validation = Join-Path $State.full_dir "derived\validation_round0.json"
        & $Python scripts\validate_canonical_run.py `
            --run-dir $State.full_dir `
            --require-complete `
            --json-out $FullRound0Validation
        $FullRound0ValidatorExit = $LASTEXITCODE
        if (-not (Test-Path -LiteralPath $FullRound0Validation)) {
            throw "Full64 round-0 validator did not produce validation_round0.json."
        }
        $FullRound0Gate = Get-Content -Raw -LiteralPath $FullRound0Validation | ConvertFrom-Json

        $State | Add-Member -NotePropertyName full_round0_validator_exit -NotePropertyValue $FullRound0ValidatorExit -Force
        $State | Add-Member -NotePropertyName full_round0_gate_passed -NotePropertyValue ([bool]$FullRound0Gate.pilot_gate_passed) -Force
        $State.phase = "full64_round0_validated"
        Save-State $State

        if ($FullRound0ValidatorExit -ne 0 -or -not $FullRound0Gate.pilot_gate_passed) {
            $State.phase = "stopped_after_full64_round0_gate"
            Save-State $State
            if ($FullRound0ValidatorExit -eq 0) {
                Sync-Provenance $State.full_dir
                $FullRound0Archive = Join-Path $ArchiveDir ((Split-Path $State.full_dir -Leaf) + ".zip")
                & $Python scripts\archive_canonical_run.py `
                    --run-dir $State.full_dir `
                    --output $FullRound0Archive `
                    --force
                if ($LASTEXITCODE -ne 0) {
                    throw "Full64 round-0 gate failed and its archive also failed. Preserve the run directory."
                }
                $FullRound0ArchiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $FullRound0Archive).Hash.ToLowerInvariant()
                "$FullRound0ArchiveHash  $(Split-Path $FullRound0Archive -Leaf)" |
                    Set-Content -LiteralPath ($FullRound0Archive + ".sha256") -Encoding ASCII
                $State | Add-Member -NotePropertyName full_round0_archive -NotePropertyValue $FullRound0Archive -Force
                $State | Add-Member -NotePropertyName full_round0_archive_sha256 -NotePropertyValue $FullRound0ArchiveHash -Force
                Save-State $State
            }
            throw "Full64 round-0 gate failed. Fast repeats are forbidden; preserve the run directory and diagnostics."
        }
    }
    else {
        $Round0GateProperty = $State.PSObject.Properties["full_round0_gate_passed"]
        if ($null -eq $Round0GateProperty -or -not [bool]$Round0GateProperty.Value) {
            throw "Repeat manifest exists but pipeline state has no passed round-0 gate. Stop for manual audit."
        }
    }

    $State.phase = "full64_fast_repeats_running"
    Save-State $State
    & $Python scripts\run_canonical_pairwise.py `
        --run-dir $State.full_dir `
        --benchmark-dir "benchmarks\local64" `
        --list "configs\local64.txt" `
        --methods forceshuttle dasatom `
        --resume `
        --repetitions 3 `
        --repeat-threshold-sec 600 `
        --timeout-sec 10800 `
        --seed 0 `
        @RunnerLockArgs
    $FullRepeatRunnerExit = $LASTEXITCODE
    $State | Add-Member -NotePropertyName full_repeat_runner_exit -NotePropertyValue $FullRepeatRunnerExit -Force
    $State.phase = "full64_final_collecting"
    Save-State $State

    & $Python scripts\collect_canonical_results.py --run-dir $State.full_dir
    if ($LASTEXITCODE -ne 0) {
        throw "Full64 final collector failed."
    }

    $FullValidation = Join-Path $State.full_dir "derived\validation.json"
    & $Python scripts\validate_canonical_run.py `
        --run-dir $State.full_dir `
        --require-complete `
        --json-out $FullValidation
    $FullValidatorExit = $LASTEXITCODE
    if (-not (Test-Path -LiteralPath $FullValidation)) {
        throw "Full64 final validator did not produce validation.json."
    }
    $FullGate = Get-Content -Raw -LiteralPath $FullValidation | ConvertFrom-Json

    $State | Add-Member -NotePropertyName full_validator_exit -NotePropertyValue $FullValidatorExit -Force
    $State | Add-Member -NotePropertyName full_gate_passed -NotePropertyValue ([bool]$FullGate.pilot_gate_passed) -Force
    $State.phase = "full64_final_validated"
    Save-State $State

    if ($FullValidatorExit -eq 0 -and $FullGate.pilot_gate_passed) {
        & $Python scripts\recompute_fidelity.py `
            --run-dir $State.full_dir `
            --config "configs\fidelity_models.json"
        if ($LASTEXITCODE -ne 0) {
            throw "Full64 fidelity sensitivity failed."
        }
    }

    $State.phase = "full64_archiving"
    Save-State $State
    Sync-Provenance $State.full_dir
    $FullArchive = Join-Path $ArchiveDir ((Split-Path $State.full_dir -Leaf) + ".zip")
    & $Python scripts\archive_canonical_run.py `
        --run-dir $State.full_dir `
        --output $FullArchive `
        --force
    if ($LASTEXITCODE -ne 0) {
        throw "Full64 archive failed."
    }
    $FullArchiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $FullArchive).Hash.ToLowerInvariant()
    "$FullArchiveHash  $(Split-Path $FullArchive -Leaf)" |
        Set-Content -LiteralPath ($FullArchive + ".sha256") -Encoding ASCII
    $State | Add-Member -NotePropertyName full_archive -NotePropertyValue $FullArchive -Force
    $State | Add-Member -NotePropertyName full_archive_sha256 -NotePropertyValue $FullArchiveHash -Force
    Save-State $State

    if ($FullValidatorExit -ne 0 -or -not $FullGate.pilot_gate_passed) {
        $State.phase = "full64_invalid_archived"
        Save-State $State
        throw "Full64 completed but failed the canonical gate. Do not use its summary as paper data."
    }

    $State.phase = "complete"
    Save-State $State
}
catch {
    $_ | Out-String | Tee-Object -FilePath $LogPath -Append
    throw
}
finally {
    if (Test-Path -LiteralPath $LockPath) {
        Remove-Item -LiteralPath $LockPath -Force
    }
}
