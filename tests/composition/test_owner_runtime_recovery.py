"""Canonical runtime startup and selected-publication recovery integration."""

import hashlib
import hmac
import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.capabilities.planning import CreateIntentionLine
from chiplog.composition.r8 import R8_SURFACES, command_bytes
from chiplog.composition.r14_runtime import R14PlanningRuntime, open_r14_runtime
from chiplog.composition.r17_ingress_runtime import open_r17_runtime
from chiplog.domain_primitives import RecordId, TenantId
from chiplog.platform._owner_publication_contracts import SingleOwnerBatch
from chiplog.platform._sqlite import (
    EventAppender,
    PhysicalPublicationCommand,
    PhysicalRecord,
    PublicationResult,
)
from chiplog.platform.authority_reads import capture_authority_storage_state
from chiplog.platform.owner_decision_journal import (
    IndependentOwnerDecisionJournal,
    OwnerJournalIntegrityError,
)
from chiplog.platform.owner_publications import (
    OwnerPublicationPending,
    PreparedOwnerPublication,
    SelectedOwnerDecision,
)
from chiplog.platform.r8_gate import BrokerDeploymentGate
from chiplog.platform.workspace_snapshot import read_connection
from tests.support.deployment_gate import KEY, entitlement, request, signature


def _mechanical_preparation(runtime: R14PlanningRuntime) -> PreparedOwnerPublication:
    """Deliberately inert semantics: proves journal lifecycle, never authentication."""
    commitment, _ = capture_authority_storage_state(runtime._database)
    with read_connection(runtime._database) as connection:
        row = connection.execute(
            "SELECT head FROM main.tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
        ).fetchone()
    frontier = 0 if row is None else row[0]
    payload = b'{"fixture":"mechanical-recovery"}'
    digest = hashlib.sha256(payload).hexdigest()
    request = SingleOwnerBatch.model_validate(
        {
            "operation": "effects.authorize",
            "identity": {
                "tenant_id": runtime._tenant_id,
                "command_id": "mechanical-command",
                "command_fingerprint": digest,
                "canonicalization_version": "chiplog.owner-publication.v1",
            },
            "authentication": {
                "kind": "WORKER",
                "invocation": {
                    "issuance_id": "fixture",
                    "issuance_fingerprint": digest,
                    "broker_epoch": "fixture",
                    "broker_session": "fixture",
                    "runtime_generation": "fixture",
                    "operation_subject": "mechanical-command",
                },
                "applicability_schema": "fixture.v1",
                "applicability_bytes": payload,
                "applicability_fingerprint": digest,
            },
            "expected": {
                "tenant_id": runtime._tenant_id,
                "tenant_frontier": frontier,
                "expected_materialization_commitment": commitment,
                "registry_head": "fixture",
                "registry_fingerprint": digest,
                "ordered_heads": (),
                "complete_manifest_fingerprint": digest,
            },
            "command": {
                "owner": "effects",
                "schema_id": "fixture.v1",
                "canonical_bytes": payload,
                "fingerprint": digest,
            },
            "complete_records": (
                {
                    "owner": "effects",
                    "record_kind": "fixture",
                    "record_id": "mechanical-record",
                    "schema_id": "chiplog.effects.record.v1",
                    "canonical_bytes": payload,
                    "fingerprint": digest,
                },
            ),
            "complete_batch_fingerprint": digest,
        }
    )
    return PreparedOwnerPublication(request, "fixture", "r6", 0, commitment)


async def test_runtime_bootstrap_and_reopen_preserve_authority_anchor(tmp_path: Path) -> None:
    database = tmp_path / "runtime.sqlite"
    async with open_r14_runtime(database) as runtime:
        commitment, _ = capture_authority_storage_state(database)
        assert runtime._commitment_journal.load(runtime._tenant_id) == commitment
        assert (
            runtime._read_ledger.current_state(runtime._tenant_id).materialization_commitment
            == commitment
        )
        assert runtime._pending_owners() == ()
        runtime._require_no_pending()
    async with open_r14_runtime(database) as reopened:
        assert capture_authority_storage_state(database)[0] == commitment
        assert reopened._commitment_journal.load(reopened._tenant_id) == commitment
        assert reopened._pending_owners() == ()
        reopened._require_no_pending()


