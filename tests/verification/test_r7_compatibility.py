from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.architecture import (
    R7_COMPATIBILITY_LEDGER,
    verify_r7_compatibility_ledger,
)


@pytest.mark.parametrize("field", ["successor_id", "successor_evidence"])
def test_ledger_rejects_unknown_successor(field: str) -> None:
    candidate = (
        replace(R7_COMPATIBILITY_LEDGER[0], successor_id="unknown")
        if field == "successor_id"
        else replace(R7_COMPATIBILITY_LEDGER[0], successor_evidence="unknown"),
        *R7_COMPATIBILITY_LEDGER[1:],
    )
    with pytest.raises(ValueError, match="unknown successor"):
        verify_r7_compatibility_ledger(candidate)


def test_ledger_rejects_missing_successor_evidence(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="evidence is missing"):
        verify_r7_compatibility_ledger(evidence_root=tmp_path)


@pytest.mark.parametrize(
    "candidate",
    [
        R7_COMPATIBILITY_LEDGER[:-1],
        (*R7_COMPATIBILITY_LEDGER, R7_COMPATIBILITY_LEDGER[-1]),
        (
            replace(R7_COMPATIBILITY_LEDGER[0], predecessor_id="orphan:unknown"),
            *R7_COMPATIBILITY_LEDGER[1:],
        ),
    ],
)
def test_ledger_rejects_missing_duplicate_and_orphan_predecessors(candidate: object) -> None:
    assert isinstance(candidate, tuple)
    with pytest.raises(ValueError, match="canonical predecessor universe"):
        verify_r7_compatibility_ledger(candidate)
