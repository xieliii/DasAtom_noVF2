#!/usr/bin/env python3
"""Compare a ForceShuttle-only run with a frozen canonical DasAtom baseline."""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from pathlib import Path
from typing import Any, Mapping, Sequence

from _common import (
    SCHEMA_VERSION,
    canonical_hash,
    csv_bytes,
    load_json,
    sha256_file,
    utc_now,
    write_bytes_atomic,
    write_json_atomic,
)


SOURCE_PREFIXES = ("DasAtom_Origin/", "canonical/", "scripts/")
BASELINE_METHOD = "dasatom"
CANDIDATE_METHOD = "forceshuttle"
BASELINE_SCENARIOS = ("baseline__batch", "baseline__per_atom")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-run-dir", type=Path, required=True)
    parser.add_argument("--baseline-run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--allow-host-mismatch",
        action="store_true",
        help="Allow a diagnostic cross-host comparison; output is marked non-paper-eligible.",
    )
    parser.add_argument(
        "--diagnostic-allow-one-repetition",
        action="store_true",
        help="Allow a one-repetition candidate diagnostic; output is marked non-paper-eligible.",
    )
    return parser


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _require_validation(run_dir: Path, label: str) -> Mapping[str, Any]:
    path = run_dir / "derived" / "validation.json"
    if not path.is_file():
        raise ValueError(f"{label} is missing {path.relative_to(run_dir)}")
    validation = load_json(path)
    if not validation.get("passed") or not validation.get("complete"):
        raise ValueError(
            f"{label} validation is not passed+complete: "
            f"passed={validation.get('passed')} complete={validation.get('complete')}"
        )
    return validation


def _manifest(run_dir: Path, label: str) -> Mapping[str, Any]:
    path = run_dir / "run_manifest.json"
    if not path.is_file():
        raise ValueError(f"{label} is missing run_manifest.json")
    manifest = load_json(path)
    if not isinstance(manifest.get("base_configuration"), Mapping):
        raise ValueError(f"{label} run manifest has no base_configuration")
    return manifest


def _fidelity_source_hash(run_dir: Path, label: str) -> str:
    path = run_dir / "derived" / "fidelity" / "fidelity_models.used.json"
    if not path.is_file():
        raise ValueError(f"{label} is missing fidelity_models.used.json")
    value = load_json(path).get("source_sha256")
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{label} fidelity model has no valid source SHA256")
    return value


def _host_compatibility(
    candidate: Mapping[str, Any], baseline: Mapping[str, Any]
) -> tuple[bool, list[str]]:
    errors = []
    for key in ("hostname", "system", "machine", "python_major_minor"):
        if candidate.get(key) != baseline.get(key):
            errors.append(
                f"host fingerprint mismatch for {key}: "
                f"candidate={candidate.get(key)!r} baseline={baseline.get(key)!r}"
            )
    return not errors, errors


def _source_compatibility(
    candidate: Mapping[str, Any], baseline: Mapping[str, Any]
) -> tuple[bool, list[str], int]:
    candidate_files = candidate.get("files", {})
    baseline_files = baseline.get("files", {})
    errors = []
    checked = 0
    for path, expected in sorted(baseline_files.items()):
        if not path.startswith(SOURCE_PREFIXES):
            continue
        checked += 1
        observed = candidate_files.get(path)
        if observed != expected:
            errors.append(
                f"source mismatch for {path}: candidate={observed!r} baseline={expected!r}"
            )
    if checked == 0:
        errors.append("baseline source snapshot contains no comparable files")
    return not errors, errors, checked


def _circuit_signature(base: Mapping[str, Any]) -> list[tuple[int, str, str, int]]:
    result = []
    for item in base.get("circuits", []):
        result.append(
            (
                int(item["index"]),
                str(item["name"]),
                str(item["sha256"]),
                int(item["size_bytes"]),
            )
        )
    return result