@pytest.mark.parametrize("reopen_with_ingress", (False, True))
@pytest.mark.parametrize("fault", ("before_commit", "after_commit"))
@pytest.mark.parametrize("inject_rival", [False, True])
async def test_pending_legacy_publication_blocks_owner_selection_and_recovers(
    tmp_path: Path,
    inject_rival: bool,
    reopen_with_ingress: bool,
    fault: Literal["before_commit", "after_commit"],
) -> None:
    database = tmp_path / "runtime.sqlite"
    open_recovery = open_r17_runtime if reopen_with_ingress else open_r14_runtime
    async with open_r14_runtime(database) as runtime:
        prepared = _mechanical_preparation(runtime)
        payload = b'{"fixture":"legacy-mechanics"}'
        command = PhysicalPublicationCommand(
            tenant_id=runtime._tenant_id,
            operation_kind="workspace.policy",
            idempotency_key="legacy-command",
            request_fingerprint=hashlib.sha256(payload).hexdigest(),
            expected_head=prepared.request.expected.tenant_frontier,
            fence_generation="r6",
            expected_fence_frontier=0,
            minimum_fence_frontier=0,
            records=(
                PhysicalRecord(
                    "legacy-record",
                    "workspace_policy",
                    "chiplog.workspace.policy.v1",
                    payload,
                    hashlib.sha256(payload).hexdigest(),
                ),
            ),
            fault=fault,
        )
        with pytest.raises(
            RuntimeError, match=r"injected (fault before commit|lost commit acknowledgement)"
        ):
            await runtime._appender.submit(command)
        pending = runtime._pending()
        assert len(pending) == 1
        with pytest.raises(OwnerPublicationPending):
            runtime._owner_decisions().select(prepared, str(pending[0]["resulting"]))
        assert runtime._owner_decisions().snapshot().decisions == ()
        if inject_rival:
            # Deliberately seed incompatible authenticated journal state by bypassing
            # composition selection. Startup must reject it before either recovery.
            IndependentOwnerDecisionJournal.select(
                runtime._owner_decisions(), prepared, str(pending[0]["resulting"])
            )
            unchanged = capture_authority_storage_state(database)[0]
    if inject_rival:
        with pytest.raises(
            OwnerJournalIntegrityError,
            match="operation=ingress_materialization"
            if reopen_with_ingress
            else "operation=startup_pending",
        ) as failure:
            async with open_recovery(database):
                pytest.fail("competing journal families must not open")
        assert failure.value.__cause__ is not None
        assert capture_authority_storage_state(database)[0] == unchanged
        return
    async with open_recovery(database) as reopened:
        assert reopened._pending() == ()
        assert capture_authority_storage_state(database)[0] == pending[0]["resulting"]
        reopened._require_no_pending()


