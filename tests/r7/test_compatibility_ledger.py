from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.architecture import (
    R7_COMPATIBILITY_LEDGER,
    R7_PARITY_CORPUS,
    verify_r7_compatibility_ledger,
)
from chiplog.architecture.r7_compatibility import PREDECESSOR_UNIVERSE


def test_ledger_is_canonical_complete_and_bound() -> None:
    assert tuple(item.predecessor_id for item in R7_COMPATIBILITY_LEDGER) == PREDECESSOR_UNIVERSE
    assert len(R7_COMPATIBILITY_LEDGER) == len(set(PREDECESSOR_UNIVERSE))
    assert len(verify_r7_compatibility_ledger(evidence_root=Path(__file__).parents[2])) == 64


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


def test_every_predecessor_has_one_owned_successor_and_evidence() -> None:
    assert all(item.successor_id and item.successor_evidence for item in R7_COMPATIBILITY_LEDGER)
    assert {item.disposition for item in R7_COMPATIBILITY_LEDGER} == {
        "RETAIN",
        "ADAPT",
        "HISTORICAL_ONLY",
        "DEPRECATE_AS_EXECUTABLE",
    }


def test_parity_corpus_freezes_canonical_request_and_compared_artifacts() -> None:
    assert len(R7_PARITY_CORPUS) == 1
    case = R7_PARITY_CORPUS[0]
    assert case.canonical_request.startswith(b'{"authority_act_id":"act-1"')
    assert case.expected_dispositions == ("COMMITTED", "REPLAY", "CONFLICT", "DENIED", "STALE")
    assert case.compared_artifacts == (
        "canonical_durable_record_bytes",
        "planning_committed_result_bytes",
        "restart_projection_bytes",
    )
