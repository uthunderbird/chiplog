from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.architecture.r7_runtime import R7_PRODUCTION_MANIFEST
from chiplog.composition.r7_supervisor import R7RuntimeSupervisor, RuntimeAdmissionEvidence
from chiplog.platform.authority_ledger import BrokerAuthorityLedger, OperationToken


class Admission:
    def __init__(self, accepted: bool = True) -> None:
        self.accepted = accepted

    def authenticate_runtime_admission(
        self, tenant_id: str, _runtime: object
    ) -> RuntimeAdmissionEvidence:
        return RuntimeAdmissionEvidence(
            tenant_id,
            "database-1",
            "genesis-1",
            "trust-1",
            "journal-1",
            "materialization-1",
            self.accepted,
        )


class MutatingAdmission(Admission):
    def authenticate_runtime_admission(
        self, tenant_id: str, runtime: object
    ) -> RuntimeAdmissionEvidence:
        good = runtime.graph_generation()  # type: ignore[attr-defined]
        bad_owner = replace(good.owners[0], provider_ids=("substituted",))
        bad = replace(good, owners=(bad_owner, *good.owners[1:]))
        runtime.graph_generation = lambda: bad  # type: ignore[attr-defined]
        return super().authenticate_runtime_admission(tenant_id, runtime)


def _token(epoch: int) -> OperationToken:
    return OperationToken(
        mode="ONE_SHOT",
        tenant_id="tenant-1",
        broker_epoch=epoch,
        owner_id="planning",
        generation_id="generation-1",
        session_id="session-1",
        target_id="planning-store",
        capability_id="event_appender",
        operation_kind="planning.commit",
        payload_fingerprint="a" * 64,
        nonce="nonce-1",
        expiry_ns=100,
    )


def _supervisor(path: Path, admission: Admission | None = None) -> R7RuntimeSupervisor:
    return R7RuntimeSupervisor(
        "tenant-1",
        path,
        R7_PRODUCTION_MANIFEST,
        Admission() if admission is None else admission,
    )


def test_restart_rotates_epoch_kills_old_workers_and_reconciles_issued_operations(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "authority.sqlite3"
    supervisor = _supervisor(ledger_path)
    try:
        first = supervisor.start_generation("generation-1")
        first_pids = {item.process_id for item in first}
        assert supervisor.broker_epoch == 1
        ledger = BrokerAuthorityLedger(ledger_path)
        ledger.admit("operation-1", "slot-1", _token(1), now_ns=1)

        second = supervisor.start_generation("generation-2")
        assert supervisor.broker_epoch == 2
        assert supervisor.reconciled_operations == ("operation-1",)
        assert all(not _process_exists(process_id) for process_id in first_pids)
        assert first_pids.isdisjoint({item.process_id for item in second})
        disposition = ledger.lookup("tenant-1", "operation-1")
        assert disposition is not None
        assert disposition.state == "OUTCOME_UNKNOWN"
        assert disposition.recovery_obligation_id == "broker-restart:2:operation-1"
    finally:
        supervisor.close()


def test_failed_trust_admission_closes_authority_empty_quarantine(tmp_path: Path) -> None:
    supervisor = _supervisor(tmp_path / "authority.sqlite3", Admission(False))
    with pytest.raises(PermissionError, match="not authenticated"):
        supervisor.start_generation("generation-1")
    assert supervisor.broker_epoch == 1
    assert supervisor.admitted_graph is None
    with pytest.raises(RuntimeError, match="not admitted"):
        supervisor.runtime()


def test_quarantine_runtime_is_not_published_as_an_admitted_generation(tmp_path: Path) -> None:
    supervisor = _supervisor(tmp_path / "authority.sqlite3")
    try:
        supervisor.start_quarantine("quarantine-1")
        assert supervisor.admitted_graph is None
        with pytest.raises(RuntimeError, match="not admitted"):
            supervisor.runtime()
        assert supervisor.quarantined_trust_session().owner_id == "deployment_trust"
    finally:
        supervisor.close()


def test_admission_promotes_the_validated_graph_snapshot_not_a_second_attestation(
    tmp_path: Path,
) -> None:
    supervisor = _supervisor(tmp_path / "authority.sqlite3", MutatingAdmission())
    try:
        supervisor.start_generation("generation-1")
        graph = supervisor.admitted_graph
        assert graph is not None
        assert graph.owners[0].provider_ids == ("chiplog.platform.r7_runtime:_OwnerProvider",)
    finally:
        supervisor.close()


def _process_exists(process_id: int) -> bool:
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    return True
