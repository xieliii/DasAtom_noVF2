#!/usr/bin/env python3
"""Rebuild canonical CSV/JSON summaries exclusively from per-attempt raw records."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from _common import (
    SCHEMA_VERSION,
    canonical_hash,
    csv_bytes,
    flatten_mapping,
    latest_execution_dirs,
    median,
    numeric,
    select_logical_execution,
    sha256_file,
    utc_now,
    validate_attempt,
    write_bytes_atomic,
    write_json_atomic,
)


ATTEMPT_BASE_FIELDS = (
    "circuit_index",
    "circuit_name",
    "method",
    "repetition",
    "seed",
    "execution",
    "outcome",
    "failure_kind",
    "return_code",
    "external_elapsed_ns",
    "external_elapsed_seconds",
    "input_sha256",
    "schedule_hash",
    "attempt_dir",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    return parser


def schedule_hash(payload: Mapping[str, Any]) -> str | None:
    direct = payload.get("schedule_hash")
    if isinstance(direct, str) and direct:
        return direct
    compiler = payload.get("compiler")
    if isinstance(compiler, Mapping):
        nested = compiler.get("schedule_hash")
        if isinstance(nested, str) and nested:
            return nested
    output_hash = payload.get("output_hash")
    if isinstance(output_hash, str) and output_hash:
        return output_hash
    return None


def _attempt_row(attempt_dir: Path, report: Mapping[str, Any], run_dir: Path) -> dict[str, Any]:
    status = report["status"]
    payload = report.get("compiler_output")
    metrics = report.get("metrics")
    row: dict[str, Any] = {
        "circuit_index": status.get("circuit_index"),
        "circuit_name": status.get("circuit_name"),
        "method": status.get("method"),
        "repetition": status.get("repetition"),
        "seed": status.get("seed"),
        "execution": status.get("execution"),
        "outcome": status.get("outcome"),
        "failure_kind": status.get("failure_kind"),
        "return_code": status.get("return_code"),
        "external_elapsed_ns": status.get("external_elapsed_ns"),
        "external_elapsed_seconds": status.get("external_elapsed_seconds"),
        "input_sha256": status.get("input_sha256"),
        "schedule_hash": schedule_hash(payload) if isinstance(payload, Mapping) else None,
        "attempt_dir": attempt_dir.relative_to(run_dir).as_posix(),
    }
    if isinstance(metrics, Mapping):
        row.update({f"metric.{key}": value for key, value in flatten_mapping(metrics).items()})
    return row


def _summary_rows(attempt_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[int, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in attempt_rows:
        key = (int(row["circuit_index"]), str(row["circuit_name"]), str(row["method"]))
        groups[key].append(row)
    summaries: list[dict[str, Any]] = []
    for (index, circuit, method), rows in sorted(groups.items()):
        outcomes = Counter(str(row["outcome"]) for row in rows)
        successful = [row for row in rows if row["outcome"] == "succeeded"]
        summary: dict[str, Any] = {
            "circuit_index": index,
            "circuit_name": circuit,
            "method": method,
            "logical_attempts": len(rows),
            "successful_attempts": len(successful),
            "failed_attempts": len(rows) - len(successful),
            "outcome_counts": json.dumps(dict(sorted(outcomes.items())), separators=(",", ":")),
            "external_elapsed_seconds_median": (
                median(float(row["external_elapsed_seconds"]) for row in successful) if successful else None
            ),
        }
        hashes = sorted({str(row["schedule_hash"]) for row in successful if row.get("schedule_hash")})
        summary["schedule_hash"] = hashes[0] if len(hashes) == 1 else None
        summary["schedule_hash_count"] = len(hashes)
        metric_names = sorted({key for row in successful for key in row if key.startswith("metric.")})
        for name in metric_names:
            values = [row[name] for row in successful if numeric(row.get(name))]
            if values:
                summary[f"{name}_median"] = median(float(value) for value in values)
            else:
                distinct = {str(row.get(name)) for row in successful if row.get(name) is not None}
                if len(distinct) == 1:
                    summary[name] = next(iter(distinct))
        summaries.append(summary)
    return summaries


def build_collection(run_dir: Path) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    grouped = latest_execution_dirs(run_dir)
    if not grouped:
        errors.append(f"no terminal attempt status files found under {run_dir / 'attempts'}")
    attempt_rows: list[dict[str, Any]] = []
    for identity, paths in sorted(grouped.items()):
        try:
            selected = select_logical_execution(paths)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        report = validate_attempt(selected)
        errors.extend(report["errors"])
        warnings.extend(report["warnings"])
        if report["passed"]:
            attempt_rows.append(_attempt_row(selected, report, run_dir))

    attempt_rows.sort(
        key=lambda row: (int(row["circuit_index"]), int(row["repetition"]), str(row["method"]))
    )
    successful = [row for row in attempt_rows if row["outcome"] == "succeeded"]
    consistency: dict[tuple[int, str, int], set[str]] = defaultdict(set)
    for row in successful:
        hash_value = row.get("schedule_hash")
        if not hash_value:
            errors.append(
                f"successful attempt has no stable schedule hash: {row['circuit_name']} {row['method']} r{row['repetition']}"
            )
            continue
        key = (int(row["circuit_index"]), str(row["method"]), int(row["seed"]))
        consistency[key].add(str(hash_value))
    nondeterminism: list[dict[str, Any]] = []
    for (index, method, seed), hashes in sorted(consistency.items()):
        if len(hashes) > 1:
            circuit = next(row["circuit_name"] for row in successful if int(row["circuit_index"]) == index)
            record = {
                "circuit_index": index,
                "circuit_name": circuit,
                "method": method,
                "seed": seed,
                "schedule_hashes": sorted(hashes),
            }
            nondeterminism.append(record)
            errors.append(
                f"same-seed schedule nondeterminism for {circuit} {method}: {', '.join(sorted(hashes))}"
            )

    summary_rows = _summary_rows(attempt_rows)
    status_counts = Counter(str(row["outcome"]) for row in attempt_rows)
    failure_kind_counts = Counter(
        str(row["failure_kind"]) for row in attempt_rows if row.get("failure_kind") is not None
    )
    summary_json = {
        "schema_version": SCHEMA_VERSION,
        "source": "per-attempt raw JSON; metrics and verification recomputed",
        "logical_attempt_count": len(attempt_rows),
        "successful_attempt_count": len(successful),
        "status_counts": dict(sorted(status_counts.items())),
        "failure_kind_counts": dict(sorted(failure_kind_counts.items())),
        "nondeterminism": nondeterminism,
        "rows": summary_rows,
    }
    attempt_fieldnames = list(ATTEMPT_BASE_FIELDS) + sorted(
        {name for row in attempt_rows for name in row if name not in ATTEMPT_BASE_FIELDS}
    )
    summary_base = [
        "circuit_index",
        "circuit_name",
        "method",
        "logical_attempts",
        "successful_attempts",
        "failed_attempts",
        "outcome_counts",
        "external_elapsed_seconds_median",
        "schedule_hash",
        "schedule_hash_count",
    ]
    summary_fieldnames = summary_base + sorted(
        {name for row in summary_rows for name in row if name not in summary_base}
    )
    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "attempt_rows": attempt_rows,
        "summary_json": summary_json,
        "attempts_json": {
            "schema_version": SCHEMA_VERSION,
            "source": "per-attempt raw JSON; metrics and verification recomputed",
            "rows": attempt_rows,
        },
        "attempts_csv": csv_bytes(attempt_rows, attempt_fieldnames),
        "summary_csv": csv_bytes(summary_rows, summary_fieldnames),
    }


def write_collection(collection: Mapping[str, Any], output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "attempts.json": output_dir / "attempts.json",
        "attempts.csv": output_dir / "attempts.csv",
        "summary.json": output_dir / "summary.json",
        "summary.csv": output_dir / "summary.csv",
    }
    write_json_atomic(paths["attempts.json"], collection["attempts_json"])
    write_bytes_atomic(paths["attempts.csv"], collection["attempts_csv"])
    write_json_atomic(paths["summary.json"], collection["summary_json"])
    write_bytes_atomic(paths["summary.csv"], collection["summary_csv"])
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_now(),
        "files": {
            name: {"sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            for name, path in paths.items()
        },
        "collection_hash": canonical_hash(
            {"attempts": collection["attempts_json"], "summary": collection["summary_json"]}
        ),
        "passed": collection["passed"],
        "errors": collection["errors"],
        "warnings": collection["warnings"],
    }
    write_json_atomic(output_dir / "derived_manifest.json", manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_dir = args.run_dir.resolve()
    output_dir = args.output_dir.resolve() if args.output_dir else run_dir / "derived"
    collection = build_collection(run_dir)
    manifest = write_collection(collection, output_dir)
    print(f"wrote canonical derived results to {output_dir}")
    print(f"logical attempts: {collection['summary_json']['logical_attempt_count']}")
    print(f"status counts: {collection['summary_json']['status_counts']}")
    for warning in collection["warnings"]:
        print(f"WARNING: {warning}", file=sys.stderr)
    for error in collection["errors"]:
        print(f"ERROR: {error}", file=sys.stderr)
    print(f"derived collection hash: {manifest['collection_hash']}")
    return 0 if collection["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
