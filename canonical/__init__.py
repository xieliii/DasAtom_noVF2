"""Canonical endpoint-layout capture and verification APIs."""

from .adapter import compile_single_qasm
from .metrics import compute_metrics
from .qasm import CircuitIR, Operation, parse_qasm
from .serialization import (
    atomic_write_json,
    canonical_json_bytes,
    canonical_sha256,
    finalize_integrity,
)
from .verifier import VerificationError, verify_compiler_output

__all__ = [
    "CircuitIR",
    "Operation",
    "VerificationError",
    "atomic_write_json",
    "canonical_json_bytes",
    "canonical_sha256",
    "compile_single_qasm",
    "compute_metrics",
    "finalize_integrity",
    "parse_qasm",
    "verify_compiler_output",
]
