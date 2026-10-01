"""Focused durable-prefix checks for inert V3 scoped Effects history."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from chiplog.capabilities.effects.h1_scoped_preparation_contracts import (
    H1ScopedDeliveryOwnerCallV1,
    PreparedH1ScopedDeliveryV1,
)
from chiplog.composition.common_cli_execution_runtime import open_installed_h1_scoped_runtime
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_postseal_recovery import (
    H1PostSealRecoveryRecordV1,
    H1PostSealRecoveryRecordV2,
    H1PostSealRecoveryRootV1,
    H1PostSealRecoverySelectedRecord,
)
from chiplog.composition.h1_scoped_delivery_producer import H1ScopedDeliveryProducer
from chiplog.composition.h1_v3_recovery_historical_source import (
    H1V3RecoveryHistoricalSource,
    H1V3RecoveryHistoricalSourceError,
)
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot
from tests.support.h1_scoped_first_b import (
    issue_real_h1_scoped_authority,
    scoped_resources,
    write_scoped_pins,
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


def _record(
    entry_id: str, raw: bytes, kind: str, stage: str | None
) -> H1PostSealRecoverySelectedRecord:
    return H1PostSealRecoverySelectedRecord(entry_id, raw, kind, stage)  # type: ignore[arg-type]


def _effects_prefix(
    *, result: bool, terminal: bool = False
) -> tuple[H1PostSealRecoverySelectedRecord, ...]:
    root = _root()
    root_record = H1PostSealRecoveryRecordV2("ROOT", root.root_id(), None, root, "SCOPED_V3")
    records: list[H1PostSealRecoverySelectedRecord] = [
        _record("root", root_record.canonical_bytes(), "ROOT", None)
    ]
    predecessor = "root"
    stages: list[tuple[str, str]] = [
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
        kwargs: dict[str, object] = {
            "stage": stage,
            "stage_ordinal": stage_names.index(stage),
        }
        if kind == "STAGE_INPUT":
            kwargs.update(
                semantic_input=payload,
                semantic_input_digest=hashlib.sha256(payload).hexdigest(),
                effects_command_id="effects-command" if stage == "EFFECTS" else None,
            )
        else:
            kwargs.update(result_bytes=payload, result_digest=hashlib.sha256(payload).hexdigest())
        raw = H1PostSealRecoveryRecordV1(
            kind, root.root_id(), predecessor, **kwargs  # type: ignore[arg-type]
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


def test_effects_history_rejects_a_terminal_record_after_the_effects_prefix() -> None:
    with pytest.raises(H1V3RecoveryHistoricalSourceError, match="extra records"):
        H1V3RecoveryHistoricalSource._required_effects_records(
            _effects_prefix(result=True, terminal=True)
        )


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


@pytest.mark.asyncio
async def test_effects_result_must_bind_the_exact_canonical_inner_request(tmp_path: Path) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    write_scoped_pins(slot.database_path)

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_runtime(
            launch, resources=scoped_resources(tmp_path)
        ) as runtime:
            _, authority = await issue_real_h1_scoped_authority(runtime)
            exchange = await H1ScopedDeliveryProducer(
                runtime, runtime._h1_producer_inventory_reader
            ).prepare(authority_capture=authority)

    call = H1ScopedDeliveryOwnerCallV1.model_validate_json(exchange.sent.canonical_payload)
    result = PreparedH1ScopedDeliveryV1.model_validate_json(exchange.returned.canonical_payload)
    H1V3RecoveryHistoricalSource._require_effects_result(
        call.request, result, exchange.returned.canonical_payload
    )

    altered = result.model_copy(update={"source_request_fingerprint": "0" * 64})
    with pytest.raises(H1V3RecoveryHistoricalSourceError, match="result differs"):
        H1V3RecoveryHistoricalSource._require_effects_result(
            call.request, altered, altered.canonical_bytes()
        )
