#!/usr/bin/env python3
"""Summarize validated canonical ablation data without drawing conclusions."""
from __future__ import annotations

import argparse
import csv
import math
from collections import Counter, defaultdict
from pathlib import Path

from _common import csv_bytes, load_json, median, write_bytes_atomic, write_json_atomic


METHODS = ("forceshuttle", "fs_no_mcts", "fs_no_force", "fs_no_lookahead")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _gm(values: list[float]) -> float | None:
    return math.exp(sum(math.log(value) for value in values) / len(values)) if values and all(value > 0 for value in values) else None


def build(run_dir: Path) -> dict:
    validation = load_json(run_dir / "derived" / "validation.json")
    if not validation.get("passed") or not validation.get("complete"):
        raise RuntimeError("ablation summary requires a passed, complete validation.json")
    attempts = _read_csv(run_dir / "derived" / "attempts.csv")
    summaries = _read_csv(run_dir / "derived" / "summary.csv")
    fidelity = _read_csv(run_dir / "derived" / "fidelity" / "fidelity_summary.csv")
    grouped = defaultdict(list)
    for row in attempts:
        grouped[row["method"]].append(row)
    per_circuit = {(row["circuit_name"], row["method"]): row for row in summaries}
    absolute = []
    for method in METHODS:
        rows = grouped[method]
        outcomes = Counter(row["outcome"] for row in rows)
        medians = [float(row["external_elapsed_seconds_median"]) for (circuit, item_method), row in per_circuit.items() if item_method == method and row["external_elapsed_seconds_median"]]
        absolute.append({"method": method, "logical_attempts": len(rows), "successful": outcomes["succeeded"], "failed": len(rows)-outcomes["succeeded"], "timeout": outcomes["timeout"], "suite_time_seconds": sum(float(row["external_elapsed_seconds"]) for row in rows), "sum_per_circuit_median_seconds": sum(medians), "median_external_runtime_seconds": median(medians) if medians else None})
    comparisons = []
    for method in METHODS[1:]:
        ratios=[]; wins=ties=losses=0
        for (circuit, item_method), row in per_circuit.items():
            if item_method != method: continue
            full=per_circuit[(circuit,"forceshuttle")]
            a=float(full["external_elapsed_seconds_median"]); b=float(row["external_elapsed_seconds_median"])
            ratios.append(a/b)
            if b<a: wins+=1
            elif b>a: losses+=1
            else: ties+=1
        comparisons.append({"method":method,"runtime_speedup_geometric_mean":_gm(ratios),"runtime_speedup_median":median(ratios),"wins":wins,"ties":ties,"losses":losses})
    diagnostics=[]
    for row in attempts:
        payload=load_json(run_dir / row["attempt_dir"] / "compiler_output.json")
        diagnostics.append({"circuit_name":row["circuit_name"],"method":row["method"],"repetition":int(row["repetition"]),**payload["compiler"].get("ablation_diagnostics",{})})
    return {"schema_version":"canonical-ablation-summary-v1","absolute":absolute,"relative_to_forceshuttle":comparisons,"fidelity_rows":fidelity,"per_circuit_rows":summaries,"diagnostics":diagnostics}


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--run-dir",type=Path,required=True); args=parser.parse_args(); run_dir=args.run_dir.resolve(); result=build(run_dir); derived=run_dir/"derived"
    write_json_atomic(derived/"ablation_summary.json",{k:v for k,v in result.items() if k not in {"per_circuit_rows","diagnostics","fidelity_rows"}})
    write_bytes_atomic(derived/"ablation_summary.csv",csv_bytes(result["absolute"]+result["relative_to_forceshuttle"]))
    write_bytes_atomic(derived/"ablation_per_circuit.csv",csv_bytes(result["per_circuit_rows"]))
    write_bytes_atomic(derived/"ablation_diagnostics.csv",csv_bytes(result["diagnostics"]))
    return 0


if __name__ == "__main__": raise SystemExit(main())
