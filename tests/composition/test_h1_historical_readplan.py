"""Historical V2 read-plan reconstruction witnesses."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from chiplog.composition import h1_completion_issuance, h1_historical_readplan
from chiplog.composition.h1_completion_readplan_registry import current_registry
from chiplog.composition.h1_historical_readplan import reconstruct_h1_historical_read_manifest
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    CompleteDeliveryBatchV2,
    ExactRecordHead,
    ObservedAbsence,
    ObservedPresence,
)
from chiplog.platform.owner_decision_journal import OwnerJournalSnapshot


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def test_historical_manifest_uses_selected_native_members_not_later_physical_rows() -> None:
    """A later physical Run is not evidence at the retained predecessor cut."""
    registry = current_registry()
    raw_run, raw_seal, raw_frontier = b"run", b"seal", b"frontier"
    run_id, seal_id = "run-1", "seal-1"
    heads = (
        ObservedPresence(
            head=ExactRecordHead(
                owner="agent_loop",
                record_kind="Run",
                subject_id=run_id,
                record_id="run-record",
                fingerprint=hashlib.sha256(raw_run).hexdigest(),
            )
        ),
        ObservedPresence(
            head=ExactRecordHead(
                owner="agent_loop",
                record_kind="ResponseSeal",
                subject_id=seal_id,
                record_id="seal-record",
                fingerprint=hashlib.sha256(raw_seal).hexdigest(),
            )
        ),
        ObservedPresence(
            head=ExactRecordHead(
                owner="agent_loop",
                record_kind="RecoveryFrontierRegistry",
                subject_id=seal_id,
                record_id="frontier-record",
                fingerprint=hashlib.sha256(raw_frontier).hexdigest(),
            )
        ),
        ObservedAbsence(owner="agent_loop", record_kind="RunCompletion", subject_id=run_id),
    )
    commitment = "a" * 64
    manifest = AuthoritativeReadManifest(
        tenant_id="tenant",
        tenant_frontier=3,
        expected_materialization_commitment=commitment,
        registry_head=registry.head,
        registry_fingerprint=registry.fingerprint,
        ordered_heads=heads,
        complete_manifest_fingerprint=hashlib.sha256(
            _canonical(
                {
                    "expected_materialization_commitment": commitment,
                    "ordered_heads": [head.model_dump(mode="json") for head in heads],
                    "registry_fingerprint": registry.fingerprint,
                    "registry_head": registry.head,
                    "tenant_frontier": 3,
                    "tenant_id": "tenant",
                }
            )
        ).hexdigest(),
    )
    batch = SimpleNamespace(identity=SimpleNamespace(tenant_id="tenant"), expected=manifest)
    issuance = SimpleNamespace(
        read_plan=SimpleNamespace(registry_bytes=registry.canonical_bytes),
        capture=SimpleNamespace(expected=manifest),
        assembly=SimpleNamespace(
            original_completion_request=SimpleNamespace(run=SimpleNamespace(run_id=run_id))
        ),
    )
    members = tuple(
        SimpleNamespace(
            decision_id="seal-decision",
            operation_kind="agent_loop.execution-complete-seal.v1",
            publication_id="seal-publication",
            commit_sequence=3,
            record_id=record_id,
            owner="agent_loop",
            schema_id=schema_id,
            canonical_bytes=raw,
        )
        for record_id, schema_id, raw in (
            ("run-record", "chiplog.agent-loop.execution-record.v2", raw_run),
            ("seal-record", "chiplog.call.response-seal.v1", raw_seal),
            ("frontier-record", "chiplog.recovery.frontier-registry.v1", raw_frontier),
        )
    )
    native = SimpleNamespace(
        seal=SimpleNamespace(decision_id="seal-decision"),
        physical_members=members,
        source=SimpleNamespace(selected_response_seal=SimpleNamespace(subject_id=seal_id)),
    )
    calls: list[object] = []

    class Rows:
        commitment = "a" * 64

        @staticmethod
        def tenant_head(tenant: str) -> int:
            assert tenant == "tenant"
            return 3

        @staticmethod
        def publications(tenant: str) -> tuple[tuple[object, ...], ...]:
            assert tenant == "tenant"
            return (
                (
                    "agent_loop.execution-complete-seal.v1",
                    "seal-publication",
                    "f" * 64,
                    3,
                    "\n".join(("run-record", "seal-record", "frontier-record")),
                ),
            )

        @staticmethod
        def require_complete(command: object) -> None:
            calls.append(command)

    actual = reconstruct_h1_historical_read_manifest(
        batch=batch,
        issuance=issuance,
        native=native,
        predecessor_rows=Rows(),
        predecessor_owner=OwnerJournalSnapshot("tenant", "predecessor", (), frozenset()),
    )

    assert actual == manifest
    assert len(calls) == 1


def test_historical_manifest_rejects_a_materialized_rival_completion_for_the_same_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = current_registry()
    expected = SimpleNamespace(
        registry_head=registry.head,
        registry_fingerprint=registry.fingerprint,
        expected_materialization_commitment="a" * 64,
        tenant_frontier=1,
    )
    batch = SimpleNamespace(identity=SimpleNamespace(tenant_id="tenant"), expected=expected)
    prior = CompleteDeliveryBatchV2.model_construct(
        identity=SimpleNamespace(
            tenant_id="tenant", command_id="prior", command_fingerprint="b" * 64
        ),
        expected=SimpleNamespace(tenant_frontier=0),
        operation="agent_loop.complete_acceptance.v2",
        complete_records=(),
    )
    decision = SimpleNamespace(prepared=SimpleNamespace(request=prior), tenant_commit_sequence=1)
    issuance = SimpleNamespace(
        read_plan=SimpleNamespace(registry_bytes=registry.canonical_bytes),
        assembly=SimpleNamespace(
            original_completion_request=SimpleNamespace(run=SimpleNamespace(run_id="run-1"))
        ),
    )

    class Rows:
        commitment = "a" * 64

        @staticmethod
        def tenant_head(_: str) -> int:
            return 1

        @staticmethod
        def require_complete(_: object) -> None:
            return None

        @staticmethod
        def publications(_: str) -> tuple[tuple[object, ...], ...]:
            return (("agent_loop.complete_acceptance.v2", "prior", "b" * 64, 1, ""),)

    monkeypatch.setattr(
        h1_completion_issuance,
        "decode_h1_completion_issuance",
        lambda _: SimpleNamespace(
            assembly=SimpleNamespace(
                original_completion_request=SimpleNamespace(run=SimpleNamespace(run_id="run-1"))
            )
        ),
    )
    monkeypatch.setattr(h1_historical_readplan, "_require_native_seal_members", lambda **_: ())

    with pytest.raises(ValueError, match="RunCompletion is present"):
        reconstruct_h1_historical_read_manifest(
            batch=batch,
            issuance=issuance,
            native=SimpleNamespace(
                source=SimpleNamespace(selected_response_seal=SimpleNamespace(subject_id="seal"))
            ),
            predecessor_rows=Rows(),
            predecessor_owner=OwnerJournalSnapshot(
                "tenant", "prior", (decision,), frozenset({"prior"})
            ),
        )