def _aggregate(values: Sequence[float], *, tolerance: float = 1e-12) -> dict[str, Any]:
    if not values:
        raise ValueError("cannot aggregate an empty value set")
    if any(value <= 0.0 or not math.isfinite(value) for value in values):
        raise ValueError("geometric ratios must be finite and positive")
    wins = sum(value > 1.0 + tolerance for value in values)
    ties = sum(abs(value - 1.0) <= tolerance for value in values)
    return {
        "count": len(values),
        "geometric_mean": math.exp(sum(math.log(value) for value in values) / len(values)),
        "median": statistics.median(values),
        "wins": wins,
        "ties": ties,
        "losses": len(values) - wins - ties,
        "minimum": min(values),
        "maximum": max(values),
    }


def _summary_index(rows: Sequence[Mapping[str, str]], method: str) -> dict[str, Mapping[str, str]]:
    result = {}
    for row in rows:
        if row.get("method") != method:
            continue
        circuit = str(row["circuit_name"])
        if circuit in result:
            raise ValueError(f"duplicate summary row for {method}/{circuit}")
        result[circuit] = row
    return result


def _fidelity_index(
    rows: Sequence[Mapping[str, str]], method: str
) -> dict[tuple[str, str], Mapping[str, str]]:
    result = {}
    for row in rows:
        if row.get("method") != method:
            continue
        key = (str(row["circuit_name"]), str(row["scenario"]))
        if key in result:
            raise ValueError(f"duplicate fidelity row for {method}/{key[0]}/{key[1]}")
        result[key] = row
    return result


def _successful_runtime(row: Mapping[str, str], *, label: str) -> float | None:
    successful_attempts = int(row.get("successful_attempts", "0") or "0")
    runtime_text = row.get("external_elapsed_seconds_median", "")
    if successful_attempts == 0:
        if runtime_text:
            raise ValueError(f"{label} has runtime but no successful attempts")
        return None
    if not runtime_text:
        raise ValueError(f"{label} has successful attempts but no runtime")
    runtime = float(runtime_text)
    if runtime <= 0.0 or not math.isfinite(runtime):
        raise ValueError(f"{label} runtime must be finite and positive")
    return runtime


