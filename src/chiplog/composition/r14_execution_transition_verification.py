"""Check owner transition bytes without invoking the transition producer.

Consistency is necessary, not authentication: selected history must bind request.run
to the exact prior record and authenticate creation/visibility/transport sources.
"""

import base64
import hashlib
import json

from pydantic import TypeAdapter

from chiplog.capabilities.agent_loop.contracts import DisclosureLabel, Frozen
from chiplog.capabilities.agent_loop.domain import join_labels
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_parsing import (
    EXECUTION_TOOLS,
    execution_response_schema,
)
from chiplog.capabilities.agent_loop.execution_transition_contracts import (
    AccumulateExecutionVisibility,
    ActivateExecutionRun,
    CaptureExecutionResponse,
    CreateExecutionRun,
    EmitExecutionAttempt,
    ExecutionTransitionProposal,
    ExecutionTransitionRequest,
    PrepareExecutionRequest,
    StartInitialExecutionTurn,
)


def _require(value: bool, reason: str) -> None:
    if not value:
        raise ValueError(reason)


def _head(value: Frozen, prefix: str) -> None:
    _require(
        getattr(value, "head", None)
        == prefix + value.model_copy(update={"head": "pending"}).digest(),
        "execution record self head differs",
    )


def _same(left: Frozen, right: Frozen, changed: set[str]) -> None:
    _require(
        left.model_dump(exclude=changed) == right.model_dump(exclude=changed),
        "owner changed fields outside selected transition",
    )


def _empty(run: ExecutionRunRecord) -> None:
    _require(
        not run.turns
        and run.delivery_acceptance is None
        and run.suspension_baseline is None
        and not run.original_obligations
        and not run.no_retry_references
        and run.root_binding == "NOT_APPLICABLE",
        "initialization cannot consume continuation or recovery state",
    )


