from __future__ import annotations

import re
from pathlib import Path

from .identity import sha256_bytes
from .models import InvariantSource

START = "## Architectural invariants"
END = "## Operation-level deployment gate"
INVARIANT = re.compile(r"^(\d+)\.\s+(.+)$")


class InvariantManifestError(ValueError):
    pass


def extract_invariant_manifest(path: Path) -> tuple[str, tuple[InvariantSource, ...]]:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    if text.count(START) != 1 or text.count(END) != 1:
        raise InvariantManifestError("canonical invariant section markers must occur once")
    section = text.split(START, 1)[1].split(END, 1)[0]
    entries: list[InvariantSource] = []
    current_number: int | None = None
    current_parts: list[str] = []

    def flush() -> None:
        nonlocal current_number, current_parts
        if current_number is None:
            return
        normalized = " ".join(" ".join(current_parts).split())
        entries.append(
            InvariantSource(
                current_number,
                f"A{current_number:02d}",
                normalized,
                sha256_bytes(normalized.encode()),
            )
        )
        current_number = None
        current_parts = []

    for line in section.splitlines():
        match = INVARIANT.match(line)
        if match:
            flush()
            current_number = int(match.group(1))
            current_parts = [match.group(2)]
        elif current_number is not None and line.strip():
            current_parts.append(line.strip())
    flush()

    numbers = [entry.number for entry in entries]
    expected = list(range(1, len(entries) + 1))
    if numbers != expected:
        raise InvariantManifestError(
            f"invariant numbering must be contiguous from 1: got {numbers}"
        )
    if len(entries) != 108:
        raise InvariantManifestError(f"expected 108 canonical invariants, got {len(entries)}")
    if len({entry.text_digest for entry in entries}) != len(entries):
        raise InvariantManifestError("duplicate invariant text is not allowed")
    expected_ids = [f"A{number:02d}" for number in range(1, 109)]
    if [entry.invariant_id for entry in entries] != expected_ids:
        raise InvariantManifestError("invariant IDs must be exactly A01-A108")
    return sha256_bytes(raw), tuple(entries)
