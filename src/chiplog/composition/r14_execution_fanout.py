"""Authenticated executable fanout through the original owner and sole writer."""

from __future__ import annotations

import base64
import secrets
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Literal, cast

from pydantic import TypeAdapter

from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallAuthorityObservation,
    CallPreparationCut,
    CallSubjectHead,
    FanOutPreparationRequest,
    OriginalCallKey,
    SealedCallInput,
)
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.execution_contracts import (
    ExecutionContinue,
    ExecutionRunRecord,
)
from chiplog.capabilities.agent_loop.execution_fan_out_contracts import (
    ExecutionCapturedFanOutProposal,
    ExecutionCapturedFanOutRequest,
    ExecutionCapturedFanOutResult,
)
from chiplog.capabilities.agent_loop.execution_parsing import (
    EXECUTION_TOOLS,
    parse_execution_response,
)
from chiplog.capabilities.agent_loop.fan_out_contracts import FanOutToolPolicy, FanOutToolRegistry
from chiplog.capabilities.agent_loop.recovery_contracts import (
    NonSchedulerFence,
    NotApplicable,
    Present,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import FanOutBound
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess

from .r14_execution_complete_seal_records import (
    CompleteSealProfile,
    ExecutionCompleteSealPhysicalEnvelope,
    ExecutionCompleteSealPhysicalEnvelopeV2,
    RetainedExecutionCompleteSeal,
    RetainedExecutionCompleteSealV2,
    build_complete_seal_envelope,
    complete_seal_physical_command,
    retained_execution_complete_seal,
)
from .r14_execution_fanout_contracts import (
    ExecutionFanOutPhysicalEnvelope,
    RetainedExecutionFanOutPreparation,
)
from .r14_execution_fanout_records import build_envelope, physical_command, reference
from .r14_loop_history import read_execution_call_history

if TYPE_CHECKING:
    from .r14_execution_runtime import R14ExecutionRuntime


def execution_registry(run: ExecutionRunRecord) -> tuple[FanOutToolRegistry, FanOutBound]:
    bound = FanOutBound(
        max_call_count=min(256, run.policy.max_tool_calls),
        max_manifest_bytes=64 * 1024,
        max_serialized_batch_bytes=16 * 1024 * 1024,
        canonicalization_version="chiplog.recovery.frontier.v1",
    )
    registry = FanOutToolRegistry(
        registry_id="hermetic-execution-fanout-v2",
        version="2",
        entries=tuple(
            FanOutToolPolicy(
                tool_name=tool.name,
                tool_version=tool.version,
                schema_id=tool.schema_id,
                tool_schema=reference(tool.schema_id, tool),
                tool_policy=reference("hermetic-execution-policy:" + tool.name, bound),
                classification="CONSEQUENTIAL"
                if tool.name == "request_self_effect"
                else "PROPOSAL_ONLY",
                retry_policy=NotApplicable(),
            )
            for tool in sorted(EXECUTION_TOOLS, key=lambda t: (t.name, t.version, t.schema_id))
        ),
    )
    return registry, bound


async def publish_execution_fanout(
    runtime: R14ExecutionRuntime,
    peer: str,
    run_id: str,
    expected_head: str,
    *,
    complete_registry: bool = False,
    complete_profile: CompleteSealProfile = "V1",
) -> ExecutionRunRecord:
    observed = await runtime._execution_actor(peer)
    with runtime._authority_gate().hold():
        runtime._check_execution_actor(observed)
        if complete_profile not in ("V1", "H1_V2"):
            raise LoopRejected("unregistered execution complete seal profile")
        snapshot, inventory, previous = read_execution_call_history(runtime)
        lineage = [row for row in snapshot.records if row.run_id == run_id]
        if (
            not lineage
            or not isinstance(lineage[-1], ExecutionRunRecord)
            or lineage[-1].head != expected_head
        ):
            raise LoopRejected("missing exact execution capture")
        captured = lineage[-1]
        if (
            captured.event != "ModelResponseCaptured"
            or captured.state != "ACTIVE"
            or captured.root_binding != "NOT_APPLICABLE"
            or captured.worker_session != runtime.current_worker()
            or any(row.request.captured_run.head == captured.head for row in previous)
        ):
            raise LoopRejected("stale, sealed or unsupported execution capture")
        turn = captured.turns[-1]
        attempt = turn.attempts[turn.selector]
        if attempt.response_base64 is None or attempt.state != "RESPONSE_CAPTURED":
            raise LoopRejected("execution capture lacks exact response")
        if complete_profile == "H1_V2":
            # The V2 registry is selected only by the H1 path.  Its workspace
            # proof and EMPTY inventories are not yet authenticated by B's
            # verifier, so do not even request an owner proposal or publish a
            # physically valid-looking V2 registry companion.
            raise LoopRejected("H1 workspace original verification is unavailable")
        registry, bound = execution_registry(captured)
        registry_ref = reference(registry.registry_id, registry)
        engine = runtime._supervisor.runtime()
        callee = engine.session("agent_loop")
        caller = BrokerSession(
            tenant_id=runtime._tenant_id,
            broker_epoch=callee.broker_epoch,
            generation_id=callee.generation_id,
            owner_id="broker",
            session_id=f"broker:{callee.generation_id}",
        )
        at = time.monotonic_ns()
        deadline = observed.request.budget.absolute_deadline_ns
        predecessor = runtime._commitment_journal.load(runtime._tenant_id)
        if predecessor is None:
            raise LoopRejected("missing execution fanout predecessor")
        capture = CallSubjectHead(
            subject_id=run_id, revision=Present(head=captured.head, fingerprint=captured.digest())
        )
        cut = CallPreparationCut(
            tenant_id=runtime._tenant_id,
            current_run=capture,
            run_state="ACTIVE",
            tenant_commit_sequence=snapshot.tenant_head,
            materialization_commitment=predecessor,
            complete_call_inventory=reference("call-inventory:" + runtime._tenant_id, inventory),
            predecessor_inventory=inventory,
            authority_registry=registry_ref,
            sources=tuple(
                CallAuthorityObservation(
                    source_id=identity,
                    family=cast(Literal["TOOL_SCHEMA", "POLICY"], family),
                    source=reference(identity, value),
                    generation=callee.generation_id,
                    frontier=str(snapshot.tenant_head),
                    canonical_value_base64=base64.b64encode(value.canonical_bytes()).decode(),
                    observed_at_ns=at,
                    valid_until_ns=deadline,
                )
                for identity, family, value in (
                    (registry.registry_id, "TOOL_SCHEMA", registry),
                    ("hermetic-execution-fanout-bound-v2", "POLICY", bound),
                )
            ),
            fence=NonSchedulerFence(
                lineage=NotApplicable(),
                physical_root=NotApplicable(),
                lease=NotApplicable(),
                clock_proof=NotApplicable(),
                run_id=run_id,
                run_head=captured.head,
                worker_session_id=runtime.current_worker(),
                runtime_generation=callee.generation_id,
            ),
        )
        parsed = parse_execution_response(
            base64.b64decode(attempt.response_base64, validate=True), attempt.manifest.artifact
        )
        calls = parsed.tool_calls if isinstance(parsed, ExecutionContinue) else ()
        policies = {item.tool_name: item for item in registry.entries}
        request = ExecutionCapturedFanOutRequest(
            request=FanOutPreparationRequest(
                command_id="seal:" + captured.head,
                original_run_id=run_id,
                original_turn_id=turn.turn_id,
                captured_response=capture,
                canonical_response_base64=attempt.response_base64,
                ordered_calls=tuple(
                    SealedCallInput(
                        original=OriginalCallKey(
                            tenant_id=captured.tenant,
                            original_run_id=run_id,
                            original_turn_id=turn.turn_id,
                            captured_response=capture,
                            ordinal=ordinal,
                            model_call_label=call.call_id,
                        ),
                        classification=policies[call.tool].classification,
                        tool_schema=policies[call.tool].tool_schema,
                        tool_policy=policies[call.tool].tool_policy,
                        canonical_call_base64=base64.b64encode(call.canonical_bytes()).decode(),
                        retry_lineage=NotApplicable(),
                    )
                    for ordinal, call in enumerate(calls)
                ),
                bound=bound,
                cut=cut,
            ),
            captured_run=captured,
            tool_registry=registry,
            tool_registry_head=registry_ref,
        )
    sent = PublicPortCall(
        operation_id="agent_loop.prepare_execution_captured_fan_out",
        request_id="execution-fanout:" + secrets.token_hex(16),
        caller=caller,
        callee=callee,
        schema_id="chiplog.call.execution-captured-fanout-preparation.v2",
        canonical_payload=request.canonical_bytes(),
        budget=CallBudget(
            remaining_calls=1, remaining_depth=1, policy_version=1, absolute_deadline_ns=deadline
        ),
    )
    reply = await engine.call(sent)
    if (
        not isinstance(reply, PublicPortSuccess)
        or reply.request_id != sent.request_id
        or reply.responder != callee
        or reply.schema_id != "chiplog.call.execution-captured-fanout-result.v2"
    ):
        raise LoopRejected("execution fanout owner exchange rejected")
    adapter: TypeAdapter[ExecutionCapturedFanOutResult] = TypeAdapter(ExecutionCapturedFanOutResult)
    proposal = adapter.validate_json(reply.canonical_payload)
    if (
        not isinstance(proposal, ExecutionCapturedFanOutProposal)
        or proposal.canonical_bytes() != reply.canonical_payload
    ):
        raise LoopRejected("execution fanout owner rejected or returned noncanonical bytes")
    evidence = RetainedExecutionFanOutPreparation(
        request=request,
        proposal=proposal,
        expected_snapshot_fingerprint=snapshot.digest(),
        caller=caller,
        callee=callee,
        request_id=sent.request_id,
        deadline_ns=deadline,
    )
    is_zero_call_complete = complete_registry and (
        proposal.sealed_run.event == "ModelCompletionPrepared"
        and proposal.fan_out.initialized_records == ()
    )
    if complete_registry and not is_zero_call_complete:
        raise LoopRejected("complete registry requires an eligible zero-call Complete")
    envelope: (
        ExecutionFanOutPhysicalEnvelope
        | ExecutionCompleteSealPhysicalEnvelope
        | ExecutionCompleteSealPhysicalEnvelopeV2
    )
    if is_zero_call_complete:
        complete_retained: RetainedExecutionCompleteSeal | RetainedExecutionCompleteSealV2 | None
        complete_retained = retained_execution_complete_seal(evidence, profile=complete_profile)
        envelope = build_complete_seal_envelope(complete_retained)
        command = complete_seal_physical_command(envelope)
    else:
        complete_retained = None
        envelope = build_envelope(evidence)
        command = physical_command(envelope)

    def guard() -> Literal["STALE"] | None:
        with runtime._authority_gate().hold():
            runtime._check_execution_actor(observed)
            if (
                read_execution_call_history(runtime) != (snapshot, inventory, previous)
                or runtime.current_worker() != captured.worker_session
                or engine.session("agent_loop") != callee
                or time.monotonic_ns() >= deadline
                or execution_registry(captured) != (registry, bound)
                or runtime._commitment_journal.load(runtime._tenant_id) != predecessor
                or (complete_retained is None and build_envelope(evidence) != envelope)
                or (
                    complete_retained is not None
                    and build_complete_seal_envelope(complete_retained) != envelope
                )
            ):
                return "STALE"
            return None

    def decide(resulting: str) -> None:
        with runtime._authority_gate().hold():
            runtime._require_no_pending()
            runtime._append_decision(
                {
                    "version": 1,
                    "kind": "DECIDED",
                    "operation_id": command.idempotency_key,
                    "operation_kind": command.operation_kind,
                    "expected_head": command.expected_head,
                    "fingerprint": command.request_fingerprint,
                    "predecessor": predecessor,
                    "resulting": resulting,
                    "records": [
                        {
                            "record_id": row.record_id,
                            "owner": row.owner,
                            "schema": row.schema_id,
                            "payload": base64.b64encode(row.canonical_bytes).decode(),
                            "digest": row.fingerprint,
                        }
                        for row in command.records
                    ],
                    **(
                        {
                            "execution_complete_seal": complete_retained.canonical_bytes().decode(),
                            "execution_complete_seal_envelope": envelope.canonical_bytes().decode(),
                        }
                        if complete_retained is not None
                        else {
                            "execution_fanout": evidence.canonical_bytes().decode(),
                            "execution_fanout_envelope": envelope.canonical_bytes().decode(),
                        }
                    ),
                }
            )

    result = await runtime._appender.submit(
        replace(command, admission_guard=guard, decision_guard=decide)
    )
    if result.disposition not in ("COMMITTED", "REPLAY"):
        raise LoopRejected("execution fanout publication " + result.disposition)
    runtime._finish_decision(command.idempotency_key)
    current, _, fanouts = read_execution_call_history(runtime)
    if evidence not in fanouts or proposal.sealed_run not in current.records:
        raise LoopRejected("selected execution fanout differs after publication")
    return proposal.sealed_run
