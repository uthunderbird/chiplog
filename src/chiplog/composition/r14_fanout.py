"""Canonical R14 fanout issuance and publication through the sole tenant writer."""

from __future__ import annotations

import base64
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Literal, cast

from pydantic import TypeAdapter

from chiplog.capabilities.agent_loop import domain
from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallAuthorityObservation,
    CallPreparationCut,
    CallSubjectHead,
    FanOutPreparationRequest,
    OriginalCallKey,
    SealedCallInput,
)
from chiplog.capabilities.agent_loop.contracts import (
    Continue,
    LoopRejected,
    LoopSnapshot,
    RunRecord,
)
from chiplog.capabilities.agent_loop.fan_out_contracts import (
    CapturedFanOutProposal,
    CapturedFanOutRequest,
    CapturedFanOutResult,
    FanOutToolPolicy,
    FanOutToolRegistry,
)
from chiplog.capabilities.agent_loop.recovery_contracts import (
    NonSchedulerFence,
    NotApplicable,
    Present,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import FanOutBound
from chiplog.capabilities.agent_loop.response_parsing import parse_captured_response
from chiplog.composition.r14_fanout_contracts import FANOUT_OPERATION, RetainedFanOutPreparation
from chiplog.composition.r14_fanout_records import (
    build_envelope,
    physical_command,
    reference,
)
from chiplog.composition.r14_loop_history import read_call_history, read_call_inventory
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess

if TYPE_CHECKING:
    from chiplog.composition.r14_runtime import R14PlanningRuntime

# Explicit offline registry limits, independent of transport frame and Run response bounds.
_HERMETIC_BOUND = FanOutBound(
    max_call_count=256,
    max_manifest_bytes=64 * 1024,
    max_serialized_batch_bytes=16 * 1024 * 1024,
    canonicalization_version="chiplog.recovery.frontier.v1",
)


def _registry(run: RunRecord) -> tuple[FanOutToolRegistry, FanOutBound]:
    attempt = domain.current_attempt(run, "RESPONSE_CAPTURED")
    bound = _HERMETIC_BOUND.model_copy(
        update={"max_call_count": min(_HERMETIC_BOUND.max_call_count, run.policy.max_tool_calls)}
    )
    registry = FanOutToolRegistry(
        registry_id="hermetic-proposal-fanout-v1",
        version="1",
        entries=tuple(
            FanOutToolPolicy(
                tool_name=tool.name,
                tool_version=tool.version,
                schema_id=tool.schema_id,
                tool_schema=reference(tool.schema_id, tool),
                tool_policy=reference("hermetic-proposal-policy:" + tool.name, bound),
                classification="PROPOSAL_ONLY",
                retry_policy=NotApplicable(),
            )
            for tool in sorted(
                attempt.manifest.artifact.tools,
                key=lambda tool: (tool.name, tool.version, tool.schema_id),
            )
        ),
    )
    return registry, bound


def _selected(runtime: R14PlanningRuntime, identity: str) -> dict[str, object] | None:
    runtime._pending()  # Authenticate the full marker sequence before looking up history.
    entries = [json.loads(raw) for _, _, raw in runtime._loop_decisions().entries()]
    found = [e for e in entries if e.get("kind") == "DECIDED" and e.get("operation_id") == identity]
    if len(found) > 1:
        raise LoopRejected("ambiguous fanout selection")
    if not found:
        return None
    entry: dict[str, object] = found[0]
    if entry.get("operation_kind") != FANOUT_OPERATION:
        raise LoopRejected("Run head already belongs to a different publication envelope")
    return entry


def _retained(entry: dict[str, object]) -> RetainedFanOutPreparation:
    raw = entry.get("fanout_preparation")
    if not isinstance(raw, str):
        raise LoopRejected("selected fanout lacks retained preparation")
    evidence = RetainedFanOutPreparation.model_validate_json(raw)
    if evidence.canonical_bytes().decode() != raw:
        raise LoopRejected("noncanonical selected fanout preparation")
    return evidence


async def _finish_selected(runtime: R14PlanningRuntime, entry: dict[str, object]) -> None:
    evidence = _retained(entry)
    envelope = build_envelope(evidence)
    if entry.get("fanout_envelope") != envelope.canonical_bytes().decode() or runtime._publication(
        entry
    ) != physical_command(envelope):
        raise LoopRejected("selected fanout envelope differs from exact physical command")
    identity = envelope.idempotency_key
    await runtime._recover_exact(
        physical_command(envelope),
        str(entry["predecessor"]),
        str(entry["resulting"]),
        is_materialized=lambda: runtime._loop_decision_materialized(identity, entry),
    )
    runtime._finish_decision(identity, expected=entry)


async def publish_fanout(
    runtime: R14PlanningRuntime,
    record: RunRecord,
    expected: LoopSnapshot,
    validate: Callable[[LoopSnapshot], None] | None,
) -> None:
    # Historical equality precedes fresh absence/session/policy checks. No await under gate.
    with runtime._authority_gate().hold():
        prior = _selected(runtime, record.head)
        if prior is not None:
            retained = _retained(prior)
            if (
                retained.accepted_run != record
                or retained.expected_snapshot_fingerprint != expected.digest()
            ):
                raise LoopRejected("replay differs from selected Run or predecessor snapshot")
    if prior is not None:
        await _finish_selected(runtime, prior)
        return

    with runtime._authority_gate().hold():
        snapshot, preparations, _ = read_call_history(runtime)
        if snapshot != expected or record.tenant != runtime._tenant_id:
            raise LoopRejected("stale or foreign fanout predecessor")
        runs = [row for row in snapshot.records if row.run_id == record.run_id]
        if not runs:
            raise LoopRejected("fanout has no captured Run predecessor")
        captured = runs[-1]
        if captured.event != "ModelResponseCaptured" or captured.root_binding != "NOT_APPLICABLE":
            raise LoopRejected("fanout requires a supported active captured response")
        if any(item.request.captured_run.head == captured.head for item in preparations):
            raise LoopRejected("captured response already sealed")
        if captured.worker_session != runtime.current_worker():
            raise LoopRejected("stale captured worker")
        domain.validate_record(captured, record)
        engine = runtime._supervisor.runtime()
        callee = engine.session("agent_loop")
        caller = BrokerSession(
            tenant_id=runtime._tenant_id,
            broker_epoch=callee.broker_epoch,
            generation_id=callee.generation_id,
            owner_id="broker",
            session_id=f"broker:{callee.generation_id}",
        )
        observed = time.monotonic_ns()
        deadline = observed + 5_000_000_000
        registry, bound = _registry(captured)
        registry_ref = reference(registry.registry_id, registry)
        inventory = read_call_inventory(runtime)
        capture = CallSubjectHead(
            subject_id=captured.run_id,
            revision=Present(head=captured.head, fingerprint=captured.digest()),
        )
        predecessor = runtime._commitment_journal.load(runtime._tenant_id)
        if predecessor is None:
            raise LoopRejected("missing independent fanout predecessor")
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
                    observed_at_ns=observed,
                    valid_until_ns=deadline,
                )
                for identity, family, value in (
                    (registry.registry_id, "TOOL_SCHEMA", registry),
                    ("hermetic-fanout-bound-v1", "POLICY", bound),
                )
            ),
            fence=NonSchedulerFence(
                lineage=NotApplicable(),
                physical_root=NotApplicable(),
                lease=NotApplicable(),
                clock_proof=NotApplicable(),
                run_id=captured.run_id,
                run_head=captured.head,
                worker_session_id=runtime.current_worker(),
                runtime_generation=callee.generation_id,
            ),
        )
        attempt = domain.current_attempt(captured, "RESPONSE_CAPTURED")
        if attempt.response_base64 is None:
            raise LoopRejected("selected capture lacks response bytes")
        response = parse_captured_response(
            base64.b64decode(attempt.response_base64, validate=True), attempt.manifest.artifact
        )
        policies = {item.tool_name: item for item in registry.entries}
        calls = response.tool_calls if isinstance(response, Continue) else ()
        request = CapturedFanOutRequest(
            request=FanOutPreparationRequest(
                command_id=record.head,
                original_run_id=captured.run_id,
                original_turn_id=captured.turns[-1].turn_id,
                captured_response=capture,
                canonical_response_base64=attempt.response_base64,
                ordered_calls=tuple(
                    SealedCallInput(
                        original=OriginalCallKey(
                            tenant_id=captured.tenant,
                            original_run_id=captured.run_id,
                            original_turn_id=captured.turns[-1].turn_id,
                            captured_response=capture,
                            ordinal=index,
                            model_call_label=call.call_id,
                        ),
                        classification="PROPOSAL_ONLY",
                        tool_schema=policies[call.tool].tool_schema,
                        tool_policy=policies[call.tool].tool_policy,
                        canonical_call_base64=base64.b64encode(call.canonical_bytes()).decode(),
                        retry_lineage=NotApplicable(),
                    )
                    for index, call in enumerate(calls)
                ),
                bound=bound,
                cut=cut,
            ),
            captured_run=captured,
            tool_registry=registry,
            tool_registry_head=registry_ref,
        )
        companions = runtime.companions(record)

    # Both owner operations occur outside writer/authority locks.
    runtime.validate(captured, record)
    sent = PublicPortCall(
        operation_id="agent_loop.prepare_captured_fan_out",
        request_id="fanout:" + secrets.token_hex(16),
        caller=caller,
        callee=callee,
        schema_id="chiplog.call.captured-fanout-preparation.v1",
        canonical_payload=request.canonical_bytes(),
        budget=CallBudget(
            remaining_calls=1, remaining_depth=1, absolute_deadline_ns=deadline, policy_version=1
        ),
    )
    reply = await engine.call(sent)
    if not isinstance(reply, PublicPortSuccess) or (
        reply.request_id != sent.request_id
        or reply.responder != callee
        or reply.schema_id != "chiplog.call.captured-fanout-result.v1"
    ):
        raise LoopRejected("fanout owner exchange rejected or changed session")
    adapter: TypeAdapter[CapturedFanOutResult] = TypeAdapter(CapturedFanOutResult)
    proposal = adapter.validate_json(reply.canonical_payload)
    if (
        not isinstance(proposal, CapturedFanOutProposal)
        or proposal.canonical_bytes() != reply.canonical_payload
    ):
        raise LoopRejected("fanout owner rejected preparation or returned noncanonical bytes")
    evidence = RetainedFanOutPreparation(
        request=request,
        proposal=proposal,
        accepted_run=record,
        companions=companions,
        expected_snapshot_fingerprint=expected.digest(),
        caller=caller,
        callee=callee,
        request_id=sent.request_id,
        deadline_ns=deadline,
    )
    envelope = build_envelope(evidence)
    command = physical_command(envelope)

    def guard() -> Literal["STALE"] | None:
        with runtime._authority_gate().hold():
            current = runtime._loop_snapshot()
            if (
                current != expected
                or runtime.current_worker() != captured.worker_session
                or runtime._supervisor.runtime().session("agent_loop") != callee
                or time.monotonic_ns() >= deadline
                or _registry(captured) != (registry, bound)
                or runtime._commitment_journal.load(runtime._tenant_id) != predecessor
                or runtime.companions(record) != companions
                or build_envelope(evidence) != envelope
            ):
                return "STALE"
            if validate is not None:
                validate(current)
            return None

    def decide(resulting: str) -> None:
        with runtime._authority_gate().hold():
            runtime._require_no_pending()
            runtime._append_decision(
                {
                    "version": 1,
                    "kind": "DECIDED",
                    "operation_id": record.head,
                    "operation_kind": FANOUT_OPERATION,
                    "expected_head": command.expected_head,
                    "fingerprint": command.request_fingerprint,
                    "predecessor": predecessor,
                    "resulting": resulting,
                    "records": [
                        {
                            "record_id": item.record_id,
                            "owner": item.owner,
                            "schema": item.schema_id,
                            "payload": base64.b64encode(item.canonical_bytes).decode(),
                            "digest": item.fingerprint,
                        }
                        for item in command.records
                    ],
                    "fanout_preparation": evidence.canonical_bytes().decode(),
                    "fanout_envelope": envelope.canonical_bytes().decode(),
                }
            )

    result = await runtime._appender.submit(
        replace(command, admission_guard=guard, decision_guard=decide)
    )
    if result.disposition not in ("COMMITTED", "REPLAY"):
        raise LoopRejected("fanout publication " + result.disposition)
    with runtime._authority_gate().hold():
        entry = _selected(runtime, record.head)
        if entry is None or (
            entry.get("fanout_preparation") != evidence.canonical_bytes().decode()
            or entry.get("fanout_envelope") != envelope.canonical_bytes().decode()
        ):
            raise LoopRejected("writer replay differs from exact prepared fanout selection")
    await _finish_selected(runtime, entry)
