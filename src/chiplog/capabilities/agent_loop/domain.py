"""Pure owner decisions for durable Run, Turn, attempt and completion transitions."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .delivery_preparation import DeliveryObservation

from .contracts import (
    AcceptedDelivery,
    AttemptState,
    Complete,
    Continue,
    DisclosureLabel,
    LocalPlanningReceipt,
    LoopRejected,
    ModelAttempt,
    NoExposureProof,
    RunRecord,
    RunState,
    ToolOutcome,
    Turn,
    VisibilityManifest,
    VisibilityMember,
)
from .response_parsing import parse_captured_response


def successor(run: RunRecord, event: str, **changes: object) -> RunRecord:
    values = run.model_dump()
    values.update(changes)
    values.update(predecessor=run.head, event=event, head="pending")
    candidate = RunRecord.model_validate(values)
    head = "loop:" + candidate.digest()
    return candidate.model_copy(update={"head": head})


def current_turn(run: RunRecord) -> Turn:
    if run.state != "ACTIVE" or not run.turns:
        raise LoopRejected("no active Turn")
    return run.turns[-1]


def current_attempt(run: RunRecord, state: AttemptState) -> ModelAttempt:
    turn = current_turn(run)
    if not turn.attempts or turn.selector != len(turn.attempts) - 1:
        raise LoopRejected("missing or nonmonotone selected generation")
    attempt = turn.attempts[turn.selector]
    if attempt.generation != turn.selector or attempt.state != state:
        raise LoopRejected("attempt transition out of order")
    return attempt


def with_turn(run: RunRecord, turn: Turn, event: str) -> RunRecord:
    turn = turn.model_copy(update={"head": event + ":" + turn.digest()})
    return successor(run, event, turns=(*run.turns[:-1], turn))


def with_attempt(run: RunRecord, attempt: ModelAttempt, event: str) -> RunRecord:
    turn = current_turn(run)
    attempt = attempt.model_copy(update={"head": event + ":" + attempt.digest()})
    turn = turn.model_copy(update={"attempts": (*turn.attempts[:-1], attempt)})
    return with_turn(run, turn, event)


def continuation(run: RunRecord) -> None:
    """Enumerate every earlier sealed response; no caller-supplied ancestry or Boolean."""
    for turn in run.turns:
        if turn.state == "REJECTED" and turn.attempts:
            rejected = turn.attempts[turn.selector]
            if (
                rejected.state != "TERMINAL_REJECTED"
                or rejected.response_base64 is None
                or not rejected.rejection
                or turn.sealed_calls != ()
            ):
                raise LoopRejected("incomplete immutable rejection disposition")
            continue  # Rejected model responses contain no accepted tool calls.
        if turn.sealed_calls is None:
            raise LoopRejected("prior response is unsealed")
        if turn.state != "ACCEPTED" or not turn.attempts:
            raise LoopRejected("prior response is not accepted")
        attempt = turn.attempts[turn.selector]
        if attempt.state != "TERMINAL_ACCEPTED" or attempt.response_base64 is None:
            raise LoopRejected("missing immutable accepted response")
        response = Continue.model_validate_json(
            base64.b64decode(attempt.response_base64, validate=True)
        )
        if tuple(outcome.call for outcome in turn.sealed_calls) != response.tool_calls:
            raise LoopRejected("partial, extra or reordered sealed calls")
        if len({call.call_id for call in response.tool_calls}) != len(response.tool_calls):
            raise LoopRejected("duplicate call identity")
        for outcome in turn.sealed_calls:
            if outcome.state != "TERMINAL" or outcome.result is None:
                raise LoopRejected("call not continuation-ready; recovery HOLD")


def transition(run: RunRecord, target: RunState) -> RunRecord:
    edges: dict[RunState, set[RunState]] = {
        "CREATED": {"ACTIVE", "ABORTED", "CANCELLED"},
        "ACTIVE": {"SUSPENDED", "ABORTED", "CANCELLED"},
        "SUSPENDED": {"ABORTED", "CANCELLED"},
    }
    if target not in edges.get(run.state, set()):
        raise LoopRejected("illegal Run transition; recovery/success requires owned acceptance")
    if run.turns:
        continuation(run)
    return successor(run, "Run" + target.title(), state=target)


def start_turn(run: RunRecord) -> RunRecord:
    if run.state != "ACTIVE":
        raise LoopRejected("Run is not ACTIVE")
    continuation(run)
    ordinal = len(run.turns) + 1
    if run.policy.kind == "MAX_TURNS" and ordinal > run.policy.max_turns:
        return successor(run, "RunBudgetSuspended", state="SUSPENDED")
    turn = Turn(
        turn_id=f"{run.run_id}/turn/{ordinal}",
        ordinal=ordinal,
        head=f"{run.head}/turn/{ordinal}",
        state="PREPARING",
    )
    return successor(run, "TurnStarted", turns=(*run.turns, turn))


def accumulate(run: RunRecord, members: tuple[VisibilityMember, ...]) -> RunRecord:
    turn = current_turn(run)
    if turn.attempts or turn.state != "PREPARING":
        raise LoopRejected("cannot rewrite a sealed historical accumulator")
    previous = {member.record_id: member for member in turn.accumulator}
    result = list(turn.accumulator)
    for member in members:
        if member.record_id in previous:
            if previous[member.record_id] != member:
                raise LoopRejected("visibility identity rebound")
            continue
        previous[member.record_id] = member
        result.append(member)
    return with_turn(
        run, turn.model_copy(update={"accumulator": tuple(result)}), "TurnVisibilityAccumulated"
    )


def join_labels(labels: tuple[DisclosureLabel, ...]) -> DisclosureLabel:
    if not labels:
        raise LoopRejected("missing disclosure label is not unrestricted")
    endpoints: set[str] | None = None
    deny = False
    for label in labels:
        allowed = label.allowed_endpoints
        if allowed != tuple(sorted(set(allowed))) or any(not item for item in allowed):
            raise LoopRejected("noncanonical endpoint restrictions")
        if (label.value == "ENDPOINT_RESTRICTED") != bool(allowed):
            raise LoopRejected("invalid disclosure lattice member")
        deny |= label.value == "DENY_ALL"
        if label.value == "ENDPOINT_RESTRICTED":
            endpoints = set(allowed) if endpoints is None else endpoints & set(allowed)
    if deny or endpoints == set():
        return DisclosureLabel(value="DENY_ALL", allowed_endpoints=())
    if endpoints is None:
        return DisclosureLabel(value="UNRESTRICTED", allowed_endpoints=())
    return DisclosureLabel(value="ENDPOINT_RESTRICTED", allowed_endpoints=tuple(sorted(endpoints)))


def context_label(run: RunRecord) -> DisclosureLabel:
    return join_labels(
        (
            DisclosureLabel(
                value="ENDPOINT_RESTRICTED", allowed_endpoints=(run.origin.endpoint_id,)
            ),
            *(
                attempt.manifest.joined_label
                for turn in run.turns[:-1]
                for attempt in turn.attempts
            ),
        )
    )


def prepare(run: RunRecord, manifest: VisibilityManifest) -> RunRecord:
    turn = current_turn(run)
    if turn.state != "PREPARING" or turn.attempts or not turn.accumulator:
        raise LoopRejected("duplicate attempt lineage or missing accumulator")
    if (
        manifest.members != turn.accumulator
        or manifest.tenant != run.tenant
        or manifest.principal != run.principal
        or manifest.contour_head != run.contour_head
        or manifest.run_id != run.run_id
        or manifest.turn_id != turn.turn_id
        or manifest.generation != 0
    ):
        raise LoopRejected("manifest is not exact current accumulated visibility")
    joined = join_labels(tuple(member.label for member in turn.accumulator))
    if manifest.joined_label != joined:
        raise LoopRejected("missing or narrower manifest label")
    prompt_members = [member for member in manifest.members if member.surface == "prompt"]
    schema_members = [member for member in manifest.members if member.surface == "schema"]
    context_members = [member for member in manifest.members if member.surface == "context"]
    if (
        len(prompt_members) != 1
        or len(schema_members) != 1
        or len(context_members) != 1
        or prompt_members[0].content != manifest.artifact.rendered
        or prompt_members[0].revision_head != manifest.artifact.content_hash
        or schema_members[0].content != manifest.artifact.response_schema_json
        or schema_members[0].revision_head != manifest.artifact.digest()
        or context_members[0].provenance_head != run.predecessor
        or context_members[0].content not in manifest.artifact.rendered
        or context_members[0].label != context_label(run)
        or prompt_members[0].label != context_members[0].label
    ):
        raise LoopRejected("request lacks exact prompt/schema/context visibility closure")
    request = json.dumps(
        {
            "prompt": manifest.artifact.rendered,
            "schema": manifest.artifact.response_schema_json,
            "manifest": manifest.digest(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(request.encode()) > run.policy.max_request_bytes:
        raise LoopRejected("request exceeds physical transport bound")
    lineage = turn.turn_id + "/slot/0"
    attempt = ModelAttempt(
        attempt_id=lineage + "/generation/0",
        lineage_id=lineage,
        generation=0,
        state="PREPARED_NOT_EMITTED",
        head=manifest.digest(),
        manifest=manifest,
        request=request,
        worker_session=manifest.worker_session,
    )
    return with_turn(
        run,
        turn.model_copy(update={"attempts": (attempt,), "state": "CALL_ACTIVE"}),
        "ModelAttemptPrepared",
    )


def emit(run: RunRecord) -> RunRecord:
    attempt = current_attempt(run, "PREPARED_NOT_EMITTED")
    return with_attempt(
        run, attempt.model_copy(update={"state": "EMITTED_OUTCOME_UNKNOWN"}), "ModelAttemptEmitted"
    )


def replace_unemitted(run: RunRecord, proof: NoExposureProof) -> RunRecord:
    attempt = current_attempt(run, "PREPARED_NOT_EMITTED")
    turn = current_turn(run)
    if (
        proof.attempt_id != attempt.attempt_id
        or proof.attempt_head != attempt.head
        or proof.run_head != run.head
        or proof.worker_session != attempt.worker_session
        or proof.provider_contract != attempt.provider_contract
    ):
        raise LoopRejected("no-exposure proof is not exact current pre-emission state")
    generation = attempt.generation + 1
    if run.policy.max_model_retries is not None and generation > run.policy.max_model_retries:
        raise LoopRejected("durable model retry budget exhausted")
    manifest = attempt.manifest.model_copy(update={"generation": generation})
    request = json.loads(attempt.request)
    request["manifest"] = manifest.digest()
    replacement = attempt.model_copy(
        update={
            "generation": generation,
            "attempt_id": f"{attempt.lineage_id}/generation/{generation}",
            "manifest": manifest,
            "head": manifest.digest(),
            "request": json.dumps(request, sort_keys=True, separators=(",", ":")),
        }
    )
    return with_turn(
        run,
        turn.model_copy(update={"attempts": (*turn.attempts, replacement), "selector": generation}),
        "ModelAttemptReplacedWithoutExposure",
    )


def capture(run: RunRecord, raw: bytes, receipt: str) -> RunRecord:
    attempt = current_attempt(run, "EMITTED_OUTCOME_UNKNOWN")
    if len(raw) > run.policy.max_response_bytes or not receipt:
        raise LoopRejected("response exceeds transport bound or receipt is missing")
    attempt = attempt.model_copy(
        update={
            "state": "RESPONSE_CAPTURED",
            "response_base64": base64.b64encode(raw).decode(),
            "receipt": receipt,
        }
    )
    updated = with_attempt(run, attempt, "ModelResponseCaptured")
    return with_turn(
        run,
        current_turn(updated).model_copy(update={"state": "RESPONSE_AVAILABLE"}),
        "ModelResponseCaptured",
    )


def reject(run: RunRecord, reason: str) -> RunRecord:
    attempt = current_attempt(run, "RESPONSE_CAPTURED")
    updated = with_attempt(
        run,
        attempt.model_copy(update={"state": "TERMINAL_REJECTED", "rejection": reason}),
        "ModelResponseRejected",
    )
    return with_turn(
        run,
        current_turn(updated).model_copy(update={"state": "REJECTED", "sealed_calls": ()}),
        "ModelResponseRejected",
    )


def accept_tools(run: RunRecord, response: Continue) -> RunRecord:
    attempt = current_attempt(run, "RESPONSE_CAPTURED")
    if (
        attempt.response_base64 is None
        or parse_captured_response(
            base64.b64decode(attempt.response_base64, validate=True), attempt.manifest.artifact
        )
        != response
    ):
        raise LoopRejected("response differs from captured bytes")
    if len(response.tool_calls) > run.policy.max_tool_calls:
        raise LoopRejected("sealed fan-out exceeds bound")
    if len({call.call_id for call in response.tool_calls}) != len(response.tool_calls):
        raise LoopRejected("duplicate call identity")
    allowed = {spec.name for spec in attempt.manifest.artifact.tools}
    if any(call.tool not in allowed for call in response.tool_calls):
        raise LoopRejected("call outside exact Turn schema")
    updated = with_attempt(
        run, attempt.model_copy(update={"state": "TERMINAL_ACCEPTED"}), "ModelResponseReceived"
    )
    turn = current_turn(updated).model_copy(
        update={
            "state": "ACCEPTED",
            "sealed_calls": tuple(
                ToolOutcome(
                    call=call,
                    state="INITIALIZED",
                    proposal_id=f"{current_turn(run).turn_id}/proposal/{call.call_id}",
                )
                for call in response.tool_calls
            ),
        }
    )
    return with_turn(run, turn, "ModelResponseReceived")


def terminal_tool(run: RunRecord, call_id: str, result: str) -> RunRecord:
    turn = current_turn(run)
    if turn.sealed_calls is None:
        raise LoopRejected("response is not sealed")
    calls = list(turn.sealed_calls)
    indexes = [i for i, outcome in enumerate(calls) if outcome.call.call_id == call_id]
    if len(indexes) != 1 or calls[indexes[0]].state != "INITIALIZED":
        raise LoopRejected("call is absent or already terminal")
    calls[indexes[0]] = calls[indexes[0]].model_copy(update={"state": "TERMINAL", "result": result})
    return with_turn(run, turn.model_copy(update={"sealed_calls": tuple(calls)}), "ToolTerminal")


def complete(run: RunRecord, response: Complete) -> RunRecord:
    attempt = current_attempt(run, "RESPONSE_CAPTURED")
    if (
        attempt.response_base64 is None
        or parse_captured_response(
            base64.b64decode(attempt.response_base64, validate=True), attempt.manifest.artifact
        )
        != response
    ):
        raise LoopRejected("completion differs from selected captured bytes")
    continuation(run.model_copy(update={"turns": run.turns[:-1]}))
    deliveries: list[AcceptedDelivery] = []
    recipients: set[tuple[str, str, str]] = set()
    for index, delivery in enumerate(response.deliveries):
        endpoint = delivery.endpoint or run.origin
        if endpoint != run.origin:
            raise LoopRejected("unregistered/stale exact endpoint selection")
        label = join_labels(
            tuple(
                member.label
                for turn in run.turns
                for historical in turn.attempts
                for member in historical.manifest.members
            )
        )
        if (
            label != attempt.manifest.joined_label
            or label.value == "DENY_ALL"
            or (
                label.value == "ENDPOINT_RESTRICTED"
                and endpoint.endpoint_id not in label.allowed_endpoints
            )
        ):
            raise LoopRejected("delivery violates complete historical disclosure manifest")
        recipient = (endpoint.provider, endpoint.recipient, endpoint.canonical_address)
        if recipient in recipients:
            raise LoopRejected("duplicate exact delivery recipient")
        recipients.add(recipient)
        if delivery.kind == "DeliveryAssertion":
            matches = [
                receipt
                for receipt in run.planning_receipts
                if receipt.evidence_id == delivery.evidence_id
            ]
            if (
                delivery.assertion_code != "LOCAL_PLANNING_COMMITTED"
                or len(matches) != 1
                or delivery.text is not None
            ):
                raise LoopRejected("assertion lacks exact evidence or code entailment")
            rendered = render_receipt(matches[0])
        else:
            if (
                delivery.assertion_code is not None
                or delivery.evidence_id is not None
                or delivery.text is None
            ):
                raise LoopRejected("non-authoritative text missing or carrying assertion evidence")
            rendered = delivery.text
        payload = delivery.model_copy(update={"endpoint": endpoint})
        deliveries.append(
            AcceptedDelivery(
                delivery_id=f"{run.run_id}/delivery/{index}",
                payload=payload,
                rendered=rendered,
                visibility_digest=attempt.manifest.digest(),
            )
        )
    updated = with_attempt(
        run, attempt.model_copy(update={"state": "TERMINAL_ACCEPTED"}), "CompleteAcceptance"
    )
    turn = current_turn(updated).model_copy(update={"state": "ACCEPTED", "sealed_calls": ()})
    return successor(
        run,
        "CompleteAcceptance",
        state="SUCCEEDED",
        turns=(*updated.turns[:-1], turn),
        deliveries=tuple(deliveries),
        accepted_text=tuple(delivery.rendered for delivery in deliveries),
    )


def fingerprint_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def render_receipt(receipt: LocalPlanningReceipt) -> str:
    return f"Committed local intention: {receipt.purpose}."


def observe_receipt(run: RunRecord, receipt: LocalPlanningReceipt) -> RunRecord:
    if run.state != "ACTIVE":
        raise LoopRejected("receipt observation requires ACTIVE Run")
    continuation(run)
    proposals = {
        outcome.proposal_id: outcome.call.text
        for turn in run.turns
        for outcome in (turn.sealed_calls or ())
        if outcome.call.tool == "propose_planning"
    }
    if (
        receipt.proposal_id not in proposals
        or receipt.purpose != proposals.get(receipt.proposal_id)
        or receipt.evidence_id != receipt.command_id
        or receipt.result_digest != hashlib.sha256(receipt.canonical_result.encode()).hexdigest()
        or any(old.evidence_id == receipt.evidence_id for old in run.planning_receipts)
    ):
        raise LoopRejected("foreign, duplicated or corrupt planning receipt")
    return successor(
        run, "PlanningReceiptObserved", planning_receipts=(*run.planning_receipts, receipt)
    )


def validate_record(
    previous: RunRecord | None,
    record: RunRecord,
    delivery_observation: DeliveryObservation | None = None,
) -> None:
    """Verify immutable ancestry and the exact owner transition, not a cached status."""
    if record.head != "loop:" + record.model_copy(update={"head": "pending"}).digest():
        raise LoopRejected("content-derived Run head mismatch")
    if previous is None:
        if (
            record.event != "RunCreated"
            or record.state != "CREATED"
            or record.predecessor is not None
            or record.turns
            or record.deliveries
            or record.accepted_text
            or record.planning_receipts
            or record.accepted_delivery_binding != "LEGACY_R13"
        ):
            raise LoopRejected("invalid Run genesis")
        return
    event = record.event
    if previous.accepted_delivery_binding != "LEGACY_R13":
        raise LoopRejected("accepted delivery Run is terminal and immutable")
    if record.accepted_delivery_binding != "LEGACY_R13" and event != "CompleteAcceptance":
        raise LoopRejected("delivery reference is only valid at CompleteAcceptance")
    if event == "PlanningReceiptObserved":
        if not record.planning_receipts:
            raise LoopRejected("missing planning receipt")
        expected = observe_receipt(previous, record.planning_receipts[-1])
    elif event in ("TurnStarted", "RunBudgetSuspended"):
        expected = start_turn(previous)
    elif event == "TurnVisibilityAccumulated":
        expected = accumulate(previous, current_turn(record).accumulator)
    elif event == "ModelAttemptPrepared":
        expected = prepare(previous, current_turn(record).attempts[-1].manifest)
    elif event == "ModelAttemptEmitted":
        expected = emit(previous)
    elif event == "ModelAttemptReplacedWithoutExposure":
        old_attempt = current_attempt(previous, "PREPARED_NOT_EMITTED")
        expected = replace_unemitted(
            previous,
            NoExposureProof(
                attempt_id=old_attempt.attempt_id,
                attempt_head=old_attempt.head,
                run_head=previous.head,
                worker_session=previous.worker_session,
            ),
        )
    elif event == "ModelResponseCaptured":
        attempt = current_attempt(record, "RESPONSE_CAPTURED")
        if attempt.response_base64 is None or attempt.receipt is None:
            raise LoopRejected("missing captured bytes or receipt")
        expected = capture(
            previous, base64.b64decode(attempt.response_base64, validate=True), attempt.receipt
        )
    elif event == "ModelResponseRejected":
        attempt = current_attempt(record, "TERMINAL_REJECTED")
        if attempt.rejection is None:
            raise LoopRejected("missing rejection reason")
        expected = reject(previous, attempt.rejection)
    elif event in ("ModelResponseReceived", "CompleteAcceptance"):
        attempt = current_attempt(previous, "RESPONSE_CAPTURED")
        if attempt.response_base64 is None:
            raise LoopRejected("missing immutable response")
        raw = base64.b64decode(attempt.response_base64, validate=True)
        if event == "ModelResponseReceived":
            expected = accept_tools(previous, Continue.model_validate_json(raw))
        elif record.accepted_delivery_binding != "LEGACY_R13":
            from .delivery_preparation import complete_delivery

            if delivery_observation is None:
                raise LoopRejected("expanded acceptance requires independent delivery observation")
            expected = complete_delivery(previous, delivery_observation)
        else:
            expected = complete(previous, Complete.model_validate_json(raw))
    elif event == "ToolTerminal":
        old_calls = current_turn(previous).sealed_calls
        new_calls = current_turn(record).sealed_calls
        if old_calls is None or new_calls is None or len(old_calls) != len(new_calls):
            raise LoopRejected("sealed-call membership changed")
        changed = [new for old, new in zip(old_calls, new_calls, strict=True) if old != new]
        if len(changed) != 1 or changed[0].result is None:
            raise LoopRejected("terminal publication must advance exactly one call")
        expected = terminal_tool(previous, changed[0].call.call_id, changed[0].result)
    elif event in ("RunActive", "RunSuspended", "RunAborted", "RunCancelled"):
        expected = transition(previous, record.state)
    else:
        raise LoopRejected("unknown orchestration event")
    if expected != record:
        raise LoopRejected("changed immutable ancestry or invalid exact transition")
