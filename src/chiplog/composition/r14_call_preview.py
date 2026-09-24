"""Authenticated original call preview selection, separate from acceptance authority."""

from __future__ import annotations

import base64
import json
import secrets
from typing import TYPE_CHECKING, Literal

from chiplog.adapters.driven.effects_broker import EffectsIntegrityError
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import LoopRejected, RunRecord
from chiplog.capabilities.agent_loop.execution_contracts import (
    ConsequentialToolCall,
    ExecutionRunRecord,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import InitializedCall
from chiplog.capabilities.effects.contracts import ExactHead
from chiplog.capabilities.effects.dispatch_authority_contracts import DispatchObservationDTO
from chiplog.capabilities.effects.dispatch_v2 import (
    PrecursorPreparationV2,
    canonical,
    digest,
    evaluate_precursor,
    reference,
)
from chiplog.capabilities.effects.dispatch_v2_contracts import (
    DispatchMandateV2,
    DispatchPrecursorRequestV2,
    DispatchPrecursorResultV2,
    InitializedCallOrigin,
    MandateHorizon,
)
from chiplog.composition import r14_call_dispatch_policy as call_policy
from chiplog.composition.r7_planning import ObservedTrustCall
from chiplog.composition.r14_call_acceptance_port import CallAcceptancePreview, CallAcceptanceTarget
from chiplog.composition.r14_call_acceptance_preview import (
    build_call_acceptance_preview,
    decode_call_acceptance_preview,
)
from chiplog.composition.r14_execution_fanout_contracts import RetainedExecutionFanOutPreparation
from chiplog.composition.r14_execution_fanout_records import build_envelope as fanout_envelope
from chiplog.composition.r14_execution_fanout_records import reference as call_reference
from chiplog.composition.r14_loop_history import read_execution_call_history
from chiplog.composition.r16_denial_inputs import _retained_trust_snapshot
from chiplog.composition.r16_dispatch_history import normative_generation
from chiplog.composition.r16_dispatch_inputs import (
    DispatchCapture,
    capture_dispatch,
    captured_policy,
    planning_scope,
    require_dispatch_scope,
)
from chiplog.composition.r16_dispatch_publication import authenticate, owner_call
from chiplog.platform.broker import (
    PublicPortCall,
    PublicPortSuccess,
)
from chiplog.platform.r7_trust import TrustOwnerCall

if TYPE_CHECKING:
    from chiplog.composition.r16_dispatch_runtime import (
        ExecutionDispatchRuntime,
    )


class CallPreviewIssuance(DispatchObservationDTO):
    """Original independently selected preview custody; a value grants no authority."""

    schema_id: Literal["chiplog.call.preview-issuance.v2"] = "chiplog.call.preview-issuance.v2"
    preview: CallAcceptancePreview
    captured: DispatchCapture
    observed: ObservedTrustCall
    initialization: RetainedExecutionFanOutPreparation
    request: PrecursorPreparationV2
    sent: PublicPortCall
    result: DispatchPrecursorResultV2


def execution_run_head(run: RunRecord | ExecutionRunRecord) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=run.run_id,
        revision=Present(head=run.head, fingerprint=digest(run.canonical_bytes())),
    )


def _effect_head(value: CallSubjectHead) -> ExactHead:
    return ExactHead(
        subject_id=value.subject_id,
        head=value.revision.head,
        fingerprint=value.revision.fingerprint,
    )


def _validate_preview_database(
    value: CallPreviewIssuance, runtime: ExecutionDispatchRuntime
) -> None:
    captured = value.captured
    runtime._check_database_identity()
    stat = runtime._database.stat()
    if captured.cut.physical_path != str(runtime._database.resolve(strict=True)) or (
        captured.cut.physical_device,
        captured.cut.physical_inode,
    ) != (stat.st_dev, stat.st_ino):
        raise ValueError("preview belongs to another physical authority database")


def validate_preview_custody_database(runtime: ExecutionDispatchRuntime) -> None:
    """Reject transplanted custody before bootstrap, without reading unrecovered history."""
    identity = "<journal>"
    try:
        with runtime._authority_gate().hold():
            for _, _, raw in runtime._require_call_preview_journal().entries():
                value = CallPreviewIssuance.model_validate_json(raw)
                identity = value.preview.preview_id
                if value.canonical_bytes() != raw:
                    raise ValueError("noncanonical selected call preview")
                _validate_preview_database(value, runtime)
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as error:
        raise EffectsIntegrityError(
            f"call-preview.read tenant={runtime._tenant_id} record={identity}"
        ) from error


