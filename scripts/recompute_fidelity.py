#!/usr/bin/env python3
"""Recompute explicitly labeled two-qubit-layout proxy scores and sensitivities."""

from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from _common import (
    REPO_ROOT,
    SCHEMA_VERSION,
    canonical_hash,
    compute_metrics,
    csv_bytes,
    load_json,
    median,
    sha256_file,
    utc_now,
    write_bytes_atomic,
    write_json_atomic,
)
from collect_canonical_results import build_collection


PARAMETERS = (
    "cz_fidelity",
    "cz_duration_us",
    "transfer_fidelity",
    "transfer_duration_us",
    "coherence_time_us",
    "move_speed_um_per_us",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "configs" / "fidelity_models.json")
    parser.add_argument("--output-dir", type=Path)
    return parser


def _validate_model(model: Mapping[str, Any]) -> None:
    missing = [name for name in PARAMETERS if name not in model]
    if missing:
        raise ValueError(f"fidelity model is missing parameters: {missing}")
    for name in ("cz_fidelity", "transfer_fidelity"):
        value = float(model[name])
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be in [0, 1], got {value}")
    for name in ("cz_duration_us", "transfer_duration_us", "coherence_time_us", "move_speed_um_per_us"):
        if float(model[name]) <= 0.0:
            raise ValueError(f"{name} must be positive")
    if model.get("transfer_semantics") not in {"batch", "per_atom"}:
        raise ValueError("transfer_semantics must be 'batch' or 'per_atom'")


