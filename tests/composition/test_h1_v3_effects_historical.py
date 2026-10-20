"""Focused durable-prefix checks for inert V3 scoped Effects history."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Literal, NotRequired, TypedDict

import pytest

from chiplog.composition.h1_postseal_recovery import (
    H1PostSealRecoveryRecordV1,
    H1PostSealRecoveryRecordV2,
    H1PostSealRecoveryRootV1,
    H1PostSealRecoverySelectedRecord,
)
from chiplog.composition.h1_v3_recovery_historical_source import (
    H1V3RecoveryHistoricalSource,
)


def _root() -> H1PostSealRecoveryRootV1:
    digest = "0" * 64
    return H1PostSealRecoveryRootV1(
        "tenant",
        "database",
        ("/durable.sqlite", 1, 1),
        "journal",
        "seal",
        "seal-head",
        digest,
        digest,
        digest,
        "loop:" + digest,
        "command",
        digest,
        digest,
        "publication",
        digest,
    )


class _StageFields(TypedDict):
    stage: str
    stage_ordinal: int
    semantic_input: NotRequired[bytes]
    semantic_input_digest: NotRequired[str]
    effects_command_id: NotRequired[str | None]
    result_bytes: NotRequired[bytes]
    result_digest: NotRequired[str]


def _record(
    entry_id: str,
    raw: bytes,
    kind: Literal["ROOT", "STAGE_INPUT", "STAGE_RESULT"],
    stage: str | None,
) -> H1PostSealRecoverySelectedRecord:
    return H1PostSealRecoverySelectedRecord(entry_id, raw, kind, stage)


def _effects_prefix(
    *, result: bool, terminal: bool = False
) -> tuple[H1PostSealRecoverySelectedRecord, ...]:
    root = _root()
    root_record = H1PostSealRecoveryRecordV2("ROOT", root.root_id(), None, root, "SCOPED_V3")
    records: list[H1PostSealRecoverySelectedRecord] = [
        _record("root", root_record.canonical_bytes(), "ROOT", None)
    ]
    predecessor = "root"
    stages: list[tuple[Literal["STAGE_INPUT", "STAGE_RESULT"], str]] = [
        ("STAGE_INPUT", "COMPLETION"),
        ("STAGE_RESULT", "COMPLETION"),
        ("STAGE_INPUT", "CONVERSATION"),
        ("STAGE_RESULT", "CONVERSATION"),
        ("STAGE_INPUT", "EFFECTS"),
    ]
    if result:
        stages.append(("STAGE_RESULT", "EFFECTS"))
    if terminal:
        stages.append(("STAGE_INPUT", "TERMINAL_WORK"))
    stage_names = ("COMPLETION", "CONVERSATION", "EFFECTS", "TERMINAL_WORK")
    for index, (kind, stage) in enumerate(stages):
        payload = f"{kind}:{stage}".encode()
        kwargs: _StageFields = {
            "stage": stage,
            "stage_ordinal": stage_names.index(stage),
        }
        if kind == "STAGE_INPUT":
            kwargs.update(
                {
                    "semantic_input": payload,
                    "semantic_input_digest": hashlib.sha256(payload).hexdigest(),
                    "effects_command_id": "effects-command" if stage == "EFFECTS" else None,
                }
            )
        else:
            kwargs.update(
                {"result_bytes": payload, "result_digest": hashlib.sha256(payload).hexdigest()}
            )
        raw = H1PostSealRecoveryRecordV1(
            kind,
            root.root_id(),
            predecessor,
            **kwargs,
        ).canonical_bytes()
        entry_id = f"entry-{index}"
        records.append(_record(entry_id, raw, kind, stage))
        predecessor = entry_id
    return tuple(records)


@pytest.mark.parametrize("result", (False, True))
def test_effects_history_accepts_only_the_exact_six_or_seven_record_prefix(result: bool) -> None:
    records = _effects_prefix(result=result)

    assert H1V3RecoveryHistoricalSource._required_effects_records(records) == records
    decoded = H1V3RecoveryHistoricalSource._decode_effects_records(records, root=_root())
    assert len(decoded) == len(records)


def test_effects_history_retains_the_exact_terminal_suffix_after_effects() -> None:
    """The V2 terminal reader consumes the same complete selected nine-record cut."""
    records = _effects_prefix(result=True, terminal=True)

    assert H1V3RecoveryHistoricalSource._required_effects_records(records) == records


def test_historical_scope_comparison_ignores_fresh_current_scope_observations() -> None:
    historical = SimpleNamespace(
        anchor="anchor",
        scope="scope",
        selected_decision_bytes=b"decision",
        selected_record_bytes=b"record",
        current_request="old-current-request",
        current_result="old-current-result",
    )
    refreshed = SimpleNamespace(
        anchor="anchor",
        scope="scope",
        selected_decision_bytes=b"decision",
        selected_record_bytes=b"record",
        current_request="fresh-current-request",
        current_result="fresh-current-result",
    )

    assert H1V3RecoveryHistoricalSource._same_historical_selected_scope(refreshed, historical)
    assert not H1V3RecoveryHistoricalSource._same_historical_selected_scope(
        refreshed, historical.__class__(**{**historical.__dict__, "scope": "other"})
    )
