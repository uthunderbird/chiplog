"""Broker adapter for the bounded V23 native cancellation accounting exchange.

It re-reads the selected physical history before building the owner wire.  The
returned value is still preparation data; it neither publishes accounting nor
starts a subsequent execution turn.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from hashlib import sha256

from pydantic import ValidationError

from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallSubjectHead,
    InitializedCallRecord,
    SealedResponseRecord,
)
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.native_one_call_accounting_contracts import (
    NATIVE_ONE_CALL_ACCOUNTING_REQUEST_SCHEMA,
    NATIVE_ONE_CALL_ACCOUNTING_RESULT_SCHEMA,
    NATIVE_ONE_CALL_ACCOUNTING_V2_REQUEST_SCHEMA,
    NATIVE_ONE_CALL_ACCOUNTING_V2_RESULT_SCHEMA,
    NativeOneCallAccountingSourceStampV2,
    PreparedNativeOneCallCancelledAccounting,
    PreparedNativeOneCallCancelledAccountingV2,
    PrepareNativeOneCallCancelledAccounting,
    PrepareNativeOneCallCancelledAccountingV2,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import InitializedCall
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.r14_cancellation_contracts import CANCELLATION_SCHEMA, NOT_EXECUTED_SCHEMA
from chiplog.composition.r14_execution_fanout_contracts import EXECUTION_RUN_SCHEMA
from chiplog.composition.r14_execution_fanout_records import reference
from chiplog.composition.r14_execution_recovery_source import (
    R14NativeCancelledCallSource,
    R14NativeCancelledCallSourceFacts,
)
from chiplog.composition.r14_fanout_contracts import INITIALIZED_SCHEMA, SEAL_SCHEMA
from chiplog.composition.r14_loop_history import read_execution_recovery_history
from chiplog.composition.r14_native_execution_record_source import (
    R14NativeExecutionRecordSourceManifest,
    _fingerprint,
    _R14NativeExecutionRecordSource,
)
from chiplog.composition.r14_native_preparation_bindings import bind_native_execution_preparation_v2
from chiplog.composition.r14_native_selected_accounting_source import (
    R14NativeOneCallCancelledFacts,
    R14NativeSelectedAccountingSource,
)
from chiplog.composition.r14_original_prepare_source import R14OriginalNativePrepareSource
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess
from chiplog.platform.workspace_snapshot import read_connection, workspace_snapshot


def _v1_request(
    facts: R14NativeCancelledCallSourceFacts,
    captured_run: ExecutionRunRecord,
    sealed_run: ExecutionRunRecord,
    seal: SealedResponseRecord,
    initialized: InitializedCallRecord,
) -> PrepareNativeOneCallCancelledAccounting:
    """Build the immutable V1 wire shared by the retained V23 producer and V24."""
    return PrepareNativeOneCallCancelledAccounting(
        command_id=(
            "native-one-call-accounting:" + facts.selected_response_seal.revision.fingerprint
        ),
        tenant_id=facts.tenant_id,
        captured_run=CallSubjectHead(
            subject_id=captured_run.run_id,
            revision=Present(head=captured_run.head, fingerprint=captured_run.digest()),
        ),
        captured_run_bytes=captured_run.canonical_bytes(),
        selected_sealed_run=facts.selected_sealed_run,
        physical_sealed_run=CallSubjectHead(
            subject_id=sealed_run.run_id,
            revision=Present(head=sealed_run.head, fingerprint=sealed_run.digest()),
        ),
        selected_sealed_run_bytes=sealed_run.canonical_bytes(),
        selected_response=facts.selected_response_seal,
        selected_response_bytes=seal.canonical_bytes(),
        initialized=facts.initialized,
        initialized_record_bytes=initialized.canonical_bytes(),
        acceptance=InitializedCall(initialized=facts.initialized.revision),
        terminal=reference(facts.cancellation_terminal.terminal_id, facts.cancellation_terminal),
        terminal_record_bytes=facts.cancellation_terminal.canonical_bytes(),
        result=reference(facts.not_executed_result.result_id, facts.not_executed_result),
        result_record_bytes=facts.not_executed_result.canonical_bytes(),
        frontier_registry=facts.frontier_registry_reference,
        frontier_registry_bytes=facts.frontier_registry_bytes,
    )


class R14NativeOneCallCancelledAccountingProducer:
    """V23 producer retained unchanged for its native V1 wire and route."""

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("native accounting producer requires installed CLI runtime")
        self._runtime = runtime

    async def prepare(
        self, facts: R14NativeCancelledCallSourceFacts
    ) -> PreparedNativeOneCallCancelledAccounting | None:
        gate = self._runtime._authority_gate()
        try:
            with gate.hold():
                self._runtime._check_database_identity()
                if R14NativeCancelledCallSource(self._runtime).collect() != facts:
                    return None
                snapshot, _, legacy_seals, seals, _, _ = read_execution_recovery_history(
                    self._runtime
                )
                if legacy_seals or len(seals) != 1:
                    return None
                seal = seals[0].proposal.fan_out.response_seal
                initialized = seals[0].proposal.fan_out.initialized_records
                if len(initialized) != 1:
                    return None
                selected_sealed_run = seals[0].proposal.sealed_run
                sealed_run = next(
                    (run for run in snapshot.records if run.head == selected_sealed_run.head),
                    None,
                )
                if (
                    sealed_run is None
                    or sealed_run != selected_sealed_run
                    or reference(sealed_run.run_id, sealed_run) != facts.selected_sealed_run
                    or facts.selected_sealed_run != facts.physical_sealed_run
                ):
                    return None
                captured_run = seals[0].request.captured_run
                if sealed_run.predecessor != captured_run.head:
                    return None
                engine = self._runtime._supervisor.runtime()
                callee = engine.session("agent_loop")
                caller = BrokerSession(
                    tenant_id=self._runtime._tenant_id,
                    broker_epoch=callee.broker_epoch,
                    generation_id=callee.generation_id,
                    owner_id="broker",
                    session_id=f"broker:{callee.generation_id}",
                )
                request = _v1_request(facts, captured_run, sealed_run, seal, initialized[0])
                sent = PublicPortCall(
                    operation_id="agent_loop.prepare_native_one_call_cancelled_accounting",
                    request_id="native-one-call-accounting:" + secrets.token_hex(16),
                    caller=caller,
                    callee=callee,
                    schema_id=NATIVE_ONE_CALL_ACCOUNTING_REQUEST_SCHEMA,
                    canonical_payload=request.canonical_bytes(),
                    budget=CallBudget(
                        remaining_calls=1,
                        remaining_depth=1,
                        policy_version=1,
                        absolute_deadline_ns=time.monotonic_ns() + 1_000_000_000,
                    ),
                )
            reply = await engine.call(sent)
            if (
                not isinstance(reply, PublicPortSuccess)
                or reply.request_id != sent.request_id
                or reply.responder != callee
                or reply.schema_id != NATIVE_ONE_CALL_ACCOUNTING_RESULT_SCHEMA
            ):
                return None
            try:
                result = PreparedNativeOneCallCancelledAccounting.model_validate_json(
                    reply.canonical_payload
                )
            except UnicodeDecodeError, ValidationError, ValueError:
                return None
            if (
                result.canonical_bytes() != reply.canonical_payload
                or result.source_request_fingerprint
                != sha256(request.canonical_bytes()).hexdigest()
            ):
                return None
            with gate.hold():
                self._runtime._check_database_identity()
            return result
        except OSError, RuntimeError, TypeError:
            raise


class R14NativeOneCallCancelledAccountingV24Producer:
    """Re-open exact source facts and invoke only the registered V24 owner route."""

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("native accounting producer requires installed CLI runtime")
        self._runtime = runtime

    async def prepare(
        self, facts: R14NativeCancelledCallSourceFacts
    ) -> PreparedNativeOneCallCancelledAccounting | None:
        exchange = await self.prepare_exchange(facts)
        return None if exchange is None else exchange.result.result

    async def prepare_exchange(
        self, facts: R14NativeCancelledCallSourceFacts
    ) -> NativeOneCallAccountingV24Exchange | None:
        """Retain the exact owner wire and physical source for sole-writer publication."""
        gate = self._runtime._authority_gate()
        try:
            with gate.hold():
                self._runtime._check_database_identity()
                acquired = self._acquire_held(facts)
                if acquired is None:
                    return None
                current, selected, stamp, provenance = acquired
                snapshot, _, legacy_seals, seals, _, _ = read_execution_recovery_history(
                    self._runtime
                )
                if legacy_seals or len(seals) != 1:
                    return None
                seal = seals[0].proposal.fan_out.response_seal
                initialized = seals[0].proposal.fan_out.initialized_records
                if len(initialized) != 1:
                    return None
                selected_sealed_run = seals[0].proposal.sealed_run
                sealed_run = next(
                    (run for run in snapshot.records if run.head == selected_sealed_run.head),
                    None,
                )
                if (
                    sealed_run is None
                    or sealed_run != selected_sealed_run
                    or reference(sealed_run.run_id, sealed_run) != current.selected_sealed_run
                    or current.selected_sealed_run != current.physical_sealed_run
                ):
                    return None
                captured_run = seals[0].request.captured_run
                if sealed_run.predecessor != captured_run.head:
                    return None
                engine = self._runtime._supervisor.runtime()
                callee = engine.session("agent_loop")
                caller = BrokerSession(
                    tenant_id=self._runtime._tenant_id,
                    broker_epoch=callee.broker_epoch,
                    generation_id=callee.generation_id,
                    owner_id="broker",
                    session_id=f"broker:{callee.generation_id}",
                )
                inner = PrepareNativeOneCallCancelledAccounting(
                    command_id=(
                        "native-one-call-accounting:"
                        + current.selected_response_seal.revision.fingerprint
                    ),
                    tenant_id=current.tenant_id,
                    captured_run=CallSubjectHead(
                        subject_id=captured_run.run_id,
                        revision=Present(
                            head=captured_run.head,
                            fingerprint=captured_run.digest(),
                        ),
                    ),
                    captured_run_bytes=captured_run.canonical_bytes(),
                    selected_sealed_run=current.selected_sealed_run,
                    physical_sealed_run=CallSubjectHead(
                        subject_id=sealed_run.run_id,
                        revision=Present(
                            head=sealed_run.head,
                            fingerprint=sealed_run.digest(),
                        ),
                    ),
                    selected_sealed_run_bytes=sealed_run.canonical_bytes(),
                    selected_response=current.selected_response_seal,
                    selected_response_bytes=seal.canonical_bytes(),
                    initialized=current.initialized,
                    initialized_record_bytes=initialized[0].canonical_bytes(),
                    acceptance=InitializedCall(initialized=current.initialized.revision),
                    terminal=reference(
                        current.cancellation_terminal.terminal_id, current.cancellation_terminal
                    ),
                    terminal_record_bytes=current.cancellation_terminal.canonical_bytes(),
                    result=reference(
                        current.not_executed_result.result_id, current.not_executed_result
                    ),
                    result_record_bytes=current.not_executed_result.canonical_bytes(),
                    frontier_registry=current.frontier_registry_reference,
                    frontier_registry_bytes=current.frontier_registry_bytes,
                )
                request = PrepareNativeOneCallCancelledAccountingV2(
                    request=inner,
                    preparation_bindings=selected.source.preparation_bindings,
                    source_stamp=stamp,
                )
                # The sidecar deliberately stays out of the retained V24 wire;
                # equality of ``acquired`` below is its owner-IPC CAS.
                if provenance.source_stamp != stamp:
                    return None
                sent = PublicPortCall(
                    operation_id="agent_loop.prepare_native_one_call_cancelled_accounting.v2",
                    request_id="native-one-call-accounting:" + secrets.token_hex(16),
                    caller=caller,
                    callee=callee,
                    schema_id=NATIVE_ONE_CALL_ACCOUNTING_V2_REQUEST_SCHEMA,
                    canonical_payload=request.canonical_bytes(),
                    budget=CallBudget(
                        remaining_calls=1,
                        remaining_depth=1,
                        policy_version=1,
                        absolute_deadline_ns=time.monotonic_ns() + 1_000_000_000,
                    ),
                )
            reply = await engine.call(sent)
            if (
                not isinstance(reply, PublicPortSuccess)
                or reply.request_id != sent.request_id
                or reply.responder != callee
                or reply.schema_id != NATIVE_ONE_CALL_ACCOUNTING_V2_RESULT_SCHEMA
            ):
                return None
            try:
                result = PreparedNativeOneCallCancelledAccountingV2.model_validate_json(
                    reply.canonical_payload
                )
                result_bytes = result.canonical_bytes()
            except UnicodeDecodeError, ValidationError, ValueError:
                return None
            if (
                result_bytes != reply.canonical_payload
                or result.source_request_fingerprint
                != sha256(request.canonical_bytes()).hexdigest()
            ):
                return None
            from chiplog.capabilities.agent_loop.native_one_call_accounting_preparation import (
                prepare_native_one_call_cancelled_accounting_v2,
            )

            if prepare_native_one_call_cancelled_accounting_v2(request) != result:
                return None
            with gate.hold():
                self._runtime._check_database_identity()
                if self._acquire_held(facts) != acquired:
                    return None
            return NativeOneCallAccountingV24Exchange(
                request, result, sent, reply, selected, provenance
            )
        except OSError, RuntimeError, TypeError:
            raise

    def _stamp_held(self) -> NativeOneCallAccountingSourceStampV2 | None:
        """Read commitment before tenant head in one explicit SQLite snapshot."""
        identity = self._runtime._database_identity
        with read_connection(self._runtime._database) as connection:
            if not connection.in_transaction:
                return None
            commitment = capture_authority_snapshot_commitment(connection, self._runtime._tenant_id)
            row = connection.execute(
                "SELECT head FROM tenant_heads WHERE tenant_id=?", (self._runtime._tenant_id,)
            ).fetchone()
            journal = self._runtime._commitment_journal.load(self._runtime._tenant_id)
        if row is None or journal != commitment or identity != self._runtime._database_identity:
            return None
        return NativeOneCallAccountingSourceStampV2(
            tenant_id=self._runtime._tenant_id,
            database_identity=identity,
            tenant_commit_sequence=int(row[0]),
            authority_materialization_commitment=commitment,
        )

    def _acquire_held(
        self, supplied: R14NativeCancelledCallSourceFacts
    ) -> (
        tuple[
            R14NativeCancelledCallSourceFacts,
            R14NativeOneCallCancelledFacts,
            NativeOneCallAccountingSourceStampV2,
            R14NativeExecutionRecordSourceManifest,
        ]
        | None
    ):
        """Acquire all SQLite-derived source reads in one mounted read snapshot."""
        with workspace_snapshot(self._runtime._database):
            return self._acquire_held_snapshot(supplied)

    def _acquire_held_snapshot(
        self, supplied: R14NativeCancelledCallSourceFacts
    ) -> (
        tuple[
            R14NativeCancelledCallSourceFacts,
            R14NativeOneCallCancelledFacts,
            NativeOneCallAccountingSourceStampV2,
            R14NativeExecutionRecordSourceManifest,
        ]
        | None
    ):
        """Bracket selected journals and the shared physical read snapshot by stamp."""
        before = self._stamp_held()
        current = R14NativeCancelledCallSource(self._runtime).collect()
        if current != supplied:
            return None
        source = self._stamp_held()
        projection = R14OriginalNativePrepareSource(self._runtime).collect(
            current.selected_response_seal
        )
        if projection is None:
            return None
        bindings = bind_native_execution_preparation_v2(projection)
        selected = R14NativeSelectedAccountingSource(
            self._runtime, projection, bindings
        ).collect_one_cancelled(projection.prepared_run)
        provenance = (
            _R14NativeExecutionRecordSource(self._runtime).collect_held(selected, source)
            if selected is not None and source is not None
            else None
        )
        after = self._stamp_held()
        if (
            before is None
            or before != source
            or source != after
            or selected is None
            or provenance is None
            or provenance.source_stamp != source
            or provenance.fingerprint
            != _fingerprint(
                provenance.tenant_id,
                provenance.source_stamp,
                provenance.original_prepared_run,
                provenance.selected_response_seal,
                provenance.initialized,
                provenance.terminal,
                provenance.result,
                provenance.complete_ordered_run_lineage,
                provenance.ordered_members,
            )
            or tuple((member.schema_id, member.record_id) for member in provenance.ordered_members)
            != (
                *(
                    (EXECUTION_RUN_SCHEMA, ref.revision.head)
                    for ref in selected.source.target_run_lineage
                ),
                (
                    SEAL_SCHEMA,
                    "record:" + current.selected_response_seal.revision.fingerprint,
                ),
                (
                    INITIALIZED_SCHEMA,
                    "record:" + selected.selected_seal.initialized_records[0].digest(),
                ),
                (
                    CANCELLATION_SCHEMA,
                    current.cancellation_terminal.terminal_id,
                ),
                (NOT_EXECUTED_SCHEMA, current.not_executed_result.result_id),
            )
            or selected.source.tenant_id != current.tenant_id
            or selected.source.database_identity != before.database_identity
            or selected.source.preparation_bindings != bindings
            or selected.original_call_id != current.original_call_id
            or selected.initialized != current.initialized
            or selected.selected_seal.response_seal != current.selected_response_seal
            or selected.selected_seal.sealed_run.subject_id
            != current.selected_sealed_run.subject_id
            or selected.selected_seal.sealed_run.revision.fingerprint
            != current.selected_sealed_run.revision.fingerprint
        ):
            return None
        return current, selected, source, provenance


@dataclass(frozen=True, slots=True)
class NativeOneCallAccountingV24Exchange:
    request: PrepareNativeOneCallCancelledAccountingV2
    result: PreparedNativeOneCallCancelledAccountingV2
    owner_request: PublicPortCall
    owner_response: PublicPortSuccess
    selected_source: R14NativeOneCallCancelledFacts
    physical_source: R14NativeExecutionRecordSourceManifest