@pytest.mark.parametrize("fault", ("before_commit", "after_commit"))
@pytest.mark.parametrize(
    "reopen_with_ingress,malformed", ((False, False), (True, False), (True, True))
)
async def test_pending_actual_planning_gate_blocks_owner_selection_and_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reopen_with_ingress: bool,
    malformed: bool,
    fault: Literal["before_commit", "after_commit"],
) -> None:
    database = tmp_path / "runtime.sqlite"
    open_recovery = open_r17_runtime if reopen_with_ingress else open_r14_runtime
    async with open_r14_runtime(database) as runtime:
        prepared = _mechanical_preparation(runtime)
        tenant = TenantId(runtime._tenant_id)
        command = CreateIntentionLine(
            RecordId(tenant, "gate-command"),
            RecordId(tenant, "line"),
            RecordId(tenant, "revision"),
            "recovery fixture",
            "act",
        )
        # This independent test entitlement is not deployment authority.
        gate = BrokerDeploymentGate(
            database.with_suffix(database.suffix + ".gate.sqlite3"),
            tenant_id=runtime._tenant_id,
            surfaces=R8_SURFACES,
            authenticate=lambda payload, proof: hmac.compare_digest(
                hmac.digest(KEY, payload, "sha256"), proof
            ),
            journal=IndependentTenantDecisionJournal(
                database.with_suffix(database.suffix + ".gate-journal")
            ),
            clock=lambda: 10,
        )
        value = entitlement()
        value = value.model_copy(
            update={
                "generation": value.generation.model_copy(update={"tenant_id": runtime._tenant_id}),
                "bounds": value.bounds.model_copy(
                    update={"capability_id": "planning.create_intention_line"}
                ),
            }
        )
        assert gate.import_current(value, signature(value), expected=None)
        runtime._gate = gate
        attempt = request(value, "gate-command").model_copy(
            update={
                "surface_id": "planning.create",
                "payload_digest": hashlib.sha256(command_bytes(command)).hexdigest(),
            }
        )
        submit = runtime._appender.submit

        async def fail_at_cut(command: PhysicalPublicationCommand) -> PublicationResult:
            return await submit(replace(command, fault=fault))

        with monkeypatch.context() as context:
            context.setattr(runtime._appender, "submit", fail_at_cut)
            with pytest.raises(
                RuntimeError, match=r"injected (fault before commit|lost commit acknowledgement)"
            ):
                await runtime.create(
                    principal_id="hermetic-principal",
                    credential_id="hermetic-credential",
                    session_id="hermetic-session",
                    command=command,
                    gate_request=attempt,
                )
        assert len(runtime._pending_gate_publications()) == 1
        with pytest.raises(OwnerPublicationPending):
            runtime._owner_decisions().select(prepared, prepared.predecessor_commitment)
        assert runtime._owner_decisions().snapshot().decisions == ()
    if malformed:
        unchanged = capture_authority_storage_state(database)[0]
        pending_gate = R14PlanningRuntime._pending_gate_publications

        def malformed_gate(self: R14PlanningRuntime) -> tuple[tuple[str, bytes], ...]:
            rows = pending_gate(self)
            assert len(rows) == 1
            identity, raw = rows[0]
            entry = json.loads(raw)
            entry["version"] = 2
            return ((identity, json.dumps(entry).encode()),)

        async def no_recovery_write(
            self: EventAppender, command: PhysicalPublicationCommand
        ) -> PublicationResult:
            pytest.fail("malformed gate history reached recovery submission")

        with monkeypatch.context() as patch:
            patch.setattr(R14PlanningRuntime, "_pending_gate_publications", malformed_gate)
            patch.setattr(EventAppender, "submit", no_recovery_write)
            with pytest.raises(
                OwnerJournalIntegrityError, match="operation=ingress_materialization"
            ):
                async with open_recovery(database):
                    pytest.fail("malformed gate envelope must not open")
        assert capture_authority_storage_state(database)[0] == unchanged
        return
    async with open_recovery(database) as reopened:
        assert reopened._pending_gate_publications() == ()
        with read_connection(database) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM main.publications WHERE tenant_id=? AND idempotency_key=?",
                (reopened._tenant_id, "gate-command"),
            ).fetchone() == (1,)
        reopened._require_no_pending()


