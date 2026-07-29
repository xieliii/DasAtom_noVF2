#!/usr/bin/env python3
"""Validate and package a canonical run as a ZIP with a SHA256 sidecar."""

from __future__ import annotations

import argparse
import os
import sys
import zipfile
from pathlib import Path
from typing import Any, Sequence

from _common import (
    SCHEMA_VERSION,
    RunLock,
    canonical_hash,
    sha256_file,
    utc_now,
    write_json_atomic,
    write_text_atomic,
)
from validate_canonical_run import validate_run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--force-unlock", action="store_true")
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Create a diagnostic archive marked ineligible for paper results.",
    )
    return parser


def _archive_files(run_dir: Path) -> list[Path]:
    ignored = {".canonical-run.lock", "archive_manifest.json"}
    files: list[Path] = []
    for path in sorted(run_dir.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"refusing to archive symbolic link: {path}")
        if path.is_file() and path.name not in ignored and not path.name.startswith(".canonical-run.lock"):
            files.append(path)
    return files


def _write_zip(run_dir: Path, output: Path, files: Sequence[Path]) -> None:
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in files:
                relative = path.relative_to(run_dir).as_posix()
                archive.write(path, arcname=f"{run_dir.name}/{relative}")
        with zipfile.ZipFile(temporary, "r") as archive:
            bad = archive.testzip()
            if bad is not None:
                raise RuntimeError(f"ZIP integrity check failed at {bad}")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_dir = args.run_dir.resolve()
    if not run_dir.is_dir():
        raise FileNotFoundError(f"run directory does not exist: {run_dir}")
    output = args.output.resolve() if args.output else run_dir.parent / f"{run_dir.name}.zip"
    if output == run_dir or run_dir in output.parents:
        raise ValueError("archive output must be outside the run directory")
    if output.exists() and not args.force:
        raise FileExistsError(f"archive already exists; use --force to replace: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    with RunLock(run_dir, force_unlock=args.force_unlock):
        report = validate_run(run_dir, run_dir / "derived", require_complete=not args.allow_incomplete)
        write_json_atomic(run_dir / "archive_validation.json", report)
        if not report["passed"]:
            raise RuntimeError("refusing to archive a run that fails canonical validation")
        files = _archive_files(run_dir)
        file_entries = {
            path.relative_to(run_dir).as_posix(): {
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
            for path in files
        }
        archive_manifest: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "created_at_utc": utc_now(),
            "run_directory_name": run_dir.name,
            "validation_passed": True,
            "complete": report.get("complete", False),
            "paper_eligible": bool(report.get("complete", False) and report.get("pilot_gate_passed", False)),
            "allow_incomplete_requested": args.allow_incomplete,
            "pilot_gate_passed": report.get("pilot_gate_passed"),
            "file_count_excluding_manifest": len(file_entries),
            "files": file_entries,
            "files_hash": canonical_hash(file_entries),
        }
        write_json_atomic(run_dir / "archive_manifest.json", archive_manifest)
        files = [*_archive_files(run_dir), run_dir / "archive_manifest.json"]
        files.sort()
        _write_zip(run_dir, output, files)

    zip_hash = sha256_file(output)
    sidecar = output.with_name(output.name + ".sha256")
    write_text_atomic(sidecar, f"{zip_hash}  {output.name}\n")
    print(f"archive: {output}")
    print(f"sha256: {zip_hash}")
    print(f"sidecar: {sidecar}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
