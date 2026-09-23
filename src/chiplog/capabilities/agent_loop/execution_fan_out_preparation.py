"""Owner preparation of executable capture fan-out; no publication authority.

Only the selected captured response is interpreted. The output retains its bytes,
initializes every ordered call, and seals the Run without terminalizing any call.
The authenticated writer must publish the entire output against the same cut.
"""

import base64

from .call_acceptance_contracts import (
    InitializedCallRecord,
    OriginalCallKey,
    PreparedCallFanOut,
    SealedResponseRecord,
)
from .call_acceptance_preparation import (
    _canonical_base64,
    _cut,
    _failure,
    _proposal_digest,
    _require,
    call_record_reference,
    call_subject_id,
)
from .contracts import ToolSpec
from .delivery_preparation import DeliveryCompletion
from .execution_contracts import (
    ConsequentialToolSpec,
    ExecutionContinue,
    ExecutionModelAttempt,
    ExecutionRunRecord,
)
from .execution_fan_out_contracts import (
    ExecutionCapturedFanOutProposal,
    ExecutionCapturedFanOutRequest,
    ExecutionCapturedFanOutResult,
)
from .execution_parsing import parse_execution_response
from .fan_out_contracts import FanOutToolPolicy
from .recovery_contracts import Absent, NonSchedulerFence, NotApplicable, Present


def _capture(request: ExecutionCapturedFanOutRequest) -> ExecutionModelAttempt:
    run, inner = request.captured_run, request.request
    _require(
        run.root_binding == "NOT_APPLICABLE",
        "scheduler capture requires the registered lease checker",
        "UNSUPPORTED",
    )
    _require(
        run.state == "ACTIVE" and run.event == "ModelResponseCaptured" and bool(run.turns),
        "Run is not an active response capture",
        "STALE",
    )
    _require(
        run.head == "loop:" + run.model_copy(update={"head": "pending"}).digest(),
        "captured Run body differs from self head",
    )
    turn = run.turns[-1]
    _require(
        turn.state == "RESPONSE_AVAILABLE"
        and turn.response_seal is None
        and turn.initialized_calls is None
        and bool(turn.attempts)
        and turn.selector == len(turn.attempts) - 1,
        "Turn is already sealed or lacks a selected captured attempt",
        "STALE",
    )
    attempt = turn.attempts[turn.selector]
    _require(
        attempt.state == "RESPONSE_CAPTURED" and attempt.generation == turn.selector,
        "selected attempt is not captured at the selected generation",
        "STALE",
    )
    capture = inner.captured_response
    _require(
        run.tenant == inner.cut.tenant_id
        and run.run_id == inner.original_run_id
        and turn.turn_id == inner.original_turn_id
        and capture.subject_id == run.run_id
        and capture.revision == Present(head=run.head, fingerprint=run.digest())
        and inner.cut.current_run == capture
        and inner.cut.run_state == run.state,
        "captured Run, original identity and current cut differ",
        "STALE",
    )
    manifest = attempt.manifest
    _require(
        manifest.tenant == run.tenant
        and manifest.principal == run.principal
        and manifest.run_id == run.run_id
        and manifest.turn_id == turn.turn_id
        and manifest.generation == attempt.generation
        and manifest.contour_head == run.contour_head,
        "selected attempt manifest belongs to another capture",
    )
    fence = inner.cut.fence
    assert isinstance(fence, NonSchedulerFence)  # _cut rejects unregistered lease paths.
    _require(
        run.worker_session
        == attempt.worker_session
        == manifest.worker_session
        == fence.worker_session_id,
        "captured worker differs from current execution fence",
        "STALE",
    )
    _require(bool(attempt.receipt), "capture lacks a transport receipt")
    _require(
        attempt.response_base64 == inner.canonical_response_base64,
        "supplied response differs from selected captured bytes",
    )
    _canonical_base64(inner.canonical_response_base64)
    return attempt


def _policies(request: ExecutionCapturedFanOutRequest) -> dict[str, FanOutToolPolicy]:
    registry = request.tool_registry
    tools = request.captured_run.turns[-1].attempts[-1].manifest.artifact.tools
    _require(
        request.tool_registry_head == call_record_reference(registry.registry_id, registry),
        "tool registry body differs from its reference",
    )
    keys = tuple(
        (entry.tool_name, entry.tool_version, entry.schema_id) for entry in registry.entries
    )
    expected = tuple(sorted((tool.name, tool.version, tool.schema_id) for tool in tools))
    _require(keys == tuple(sorted(set(keys))) == expected, "tool registry coverage differs")
    by_name: dict[str, ToolSpec | ConsequentialToolSpec] = {tool.name: tool for tool in tools}
    for entry in registry.entries:
        tool = by_name[entry.tool_name]
        _require(
            entry.tool_schema.subject_id == tool.schema_id
            and entry.tool_schema.revision
            == Present(head="record:" + tool.digest(), fingerprint=tool.digest()),
            "tool schema reference differs from selected ToolSpec",
        )
        classification = "CONSEQUENTIAL" if tool.name == "request_self_effect" else "PROPOSAL_ONLY"
        _require(
            entry.classification == classification
            and isinstance(entry.retry_policy, NotApplicable),
            "registered tool classification or retry policy differs",
            "UNSUPPORTED",
        )
    return {entry.tool_name: entry for entry in registry.entries}