def validate_call_preview(value: CallPreviewIssuance, runtime: ExecutionDispatchRuntime) -> None:
    preview, mandate = decode_call_acceptance_preview(value.preview.canonical_bytes())
    captured, observed, sent = value.captured, value.observed, value.sent
    worker = captured.cut.worker
    if worker is None or len(captured.sessions) != 4:
        raise ValueError("preview lacks original worker/session inventory")
    _validate_preview_database(value, runtime)
    fanout_envelope(value.initialization)
    originals = tuple(
        row
        for row in value.initialization.proposal.fan_out.initialized_records
        if row.original_call_id == preview.target.original_call_id
    )
    if len(originals) != 1 or originals[0].call.classification != "CONSEQUENTIAL":
        raise ValueError("preview has no exact consequential initialization")
    initialized = originals[0]
    tool = ConsequentialToolCall.model_validate_json(
        base64.b64decode(initialized.call.canonical_call_base64, validate=True)
    )
    origin = mandate.origin
    policy = call_policy.policy_reference()
    basis = reference("dispatch.preexisting-principal", captured.principal)
    scope = reference("dispatch.planning-scope", planning_scope(captured, None))
    if (
        not isinstance(origin, InitializedCallOrigin)
        or preview.target.initialized != call_reference(initialized.original_call_id, initialized)
        or preview.target.current_run != execution_run_head(worker.run)
        or origin.original_run_id != initialized.call.original.original_run_id
        or origin.original_run_id != worker.run.run_id
        or origin.initialization_run_head
        != _effect_head(execution_run_head(value.initialization.proposal.sealed_run))
        or origin.initialized_head != _effect_head(preview.target.initialized)
        or origin.tool_name != tool.tool
        or origin.tool_schema != "chiplog.request-self-effect.v2"
        or origin.tool_policy != _effect_head(initialized.call.tool_policy)
        or origin.sealed_arguments != tool.arguments.model_dump_json().encode()
        or mandate.payload != tool.arguments.payload
        or mandate.bundle_members
        != call_policy.bundle_references(initialized.original_call_id, tool.arguments)
        or mandate.operation_profile != call_policy.policy_reference()
        or mandate.semantics != call_policy.SEMANTICS
        or captured_policy(captured) != (call_policy.policy_bytes(), call_policy.SEMANTICS)
        or mandate.planning_revision != scope
        or mandate.preexisting_authority_basis != basis
        or mandate.authority_sources != (basis, scope, policy)
        or mandate.affected_party_constraints
        != (reference("self-only", canonical(["hermetic-principal", "hermetic-account"])),)
        or mandate.normative_conflict_generation != normative_generation(captured.history, None)
        or mandate.dependencies
        or mandate.factual_assertion_evidence
        or mandate.verification_contradiction
        or mandate.authority_applicability != (policy,)
        or mandate.consequence_scope != reference("opaque-self-emission", tool.arguments.payload)
        or mandate.communication_mandate != reference("opaque-self-payload", tool.arguments.payload)
        or mandate.disclosure_projection != reference("exact-payload-only", tool.arguments.payload)
        or mandate.channel_class != "HERMETIC_SELF"
        or mandate.interaction_context != reference("original-call", initialized.canonical_bytes())
        or mandate.idempotency_fence_key != initialized.original_call_id + "/single-effect"
        or mandate.tenant_id != captured.cut.tenant_id
        or mandate.tenant_id != runtime._tenant_id
        or mandate.principal_id != mandate.actor_id
        or mandate.actor_id != "hermetic-principal"
        or mandate.horizon.clock_contract != call_policy.CLOCK
        or mandate.horizon.clock_epoch != captured.resources.clock_epoch
        or mandate.horizon.continuity_policy != policy
        or mandate.horizon.not_before_ns != captured.observed_time_ns
        or not 0
        < mandate.horizon.expires_at_ns - mandate.horizon.not_before_ns
        <= call_policy.MAX_HORIZON_NS
        or worker.owner_session != captured.sessions[2]
        or worker.run.state != "ACTIVE"
        or not runtime._require_dispatch_resources().verify_historical(captured.resources)
        or mandate.recipient
        != runtime._require_dispatch_resources().historical_recipient(captured.resources)
        or value.request.mandate != mandate
        or value.result != evaluate_precursor(value.request.request, mandate)
        or sent.callee != captured.sessions[0]
        or sent.operation_id != "effects.evaluate_dispatch_mandate_v2"
        or sent.schema_id != value.request.schema_id
        or sent.canonical_payload != value.request.canonical_bytes()
        or sent.budget.absolute_deadline_ns <= captured.observed_time_ns
    ):
        raise ValueError("original preview call, mandate, resources or precursor differs")
    payload = json.loads(observed.request.canonical_payload)
    trust_call = TrustOwnerCall(
        mode=payload["mode"],
        snapshot_bytes=base64.b64decode(payload["snapshot_bytes"], validate=True),
        request_bytes=base64.b64decode(payload["request_bytes"], validate=True),
    )
    trust = _retained_trust_snapshot(trust_call.snapshot_bytes)
    credential = trust["credential"]
    credentials = json.loads(trust_call.request_bytes)
    principal = json.loads(captured.principal)
    expected = {
        "contour": "CLI",
        "credential_head": credential["head"],
        "freshness_sequence": trust["freshness"],
        "materialization_head": trust["materialization_head"],
        "peer_credential": credential["peer_credential"],
        "principal_id": trust["principal_id"],
        "session_head": credential["session_head"],
        "source_head": "local",
        "tenant_id": trust["tenant_id"],
        "trust_head": trust["trust_head"],
    }
    if (
        trust["phase"] != "ACTIVE"
        or credential.get("revoked")
        or principal != expected
        or principal["tenant_id"] != mandate.tenant_id
        or principal["principal_id"] != mandate.principal_id
        or credentials["contour"] != "CLI"
        or any(
            credentials[key] != credential[key]
            for key in ("credential_id", "session_id", "peer_credential")
        )
        or observed.result.reference_bytes != captured.principal
        or observed.result.disposition != "VALID"
        or observed.request.operation_id != "deployment_trust.authenticate"
        or observed.request.schema_id != "chiplog.deployment-trust.owner-call.v1"
        or observed.request.callee != captured.sessions[3]
        or trust_call.mode != "AUTHENTICATE"
        or trust_call.canonical_bytes() != observed.request.canonical_payload
        or trust_call.snapshot_bytes != observed.observation.snapshot_bytes
        or not isinstance(observed.response, PublicPortSuccess)
        or observed.response.request_id != observed.request.request_id
        or observed.response.responder != observed.request.callee
        or observed.response.schema_id != "chiplog.deployment-trust.owner-result.v1"
        or observed.response.canonical_payload != observed.result.canonical_bytes()
    ):
        raise ValueError("retained preview principal/IPC provenance differs")


