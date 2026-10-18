"""Atomic selected accounting/readiness adapter; complete source defaults to HOLD.

Source ports are broker composition capabilities, never caller supplied cut DTOs.
Historical authentication must reconstruct the selected cut before SQL recovery.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal, Protocol

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallPreparationRejected
from chiplog.capabilities.agent_loop.contracts import LoopHold, LoopRejected
from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
    PreparedExecutionAccounting,
    PreparedExecutionContinuation,
    PrepareExecutionAccounting,
    PrepareExecutionContinuation,
)
from chiplog.capabilities.agent_loop.execution_recovery_observations import ExecutionRecoveryCut
from chiplog.capabilities.agent_loop.recovery_contracts import RecoveryDTO
from chiplog.capabilities.agent_loop.recovery_wire_contracts import (
    EXECUTION_RECOVERY_REQUEST_SCHEMA,
    EXECUTION_RECOVERY_RESULT_SCHEMA,
)
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess

if TYPE_CHECKING:
    from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
    from chiplog.composition.r7_planning import ObservedTrustCall
    from chiplog.composition.r14_execution_runtime import R14ExecutionRuntime
    from chiplog.composition.r14_runtime import R14PlanningRuntime

OPERATION = "agent_loop.execution-accounting-readiness.v1"
ACCOUNTING_SCHEMA = "chiplog.execution.sealed-accounting.v1"
CONTINUATION_SCHEMA = "chiplog.execution.continuation-ready.v1"


class AuthenticatedRecoveryCutSource(Protocol):
    """Trusted composition independently enumerates all selected/physical families.

    Both methods run with the authority gate held. Historical authentication must
    reconstruct the pre-publication cut from selected history and verify the exact
    atomic predecessor/result bridge; accepting a cut because it decodes is invalid.
    """

    def collect_held(self, runtime: CommonCliExecutionRuntime) -> ExecutionRecoveryCut | None: ...
    def authenticate_selected_held(
        self,
        runtime: CommonCliExecutionRuntime,
        evidence: RetainedRecoveryPublication,
        entry: dict[str, object],
    ) -> bool: ...


class HoldRecoveryCutSource:
    def collect_held(self, runtime: CommonCliExecutionRuntime) -> ExecutionRecoveryCut | None:
        return None

    def authenticate_selected_held(
        self,
        runtime: CommonCliExecutionRuntime,
        evidence: RetainedRecoveryPublication,
        entry: dict[str, object],
    ) -> bool:
        return False


def source(runtime):
    port = getattr(runtime, "_full_recovery_cut_source", None)
    if not (
        callable(getattr(port, "collect_held", None))
        and callable(getattr(port, "authenticate_selected_held", None))
    ):
        return HoldRecoveryCutSource()
    return port


RECOVERY_ACTOR_BUDGET_NS = 30_000_000_000
RECOVERY_ACTOR_BUDGET_POLICY_VERSION = 26


class RecoveryActorBudgetV1(RecoveryDTO):
    profile: Literal["chiplog.execution.recovery-actor-budget.v1"] = (
        "chiplog.execution.recovery-actor-budget.v1"
    )
    issued_monotonic_ns: int
    trust_snapshot_bytes: bytes
    trust_journal_head: str
    request: PublicPortCall
    response: PublicPortSuccess


async def issue_execution_recovery_actor_v1(
    runtime: R14ExecutionRuntime, peer: str
) -> tuple[ObservedTrustCall, RecoveryActorBudgetV1]:
    """Issue the closed V26 budget before dispatch; never extend an issued call."""
    import os

    from chiplog.composition.r7_planning import ObservedTrustCall

    if peer != "hermetic-ingress":
        raise LoopRejected("unregistered execution recovery ingress")
    with runtime._authority_gate().hold():
        observation = runtime._trust.capture_verified_observation()
        issued = time.monotonic_ns()
        request = runtime._trust_call_request(
            "AUTHENTICATE",
            {
                "contour": "CLI",
                "credential_id": "hermetic-credential",
                "peer_credential": f"uid:{os.getuid()}",
                "session_id": "hermetic-session",
            },
            snapshot_bytes=observation.snapshot_bytes,
        )
        request = request.model_copy(
            update={
                "budget": CallBudget(
                    remaining_calls=1,
                    remaining_depth=1,
                    absolute_deadline_ns=issued + RECOVERY_ACTOR_BUDGET_NS,
                    policy_version=RECOVERY_ACTOR_BUDGET_POLICY_VERSION,
                )
            }
        )
    response = await runtime._supervisor.runtime().call(request)
    if (
        not isinstance(response, PublicPortSuccess)
        or response.request_id != request.request_id
        or response.responder != request.callee
        or response.schema_id != "chiplog.deployment-trust.owner-result.v1"
    ):
        raise LoopRejected("recovery actor budget owner authentication unavailable")
    result = runtime._decode_trust_result(response.canonical_payload)
    observed = ObservedTrustCall(observation, request, response, result)
    with runtime._authority_gate().hold():
        runtime._check_execution_actor(observed)
        if observation.journal_head is None:
            raise LoopRejected("recovery actor lacks authenticated trust head")
        receipt = RecoveryActorBudgetV1(
            issued_monotonic_ns=issued,
            trust_snapshot_bytes=observation.snapshot_bytes,
            trust_journal_head=observation.journal_head,
            request=request,
            response=response,
        )
        validate_recovery_actor_budget(receipt)
    return observed, receipt


def validate_recovery_actor_budget(receipt: RecoveryActorBudgetV1) -> None:
    from chiplog.platform.r7_trust import (
        TrustOwnerResult,
        decode_trust_owner_call_canonical,
    )

    request, response = receipt.request, receipt.response
    call = decode_trust_owner_call_canonical(request.canonical_payload)
    result = json.loads(response.canonical_payload)
    if result.get("reference_bytes") is not None:
        result["reference_bytes"] = base64.b64decode(result["reference_bytes"], validate=True)
    result = TrustOwnerResult.model_validate(result)
    if (
        receipt.issued_monotonic_ns < 1
        or request.budget.policy_version != RECOVERY_ACTOR_BUDGET_POLICY_VERSION
        or request.budget.remaining_calls != 1
        or request.budget.remaining_depth != 1
        or request.budget.absolute_deadline_ns
        != receipt.issued_monotonic_ns + RECOVERY_ACTOR_BUDGET_NS
        or request.operation_id != "deployment_trust.authenticate"
        or request.schema_id != "chiplog.deployment-trust.owner-call.v1"
        or request.caller.owner_id != "broker"
        or request.callee.owner_id != "deployment_trust"
        or request.caller.tenant_id != request.callee.tenant_id
        or call.mode != "AUTHENTICATE"
        or call.snapshot_bytes != receipt.trust_snapshot_bytes
        or response.request_id != request.request_id
        or response.responder != request.callee
        or response.schema_id != "chiplog.deployment-trust.owner-result.v1"
        or response.canonical_payload != result.canonical_bytes()
        or result.disposition != "VALID"
        or result.reference_bytes is None
    ):
        raise ValueError("recovery actor budget differs from closed original issuance")


class RetainedRecoveryPublication(RecoveryDTO):
    accounting_request: PrepareExecutionAccounting
    accounting_result: PreparedExecutionAccounting
    accounting_call: PublicPortCall
    accounting_reply: PublicPortSuccess
    continuation_request: PrepareExecutionContinuation
    continuation_result: PreparedExecutionContinuation
    continuation_call: PublicPortCall
    continuation_reply: PublicPortSuccess
    actor_budget_v1: RecoveryActorBudgetV1 | None = None

    def canonical_bytes(self) -> bytes:
        raw = super().canonical_bytes()
        if self.actor_budget_v1 is not None:
            return raw
        # Earlier selected V26 evidence predates the optional closed budget.
        # Preserve its exact byte identity when decoding historical entries.
        value = json.loads(raw)
        del value["actor_budget_v1"]
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def command(evidence: RetainedRecoveryPublication) -> PhysicalPublicationCommand:
    from chiplog.capabilities.agent_loop.execution_recovery_preparation import (
        prepare_execution_accounting,
        prepare_execution_continuation,
    )

    a, c = evidence.accounting_request, evidence.continuation_request
    if evidence.actor_budget_v1 is not None:
        validate_recovery_actor_budget(evidence.actor_budget_v1)
        if evidence.actor_budget_v1.request.callee.tenant_id != a.cut.tenant_id:
            raise ValueError("recovery actor budget tenant differs from original cut")
    if (
        a.cut != c.cut
        or a.command_id != c.command_id
        or c.complete_accounting != evidence.accounting_result.complete_accounting
        or len(c.complete_accounting) != 1
        or len(evidence.continuation_result.complete_continuations) != 1
        or prepare_execution_accounting(a) != evidence.accounting_result
        or prepare_execution_continuation(c) != evidence.continuation_result
    ):
        raise ValueError("recovery accounting/readiness exact owner result differs")
    for operation, request, result, sent, reply in (
        (
            "agent_loop.prepare_execution_accounting.v1",
            a,
            evidence.accounting_result,
            evidence.accounting_call,
            evidence.accounting_reply,
        ),
        (
            "agent_loop.prepare_execution_continuation.v1",
            c,
            evidence.continuation_result,
            evidence.continuation_call,
            evidence.continuation_reply,
        ),
    ):
        if (
            sent.operation_id != operation
            or sent.schema_id != EXECUTION_RECOVERY_REQUEST_SCHEMA
            or sent.canonical_payload != request.canonical_bytes()
            or sent.caller.owner_id != "broker"
            or sent.callee.owner_id != "agent_loop"
            or sent.caller.tenant_id != a.cut.tenant_id
            or sent.callee.tenant_id != a.cut.tenant_id
            or reply.responder != sent.callee
            or reply.request_id != sent.request_id
            or reply.schema_id != EXECUTION_RECOVERY_RESULT_SCHEMA
            or reply.canonical_payload != result.canonical_bytes()
            or result.source_request_fingerprint != request.digest()
        ):
            raise ValueError("recovery retained owner exchange differs")
    if evidence.accounting_call.callee != evidence.continuation_call.callee:
        raise ValueError("recovery owner session changed between exchanges")
    accounting = c.complete_accounting[0]
    readiness = evidence.continuation_result.complete_continuations[0]
    if readiness.accounting != accounting:
        raise ValueError("recovery atomic pair accounting differs")
    records = tuple(
        PhysicalRecord(identity, "agent_loop", schema, record.canonical_bytes(), record.digest())
        for identity, schema, record in (
            (accounting.accounting_id, ACCOUNTING_SCHEMA, accounting),
            (readiness.readiness_id, CONTINUATION_SCHEMA, readiness),
        )
    )
    expected_identity = "execution-accounting-readiness:" + a.cut.digest()
    if a.command_id != expected_identity:
        raise ValueError("recovery operation identity differs from cut")
    return PhysicalPublicationCommand(
        tenant_id=a.cut.tenant_id,
        operation_kind=OPERATION,
        idempotency_key=a.command_id,
        request_fingerprint=evidence.digest(),
        expected_head=a.cut.tenant_commit_sequence,
        fence_generation="r6",
        expected_fence_frontier=0,
        minimum_fence_frontier=0,
        records=records,
    )


@dataclass(frozen=True, repr=False)
class _ReadinessArtifacts:
    evidence_bytes: bytes
    base_command: PhysicalPublicationCommand


def _prepare_readiness_artifacts(evidence: RetainedRecoveryPublication) -> _ReadinessArtifacts:
    """Validate pure owner derivation before issuing the live actor receipt."""
    if evidence.actor_budget_v1 is not None:
        raise ValueError("readiness artifacts require pre-issuance evidence")
    base_command = command(evidence)
    return _ReadinessArtifacts(evidence.canonical_bytes(), base_command)


def _bind_readiness_artifacts(
    evidence: RetainedRecoveryPublication, artifacts: _ReadinessArtifacts
) -> PhysicalPublicationCommand:
    receipt = evidence.actor_budget_v1
    if receipt is None:
        raise ValueError("readiness binding requires a closed actor receipt")
    validate_recovery_actor_budget(receipt)
    if receipt.request.callee.tenant_id != evidence.accounting_request.cut.tenant_id:
        raise ValueError("recovery actor budget tenant differs from original cut")
    if (
        evidence.model_copy(update={"actor_budget_v1": None}).canonical_bytes()
        != artifacts.evidence_bytes
    ):
        raise ValueError("readiness evidence differs from validated artifacts")
    return replace(artifacts.base_command, request_fingerprint=evidence.digest())


def retained(entry: Mapping[str, object]) -> RetainedRecoveryPublication:
    raw = entry.get("execution_recovery_evidence")
    if not isinstance(raw, str):
        raise ValueError("recovery selected evidence missing")
    evidence = RetainedRecoveryPublication.model_validate_json(raw)
    if evidence.canonical_bytes().decode() != raw:
        raise ValueError("recovery selected evidence noncanonical")
    return evidence


def validate_selected(
    runtime,
    entry,
    physical_command,
    *,
    through_sequence=None,
    authenticated_loop_decisions=None,
    verified_h1_prepares=None,
):
    evidence = retained(entry)
    if evidence.actor_budget_v1 is not None:
        from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
            ExactHead,
            HermeticTrustObservationV1,
        )

        receipt = evidence.actor_budget_v1
        entries = runtime._trust._journal.entries()
        positions = [
            i
            for i, (identity, _, _) in enumerate(entries)
            if identity == receipt.trust_journal_head
        ]
        if len(positions) != 1:
            raise ValueError("selected recovery actor trust head absent or ambiguous")
        position = positions[0]
        logical = runtime._trust.owner_snapshot_entries()
        prefix = runtime._trust.historical_prefix(
            HermeticTrustObservationV1(
                physical_journal_head=ExactHead(
                    identity="deployment-trust/journal",
                    head=receipt.trust_journal_head,
                    fingerprint=hashlib.sha256(entries[position][2]).hexdigest(),
                ),
                logical_snapshot_head=logical[position][0],
            )
        )
        if prefix.snapshot_bytes != receipt.trust_snapshot_bytes:
            raise ValueError("selected recovery actor original trust prefix differs")
    from .r14_native_recovery_cut import (
        _NATIVE_AUTHENTICATE_SELECTED_HELD,
        NativeRecoveryCutSource,
    )

    cut_source = source(runtime)
    derived_command = None
    if (
        type(cut_source) is NativeRecoveryCutSource
        and getattr(cut_source.authenticate_selected_held, "__self__", None) is cut_source
        and getattr(cut_source.authenticate_selected_held, "__func__", None)
        is _NATIVE_AUTHENTICATE_SELECTED_HELD
    ):
        from .r14_native_recovery_historical import (
            _authenticate_native_recovery_selected,
            _read_derived_recovery,
        )

        runtime._authority_gate().require_held()
        if authenticated_loop_decisions is None:
            derived_command = command(evidence)
        else:
            evidence, derived_command = _read_derived_recovery(
                runtime, entry, authenticated_loop_decisions
            )
        authenticated = _authenticate_native_recovery_selected(
            cut_source,
            runtime,
            evidence,
            entry,
            derived_command,
            through_sequence=through_sequence,
            authenticated_loop_decisions=authenticated_loop_decisions,
            verified_h1_prepares=verified_h1_prepares,
        )
    elif through_sequence is None:
        if authenticated_loop_decisions is not None:
            if isinstance(cut_source, NativeRecoveryCutSource):
                authenticated = cut_source.authenticate_selected_held(
                    runtime,
                    evidence,
                    entry,
                    authenticated_loop_decisions=authenticated_loop_decisions,
                    verified_h1_prepares=verified_h1_prepares,
                )
            else:
                authenticated = cut_source.authenticate_selected_held(runtime, evidence, entry)
        else:
            authenticated = cut_source.authenticate_selected_held(runtime, evidence, entry)
    else:
        from .r14_native_recovery_historical import authenticate_native_recovery_selected

        authenticated = authenticate_native_recovery_selected(
            source(runtime),
            runtime,
            evidence,
            entry,
            through_sequence=through_sequence,
            authenticated_loop_decisions=authenticated_loop_decisions,
            verified_h1_prepares=verified_h1_prepares,
        )
    if derived_command is None:
        derived_command = command(evidence)
    if derived_command != physical_command or not authenticated:
        raise ValueError("recovery selected cut or exact atomic publication unauthenticated")
    return evidence


async def _exchange[T: RecoveryDTO](
    runtime: R14PlanningRuntime,
    request: RecoveryDTO,
    operation: str,
    result_type: type[T],
    *,
    request_schema: str = EXECUTION_RECOVERY_REQUEST_SCHEMA,
) -> tuple[T, PublicPortCall, PublicPortSuccess]:
    engine = runtime._supervisor.runtime()
    callee = engine.session("agent_loop")
    caller = BrokerSession(
        tenant_id=runtime._tenant_id,
        broker_epoch=callee.broker_epoch,
        generation_id=callee.generation_id,
        owner_id="broker",
        session_id=f"broker:{callee.generation_id}",
    )
    sent = PublicPortCall(
        operation_id=operation,
        request_id="execution-recovery:" + secrets.token_hex(16),
        caller=caller,
        callee=callee,
        schema_id=request_schema,
        canonical_payload=request.canonical_bytes(),
        budget=CallBudget(
            remaining_calls=1,
            remaining_depth=1,
            policy_version=1,
            absolute_deadline_ns=time.monotonic_ns() + 1_000_000_000,
        ),
    )
    reply = await engine.call(sent)
    if not isinstance(reply, PublicPortSuccess):
        raise LoopRejected("recovery owner IPC unavailable")
    try:
        result = result_type.model_validate_json(reply.canonical_payload)
    except ValueError as error:
        try:
            rejected = CallPreparationRejected.model_validate_json(reply.canonical_payload)
        except ValueError:
            from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
                ExecutionRecoveryClassified,
            )

            try:
                classified = ExecutionRecoveryClassified.model_validate_json(
                    reply.canonical_payload
                )
            except ValueError:
                raise LoopRejected("recovery owner preparation HOLD") from error
            if classified.source_request_fingerprint != request.digest():
                raise LoopRejected("recovery owner classification request differs") from error
            raise LoopRejected(
                f"recovery owner classified {classified.disposition}: {classified.reason}"
            ) from error
        detail = f"recovery owner preparation {rejected.code}: {rejected.reason}"
        if rejected.code == "HOLD":
            raise LoopHold(detail) from error
        raise LoopRejected(detail) from error
    return result, sent, reply


async def _finish(runtime, entry):
    with runtime._authority_gate().hold():
        evidence = validate_selected(runtime, entry, runtime._publication(entry))
        batch = command(evidence)
    await runtime._recover_exact(
        batch,
        str(entry["predecessor"]),
        str(entry["resulting"]),
        is_materialized=lambda: runtime._loop_decision_materialized(batch.idempotency_key, entry),
    )
    runtime._finish_decision(batch.idempotency_key, expected=entry)
    from chiplog.composition.r14_loop_history import _read_call_history

    _read_call_history(runtime)
    return evidence


def _capture_readiness_inputs(runtime):
    from .r14_call_history_witness import capture_call_history_journal_inputs

    try:
        journals = capture_call_history_journal_inputs(runtime)
        if journals is None:
            return None
        return journals, runtime._trust.capture_verified_observation()
    except OSError, ValueError, TypeError, RuntimeError:
        # Missing optimization evidence restores the original full collector.
        return None


@dataclass(repr=False)
class _ReadinessInputWitness:
    inputs: tuple
    valid: bool = True

    def unchanged(self, runtime) -> bool:
        if self.valid:
            try:
                self.valid = _capture_readiness_inputs(runtime) == self.inputs
            except OSError, ValueError, TypeError, RuntimeError:
                self.valid = False
        return self.valid


def _resumed_original_readiness_cut(cut: ExecutionRecoveryCut) -> bool:
    profiles = tuple(
        row.schema_id
        for row in cut.complete_sources
        if row.schema_id.startswith("chiplog.execution.native-one-call-recovery-profile.")
    )
    if profiles != ("chiplog.execution.native-one-call-recovery-profile.v5",):
        return False
    if not cut.complete_ordered_run_lineage or len(cut.complete_ordered_responses) != 1:
        return False
    run = cut.complete_ordered_run_lineage[-1]
    calls = cut.complete_ordered_responses[0].ordered_calls
    return (
        run.state == "ACTIVE"
        and run.suspension_baseline is not None
        and len(run.original_obligations) == 1
        and len(run.turns) == 1
        and len(cut.original_closures) == 1
        and len(cut.current_reductions) == 1
        and len(calls) == 1
        and calls[0].acceptance.kind == "CONSEQUENTIAL_ACCEPTED"
        and calls[0].disposition.kind == "RECOVERY_REQUIRED"
    )


async def publish_execution_accounting_readiness(runtime, peer="hermetic-ingress"):
    observed = await runtime._execution_actor(peer)
    with runtime._authority_gate().hold():
        runtime._check_execution_actor(observed)
        runtime._pending()
        prior = [
            json.loads(raw)
            for _, _, raw in runtime._loop_decisions().entries()
            if (entry := json.loads(raw)).get("kind") == "DECIDED"
            and entry.get("operation_kind") == OPERATION
        ]
        inputs_before = _capture_readiness_inputs(runtime)
        cut = source(runtime).collect_held(runtime)
        if cut is None or cut.tenant_id != runtime._tenant_id:
            raise LoopRejected("complete authenticated recovery cut unavailable; HOLD")
        witness = None
        if (
            inputs_before is not None
            and _resumed_original_readiness_cut(cut)
            and _capture_readiness_inputs(runtime) == inputs_before
        ):
            witness = _ReadinessInputWitness(inputs_before)
        current_pairs = []
        for entry in prior:
            selected_batch = runtime._publication(entry)
            validate_selected(runtime, entry, selected_batch)
            if (
                int(entry.get("expected_head", -1)) + 1 == cut.tenant_commit_sequence
                and entry.get("resulting") == cut.materialization_commitment
            ):
                current_pairs.append(entry)
        if len(current_pairs) > 1:
            raise LoopRejected("ambiguous readiness pair for current recovery cut")
        replay_entry = current_pairs[0] if current_pairs else None
        predecessor = runtime._commitment_journal.load(runtime._tenant_id)
        if predecessor != cut.materialization_commitment:
            raise LoopRejected("recovery cut predecessor differs")
        worker = runtime.current_worker()
        session = runtime._supervisor.runtime().session("agent_loop")
    if replay_entry is not None:
        return await _finish(runtime, replay_entry)
    identity = "execution-accounting-readiness:" + cut.digest()
    a = PrepareExecutionAccounting(command_id=identity, cut=cut)
    ar, ac, ap = await _exchange(
        runtime, a, "agent_loop.prepare_execution_accounting.v1", PreparedExecutionAccounting
    )
    c = PrepareExecutionContinuation(
        command_id=identity, cut=cut, complete_accounting=ar.complete_accounting
    )
    cr, cc, cp = await _exchange(
        runtime, c, "agent_loop.prepare_execution_continuation.v1", PreparedExecutionContinuation
    )
    evidence = RetainedRecoveryPublication(
        accounting_request=a,
        accounting_result=ar,
        accounting_call=ac,
        accounting_reply=ap,
        continuation_request=c,
        continuation_result=cr,
        continuation_call=cc,
        continuation_reply=cp,
    )
    artifacts = _prepare_readiness_artifacts(evidence)
    observed_v26, actor_budget = await issue_execution_recovery_actor_v1(runtime, peer)
    with runtime._authority_gate().hold():
        if not runtime._same_execution_actor_identity(observed, observed_v26):
            raise LoopRejected("recovery authenticated actor changed before publication")
        observed = observed_v26
    evidence = evidence.model_copy(update={"actor_budget_v1": actor_budget})
    batch = _bind_readiness_artifacts(evidence, artifacts)

    def guard(*, check_cut: bool = True):
        with runtime._authority_gate().hold():
            try:
                runtime._check_execution_actor(observed)
            except ValueError, TypeError, RuntimeError:
                return "STALE:authority-before-cut"
            reuse_cut = witness is not None and witness.unchanged(runtime)
            if check_cut and not reuse_cut:
                try:
                    current_cut = source(runtime).collect_held(runtime)
                except ValueError, TypeError, RuntimeError:
                    return "STALE:recovery-cut-read"
                if current_cut != cut:
                    return "STALE:recovery-cut-changed"
            try:
                current_worker = runtime.current_worker()
            except ValueError, TypeError, RuntimeError:
                return "STALE:worker-read"
            if current_worker != worker:
                return "STALE:worker-generation"
            try:
                current_session = runtime._supervisor.runtime().session("agent_loop")
            except ValueError, TypeError, RuntimeError:
                return "STALE:owner-session-read"
            if current_session != session:
                return "STALE:owner-session"
            try:
                current_predecessor = runtime._commitment_journal.load(runtime._tenant_id)
            except ValueError, TypeError, RuntimeError:
                return "STALE:commitment-journal-read"
            if current_predecessor != predecessor:
                return "STALE:commitment-journal"
            try:
                runtime._check_execution_actor(observed)
            except ValueError, TypeError, RuntimeError:
                return "STALE:authority-after-cut"
        return None

    def decide(resulting):
        reason = guard()
        if reason is not None:
            raise LoopRejected("recovery source changed at final decision: " + reason)
        runtime._require_no_pending()
        runtime._check_execution_actor(observed)
        runtime._append_decision(
            {
                "version": 1,
                "kind": "DECIDED",
                "operation_id": identity,
                "operation_kind": OPERATION,
                "expected_head": batch.expected_head,
                "fingerprint": batch.request_fingerprint,
                "predecessor": predecessor,
                "resulting": resulting,
                "records": [
                    {
                        "record_id": r.record_id,
                        "owner": r.owner,
                        "schema": r.schema_id,
                        "payload": base64.b64encode(r.canonical_bytes).decode(),
                        "digest": r.fingerprint,
                    }
                    for r in batch.records
                ],
                "execution_recovery_evidence": evidence.canonical_bytes().decode(),
            }
        )

    result = await runtime._appender.submit(
        replace(
            batch,
            expected_preimage_commitment=cut.materialization_commitment,
            admission_guard=lambda: guard(check_cut=False),
            decision_guard=decide,
        )
    )
    if result.disposition not in ("COMMITTED", "REPLAY"):
        raise LoopRejected("recovery atomic publication " + result.disposition)
    with runtime._authority_gate().hold():
        entries = [
            json.loads(raw)
            for _, _, raw in runtime._loop_decisions().entries()
            if (entry := json.loads(raw)).get("kind") == "DECIDED"
            and entry.get("operation_id") == identity
        ]
        if len(entries) != 1 or retained(entries[0]) != evidence:
            raise LoopRejected("recovery selected evidence changed")
    return await _finish(runtime, entries[0])


def validate_recovery_startup(runtime):
    """Reject malformed selected recovery evidence before pending SQL is repaired."""
    runtime._pending()
    for _, _, raw in runtime._loop_decisions().entries():
        entry = json.loads(raw)
        if entry.get("kind") != "DECIDED":
            continue
        batch = runtime._publication(entry)
        if (
            batch.operation_kind == OPERATION
            or "execution_recovery_evidence" in entry
            or any(
                row.schema_id in (ACCOUNTING_SCHEMA, CONTINUATION_SCHEMA) for row in batch.records
            )
        ):
            validate_selected(runtime, entry, batch)


def authenticate_selected_recovery_prefix_held(runtime, actual, anchored) -> bool:
    """Authenticate only the unchanged historical prefix of one pending V26 write.

    This permits original-source historical reads, never continuation or cut
    authority. The complete source validator must still authenticate C before
    startup recovery may materialize the selected accounting/readiness batch.
    """
    import sqlite3
    from contextlib import closing

    from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
    from chiplog.platform.publication_readback import inspect_publication
    from chiplog.platform.workspace_snapshot import read_connection

    runtime._authority_gate().require_held()
    try:
        runtime._check_database_identity()
        pending = runtime._pending()
        if len(pending) == 1 and pending[0].get("operation_kind") in {
            "agent_loop.accepted-open-accounting.v1",
            "agent_loop.accepted-open-recovery.v1",
            "agent_loop.execution-suspension.v1",
        }:
            from .r14_historical_suffix import authenticate_selected_open_prefix_held

            return authenticate_selected_open_prefix_held(runtime, actual, anchored)
        if (
            len(pending) == 1
            and pending[0].get("operation_kind") == "agent_loop.execution-next-turn.v1"
        ):
            from .r14_next_turn_pending import authenticate_selected_next_turn_prefix_held

            return authenticate_selected_next_turn_prefix_held(runtime, actual, anchored)
        if (
            len(pending) == 1
            and pending[0].get("operation_kind") == "agent_loop.original-resolver-invocation.v1"
        ):
            from .r14_invocation_pending import authenticate_selected_invocation_prefix_held

            return authenticate_selected_invocation_prefix_held(runtime, actual, anchored)
        if (
            len(pending) == 1
            and pending[0].get("operation_kind") == "agent_loop.original-call-resolution.v1"
        ):
            from .r14_original_call_resolution_source import (
                authenticate_selected_resolution_prefix_held,
            )

            return authenticate_selected_resolution_prefix_held(runtime, actual, anchored)
        if len(pending) != 1 or runtime._pending_owners() or runtime._pending_gate_publications():
            return False
        entry = pending[0]
        if entry.get("operation_kind") != OPERATION:
            return False
        evidence = retained(entry)
        batch = command(evidence)
        cut = evidence.accounting_request.cut
        if (
            runtime._publication(entry) != batch
            or batch.tenant_id != runtime._tenant_id
            or cut.database_id != runtime._database_identity[0]
            or entry.get("predecessor") != cut.materialization_commitment
            or batch.expected_head != cut.tenant_commit_sequence
            or tuple(row.schema_id for row in batch.records)
            != (ACCOUNTING_SCHEMA, CONTINUATION_SCHEMA)
            or anchored != runtime._commitment_journal.load(runtime._tenant_id)
        ):
            return False
        predecessor, resulting = entry.get("predecessor"), entry.get("resulting")
        with read_connection(runtime._database) as connection:
            if actual != capture_authority_snapshot_commitment(connection, runtime._tenant_id):
                return False
            state = inspect_publication(connection, batch, batch.expected_head + 1)
            if state == "ABSENT":
                if actual != predecessor or anchored != predecessor:
                    return False
            elif state == "COMPLETE":
                if actual != resulting or anchored not in (predecessor, resulting):
                    return False
            else:
                return False
            # No live mutation. Removing precisely the verified publication must
            # recover the independently anchored C hash, including every table.
            with closing(sqlite3.connect(":memory:")) as historical:
                connection.backup(historical)
                historical.execute("BEGIN")
                if state == "COMPLETE":
                    for row in batch.records:
                        deleted = historical.execute(
                            "DELETE FROM records WHERE tenant_id=? AND record_id=?",
                            (runtime._tenant_id, row.record_id),
                        ).rowcount
                        if deleted != 1:
                            return False
                    deleted = historical.execute(
                        "DELETE FROM publications WHERE tenant_id=? AND operation_kind=? "
                        "AND idempotency_key=?",
                        (runtime._tenant_id, batch.operation_kind, batch.idempotency_key),
                    ).rowcount
                    if deleted != 1:
                        return False
                    historical.execute(
                        "UPDATE tenant_heads SET head=? WHERE tenant_id=?",
                        (batch.expected_head, runtime._tenant_id),
                    )
                if (
                    capture_authority_snapshot_commitment(historical, runtime._tenant_id)
                    != predecessor
                ):
                    return False
                sequence = batch.expected_head + 1
                for row in batch.records:
                    historical.execute(
                        "INSERT INTO records VALUES(?,?,?,?,?,?)",
                        (
                            runtime._tenant_id,
                            row.record_id,
                            row.owner,
                            row.schema_id,
                            row.canonical_bytes,
                            sequence,
                        ),
                    )
                historical.execute(
                    "INSERT INTO publications VALUES(?,?,?,?,?,?)",
                    (
                        runtime._tenant_id,
                        batch.operation_kind,
                        batch.idempotency_key,
                        batch.request_fingerprint,
                        sequence,
                        "\n".join(row.record_id for row in batch.records),
                    ),
                )
                historical.execute(
                    "UPDATE tenant_heads SET head=? WHERE tenant_id=?",
                    (sequence, runtime._tenant_id),
                )
                if (
                    capture_authority_snapshot_commitment(historical, runtime._tenant_id)
                    != resulting
                ):
                    return False
        runtime._check_database_identity()
        return True
    except ValueError, TypeError, RuntimeError, OSError, sqlite3.Error:
        return False
