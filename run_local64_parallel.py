#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import shutil
import subprocess
import sys
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the 64-circuit ForceShuttle vs DasAtom benchmark in parallel."
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--bundle-name", type=str, default=".tmp_local64_win_parallel")
    parser.add_argument("--timeout-sec", type=int, default=3 * 60 * 60)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--smoke-only", action="store_true")
    return parser.parse_args()


def load_benchmark_list(list_path: Path, smoke_only: bool) -> list[str]:
    circuits = [line.strip() for line in list_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return circuits[:1] if smoke_only else circuits


def find_qasm(data_root: Path, name: str) -> Path:
    matches = list(data_root.rglob(name))
    if not matches:
        raise FileNotFoundError(f"Cannot find {name} under {data_root}")
    return matches[0]


def result_file(out_dir: Path, stem: str) -> Path:
    return out_dir / "Rb2Re4" / f"{stem}.qasm_rb2.xlsx"


def run_task(task: dict) -> tuple[str, str, float]:
    label = task["label"]
    stem = task["stem"]
    expected = task["expected"]
    log_file = task["log_file"]
    timeout_sec = task["timeout_sec"]
    cmd = task["cmd"]

    if expected.exists():
        return label, stem, 0.0

    expected.parent.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with log_file.open("w", encoding="utf-8") as lf:
        lf.write(f"[{label} START] {stem}\n")
        lf.flush()
        try:
            proc = subprocess.run(
                cmd,
                stdout=lf,
                stderr=subprocess.STDOUT,
                timeout=timeout_sec,
                check=False,
            )
            elapsed = time.time() - started
            lf.write(f"\n[{label} EXIT {proc.returncode}] {stem} elapsed={elapsed:.3f}s\n")
            if proc.returncode != 0:
                return f"{label}_FAIL", stem, elapsed
            if not expected.exists():
                return f"{label}_NO_RESULT", stem, elapsed
            return label, stem, elapsed
        except subprocess.TimeoutExpired:
            elapsed = time.time() - started
            lf.write(f"\n[{label} TIMEOUT] {stem} exceeded {timeout_sec}s elapsed={elapsed:.3f}s\n")
            return f"{label}_TIMEOUT", stem, elapsed


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    cur = root / "DasAtom"
    base = root / "DasAtom_Origin"
    list_path = root / "local64_benchmark_list.txt"

    bundle = root / args.bundle_name
    cases = bundle / "cases"
    logs_current = bundle / "logs_current"
    logs_baseline = bundle / "logs_baseline"
    driver_log = bundle / "local64_parallel_driver.log"
    current_out_root = cur / ".tmp_local64_current"
    baseline_out_root = base / ".tmp_local64_baseline"

    bundle.mkdir(parents=True, exist_ok=True)
    cases.mkdir(parents=True, exist_ok=True)
    logs_current.mkdir(parents=True, exist_ok=True)
    logs_baseline.mkdir(parents=True, exist_ok=True)
    current_out_root.mkdir(parents=True, exist_ok=True)
    baseline_out_root.mkdir(parents=True, exist_ok=True)

    circuits = load_benchmark_list(list_path, args.smoke_only)
    for qasm_name in circuits:
        src = find_qasm(cur / "Data", qasm_name)
        stem = qasm_name.removesuffix(".qasm")
        case_dir = cases / stem
        case_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, case_dir / qasm_name)

    tasks = []
    for qasm_name in circuits:
        stem = qasm_name.removesuffix(".qasm")
        case_dir = cases / stem
        cur_out = current_out_root / stem
        base_out = baseline_out_root / stem
        tasks.append(
            {
                "label": "CUR",
                "stem": stem,
                "expected": result_file(cur_out, stem),
                "log_file": logs_current / f"{stem}.log",
                "timeout_sec": args.timeout_sec,
                "cmd": [
                    sys.executable,
                    str(cur / "DasAtom.py"),
                    f"local64_cur_{stem}",
                    str(case_dir),
                    "--engine",
                    "noVF2",
                    "--results_folder",
                    str(cur_out),
                    "--no_save_embeddings",
                ],
            }
        )
        tasks.append(
            {
                "label": "BASE",
                "stem": stem,
                "expected": result_file(base_out, stem),
                "log_file": logs_baseline / f"{stem}.log",
                "timeout_sec": args.timeout_sec,
                "cmd": [
                    sys.executable,
                    str(base / "DasAtom.py"),
                    f"local64_base_{stem}",
                    str(case_dir),
                    "--results_folder",
                    str(base_out),
                    "--no_save_embeddings",
                ],
            }
        )

    pending = [task for task in tasks if not task["expected"].exists()]
    with driver_log.open("a", encoding="utf-8") as f:
        f.write(f"[START] workers={args.workers} total_tasks={len(tasks)} pending={len(pending)}\n")

    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as executor:
        future_to_task = {executor.submit(run_task, task): task for task in pending}
        for future in concurrent.futures.as_completed(future_to_task):
            label, stem, elapsed = future.result()
            with driver_log.open("a", encoding="utf-8") as f:
                f.write(f"[{label}] {stem} elapsed={elapsed:.3f}s\n")

    with driver_log.open("a", encoding="utf-8") as f:
        f.write("[DONE]\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