def read_call_previews(runtime: ExecutionDispatchRuntime) -> tuple[CallPreviewIssuance, ...]:
    with runtime._authority_gate().hold():
        return _read_call_previews(runtime)


def _read_call_previews(runtime: ExecutionDispatchRuntime) -> tuple[CallPreviewIssuance, ...]:
    identity = "<journal>"
    try:
        journal = runtime._require_call_preview_journal()
        result: dict[str, CallPreviewIssuance] = {}
        for _, _, raw in journal.entries():
            value = CallPreviewIssuance.model_validate_json(raw)
            identity = value.preview.preview_id
            if value.canonical_bytes() != raw or identity in result:
                raise ValueError("noncanonical or duplicate selected call preview")
            validate_call_preview(value, runtime)
            result[identity] = value
        if result:
            snapshot, _, initializations = read_execution_call_history(runtime)
            for value in result.values():
                identity = value.preview.preview_id
                worker = value.captured.cut.worker
                if (
                    value.initialization not in initializations
                    or worker is None
                    or worker.run not in snapshot.records
                    or value.captured.cut.tenant_frontier > snapshot.tenant_head
                ):
                    raise ValueError(
                        "preview lacks its original independently selected Run history"
                    )
        runtime._require_call_preview_journal()
        return tuple(result.values())
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as error:
        raise EffectsIntegrityError(
            f"call-preview.read tenant={runtime._tenant_id} record={identity}"
        ) from error


