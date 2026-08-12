#!/usr/bin/env python3
"""Read-only comparison of physical schedules, excluding source metadata and wall time."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from _common import latest_execution_dirs, load_json, select_logical_execution, write_json_atomic

FIELDS = (
    "layout_qubit_count",
    "grid_size",
    "partitions",
    "embeddings",
    "parallel_groups",
    "movement_transitions",
)


def _selected(run_dir: Path, circuit_name: str, method: str) -> dict:
    manifest = load_json(run_dir / "run_manifest.json")
    circuit = next(item for item in manifest["base_configuration"]["circuits"] if item["name"] == circuit_name)
    executions = latest_execution_dirs(run_dir).get((int(circuit["index"]), 0, method), [])
    if not executions:
        raise FileNotFoundError(f"missing compiler output for {circuit_name} {method} in {run_dir}")
    selected = select_logical_execution(executions)
    return load_json(selected / "compiler_output.json")


def compare(candidate_run_dir: Path, candidate_method: str, reference_run_dir: Path, reference_method: str, circuits: list[str]) -> dict:
    rows = []
    for name in circuits:
        candidate_payload = _selected(candidate_run_dir, name, candidate_method)
        reference_payload = _selected(reference_run_dir, name, reference_method)
        candidate = candidate_payload["compiler"]
        reference = reference_payload["compiler"]
        equal_fields = {field: candidate.get(field) == reference.get(field) for field in FIELDS}
        rows.append({"circuit_name": name, "physical_schedule_equal": all(equal_fields.values()), "fields_equal": equal_fields, "metrics_equal": candidate_payload.get("metrics") == reference_payload.get("metrics")})
    return {"schema_version": "physical-schedule-compare-v1", "matched_circuits": len(rows), "physical_schedules_equal": sum(row["physical_schedule_equal"] for row in rows), "metrics_equal": sum(row["metrics_equal"] for row in rows), "errors": [], "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-run-dir", type=Path, required=True)
    parser.add_argument("--candidate-method", required=True)
    parser.add_argument("--reference-run-dir", type=Path, required=True)
    parser.add_argument("--reference-method", required=True)
    parser.add_argument("--circuits", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    names = [line.strip() for line in args.circuits.read_text(encoding="utf-8-sig").splitlines() if line.strip() and not line.startswith("#")]
    result = compare(args.candidate_run_dir.resolve(), args.candidate_method, args.reference_run_dir.resolve(), args.reference_method, names)
    write_json_atomic(args.output, result)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
