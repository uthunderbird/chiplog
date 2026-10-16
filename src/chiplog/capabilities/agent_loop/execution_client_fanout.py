"""Seal the selected captured response into exact original call identities."""

from __future__ import annotations

import base64
import hashlib
from typing import Literal

from .call_acceptance_contracts import (
    InitializedCallRecord,
    InitializedReadOnlyLineage,
    OriginalCallKey,
    SealedCallInput,
    SealedResponseRecord,
)
from .call_acceptance_preparation import call_subject_id
from .contracts import LoopRejected
from .delivery_preparation import DeliveryCompletion
from .execution_client_contracts import NativeCallStateV1
from .execution_client_transitions import ExecutionRun, advance, current, headed, reference
from .execution_contracts import ExecutionContinue, ExecutionPromptArtifact
from .execution_history_contracts import (
    ExecutionContinueV3,
    ExecutionRunRecordV3,
    execution_response_adapter_v3,
)
from .execution_parsing import parse_execution_response
from .recovery_contracts import Absent, NotApplicable
from .recovery_frontier_contracts import FanOutBound, ReadOnlyRetryLineage


def seal(
    run: ExecutionRun,
) -> tuple[
    ExecutionRun,
    SealedResponseRecord,
    tuple[InitializedCallRecord, ...],
    tuple[NativeCallStateV1, ...],
]:
    run = current(run)
    if run.state != "ACTIVE" or not run.turns:
        raise LoopRejected("seal requires an active captured Run")
    turn = run.turns[-1]
    if (
        turn.state != "RESPONSE_AVAILABLE"
        or turn.response_seal is not None
        or turn.initialized_calls is not None
        or not turn.attempts
    ):
        raise LoopRejected("Turn is not an unsealed capture")
    attempt = turn.attempts[turn.selector]
    if (
        attempt.state != "RESPONSE_CAPTURED"
        or attempt.response_base64 is None
        or not attempt.receipt
    ):
        raise LoopRejected("selected attempt lacks captured response")
    raw = base64.b64decode(attempt.response_base64, validate=True)
    response: ExecutionContinue | ExecutionContinueV3 | DeliveryCompletion
    if isinstance(run, ExecutionRunRecordV3):
        response = execution_response_adapter_v3().validate_json(raw)
    else:
        assert isinstance(attempt.manifest.artifact, ExecutionPromptArtifact)
        response = parse_execution_response(raw, attempt.manifest.artifact)
    calls = (
        response.tool_calls
        if isinstance(response, (ExecutionContinue, ExecutionContinueV3))
        else ()
    )
    if len(calls) > run.policy.max_tool_calls or len({call.call_id for call in calls}) != len(
        calls
    ):
        raise LoopRejected("duplicate call labels or fan-out bound exceeded")
    if isinstance(response, DeliveryCompletion) and (
        response.tenant != run.tenant
        or response.run_id != run.run_id
        or response.turn_id != turn.turn_id
    ):
        raise LoopRejected("Complete belongs to another Run or Turn")
    captured = reference(run.run_id, run)
    tools = {tool.name: tool for tool in attempt.manifest.artifact.tools}
    records, states = [], []
    for ordinal, call in enumerate(calls):
        spec = tools.get(call.tool)
        if spec is None:
            raise LoopRejected("unregistered model tool")
        original = OriginalCallKey(
            tenant_id=run.tenant,
            original_run_id=run.run_id,
            original_turn_id=turn.turn_id,
            captured_response=captured,
            ordinal=ordinal,
            model_call_label=call.call_id,
        )
        identity = call_subject_id(original)
        classification: Literal["READ_ONLY", "CONSEQUENTIAL", "PROPOSAL_ONLY"] = (
            "READ_ONLY"
            if call.tool == "read_conversation_history"
            else "CONSEQUENTIAL"
            if call.tool == "request_self_effect"
            else "PROPOSAL_ONLY"
        )
        lineage: NotApplicable | InitializedReadOnlyLineage = NotApplicable()
        if classification == "READ_ONLY":
            lineage = InitializedReadOnlyLineage(
                lineage=ReadOnlyRetryLineage(
                    lineage_id=identity + "/readonly",
                    original_call_id=identity,
                    max_attempts=3,
                    budget_version="readonly.max3.v2",
                    reducer_id="readonly.lineage.v2",
                    reducer_version="2",
                )
            )
        policy_digest = hashlib.sha256((spec.schema_id + ":" + classification).encode()).hexdigest()
        from .call_acceptance_contracts import CallSubjectHead
        from .recovery_contracts import Present

        record = InitializedCallRecord(
            original_call_id=identity,
            predecessor=Absent(),
            call=SealedCallInput(
                original=original,
                classification=classification,
                tool_schema=reference(spec.schema_id, spec),
                tool_policy=CallSubjectHead(
                    subject_id="postgres-policy:" + spec.schema_id,
                    revision=Present(head="record:" + policy_digest, fingerprint=policy_digest),
                ),
                canonical_call_base64=base64.b64encode(call.canonical_bytes()).decode(),
                retry_lineage=lineage,
            ),
        )
        records.append(record)
        states.append(
            headed(
                NativeCallStateV1(
                    head="pending",
                    predecessor=None,
                    original_call_id=identity,
                    run_id=run.run_id,
                    initialized=reference(identity, record),
                    state="TERMINAL" if classification == "PROPOSAL_ONLY" else "INITIALIZED",
                    result_base64=base64.b64encode(call.canonical_bytes()).decode()
                    if classification == "PROPOSAL_ONLY"
                    else None,
                ),
                "call-state:",
            )
        )
    references = tuple(reference(record.original_call_id, record) for record in records)
    response_seal = SealedResponseRecord(
        response_seal_id="response-seal:" + run.digest(),
        tenant_id=run.tenant,
        original_run_id=run.run_id,
        original_turn_id=turn.turn_id,
        captured_response=captured,
        complete_ordered_initialized=references,
        bound=FanOutBound(
            max_call_count=run.policy.max_tool_calls,
            max_manifest_bytes=run.policy.max_request_bytes,
            # The exact captured Run is already durable. Charge newly sealed
            # fanout growth, while carrying its immutable history byte-for-byte.
            max_serialized_batch_bytes=(
                len(run.canonical_bytes())
                + run.policy.max_request_bytes
                + run.policy.max_response_bytes
            ),
            canonicalization_version="chiplog.recovery.frontier.v1",
        ),
    )
    continuation = isinstance(response, (ExecutionContinue, ExecutionContinueV3))
    sealed_attempt = (
        headed(attempt.model_copy(update={"state": "TERMINAL_ACCEPTED"}), "execution-attempt:")
        if continuation
        else attempt
    )
    sealed_turn = headed(
        turn.model_copy(
            update={
                "state": "ACCEPTED" if continuation else "RESPONSE_AVAILABLE",
                "attempts": (*turn.attempts[:-1], sealed_attempt),
                "response_seal": reference(response_seal.response_seal_id, response_seal),
                "initialized_calls": references,
            }
        ),
        "execution-turn:",
    )
    sealed = advance(
        run,
        "ModelResponseSealed" if continuation else "ModelCompletionPrepared",
        turns=(*run.turns[:-1], sealed_turn),
    )
    size = sum(len(item.canonical_bytes()) for item in (sealed, response_seal, *records, *states))
    if size > response_seal.bound.max_serialized_batch_bytes:
        raise LoopRejected("serialized sealed batch exceeds normal byte bound")
    return sealed, response_seal, tuple(records), tuple(states)