def build_comparison(
    candidate_run_dir: Path,
    baseline_run_dir: Path,
    *,
    allow_host_mismatch: bool = False,
    diagnostic_allow_one_repetition: bool = False,
) -> dict[str, Any]:
    candidate_run_dir = candidate_run_dir.resolve(strict=True)
    baseline_run_dir = baseline_run_dir.resolve(strict=True)
    candidate_validation = _require_validation(candidate_run_dir, "candidate")
    baseline_validation = _require_validation(baseline_run_dir, "baseline")
    candidate_manifest = _manifest(candidate_run_dir, "candidate")
    baseline_manifest = _manifest(baseline_run_dir, "baseline")
    candidate_base = candidate_manifest["base_configuration"]
    baseline_base = baseline_manifest["base_configuration"]

    errors: list[str] = []
    warnings: list[str] = []
    required_candidate_attempts = 1 if diagnostic_allow_one_repetition else 3
    if diagnostic_allow_one_repetition:
        warnings.append("candidate repetition requirement reduced to one for diagnostics")

    if candidate_base.get("methods") != [CANDIDATE_METHOD]:
        errors.append(
            f"candidate methods must be [{CANDIDATE_METHOD!r}], got {candidate_base.get('methods')!r}"
        )
    if BASELINE_METHOD not in baseline_base.get("methods", []):
        errors.append("baseline run does not contain dasatom")
    if int(candidate_base.get("seed", -1)) != int(baseline_base.get("seed", -2)):
        errors.append("candidate and baseline seed differ")
    if candidate_base.get("benchmark_list_sha256") != baseline_base.get("benchmark_list_sha256"):
        errors.append("candidate and baseline benchmark list SHA256 differ")
    if _circuit_signature(candidate_base) != _circuit_signature(baseline_base):
        errors.append("candidate and baseline circuit manifests differ")

    source_ok, source_errors, source_checked = _source_compatibility(
        candidate_base.get("source_snapshot", {}),
        baseline_base.get("source_snapshot", {}),
    )
    errors.extend(source_errors)

    candidate_model_hash = _fidelity_source_hash(candidate_run_dir, "candidate")
    baseline_model_hash = _fidelity_source_hash(baseline_run_dir, "baseline")
    if candidate_model_hash != baseline_model_hash:
        errors.append("candidate and baseline fidelity model SHA256 differ")

    host_ok, host_errors = _host_compatibility(
        candidate_base.get("host_fingerprint", {}),
        baseline_base.get("host_fingerprint", {}),
    )
    if not host_ok:
        if allow_host_mismatch:
            warnings.extend(host_errors)
        else:
            errors.extend(host_errors)

    candidate_summary = _summary_index(
        _read_csv(candidate_run_dir / "derived" / "summary.csv"), CANDIDATE_METHOD
    )
    baseline_summary = _summary_index(
        _read_csv(baseline_run_dir / "derived" / "summary.csv"), BASELINE_METHOD
    )
    expected_circuits = [item[1] for item in _circuit_signature(candidate_base)]
    if set(candidate_summary) != set(expected_circuits):
        errors.append("candidate summary does not contain exactly the manifest circuits")
    for circuit, row in sorted(candidate_summary.items()):
        if (
            int(row["successful_attempts"]) != required_candidate_attempts
            or int(row["logical_attempts"]) != required_candidate_attempts
        ):
            errors.append(
                f"candidate {circuit} does not have exactly "
                f"{required_candidate_attempts} successful attempts"
            )
        if int(row["schedule_hash_count"]) != 1:
            errors.append(f"candidate {circuit} is not schedule-deterministic across repetitions")
        try:
            _successful_runtime(row, label=f"candidate {circuit}")
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))

    baseline_runtimes: dict[str, float] = {}
    for circuit, row in sorted(baseline_summary.items()):
        try:
            runtime = _successful_runtime(row, label=f"baseline {circuit}")
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))
            continue
        if runtime is not None:
            baseline_runtimes[circuit] = runtime

    candidate_fidelity = _fidelity_index(
        _read_csv(candidate_run_dir / "derived" / "fidelity" / "fidelity_summary.csv"),
        CANDIDATE_METHOD,
    )
    baseline_fidelity = _fidelity_index(
        _read_csv(baseline_run_dir / "derived" / "fidelity" / "fidelity_summary.csv"),
        BASELINE_METHOD,
    )
    scenarios = sorted({scenario for _, scenario in candidate_fidelity})
    if set(scenarios) != {scenario for _, scenario in baseline_fidelity}:
        errors.append("candidate and baseline fidelity scenario sets differ")

    if errors:
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at_utc": utc_now(),
            "passed": False,
            "paper_eligible": False,
            "errors": errors,
            "warnings": warnings,
            "compatibility": {
                "source_compatible": source_ok,
                "source_files_checked": source_checked,
                "host_compatible": host_ok,
                "fidelity_model_sha256": candidate_model_hash,
            },
        }

    matched = sorted(set(candidate_summary).intersection(baseline_runtimes))
    comparison_rows: list[dict[str, Any]] = []
    runtime_ratios = []
    scenario_ratios: dict[str, list[float]] = {scenario: [] for scenario in scenarios}
    for circuit in matched:
        candidate_row = candidate_summary[circuit]
        baseline_row = baseline_summary[circuit]
        candidate_runtime = _successful_runtime(candidate_row, label=f"candidate {circuit}")
        baseline_runtime = baseline_runtimes[circuit]
        if candidate_runtime is None:
            raise ValueError(f"candidate {circuit} unexpectedly has no successful runtime")
        runtime_ratio = baseline_runtime / candidate_runtime
        runtime_ratios.append(runtime_ratio)
        row: dict[str, Any] = {
            "circuit_name": circuit,
            "forceshuttle_runtime_seconds_median": candidate_runtime,
            "dasatom_runtime_seconds_median": baseline_runtime,
            "runtime_speedup_dasatom_over_forceshuttle": runtime_ratio,
        }
        for scenario in scenarios:
            candidate_fidelity_row = candidate_fidelity.get((circuit, scenario))
            baseline_fidelity_row = baseline_fidelity.get((circuit, scenario))
            if candidate_fidelity_row is None or baseline_fidelity_row is None:
                raise ValueError(f"missing fidelity row for {circuit}/{scenario}")
            candidate_score = float(candidate_fidelity_row["two_qubit_layout_proxy_score_median"])
            baseline_score = float(baseline_fidelity_row["two_qubit_layout_proxy_score_median"])
            ratio = candidate_score / baseline_score
            scenario_ratios[scenario].append(ratio)
            if scenario in BASELINE_SCENARIOS:
                suffix = "batch" if scenario.endswith("batch") else "per_atom"
                row[f"forceshuttle_fidelity_{suffix}"] = candidate_score
                row[f"dasatom_fidelity_{suffix}"] = baseline_score
                row[f"fidelity_ratio_{suffix}"] = ratio
        comparison_rows.append(row)

    unavailable = sorted(set(expected_circuits) - set(baseline_runtimes))
    scenario_aggregates = {
        scenario: _aggregate(values) for scenario, values in scenario_ratios.items() if values
    }
    aggregate = {
        "runtime_speedup_dasatom_over_forceshuttle": _aggregate(runtime_ratios),
        "fidelity_ratio_forceshuttle_over_dasatom": scenario_aggregates,
    }
    result = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_now(),
        "passed": True,
        "paper_eligible": (
            host_ok and not allow_host_mismatch and not diagnostic_allow_one_repetition
        ),
        "errors": [],
        "warnings": warnings,
        "scope": {
            "candidate_method": CANDIDATE_METHOD,
            "baseline_method": BASELINE_METHOD,
            "runtime_measurement_design": "same_machine_frozen_baseline_non_interleaved",
            "fidelity_scope": "two_qubit_endpoint_layout_proxy",
            "candidate_repetitions_required": required_candidate_attempts,
        },
        "compatibility": {
            "source_compatible": source_ok,
            "source_files_checked": source_checked,
            "host_compatible": host_ok,
            "benchmark_list_sha256": candidate_base.get("benchmark_list_sha256"),
            "fidelity_model_sha256": candidate_model_hash,
            "candidate_commit": candidate_base.get("git_commit"),
            "baseline_commit": baseline_base.get("git_commit"),
            "candidate_validation_hash": canonical_hash(candidate_validation),
            "baseline_validation_hash": canonical_hash(baseline_validation),
        },
        "matched_circuit_count": len(comparison_rows),
        "baseline_unavailable_circuits": unavailable,
        "aggregate": aggregate,
        "rows": comparison_rows,
    }
    result["result_hash"] = canonical_hash(
        {"compatibility": result["compatibility"], "aggregate": aggregate, "rows": comparison_rows}
    )
    return result