def build_scenarios(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    explicit = config.get("models")
    if isinstance(explicit, list):
        scenarios: list[dict[str, Any]] = []
        for index, raw in enumerate(explicit):
            model = dict(raw)
            model.setdefault("name", f"model_{index:03d}")
            _validate_model(model)
            scenarios.append(model)
        if not scenarios:
            raise ValueError("models list is empty")
        return scenarios

    defaults = dict(config.get("defaults", {}))
    sensitivity = config.get("sensitivity", {})
    semantics_values = sensitivity.get("transfer_semantics", ["batch", "per_atom"])
    scenarios = []
    seen: set[str] = set()
    for semantics in semantics_values:
        baseline = {**defaults, "transfer_semantics": semantics}
        baseline["name"] = f"baseline__{semantics}"
        _validate_model(baseline)
        scenarios.append(baseline)
        seen.add(canonical_hash({key: baseline[key] for key in (*PARAMETERS, "transfer_semantics")}))
        for parameter in (
            "transfer_fidelity",
            "transfer_duration_us",
            "coherence_time_us",
            "move_speed_um_per_us",
        ):
            for value in sensitivity.get(parameter, []):
                model = {**defaults, parameter: value, "transfer_semantics": semantics}
                signature = canonical_hash({key: model[key] for key in (*PARAMETERS, "transfer_semantics")})
                if signature in seen:
                    continue
                model["name"] = f"sensitivity__{semantics}__{parameter}__{value:g}"
                _validate_model(model)
                scenarios.append(model)
                seen.add(signature)
    if not scenarios:
        raise ValueError("fidelity configuration produced no scenarios")
    return scenarios


def proxy_score(metrics: Mapping[str, Any], layout_qubits: int, model: Mapping[str, Any]) -> dict[str, float]:
    semantics = str(model["transfer_semantics"])
    if semantics == "batch":
        transfer_count = int(metrics["transfer_rounds"])
        movement_distance_um = float(metrics["batch_critical_distance_um"])
    else:
        transfer_count = int(metrics["atom_transfer_events"])
        movement_distance_um = float(metrics["endpoint_sum_distance_um"])
    gate_count = int(metrics["two_qubit_operation_count"])
    gate_layers = int(metrics["parallel_gate_group_count"])
    gate_duration_us = gate_layers * float(model["cz_duration_us"])
    transfer_duration_us = transfer_count * float(model["transfer_duration_us"])
    travel_duration_us = movement_distance_um / float(model["move_speed_um_per_us"])
    movement_duration_us = transfer_duration_us + travel_duration_us
    total_duration_us = gate_duration_us + movement_duration_us
    idle_exposure_us = max(
        0.0,
        layout_qubits * total_duration_us - gate_count * float(model["cz_duration_us"]),
    )
    coherence_factor = math.exp(-idle_exposure_us / float(model["coherence_time_us"]))
    cz_factor = float(model["cz_fidelity"]) ** gate_count
    transfer_factor = float(model["transfer_fidelity"]) ** transfer_count
    return {
        "transfer_count": transfer_count,
        "movement_distance_um": movement_distance_um,
        "gate_duration_us": gate_duration_us,
        "transfer_duration_total_us": transfer_duration_us,
        "travel_duration_us": travel_duration_us,
        "movement_duration_us": movement_duration_us,
        "total_duration_us": total_duration_us,
        "idle_exposure_us": idle_exposure_us,
        "coherence_factor": coherence_factor,
        "cz_factor": cz_factor,
        "transfer_factor": transfer_factor,
        "two_qubit_layout_proxy_score": coherence_factor * cz_factor * transfer_factor,
    }


def build_fidelity(run_dir: Path, config_path: Path) -> dict[str, Any]:
    collection = build_collection(run_dir)
    if not collection["passed"]:
        raise RuntimeError("raw collection failed:\n" + "\n".join(collection["errors"]))
    config = load_json(config_path)
    scenarios = build_scenarios(config)
    rows: list[dict[str, Any]] = []
    for attempt in collection["attempt_rows"]:
        if attempt["outcome"] != "succeeded":
            continue
        compiler_output = load_json(run_dir / str(attempt["attempt_dir"]) / "compiler_output.json")
        metrics = compute_metrics(compiler_output)
        layout_qubits = int(compiler_output["circuit"]["layout_qubit_count"])
        for scenario in scenarios:
            result = proxy_score(metrics, layout_qubits, scenario)
            rows.append(
                {
                    "circuit_index": attempt["circuit_index"],
                    "circuit_name": attempt["circuit_name"],
                    "method": attempt["method"],
                    "repetition": attempt["repetition"],
                    "seed": attempt["seed"],
                    "schedule_hash": attempt["schedule_hash"],
                    "scenario": scenario["name"],
                    "transfer_semantics": scenario["transfer_semantics"],
                    "layout_qubit_count": layout_qubits,
                    **{name: scenario[name] for name in PARAMETERS},
                    **result,
                }
            )
    rows.sort(
        key=lambda row: (
            int(row["circuit_index"]),
            str(row["method"]),
            str(row["scenario"]),
            int(row["repetition"]),
        )
    )
    grouped: dict[tuple[int, str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[
            (int(row["circuit_index"]), str(row["circuit_name"]), str(row["method"]), str(row["scenario"]))
        ].append(row)
    summary_rows: list[dict[str, Any]] = []
    for (index, circuit, method, scenario), group in sorted(grouped.items()):
        first = group[0]
        summary_rows.append(
            {
                "circuit_index": index,
                "circuit_name": circuit,
                "method": method,
                "scenario": scenario,
                "transfer_semantics": first["transfer_semantics"],
                "successful_repetitions": len(group),
                "two_qubit_layout_proxy_score_median": median(
                    float(row["two_qubit_layout_proxy_score"]) for row in group
                ),
                "total_duration_us_median": median(float(row["total_duration_us"]) for row in group),
                "idle_exposure_us_median": median(float(row["idle_exposure_us"]) for row in group),
                "transfer_count": first["transfer_count"],
                "movement_distance_um": first["movement_distance_um"],
                **{name: first[name] for name in PARAMETERS},
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "score_name": "two_qubit_layout_proxy_score",
        "scope": {
            "two_qubit_layout_only": True,
            "continuous_motion_verified": False,
            "one_qubit_gates_scheduled": False,
            "warning": (
                "Sensitivity values are model-dependent proxy scores, not measured hardware fidelity. "
                "Batch and per-atom transfer semantics are reported separately."
            ),
        },
        "config_sha256": sha256_file(config_path),
        "config": config,
        "scenario_count": len(scenarios),
        "attempt_rows": rows,
        "summary_rows": summary_rows,
    }


def write_fidelity(result: Mapping[str, Any], output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    attempts_json = {
        key: result[key]
        for key in ("schema_version", "score_name", "scope", "config_sha256", "scenario_count", "attempt_rows")
    }
    summary_json = {
        key: result[key]
        for key in ("schema_version", "score_name", "scope", "config_sha256", "scenario_count", "summary_rows")
    }
    files = {
        "fidelity_models.used.json": output_dir / "fidelity_models.used.json",
        "fidelity_sensitivity.json": output_dir / "fidelity_sensitivity.json",
        "fidelity_sensitivity.csv": output_dir / "fidelity_sensitivity.csv",
        "fidelity_summary.json": output_dir / "fidelity_summary.json",
        "fidelity_summary.csv": output_dir / "fidelity_summary.csv",
    }
    write_json_atomic(
        files["fidelity_models.used.json"],
        {"source_sha256": result["config_sha256"], "model_config": result["config"]},
    )
    write_json_atomic(files["fidelity_sensitivity.json"], attempts_json)
    write_bytes_atomic(files["fidelity_sensitivity.csv"], csv_bytes(result["attempt_rows"]))
    write_json_atomic(files["fidelity_summary.json"], summary_json)
    write_bytes_atomic(files["fidelity_summary.csv"], csv_bytes(result["summary_rows"]))
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_now(),
        "files": {
            name: {"sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            for name, path in files.items()
        },
        "result_hash": canonical_hash({"attempts": attempts_json, "summary": summary_json}),
    }
    write_json_atomic(output_dir / "fidelity_manifest.json", manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_dir = args.run_dir.resolve()
    config_path = args.config.resolve()
    output_dir = args.output_dir.resolve() if args.output_dir else run_dir / "derived" / "fidelity"
    result = build_fidelity(run_dir, config_path)
    manifest = write_fidelity(result, output_dir)
    print(f"wrote {len(result['attempt_rows'])} sensitivity rows to {output_dir}")
    print(f"result hash: {manifest['result_hash']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
