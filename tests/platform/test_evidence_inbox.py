from __future__ import annotations

import asyncio
from hashlib import sha256
from pathlib import Path
from typing import Literal, cast

import pytest

from chiplog.platform._sqlite import (
    EventAppender,
    EvidenceFollowupCommand,
    EvidenceIngressCommand,
    EvidencePossibleLoss,
    FenceAdvanceCommand,
    LostCommitAcknowledgement,
    PhysicalPublicationCommand,
    PhysicalRecord,
    SQLiteMaterializer,
)


async def opened(path: Path) -> tuple[SQLiteMaterializer, EventAppender]:
    store = SQLiteMaterializer(path, record_contracts={"fixture-owner": "schema.v1"})
    appender = EventAppender(store, capacity=1, evidence_capacity=2)
    await appender.advance_fence(FenceAdvanceCommand("tenant-1", "fence-1", 0))
    return store, appender


def ingress(
    *,
    source_id: str = "source-1",
    evidence_id: str = "evidence-1",
    fingerprint: str = "fingerprint-1",
    kind: str = "PUSH",
    redelivery: bool = True,
    fault: str = "none",
) -> EvidenceIngressCommand:
    return EvidenceIngressCommand(
        "tenant-1",
        source_id,
        evidence_id,
        fingerprint,
        b"authenticated evidence",
        cast(Literal["PUSH", "POLL", "RECONCILIATION"], kind),
        redelivery,
        cast(Literal["none", "before_commit", "after_commit"], fault),
    )


def followup(
    expected: str,
    next_state: str,
    *,
    source_id: str = "source-1",
    evidence_id: str = "evidence-1",
    cursor: str | None = None,
) -> EvidenceFollowupCommand:
    return EvidenceFollowupCommand(
        "tenant-1",
        source_id,
        evidence_id,
        expected,
        next_state,
        f"attempt:{next_state}",
        "transport.v1",
        cursor,
    )


async def test_inbox_materialization_and_local_ack_are_one_atomic_state(tmp_path: Path) -> None:
    store, appender = await opened(tmp_path / "store.sqlite3")
    result = await appender.submit_evidence(ingress())
    assert result.state == "LOCAL_ACK_AUTHORIZED"
    assert store.evidence_state("tenant-1", "source-1", "evidence-1") == result.state
    assert (await appender.submit_evidence(ingress())).disposition == "REPLAY"
    assert (
        await appender.submit_evidence(ingress(fingerprint="changed"))
    ).disposition == "CONFLICT"
    await appender.close()


async def test_every_inbox_commit_ack_cut_is_explicit(tmp_path: Path) -> None:
    store, appender = await opened(tmp_path / "store.sqlite3")
    with pytest.raises(RuntimeError, match="before commit"):
        await appender.submit_evidence(ingress(fault="before_commit"))
    assert store.evidence_state("tenant-1", "source-1", "evidence-1") is None
    with pytest.raises(LostCommitAcknowledgement):
        await appender.submit_evidence(ingress(fault="after_commit"))
    assert store.evidence_state("tenant-1", "source-1", "evidence-1") == "LOCAL_ACK_AUTHORIZED"
    assert (await appender.submit_evidence(ingress())).disposition == "REPLAY"
    await appender.close()


async def test_non_redeliverable_failed_commit_surfaces_possible_loss(tmp_path: Path) -> None:
    store, appender = await opened(tmp_path / "store.sqlite3")
    with pytest.raises(EvidencePossibleLoss, match="may be lost"):
        await appender.submit_evidence(ingress(redelivery=False, fault="before_commit"))
    assert store.evidence_state("tenant-1", "source-1", "evidence-1") is None
    await appender.close()


@pytest.mark.parametrize(
    ("kind", "states", "cursor"),
    [
        (
            "PUSH",
            (
                "PUSH_RESPONSE_ATTEMPT_ISSUED",
                "PUSH_RESPONSE_LOCAL_COMPLETION_OBSERVED",
                "PROVIDER_RECEIPT_OBSERVED",
            ),
            None,
        ),
        ("POLL", ("POLL_CURSOR_ADVANCE_AUTHORIZED", "POLL_CURSOR_APPLIED"), "cursor-2"),
        (
            "RECONCILIATION",
            ("RECONCILIATION_RELEASE_AUTHORIZED", "RECONCILIATION_OBLIGATION_RELEASED"),
            None,
        ),
    ],
)
async def test_closed_transport_followup_graph(
    tmp_path: Path, kind: str, states: tuple[str, ...], cursor: str | None
) -> None:
    _, appender = await opened(tmp_path / f"{kind}.sqlite3")
    await appender.submit_evidence(ingress(kind=kind))
    previous = "LOCAL_ACK_AUTHORIZED"
    for state in states:
        assert (
            await appender.submit_evidence(followup(previous, state, cursor=cursor))
        ).state == state
        previous = state
    await appender.close()


async def test_poll_cursor_cannot_apply_before_authorization(tmp_path: Path) -> None:
    store, appender = await opened(tmp_path / "store.sqlite3")
    await appender.submit_evidence(ingress(kind="POLL"))
    with pytest.raises(ValueError, match="illegal evidence"):
        await appender.submit_evidence(
            followup("LOCAL_ACK_AUTHORIZED", "POLL_CURSOR_APPLIED", cursor="cursor-2")
        )
    assert store.evidence_state("tenant-1", "source-1", "evidence-1") == "LOCAL_ACK_AUTHORIZED"
    await appender.close()


async def test_same_evidence_id_across_sources_has_unambiguous_followup(tmp_path: Path) -> None:
    store, appender = await opened(tmp_path / "store.sqlite3")
    await appender.submit_evidence(ingress(source_id="source-a", kind="PUSH"))
    await appender.submit_evidence(ingress(source_id="source-b", kind="POLL"))
    await appender.submit_evidence(
        followup(
            "LOCAL_ACK_AUTHORIZED",
            "POLL_CURSOR_ADVANCE_AUTHORIZED",
            source_id="source-b",
            cursor="cursor-2",
        )
    )
    assert store.evidence_state("tenant-1", "source-a", "evidence-1") == "LOCAL_ACK_AUTHORIZED"
    assert (
        store.evidence_state("tenant-1", "source-b", "evidence-1")
        == "POLL_CURSOR_ADVANCE_AUTHORIZED"
    )
    await appender.close()


async def test_reserved_lane_is_admitted_and_drains_ahead_of_ordinary_work(tmp_path: Path) -> None:
    _, appender = await opened(tmp_path / "store.sqlite3")
    payload = b"ordinary"
    ordinary_command = PhysicalPublicationCommand(
        "tenant-1",
        "fixture",
        "ordinary",
        "ordinary",
        0,
        "fence-1",
        0,
        0,
        (
            PhysicalRecord(
                "ordinary", "fixture-owner", "schema.v1", payload, sha256(payload).hexdigest()
            ),
        ),
    )
    ordinary = asyncio.create_task(appender.submit(ordinary_command))
    evidence = asyncio.create_task(appender.submit_evidence(ingress()))
    evidence_result, ordinary_result = await asyncio.gather(evidence, ordinary)
    assert evidence_result.state == "LOCAL_ACK_AUTHORIZED"
    assert ordinary_result.disposition == "COMMITTED"
    await appender.close()