async def preview_call_acceptance(
    runtime: ExecutionDispatchRuntime, peer: str, target: CallAcceptanceTarget
) -> CallAcceptancePreview:
    observed = await authenticate(runtime, peer)
    with runtime._authority_gate().hold():
        captured = capture_dispatch(
            runtime,
            runtime._require_dispatch_resources(),
            observed,
            target.current_run.subject_id,
            original_call_id=target.original_call_id,
        )
        worker = captured.cut.worker
        assert worker is not None
        _, inventory, preparations = read_execution_call_history(runtime)
        matches = [
            row
            for row in inventory.ordered_calls
            if row.original_call_id == target.original_call_id
        ]
        if (
            len(matches) != 1
            or not isinstance(matches[0].acceptance, InitializedCall)
            or not isinstance(matches[0].terminal, Absent)
            or matches[0].initialized != target.initialized
            or target.current_run != execution_run_head(worker.run)
        ):
            raise LoopRejected("call preview target is stale, accepted or absent")
        initialized = matches[0].initialized_record
        initialization = next(
            item
            for item in preparations
            if initialized in item.proposal.fan_out.initialized_records
        )
        tool = ConsequentialToolCall.model_validate_json(
            base64.b64decode(initialized.call.canonical_call_base64, validate=True)
        )
        policy = call_policy.policy_reference()
        basis = reference("dispatch.preexisting-principal", captured.principal)
        scope = reference("dispatch.planning-scope", planning_scope(captured, None))
        mandate = DispatchMandateV2(
            schema_id="chiplog.effects.dispatch-mandate.v2",
            mandate_id=target.original_call_id + "/mandate/" + secrets.token_hex(24),
            tenant_id=runtime._tenant_id,
            principal_id="hermetic-principal",
            actor_id="hermetic-principal",
            operation_profile=policy,
            origin=InitializedCallOrigin(
                kind="INITIALIZED_CONSEQUENTIAL_CALL",
                original_run_id=worker.run.run_id,
                original_call_id=target.original_call_id,
                initialization_run_head=_effect_head(
                    execution_run_head(initialization.proposal.sealed_run)
                ),
                initialized_head=_effect_head(target.initialized),
                tool_name=tool.tool,
                tool_schema="chiplog.request-self-effect.v2",
                tool_policy=_effect_head(initialized.call.tool_policy),
                sealed_arguments=tool.arguments.model_dump_json().encode(),
            ),
            planning_revision=scope,
            preexisting_authority_basis=basis,
            authority_sources=(basis, scope, policy),
            affected_party_constraints=(
                reference("self-only", canonical(["hermetic-principal", "hermetic-account"])),
            ),
            normative_conflict_generation=normative_generation(captured.history, None),
            dependencies=(),
            factual_assertion_evidence=(),
            verification_contradiction=(),
            authority_applicability=(policy,),
            consequence_scope=reference("opaque-self-emission", tool.arguments.payload),
            communication_mandate=reference("opaque-self-payload", tool.arguments.payload),
            disclosure_projection=reference("exact-payload-only", tool.arguments.payload),
            channel_class="HERMETIC_SELF",
            interaction_context=reference("original-call", initialized.canonical_bytes()),
            recipient=runtime._require_dispatch_resources().recipient(captured.resources),
            payload=tool.arguments.payload,
            effect_fingerprint=digest(tool.arguments.payload),
            bundle_members=call_policy.bundle_references(target.original_call_id, tool.arguments),
            idempotency_fence_key=target.original_call_id + "/single-effect",
            horizon=MandateHorizon(
                clock_contract=call_policy.CLOCK,
                clock_epoch=captured.resources.clock_epoch,
                not_before_ns=captured.observed_time_ns,
                expires_at_ns=captured.observed_time_ns + call_policy.MAX_HORIZON_NS,
                continuity_policy=policy,
            ),
            semantics=call_policy.SEMANTICS,
        )
        require_dispatch_scope(
            captured, runtime._require_dispatch_resources(), mandate, None, first_send=False
        )
        preview = build_call_acceptance_preview(target, mandate)
        request = PrecursorPreparationV2(
            schema_id="chiplog.effects.precursor-preparation.v2",
            mandate=mandate,
            request=DispatchPrecursorRequestV2(
                schema_id="chiplog.effects.dispatch-precursor-request.v2",
                request_id=preview.preview_id + "/precursor",
                mandate_digest=digest(mandate.canonical_bytes()),
                interpretation_policy=policy,
                preexisting_source_heads=mandate.authority_sources,
            ),
        )
    sent, raw = await owner_call(
        runtime,
        "effects.evaluate_dispatch_mandate_v2",
        request.schema_id,
        request.canonical_bytes(),
    )
    with runtime._authority_gate().hold():
        current = capture_dispatch(
            runtime,
            runtime._require_dispatch_resources(),
            observed,
            worker.run.run_id,
            original_call_id=target.original_call_id,
        )
        if current != captured:
            raise LoopRejected("call preview sources changed during precursor evaluation")
        require_dispatch_scope(
            current, runtime._require_dispatch_resources(), mandate, None, first_send=False
        )
        value = CallPreviewIssuance(
            preview=preview,
            captured=captured,
            observed=observed,
            initialization=initialization,
            request=request,
            sent=sent,
            result=DispatchPrecursorResultV2.model_validate_json(raw),
        )
        validate_call_preview(value, runtime)
        previous = read_call_previews(runtime)
        if any(item.preview.preview_id == preview.preview_id for item in previous):
            raise LoopRejected("call preview identity already selected")
        journal = runtime._require_call_preview_journal()
        entries = journal.entries()
        try:
            journal.append(value.canonical_bytes(), entries[-1][0] if entries else None)
            if read_call_previews(runtime)[-1] != value:
                raise ValueError("selected preview readback differs")
        except (ValueError, RuntimeError, OSError) as error:
            raise EffectsIntegrityError(
                f"call-preview.select tenant={runtime._tenant_id} record={preview.preview_id}"
            ) from error
        return preview