def _seal_run(
    run: ExecutionRunRecord,
    fan_out: PreparedCallFanOut,
    response: ExecutionContinue | DeliveryCompletion,
) -> ExecutionRunRecord:
    turn = run.turns[-1]
    attempt = turn.attempts[-1]
    if isinstance(response, ExecutionContinue):
        attempt = attempt.model_copy(update={"state": "TERMINAL_ACCEPTED", "head": "pending"})
        attempt = attempt.model_copy(update={"head": "execution-attempt:" + attempt.digest()})
    # Complete remains only a captured proposal. CompleteAcceptance must still
    # CAS this exact RESPONSE_CAPTURED attempt together with delivery and SUCCEEDED.
    turn = turn.model_copy(
        update={
            "head": "pending",
            "state": "ACCEPTED"
            if isinstance(response, ExecutionContinue)
            else "RESPONSE_AVAILABLE",
            "attempts": (*turn.attempts[:-1], attempt),
            "response_seal": fan_out.complete_ordered_record_manifest[0],
            "initialized_calls": fan_out.response_seal.complete_ordered_initialized,
        }
    )
    turn = turn.model_copy(update={"head": "execution-turn:" + turn.digest()})
    values = run.model_dump()
    values.update(
        head="pending",
        predecessor=run.head,
        event="ModelResponseSealed"
        if isinstance(response, ExecutionContinue)
        else "ModelCompletionPrepared",
        turns=(*run.turns[:-1], turn),
    )
    result = ExecutionRunRecord.model_validate(values)
    return result.model_copy(update={"head": "loop:" + result.digest()})


def prepare_execution_captured_fan_out(
    request: ExecutionCapturedFanOutRequest,
) -> ExecutionCapturedFanOutResult:
    try:
        request = ExecutionCapturedFanOutRequest.model_validate_json(request.canonical_bytes())
        inner, run = request.request, request.captured_run
        occupied = set(_cut(inner.cut))
        attempt = _capture(request)
        raw = base64.b64decode(inner.canonical_response_base64, validate=True)
        _require(len(raw) <= run.policy.max_response_bytes, "capture exceeds response byte bound")
        response = parse_execution_response(raw, attempt.manifest.artifact)
        policies = _policies(request)
        if isinstance(response, DeliveryCompletion):
            _require(
                response.tenant == run.tenant
                and response.run_id == run.run_id
                and response.turn_id == inner.original_turn_id,
                "delivery completion belongs to another captured Run or Turn",
            )
        calls = response.tool_calls if isinstance(response, ExecutionContinue) else ()
        _require(len(inner.ordered_calls) == len(calls), "sealed calls omit or add captured calls")
        _require(
            len(calls) <= min(inner.bound.max_call_count, run.policy.max_tool_calls),
            "complete captured fan-out exceeds call bound",
            "DENIED",
        )
        records = []
        for ordinal, (parsed, sealed) in enumerate(zip(calls, inner.ordered_calls, strict=True)):
            policy = policies[parsed.tool]
            original = OriginalCallKey(
                tenant_id=run.tenant,
                original_run_id=run.run_id,
                original_turn_id=inner.original_turn_id,
                captured_response=inner.captured_response,
                ordinal=ordinal,
                model_call_label=parsed.call_id,
            )
            _require(
                sealed.original == original
                and sealed.canonical_call_base64
                == base64.b64encode(parsed.canonical_bytes()).decode()
                and sealed.tool_schema == policy.tool_schema
                and sealed.tool_policy == policy.tool_policy
                and sealed.classification == policy.classification
                and isinstance(sealed.retry_lineage, NotApplicable),
                "sealed call differs from exact captured call or registered policy",
            )
            identity = call_subject_id(original)
            _require(identity not in occupied, "original call already initialized", "CONFLICT")
            occupied.add(identity)
            records.append(
                InitializedCallRecord(original_call_id=identity, call=sealed, predecessor=Absent())
            )
        initialized = tuple(call_record_reference(row.original_call_id, row) for row in records)
        seal = SealedResponseRecord(
            response_seal_id="response-seal:" + inner.digest(),
            tenant_id=run.tenant,
            original_run_id=run.run_id,
            original_turn_id=inner.original_turn_id,
            captured_response=inner.captured_response,
            complete_ordered_initialized=initialized,
            bound=inner.bound,
        )
        manifest = (call_record_reference(seal.response_seal_id, seal), *initialized)
        manifest_bytes = b"[" + b",".join(ref.canonical_bytes() for ref in manifest) + b"]"
        _require(
            len(manifest_bytes) <= inner.bound.max_manifest_bytes,
            "complete fan-out manifest exceeds byte bound",
            "DENIED",
        )
        fan_out = PreparedCallFanOut(
            source_request_fingerprint=inner.digest(),
            response_seal=seal,
            initialized_records=tuple(records),
            complete_ordered_record_manifest=manifest,
            proposal_fingerprint="0" * 64,
        )
        fan_out = fan_out.model_copy(update={"proposal_fingerprint": _proposal_digest(fan_out)})
        proposal = ExecutionCapturedFanOutProposal(
            source_request_fingerprint=request.digest(),
            fan_out=fan_out,
            sealed_run=_seal_run(run, fan_out, response),
            proposal_fingerprint="0" * 64,
        )
        proposal = proposal.model_copy(update={"proposal_fingerprint": _proposal_digest(proposal)})
        _require(
            len(proposal.canonical_bytes()) <= inner.bound.max_serialized_batch_bytes,
            "complete owner fan-out output exceeds byte bound",
            "DENIED",
        )
        return proposal
    except (ValueError, TypeError, IndexError, KeyError) as error:
        return _failure(getattr(getattr(request, "request", None), "command_id", None), error)
