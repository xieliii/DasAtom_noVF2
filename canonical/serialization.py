"""Canonical JSON hashing and atomic output helpers."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize JSON deterministically with no platform-dependent whitespace."""

    return (
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def payload_without_integrity(payload: dict) -> dict:
    clean = deepcopy(payload)
    clean.pop("integrity", None)
    return clean


def integrity_projection(payload: dict) -> dict:
    """Content protected by canonical_payload_sha256.

    ``verification`` is a derived report computed after integrity is fixed.  It
    is independently recomputed and compared by the run validator, avoiding a
    self-referential hash/report cycle.
    """

    clean = payload_without_integrity(payload)
    clean.pop("verification", None)
    return clean


def schedule_projection(payload: dict) -> dict:
    """Return the deterministic algorithm output covered by schedule_sha256."""

    compiler = payload.get("compiler", {})
    return {
        "schema_version": payload.get("schema_version"),
        "scope": payload.get("scope"),
        "method": payload.get("method"),
        "input": payload.get("input"),
        "config": payload.get("config"),
        "circuit": payload.get("circuit"),
        "compiler": {
            "layout_qubit_count": compiler.get("layout_qubit_count"),
            "grid_size": compiler.get("grid_size"),
            "partitions": compiler.get("partitions"),
            "embeddings": compiler.get("embeddings"),
            "parallel_groups": compiler.get("parallel_groups"),
            "movement_transitions": compiler.get("movement_transitions"),
        },
    }


def finalize_integrity(payload: dict) -> dict:
    """Return a copy with non-self-referential canonical integrity hashes."""

    final = payload_without_integrity(payload)
    final.pop("schedule_hash", None)
    schedule_hash = canonical_sha256(schedule_projection(final))
    final["schedule_hash"] = schedule_hash
    final["integrity"] = {
        "algorithm": "sha256",
        "schedule_sha256": schedule_hash,
        "canonical_payload_sha256": canonical_sha256(integrity_projection(final)),
    }
    return final


def atomic_write_json(path: str | Path, value: Any) -> Path:
    """Write canonical JSON using replace-on-close in the destination directory."""

    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = canonical_json_bytes(value)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return destination