@pytest.mark.parametrize("cut", ["selected", "physical", "anchor", "read_ledger", "corrupt"])
async def test_selected_recovery_converges_after_each_durable_cut(tmp_path: Path, cut: str) -> None:
    database = tmp_path / "runtime.sqlite"
    selected: list[SelectedOwnerDecision] = []
    async with open_r14_runtime(database) as runtime:
        prepared = _mechanical_preparation(runtime)
        request = prepared.request

        def select(resulting: str) -> None:
            selected.append(runtime._owner_decisions().select(prepared, resulting))

        command = PhysicalPublicationCommand(
            tenant_id=runtime._tenant_id,
            operation_kind=request.operation,
            idempotency_key=request.identity.command_id,
            request_fingerprint=request.identity.command_fingerprint,
            expected_head=request.expected.tenant_frontier,
            fence_generation="r6",
            expected_fence_frontier=0,
            minimum_fence_frontier=0,
            records=tuple(
                PhysicalRecord(
                    item.record_id,
                    item.owner,
                    item.schema_id,
                    item.canonical_bytes,
                    item.fingerprint,
                )
                for item in request.complete_records
            ),
            decision_guard=select,
        )
        if cut == "selected":
            with pytest.raises(RuntimeError, match="injected fault before commit"):
                await runtime._appender.submit(replace(command, fault="before_commit"))
        else:
            assert (await runtime._appender.submit(command)).disposition == "COMMITTED"
        decision = selected[0]
        if cut in ("anchor", "read_ledger"):
            runtime._commitment_journal.commit(runtime._tenant_id, decision.resulting_commitment)
        if cut == "read_ledger":
            runtime._anchor_materialization(
                request.identity.command_id,
                prepared.predecessor_commitment,
                decision.resulting_commitment,
            )
        with pytest.raises(OwnerPublicationPending):
            runtime._require_no_pending()
        with pytest.raises(OwnerPublicationPending):
            runtime.decide_publication(command, decision.resulting_commitment)
        with pytest.raises(OwnerPublicationPending):
            runtime._publication_decision_guard(None, decision.resulting_commitment)
    if cut == "corrupt":
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute(
                "UPDATE records SET canonical_bytes=? WHERE tenant_id=? AND record_id=?",
                (b"corrupted", request.identity.tenant_id, "mechanical-record"),
            )
        with pytest.raises(OwnerJournalIntegrityError) as failure:
            async with open_r14_runtime(database):
                pytest.fail("corrupted selected publication must not open")
        assert failure.value.__cause__ is not None
        assert "operation=recover_physical" in str(failure.value)
        assert "tenant=hermetic-tenant" in str(failure.value)
        assert "record=mechanical-command" in str(failure.value)
        return
    async with open_r14_runtime(database) as reopened:
        assert capture_authority_storage_state(database)[0] == decision.resulting_commitment
        assert (
            reopened._commitment_journal.load(reopened._tenant_id) == decision.resulting_commitment
        )
        assert (
            reopened._owner_decisions().lookup(reopened._tenant_id, request.identity.command_id)
            == decision
        )
        assert reopened._pending_owners() == ()
        assert (
            reopened._read_ledger.current_state(reopened._tenant_id).materialization_commitment
            == decision.resulting_commitment
        )
        reopened._require_no_pending()
        # A later real writer publication must not be rewound by a repeated marker.
        payload = b'{"fixture":"later-legacy-mechanics"}'
        later = PhysicalPublicationCommand(
            tenant_id=reopened._tenant_id,
            operation_kind="workspace.policy",
            idempotency_key="later-command",
            request_fingerprint=hashlib.sha256(payload).hexdigest(),
            expected_head=decision.tenant_commit_sequence,
            fence_generation="r6",
            expected_fence_frontier=0,
            minimum_fence_frontier=0,
            records=(
                PhysicalRecord(
                    "later-record",
                    "workspace_policy",
                    "chiplog.workspace.policy.v1",
                    payload,
                    hashlib.sha256(payload).hexdigest(),
                ),
            ),
        )
        assert (await reopened._appender.submit(later)).disposition == "COMMITTED"
        current = capture_authority_storage_state(database)[0]
        assert current != decision.resulting_commitment
        before = reopened._owner_decisions().snapshot()
        reopened._owner_decisions().materialized(decision)
        assert reopened._owner_decisions().snapshot() == before
        assert reopened._commitment_journal.load(reopened._tenant_id) == current
        assert (
            reopened._read_ledger.current_state(reopened._tenant_id).materialization_commitment
            == current
        )