def _prepared(request: PrepareExecutionRequest, result: ExecutionRunRecord) -> None:
    run, manifest = request.run, request.manifest
    before, after = run.turns[-1], result.turns[-1]
    _require(
        before.state == "PREPARING"
        and not before.attempts
        and before.selector == 0
        and bool(before.accumulator)
        and run.policy.live_model is None,
        "not a first hermetic preparation",
    )
    _require(
        manifest.members == before.accumulator
        and len({m.record_id for m in manifest.members}) == len(manifest.members)
        and (
            manifest.tenant,
            manifest.principal,
            manifest.run_id,
            manifest.turn_id,
            manifest.contour_head,
            manifest.worker_session,
            manifest.generation,
        )
        == (
            run.tenant,
            run.principal,
            run.run_id,
            before.turn_id,
            run.contour_head,
            run.worker_session,
            0,
        ),
        "prepared manifest differs from original context",
    )
    artifact = manifest.artifact
    _require(
        artifact.tools == EXECUTION_TOOLS
        and artifact.response_schema_json == execution_response_schema(),
        "unregistered prepared generator",
    )
    _require(
        manifest.joined_label == join_labels(tuple(m.label for m in manifest.members)),
        "prepared disclosure join differs",
    )
    expected_context = join_labels(
        (
            DisclosureLabel(
                value="ENDPOINT_RESTRICTED",
                allowed_endpoints=(run.origin.recipient.endpoint.identity,),
            ),
            *(a.manifest.joined_label for t in run.turns[:-1] for a in t.attempts),
        )
    )
    prompt = [m for m in manifest.members if m.surface == "prompt"]
    schema = [m for m in manifest.members if m.surface == "schema"]
    context = [m for m in manifest.members if m.surface == "context"]
    _require(len(prompt) == len(schema) == len(context) == 1, "incomplete prepared visibility")
    _require(
        prompt[0].content == artifact.rendered
        and prompt[0].revision_head == artifact.content_hash
        and schema[0].content == artifact.response_schema_json
        and schema[0].revision_head == artifact.digest()
        and context[0].provenance_head == run.predecessor
        and context[0].content in artifact.rendered
        and context[0].label == prompt[0].label == expected_context,
        "prepared visibility differs",
    )
    transport = json.dumps(
        {
            "prompt": artifact.rendered,
            "schema": artifact.response_schema_json,
            "manifest": manifest.digest(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    _require(
        len(transport.encode()) <= run.policy.max_request_bytes
        and len(after.attempts) == 1
        and after.state == "CALL_ACTIVE",
        "prepared request bound or attempt count differs",
    )
    _same(before, after, {"head", "state", "attempts"})
    attempt = after.attempts[0]
    _head(attempt, "execution-attempt:")
    lineage = before.turn_id + "/slot/0"
    _require(
        attempt.manifest == manifest
        and attempt.request == transport
        and attempt.attempt_id == lineage + "/generation/0"
        and attempt.lineage_id == lineage
        and attempt.generation == 0
        and attempt.state == "PREPARED_NOT_EMITTED"
        and attempt.worker_session == run.worker_session
        and attempt.live_model is None
        and attempt.provider_contract == "hermetic-model.v1"
        and attempt.recipient == "hermetic-model"
        and attempt.response_base64 is None
        and attempt.receipt is None
        and attempt.rejection is None,
        "prepared attempt differs from original command",
    )


def verify_execution_transition(
    request: ExecutionTransitionRequest, proposal: ExecutionTransitionProposal
) -> None:
    adapter: TypeAdapter[ExecutionTransitionRequest] = TypeAdapter(ExecutionTransitionRequest)
    request = adapter.validate_json(request.canonical_bytes())
    proposal = ExecutionTransitionProposal.model_validate_json(proposal.canonical_bytes())
    wire = json.loads(proposal.canonical_bytes())
    del wire["proposal_fingerprint"]
    _require(
        proposal.source_request_fingerprint == request.digest()
        and proposal.proposal_fingerprint
        == hashlib.sha256(
            json.dumps(wire, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest(),
        "owner proposal fingerprint differs",
    )
    result = proposal.run
    _head(result, "loop:")
    if isinstance(request, CreateExecutionRun):
        _empty(result)
        _require(
            result.state == "CREATED"
            and result.event == "RunCreated"
            and result.predecessor is None,
            "invalid initial execution state",
        )
        _require(
            result.model_dump(
                exclude={
                    "schema_id",
                    "head",
                    "predecessor",
                    "state",
                    "event",
                    "turns",
                    "delivery_acceptance",
                    "suspension_baseline",
                    "original_obligations",
                    "no_retry_references",
                    "root_binding",
                }
            )
            == request.model_dump(exclude={"kind", "command_id"}),
            "creation source fields differ",
        )
        return
    previous = request.run
    _head(previous, "loop:")
    _require(result.predecessor == previous.head, "execution predecessor differs")
    if isinstance(request, ActivateExecutionRun):
        _empty(previous)
        _require(
            previous.state == "CREATED"
            and previous.event == "RunCreated"
            and previous.predecessor is None
            and result.state == "ACTIVE"
            and result.event == "RunActivated",
            "invalid activation",
        )
        _same(previous, result, {"head", "predecessor", "state", "event"})
        return
    _same(previous, result, {"head", "predecessor", "event", "turns"})
    _require(previous.state == "ACTIVE", "execution transition needs active Run")
    if isinstance(request, StartInitialExecutionTurn):
        _empty(previous)
        _require(
            previous.event == "RunActivated"
            and result.event == "TurnStarted"
            and len(result.turns) == 1,
            "initial Turn cannot replace continuation",
        )
        turn = result.turns[0]
        _head(turn, "execution-turn:")
        _require(
            turn.turn_id == previous.run_id + "/turn/1"
            and turn.ordinal == 1
            and turn.state == "PREPARING"
            and not turn.accumulator
            and not turn.attempts
            and turn.selector == 0
            and turn.response_seal is None
            and turn.initialized_calls is None,
            "invalid first Turn",
        )
        return
    _require(
        bool(previous.turns)
        and len(previous.turns) == len(result.turns)
        and previous.turns[:-1] == result.turns[:-1],
        "historical Turn mutation",
    )
    before, after = previous.turns[-1], result.turns[-1]
    _head(after, "execution-turn:")
    _require(
        before.response_seal is None and before.initialized_calls is None,
        "transition cannot rewrite sealed Turn",
    )
    if isinstance(request, AccumulateExecutionVisibility):
        _same(before, after, {"head", "accumulator"})
        _require(
            before.state == "PREPARING"
            and not before.attempts
            and result.event == "TurnVisibilityAccumulated",
            "invalid accumulation",
        )
        members = {m.record_id: m for m in before.accumulator}
        _require(len(members) == len(before.accumulator), "duplicate original visibility")
        for member in request.members:
            _require(
                member.record_id not in members or members[member.record_id] == member,
                "visibility identity rebound",
            )
            members[member.record_id] = member
        _require(after.accumulator == tuple(members.values()), "accumulated closure differs")
    elif isinstance(request, PrepareExecutionRequest):
        _require(result.event == "ModelAttemptPrepared", "wrong preparation event")
        _prepared(request, result)
    else:
        _require(
            isinstance(request, EmitExecutionAttempt | CaptureExecutionResponse),
            "unregistered transition",
        )
        assert isinstance(request, EmitExecutionAttempt | CaptureExecutionResponse)
        _same(before, after, {"head", "state", "attempts"})
        _require(
            before.state == "CALL_ACTIVE"
            and bool(before.attempts)
            and before.selector == len(before.attempts) - 1
            and len(after.attempts) == len(before.attempts)
            and after.attempts[:-1] == before.attempts[:-1],
            "attempt selection changed",
        )
        old, new = before.attempts[-1], after.attempts[-1]
        _head(new, "execution-attempt:")
        _require(
            old.generation == before.selector
            and old.response_base64 is None
            and old.receipt is None
            and old.rejection is None,
            "attempt already has outcome",
        )
        manifest = old.manifest
        _require(
            (
                manifest.tenant,
                manifest.principal,
                manifest.run_id,
                manifest.turn_id,
                manifest.generation,
                manifest.contour_head,
                manifest.worker_session,
                old.worker_session,
            )
            == (
                previous.tenant,
                previous.principal,
                previous.run_id,
                before.turn_id,
                old.generation,
                previous.contour_head,
                previous.worker_session,
                previous.worker_session,
            ),
            "attempt source identity differs",
        )
        if isinstance(request, EmitExecutionAttempt):
            _same(old, new, {"head", "state"})
            _require(
                old.state == "PREPARED_NOT_EMITTED"
                and new.state == "EMITTED_OUTCOME_UNKNOWN"
                and after.state == "CALL_ACTIVE"
                and result.event == "ModelAttemptEmitted",
                "invalid emission",
            )
        else:
            _same(old, new, {"head", "state", "response_base64", "receipt"})
            _require(
                old.state == "EMITTED_OUTCOME_UNKNOWN"
                and new.state == "RESPONSE_CAPTURED"
                and after.state == "RESPONSE_AVAILABLE"
                and result.event == "ModelResponseCaptured"
                and len(request.raw) <= previous.policy.max_response_bytes
                and bool(request.receipt)
                and new.response_base64 == base64.b64encode(request.raw).decode()
                and new.receipt == request.receipt,
                "invalid capture",
            )
