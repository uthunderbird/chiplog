"""Shared inert fan-out builders; supplied values confer no authority."""

import base64
import json

import chiplog.capabilities.agent_loop.call_acceptance_contracts as call
import chiplog.capabilities.agent_loop.fan_out_contracts as fan
from chiplog.capabilities.agent_loop import contracts as loop
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    NonSchedulerFence,
    NotApplicable,
    Present,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import FanOutBound


def head(subject: str, digest: str = "a" * 64) -> call.CallSubjectHead:
    return call.CallSubjectHead(
        subject_id=subject, revision=Present(head="record:" + digest, fingerprint=digest)
    )


def shape_request() -> fan.CapturedFanOutRequest:
    tool = loop.ToolSpec(name="propose_planning", schema_id="planning-schema")
    parsed = loop.Continue(
        kind="Continue",
        tool_calls=(loop.ToolCall(call_id="model-call", tool=tool.name, text="Plan café"),),
    )
    # The durable capture predates canonical serialization: retain whitespace and escapes.
    raw = json.dumps(parsed.model_dump(mode="json"), indent=2).encode() + b"\n"
    encoded = base64.b64encode(raw).decode()
    artifact = loop.PromptArtifact(
        content_hash="b" * 64,
        library_version="1",
        tools=(tool,),
        response_schema_json=json.dumps(loop.Continue.model_json_schema()),
        rendered="Plan",
    )
    manifest = loop.VisibilityManifest(
        tenant="tenant",
        principal="actor",
        contour_head="contour",
        run_id="run",
        turn_id="turn",
        generation=0,
        worker_session="worker",
        members=(),
        joined_label=loop.DisclosureLabel(value="UNRESTRICTED", allowed_endpoints=()),
        artifact=artifact,
    )
    attempt = loop.ModelAttempt(
        attempt_id="attempt",
        lineage_id="lineage",
        generation=0,
        state="RESPONSE_CAPTURED",
        head="attempt-head",
        manifest=manifest,
        request="Plan",
        worker_session="worker",
        response_base64=encoded,
        receipt="receipt",
    )
    run = loop.RunRecord(
        tenant="tenant",
        principal="actor",
        run_id="run",
        state="ACTIVE",
        head="run-head",
        predecessor="previous-run-head",
        prompt="Plan",
        policy=loop.BudgetPolicy(),
        origin=loop.EndpointSelection(
            kind="ORIGIN_EXACT",
            ingress_binding_head="ingress",
            endpoint_head="endpoint-head",
            endpoint_id="endpoint",
            provider="hermetic-local",
            recipient="actor",
            canonical_address="local:actor",
            credential_binding_head="credential",
        ),
        contour_head="contour",
        policy_head="policy",
        worker_session="worker",
        turns=(
            loop.Turn(
                turn_id="turn",
                ordinal=1,
                head="turn-head",
                state="RESPONSE_AVAILABLE",
                attempts=(attempt,),
                selector=0,
            ),
        ),
        event="ModelResponseCaptured",
    )
    captured = call.CallSubjectHead(
        subject_id=run.run_id, revision=Present(head=run.head, fingerprint=run.digest())
    )
    entry = fan.FanOutToolPolicy(
        tool_name=tool.name,
        tool_version=tool.version,
        schema_id=tool.schema_id,
        tool_schema=head(tool.schema_id, tool.digest()),
        tool_policy=head("policy"),
        classification="PROPOSAL_ONLY",
        retry_policy=NotApplicable(),
    )
    registry = fan.FanOutToolRegistry(registry_id="registry", version="1", entries=(entry,))
    cut = call.CallPreparationCut(
        tenant_id="tenant",
        current_run=captured,
        run_state="ACTIVE",
        tenant_commit_sequence=1,
        materialization_commitment="b" * 64,
        complete_call_inventory=head("inventory"),
        predecessor_inventory=call.CallInventorySnapshot(
            tenant_id="tenant",
            tenant_commit_sequence=1,
            ordered_calls=(),
        ),
        authority_registry=head("authority"),
        sources=(
            call.CallAuthorityObservation(
                source_id="actor",
                family="ACTOR",
                source=head("actor"),
                generation="0",
                frontier="frontier",
                canonical_value_base64="e30=",
                observed_at_ns=1,
                valid_until_ns=2,
            ),
        ),
        fence=NonSchedulerFence(
            lineage=NotApplicable(),
            physical_root=NotApplicable(),
            lease=NotApplicable(),
            clock_proof=NotApplicable(),
            run_id=run.run_id,
            run_head=run.head,
            worker_session_id=run.worker_session,
            runtime_generation="0",
        ),
    )
    request = call.FanOutPreparationRequest(
        command_id="prepare",
        original_run_id=run.run_id,
        original_turn_id="turn",
        captured_response=captured,
        canonical_response_base64=encoded,
        ordered_calls=(
            call.SealedCallInput(
                original=call.OriginalCallKey(
                    tenant_id=run.tenant,
                    original_run_id=run.run_id,
                    original_turn_id="turn",
                    captured_response=captured,
                    ordinal=0,
                    model_call_label="model-call",
                ),
                classification=entry.classification,
                tool_schema=entry.tool_schema,
                tool_policy=entry.tool_policy,
                retry_lineage=NotApplicable(),
                canonical_call_base64=base64.b64encode(
                    parsed.tool_calls[0].canonical_bytes()
                ).decode(),
            ),
        ),
        bound=FanOutBound(
            max_call_count=8,
            max_manifest_bytes=65536,
            max_serialized_batch_bytes=131072,
            canonicalization_version="chiplog.recovery.frontier.v1",
        ),
        cut=cut,
    )
    return fan.CapturedFanOutRequest(
        request=request,
        captured_run=run,
        tool_registry=registry,
        tool_registry_head=head(registry.registry_id, registry.digest()),
    )


def shape_proposal(request: fan.CapturedFanOutRequest) -> fan.CapturedFanOutProposal:
    inner = request.request
    initialized = call.InitializedCallRecord(
        original_call_id="call:" + inner.ordered_calls[0].original.digest(),
        call=inner.ordered_calls[0],
        predecessor=Absent(),
    )
    initialized_head = head(initialized.original_call_id, initialized.digest())
    seal = call.SealedResponseRecord(
        response_seal_id="response-seal:" + inner.digest(),
        tenant_id="tenant",
        original_run_id="run",
        original_turn_id="turn",
        captured_response=inner.captured_response,
        complete_ordered_initialized=(initialized_head,),
        bound=inner.bound,
    )
    return fan.CapturedFanOutProposal(
        source_request_fingerprint=request.digest(),
        proposal_fingerprint="c" * 64,
        fan_out=call.PreparedCallFanOut(
            source_request_fingerprint=inner.digest(),
            response_seal=seal,
            initialized_records=(initialized,),
            complete_ordered_record_manifest=(
                head(seal.response_seal_id, seal.digest()),
                initialized_head,
            ),
            proposal_fingerprint="d" * 64,
        ),
    )
