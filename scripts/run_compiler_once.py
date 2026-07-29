#!/usr/bin/env python3
"""Run one explicit QASM and atomically emit canonical compiler_output.json."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from canonical import atomic_write_json, compile_single_qasm  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Capture and verify one endpoint-layout schedule from ForceShuttle "
            "or the original DasAtom implementation."
        )
    )
    parser.add_argument("--method", required=True, choices=("forceshuttle", "dasatom"))
    parser.add_argument("--qasm", required=True, type=Path, help="Exact QASM file; no recursive lookup is performed.")
    parser.add_argument(
        "--output",
        "--output-dir",
        dest="output",
        required=True,
        type=Path,
        help="Destination JSON path, or an attempt directory containing compiler_output.json.",
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--rb", "--interaction-radius", dest="rb", type=float, default=2.0)
    parser.add_argument("--re", "--exclusion-radius", dest="re", type=float, default=4.0)
    parser.add_argument("--grid-pitch-x-um", type=float, default=3.0)
    parser.add_argument("--grid-pitch-y-um", type=float, default=3.0)
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=REPOSITORY_ROOT,
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def output_path(raw: Path) -> Path:
    expanded = raw.expanduser()
    if expanded.suffix.lower() == ".json":
        return expanded
    return expanded / "compiler_output.json"


def main() -> int:
    args = parse_args()
    destination = output_path(args.output)
    payload = compile_single_qasm(
        args.method,
        args.qasm,
        repository_root=args.repository_root,
        seed=args.seed,
        interaction_radius=args.rb,
        exclusion_radius=args.re,
        grid_pitch_x_um=args.grid_pitch_x_um,
        grid_pitch_y_um=args.grid_pitch_y_um,
    )
    written = atomic_write_json(destination, payload)
    summary = {
        "compiler_output": str(written),
        "method": payload["method"]["id"],
        "qasm_sha256": payload["input"]["qasm_sha256"],
        "schedule_hash": payload["schedule_hash"],
        "verification_passed": payload["verification"]["passed"],
    }
    print(json.dumps(summary, sort_keys=True))
    return 0 if payload["verification"]["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