def write_comparison(result: Mapping[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "frozen_baseline_comparison.json"
    csv_path = output_dir / "frozen_baseline_comparison.csv"
    manifest_path = output_dir / "frozen_baseline_manifest.json"
    write_json_atomic(report_path, result)
    write_bytes_atomic(csv_path, csv_bytes(result.get("rows", [])))
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_now(),
        "passed": result.get("passed", False),
        "paper_eligible": result.get("paper_eligible", False),
        "result_hash": result.get("result_hash"),
        "files": {
            report_path.name: {"sha256": sha256_file(report_path), "size_bytes": report_path.stat().st_size},
            csv_path.name: {"sha256": sha256_file(csv_path), "size_bytes": csv_path.stat().st_size},
        },
    }
    write_json_atomic(manifest_path, manifest)


def main() -> int:
    args = build_parser().parse_args()
    candidate = args.candidate_run_dir.resolve(strict=True)
    baseline = args.baseline_run_dir.resolve(strict=True)
    output = (args.output_dir or candidate / "derived" / "frozen_baseline").resolve()
    result = build_comparison(
        candidate,
        baseline,
        allow_host_mismatch=bool(args.allow_host_mismatch),
        diagnostic_allow_one_repetition=bool(args.diagnostic_allow_one_repetition),
    )
    write_comparison(result, output)
    print(f"comparison passed: {result['passed']}")
    print(f"paper eligible: {result['paper_eligible']}")
    print(f"matched circuits: {result.get('matched_circuit_count', 0)}")
    if result.get("result_hash"):
        print(f"result hash: {result['result_hash']}")
    for error in result.get("errors", []):
        print(f"ERROR: {error}")
    for warning in result.get("warnings", []):
        print(f"WARNING: {warning}")
    return 0 if result.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
