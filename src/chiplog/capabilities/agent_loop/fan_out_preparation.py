"""Pure fan-out proposals from captured bytes; no history or publication authority."""

import base64

from . import domain
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
from .contracts import Complete, Continue, ModelAttempt, ToolSpec
from .delivery_preparation import DeliveryCompletion
from .fan_out_contracts import (
    CapturedFanOutProposal,
    CapturedFanOutRequest,
    CapturedFanOutResult,
    FanOutToolPolicy,
)
from .recovery_contracts import Absent, NonSchedulerFence, NotApplicable, Present
from .response_parsing import parse_captured_response


def _captured(request: CapturedFanOutRequest) -> ModelAttempt:
    run, inner = request.captured_run, request.request
    _require(
        run.root_binding == "NOT_APPLICABLE",
        "scheduler-rooted fan-out requires the registered lease checker",
        "UNSUPPORTED",
    )
    _require(
        run.state == "ACTIVE" and run.event == "ModelResponseCaptured",
        "Run is not an active response capture",
        "STALE",
    )
    _require(
        run.head == "loop:" + run.model_copy(update={"head": "pending"}).digest(),
        "captured Run body differs from self head",
    )
    turn = domain.current_turn(run)
    attempt = domain.current_attempt(run, "RESPONSE_CAPTURED")
    _require(turn.state == "RESPONSE_AVAILABLE", "Turn is not response available", "STALE")
    capture = inner.captured_response
    _require(
        run.tenant == inner.cut.tenant_id
        and run.run_id == inner.original_run_id
        and turn.turn_id == inner.original_turn_id
        and capture.subject_id == run.run_id
        and capture.revision == Present(head=run.head, fingerprint=run.digest())
        and inner.cut.current_run == capture,
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
    # _cut already rejected scheduler fences before this check.
    assert isinstance(fence, NonSchedulerFence)
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


def _policies(
    request: CapturedFanOutRequest, tools: tuple[ToolSpec, ...]
) -> dict[str, FanOutToolPolicy]:
    registry = request.tool_registry
    _require(
        request.tool_registry_head == call_record_reference(registry.registry_id, registry),
        "tool registry body differs from its reference",
    )
    keys = tuple((e.tool_name, e.tool_version, e.schema_id) for e in registry.entries)
    expected = tuple(sorted((tool.name, tool.version, tool.schema_id) for tool in tools))
    _require(keys == tuple(sorted(set(keys))) == expected, "tool registry coverage differs")
    by_name: dict[str, ToolSpec] = {tool.name: tool for tool in tools}
    _require(len(by_name) == len(tools), "duplicate captured ToolSpec name")
    for entry in registry.entries:
        tool = by_name[entry.tool_name]
        _require(
            entry.tool_schema.subject_id == tool.schema_id
            and entry.tool_schema.revision
            == Present(head="record:" + tool.digest(), fingerprint=tool.digest()),
            "tool schema reference differs from selected ToolSpec",
        )
        # The selected registered parser currently admits only these proposal tools.
        # A supplied registry may not reinterpret them as executable operations.
        _require(
            tool.name in ("propose_planning", "propose_intent")
            and entry.classification == "PROPOSAL_ONLY"
            and isinstance(entry.retry_policy, NotApplicable),
            "unregistered executable tool classification or retry policy",
            "UNSUPPORTED",
        )
    return {entry.tool_name: entry for entry in registry.entries}


def _records(
    request: CapturedFanOutRequest,
    response: Continue | Complete | DeliveryCompletion,
    policies: dict[str, FanOutToolPolicy],
    occupied: set[str],
) -> tuple[InitializedCallRecord, ...]:
    inner, run = request.request, request.captured_run
    calls = response.tool_calls if isinstance(response, Continue) else ()
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
            and sealed.canonical_call_base64 == base64.b64encode(parsed.canonical_bytes()).decode()
            and sealed.tool_schema == policy.tool_schema
            and sealed.tool_policy == policy.tool_policy
            and sealed.classification == policy.classification
            and isinstance(sealed.retry_lineage, NotApplicable),
            "sealed call differs from exact captured call or registered policy",
        )
        identity = call_subject_id(original)
        _require(identity not in occupied, "original call is already initialized", "CONFLICT")
        occupied.add(identity)
        records.append(
            InitializedCallRecord(original_call_id=identity, call=sealed, predecessor=Absent())
        )
    return tuple(records)


def prepare_captured_fan_out(request: CapturedFanOutRequest) -> CapturedFanOutResult:
    try:
        request = CapturedFanOutRequest.model_validate_json(request.canonical_bytes())
        inner, run = request.request, request.captured_run
        inventory = _cut(inner.cut)
        attempt = _captured(request)
        raw = base64.b64decode(inner.canonical_response_base64, validate=True)
        _require(len(raw) <= run.policy.max_response_bytes, "capture exceeds response byte bound")
        response = parse_captured_response(raw, attempt.manifest.artifact)
        if isinstance(response, DeliveryCompletion):
            _require(
                response.tenant == run.tenant
                and response.run_id == run.run_id
                and response.turn_id == inner.original_turn_id,
                "delivery completion belongs to another captured Run or Turn",
            )
        policies = _policies(request, attempt.manifest.artifact.tools)
        records = _records(request, response, policies, set(inventory))
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
            initialized_records=records,
            complete_ordered_record_manifest=manifest,
            proposal_fingerprint="0" * 64,
        )
        fan_out = fan_out.model_copy(update={"proposal_fingerprint": _proposal_digest(fan_out)})
        proposal = CapturedFanOutProposal(
            source_request_fingerprint=request.digest(),
            fan_out=fan_out,
            proposal_fingerprint="0" * 64,
        )
        return proposal.model_copy(update={"proposal_fingerprint": _proposal_digest(proposal)})
    except (ValueError, TypeError, IndexError, KeyError) as error:
        return _failure(getattr(getattr(request, "request", None), "command_id", None), error)
