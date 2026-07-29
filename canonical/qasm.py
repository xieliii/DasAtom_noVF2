"""Small, independent OpenQASM 2 parser for experiment auditing.

The compiler still uses Qiskit.  This parser deliberately does not: it creates
the source-of-truth two-qubit operation occurrences used by the verifier.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Iterable


_QREG_RE = re.compile(r"^qreg\s+([A-Za-z_]\w*)\s*\[\s*(\d+)\s*\]$", re.IGNORECASE)
_CREG_RE = re.compile(r"^creg\s+([A-Za-z_]\w*)\s*\[\s*(\d+)\s*\]$", re.IGNORECASE)
_OP_RE = re.compile(
    r"^(?:if\s*\([^)]*\)\s*)?([A-Za-z_]\w*)"
    r"(?:\s*\((.*)\))?\s+(.+)$",
    re.IGNORECASE,
)
_INDEXED_ARG_RE = re.compile(r"^([A-Za-z_]\w*)\s*\[\s*(\d+)\s*\]$")
_BARE_ARG_RE = re.compile(r"^([A-Za-z_]\w*)$")


class QasmParseError(ValueError):
    """Raised when the audit parser cannot unambiguously read a QASM file."""


@dataclass(frozen=True)
class Operation:
    """A quantum operation occurrence after register-wide expansion."""

    operation_id: str
    source_index: int
    name: str
    qubits: tuple[int, ...]

    def to_dict(self) -> dict:
        return {
            "operation_id": self.operation_id,
            "source_index": self.source_index,
            "name": self.name,
            "qubits": list(self.qubits),
        }


@dataclass(frozen=True)
class CircuitIR:
    """Auditable source circuit facts needed by endpoint-layout verification."""

    filename: str
    qasm_sha256: str
    declared_qubit_count: int
    layout_qubit_count: int
    operation_count: int
    one_qubit_operation_count: int
    two_qubit_operation_count: int
    nonunitary_operation_count: int
    unsupported_operation_count: int
    two_qubit_operations: tuple[Operation, ...]

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "qasm_sha256": self.qasm_sha256,
            "declared_qubit_count": self.declared_qubit_count,
            "layout_qubit_count": self.layout_qubit_count,
            "operation_count": self.operation_count,
            "one_qubit_operation_count": self.one_qubit_operation_count,
            "two_qubit_operation_count": self.two_qubit_operation_count,
            "nonunitary_operation_count": self.nonunitary_operation_count,
            "unsupported_operation_count": self.unsupported_operation_count,
            "two_qubit_operations": [op.to_dict() for op in self.two_qubit_operations],
        }


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//[^\r\n]*", "", text)


def _statements(text: str) -> Iterable[str]:
    """Yield semicolon-terminated statements and reject custom gate bodies."""

    clean = _strip_comments(text)
    if re.search(r"\bgate\s+[A-Za-z_]\w*.*\{", clean, flags=re.IGNORECASE | re.DOTALL):
        raise QasmParseError("Custom gate declarations are outside the canonical audit parser scope.")
    for raw in clean.split(";"):
        statement = " ".join(raw.strip().split())
        if statement:
            yield statement


def _split_operands(text: str) -> list[str]:
    operands: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            operands.append(text[start:index].strip())
            start = index + 1
    operands.append(text[start:].strip())
    return [item for item in operands if item]


def _resolve_quantum_operand(
    operand: str,
    registers: dict[str, tuple[int, int]],
) -> list[int] | None:
    indexed = _INDEXED_ARG_RE.fullmatch(operand)
    if indexed:
        name, raw_index = indexed.groups()
        if name not in registers:
            return None
        offset, size = registers[name]
        index = int(raw_index)
        if index >= size:
            raise QasmParseError(f"Qubit index out of range: {operand}")
        return [offset + index]

    bare = _BARE_ARG_RE.fullmatch(operand)
    if bare and bare.group(1) in registers:
        offset, size = registers[bare.group(1)]
        return list(range(offset, offset + size))
    return None


def _expand_operands(operand_text: str, registers: dict[str, tuple[int, int]]) -> list[tuple[int, ...]]:
    resolved: list[list[int]] = []
    for operand in _split_operands(operand_text):
        qubits = _resolve_quantum_operand(operand, registers)
        if qubits is not None:
            resolved.append(qubits)

    if not resolved:
        return []
    widths = {len(item) for item in resolved if len(item) > 1}
    if len(widths) > 1:
        raise QasmParseError("Register-wide operands have incompatible widths.")
    width = next(iter(widths), 1)
    if width > 1 and any(len(item) not in (1, width) for item in resolved):
        raise QasmParseError("Cannot broadcast register-wide quantum operands.")
    return [tuple(item[0] if len(item) == 1 else item[i] for item in resolved) for i in range(width)]


def parse_qasm(path: str | Path) -> CircuitIR:
    """Parse source operation occurrences from an explicit OpenQASM 2 file."""

    qasm_path = Path(path).expanduser().resolve(strict=True)
    raw = qasm_path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise QasmParseError(f"QASM is not UTF-8: {qasm_path}") from exc

    qregs: dict[str, tuple[int, int]] = {}
    cregs: set[str] = set()
    declared_qubits = 0
    source_index = 0
    one_qubit_count = 0
    nonunitary_count = 0
    unsupported_count = 0
    operations: list[Operation] = []

    for statement in _statements(text):
        lowered = statement.lower()
        if lowered.startswith("openqasm ") or lowered.startswith("include "):
            continue

        qreg_match = _QREG_RE.fullmatch(statement)
        if qreg_match:
            name, raw_size = qreg_match.groups()
            if name in qregs:
                raise QasmParseError(f"Duplicate qreg declaration: {name}")
            size = int(raw_size)
            qregs[name] = (declared_qubits, size)
            declared_qubits += size
            continue

        creg_match = _CREG_RE.fullmatch(statement)
        if creg_match:
            cregs.add(creg_match.group(1))
            continue

        op_match = _OP_RE.fullmatch(statement)
        if not op_match:
            raise QasmParseError(f"Cannot parse QASM statement: {statement}")
        name, _params, operand_text = op_match.groups()
        name = name.lower()

        if name == "barrier":
            continue
        if name in {"measure", "reset"}:
            nonunitary_count += 1
            source_index += 1
            continue

        expanded = _expand_operands(operand_text, qregs)
        if not expanded:
            raise QasmParseError(f"Operation has no recognized quantum operands: {statement}")
        for qubits in expanded:
            if len(qubits) == 1:
                one_qubit_count += 1
            elif len(qubits) == 2:
                occurrence = len(operations)
                operations.append(
                    Operation(
                        operation_id=f"q2_{occurrence:08d}",
                        source_index=source_index,
                        name=name,
                        qubits=qubits,
                    )
                )
            else:
                unsupported_count += 1
            source_index += 1

    if not qregs:
        raise QasmParseError("No qreg declaration found.")
    layout_qubits = max((max(op.qubits) + 1 for op in operations), default=0)
    return CircuitIR(
        filename=qasm_path.name,
        qasm_sha256=hashlib.sha256(raw).hexdigest(),
        declared_qubit_count=declared_qubits,
        layout_qubit_count=layout_qubits,
        operation_count=source_index,
        one_qubit_operation_count=one_qubit_count,
        two_qubit_operation_count=len(operations),
        nonunitary_operation_count=nonunitary_count,
        unsupported_operation_count=unsupported_count,
        two_qubit_operations=tuple(operations),
    )
