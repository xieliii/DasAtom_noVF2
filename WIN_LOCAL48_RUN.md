# Windows local48 rerun guide

This rerun is intended to reproduce the same 48-circuit benchmark on a Windows laptop with the same-machine requirement:

- `ForceShuttle` (current) and `DasAtom` (baseline) must run on the same device.
- Use the exact same benchmark list: [local48_benchmark_list.txt](/Users/shirley/Dream/Code/DasAtom_noVF2/local48_benchmark_list.txt)
- Keep the Windows results separate from the current Mac run.

## 1. Copy the whole project to Windows

Copy the full `DasAtom_noVF2` directory to a Windows path, for example:

```powershell
D:\DasAtom_noVF2
```

The copied folder must contain:

- `DasAtom`
- `DasAtom_Origin`
- `local48_benchmark_list.txt`
- `run_local48_pairwise.py`

## 2. Prepare Python

Use one Python environment for both current and baseline.

Recommended:

```powershell
python --version
```

Install the dependencies you already use for the Mac run in that same environment.

## 3. Optional smoke test

Before the full run, do a one-circuit smoke test:

```powershell
cd D:\DasAtom_noVF2
python run_local48_pairwise.py --bundle-name .tmp_local48_win_smoke --smoke-only
```

If this finishes and produces one result on both sides, the Windows environment is ready.

## 4. Start the full 48-circuit run

```powershell
cd D:\DasAtom_noVF2
python run_local48_pairwise.py --bundle-name .tmp_local48_win
```

This script:

- prepares identical per-circuit input folders
- alternates current and baseline order circuit by circuit
- skips circuits that already have results
- applies the same `3h` timeout to both sides

## 5. Progress locations

Driver log:

```powershell
D:\DasAtom_noVF2\.tmp_local48_win\local48_driver.log
```

Current per-circuit logs:

```powershell
D:\DasAtom_noVF2\.tmp_local48_win\logs_current
```

Baseline per-circuit logs:

```powershell
D:\DasAtom_noVF2\.tmp_local48_win\logs_baseline
```

Current results:

```powershell
D:\DasAtom_noVF2\DasAtom\.tmp_local48_current
```

Baseline results:

```powershell
D:\DasAtom_noVF2\DasAtom_Origin\.tmp_local48_baseline
```

## 6. Do not mix result tables

Keep the Windows rerun as a separate experiment set.

Do not merge it directly with:

- the Mac `72circuits_main` results
- the server `main81_server_timeout` results

First generate a standalone Windows summary table, then decide whether it becomes the new main result set.
