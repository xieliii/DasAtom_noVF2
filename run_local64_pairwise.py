#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the 64-circuit ForceShuttle vs DasAtom benchmark pairwise on the same machine."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Project root containing DasAtom, DasAtom_Origin, and local64_benchmark_list.txt.",
    )
    parser.add_argument(
        "--bundle-name",
        type=str,
        default=".tmp_local64_m4",
        help="Name of the temporary bundle directory created under the project root.",
    )
    parser.add_argument(
        "--timeout-sec",
        type=int,
        default=3 * 60 * 60,
        help="Per-circuit timeout in seconds for both current and baseline.",
    )
    parser.add_argument(
        "--smoke-only",
        action="store_true",
        help="Only run the first circuit in the list for a smoke test.",
    )
    return parser.parse_args()


def load_benchmark_list(list_path: Path, smoke_only: bool) -> list[str]:
    circuits = [line.strip() for line in list_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if smoke_only:
        circuits = circuits[:1]
    return circuits


def find_qasm(data_root: Path, name: str) -> Path:
    matches = list(data_root.rglob(name))
    if not matches:
        raise FileNotFoundError(f"Cannot find {name} under {data_root}")
    return matches[0]


def result_file(out_dir: Path, stem: str) -> Path:
    return out_dir / "Rb2Re4" / f"{stem}.qasm_rb2.xlsx"


def run_one(
    label: str,
    cmd: list[str],
    out_dir: Path,
    stem: str,
    log_file: Path,
    timeout_sec: int,
    driver_log: Path,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    expected = result_file(out_dir, stem)
    if expected.exists():
        with driver_log.open("a", encoding="utf-8") as f:
            f.write(f"[{label} SKIP] {stem}\n")
        return

    with driver_log.open("a", encoding="utf-8") as f:
        f.write(f"[{label} RUN] {stem}\n")

    with log_file.open("w", encoding="utf-8") as lf:
        try:
            subprocess.run(
                cmd,
                stdout=lf,
                stderr=subprocess.STDOUT,
                timeout=timeout_sec,
                check=False,
            )
        except subprocess.TimeoutExpired:
            lf.write(f"\n[TIMEOUT] exceeded {timeout_sec} seconds\n")
            with driver_log.open("a", encoding="utf-8") as f:
                f.write(f"[{label} TIMEOUT] {stem}\n")


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
    driver_log = bundle / "local64_driver.log"
    current_out_root = cur / ".tmp_local48_current"
    baseline_out_root = base / ".tmp_local48_baseline"

    if not list_path.exists():
        raise FileNotFoundError(f"Missing benchmark list: {list_path}")
    if not (cur / "DasAtom.py").exists():
        raise FileNotFoundError(f"Missing current compiler entry: {cur / 'DasAtom.py'}")
    if not (base / "DasAtom.py").exists():
        raise FileNotFoundError(f"Missing baseline compiler entry: {base / 'DasAtom.py'}")

    bundle.mkdir(parents=True, exist_ok=True)
    cases.mkdir(parents=True, exist_ok=True)
    logs_current.mkdir(parents=True, exist_ok=True)
    logs_baseline.mkdir(parents=True, exist_ok=True)
    current_out_root.mkdir(parents=True, exist_ok=True)
    baseline_out_root.mkdir(parents=True, exist_ok=True)
    driver_log.write_text("", encoding="utf-8")

    circuits = load_benchmark_list(list_path, args.smoke_only)

    for qasm_name in circuits:
        src = find_qasm(cur / "Data", qasm_name)
        stem = qasm_name.removesuffix(".qasm")
        case_dir = cases / stem
        case_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, case_dir / qasm_name)

    for idx, qasm_name in enumerate(circuits):
        stem = qasm_name.removesuffix(".qasm")
        case_dir = cases / stem

        current_cmd = [
            sys.executable,
            str(cur / "DasAtom.py"),
            f"local64_cur_{stem}",
            str(case_dir),
            "--engine",
            "noVF2",
            "--results_folder",
            str(current_out_root / stem),
            "--no_save_embeddings",
        ]
        baseline_cmd = [
            sys.executable,
            str(base / "DasAtom.py"),
            f"local64_base_{stem}",
            str(case_dir),
            "--results_folder",
            str(baseline_out_root / stem),
            "--no_save_embeddings",
        ]

        if idx % 2 == 0:
            order = [
                ("CUR", current_cmd, current_out_root / stem, logs_current / f"{stem}.log"),
                ("BASE", baseline_cmd, baseline_out_root / stem, logs_baseline / f"{stem}.log"),
            ]
        else:
            order = [
                ("BASE", baseline_cmd, baseline_out_root / stem, logs_baseline / f"{stem}.log"),
                ("CUR", current_cmd, current_out_root / stem, logs_current / f"{stem}.log"),
            ]

        for label, cmd, out_dir, log_file in order:
            run_one(label, cmd, out_dir, stem, log_file, args.timeout_sec, driver_log)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
