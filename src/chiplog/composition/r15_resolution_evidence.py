"""Closed overflow-resolution policy and selected provenance checks."""

from __future__ import annotations

import base64
import json

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    ResolveIntervalCommand,
    SchedulerContextRef,
)
from chiplog.capabilities.agent_loop.scheduler_preparation import (
    BATCH_SCHEMA,
    prepare_interval_request,
)
from chiplog.composition.r15_resolution_contracts import (
    ResolutionIssuanceEvidence,
    ResolutionPolicy,
    ResolutionPolicyAdoption,
    ResolutionPolicyPreview,
)
from chiplog.composition.r15_scheduler_registry import _canonical, _digest, _reference
from chiplog.composition.r15_tick_contracts import TickSourceCut
from chiplog.platform._owner_publication_contracts import SingleOwnerBatch, WorkerAuthentication
from chiplog.platform.owner_decision_journal import OwnerJournalIntegrityError

ISSUANCE_SCHEMA = "chiplog.scheduler.resolution-issuance.v1"
PREPARATION_SCHEMA = "chiplog.scheduler.interval-preparation.v1"
SERVICE = "r15-hermetic-overflow-operator-v1"


def resolution_id(act: str) -> str:
    return _reference(
        "r15-resolution-act-v1",
        ("hermetic-tenant", "hermetic-principal", "scheduler.resolve_interval", act),
    )


def adopted(adoption: ResolutionPolicyAdoption) -> ResolutionPolicyPreview:
    preview = ResolutionPolicyPreview.model_validate_json(adoption.policy_bytes)
    if (
        preview.canonical_bytes() != adoption.policy_bytes
        or preview.adoption_act_id != adoption.adoption_act_id
        or preview.policy != ResolutionPolicy()
    ):
        raise LoopRejected("noncanonical or unregistered resolution policy")
    return preview


def context_for(
    adoption: ResolutionPolicyAdoption,
    generations: bytes,
    cut: TickSourceCut,
    nonce: str,
) -> SchedulerContextRef:
    body = {
        "adoption": adoption.model_dump(mode="json"),
        "generations": json.loads(generations),
        "cut": cut.model_dump(mode="json"),
        "nonce": nonce,
    }
    return SchedulerContextRef(
        tenant_id=cut.tenant_id,
        service_identity=SERVICE,
        session_id="hermetic-session",
        mandate_head=_reference("r15-resolution-authority-v1", body),
        issuance_id="r15-resolution-context:" + nonce,
        issuance_fingerprint=_digest(_canonical(body)),
    )


def operator_proof(adoption: ResolutionPolicyAdoption, context: SchedulerContextRef) -> Present:
    body = {
        "adoption": adoption.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
    }
    return Present(
        head=_reference("r15-resolution-operator-v1", body), fingerprint=_digest(_canonical(body))
    )


def cut_proof(context: SchedulerContextRef, cut: TickSourceCut) -> Present:
    body = {"context": context.model_dump(mode="json"), "cut": cut.model_dump(mode="json")}
    return Present(
        head=_reference("r15-resolution-cut-v1", body), fingerprint=_digest(_canonical(body))
    )


def validate_resolution_batch(batch: SingleOwnerBatch) -> ResolutionIssuanceEvidence:
    """Interpret an independently selected resolution, including physical owner frames."""
    if (
        batch.operation != "scheduler.resolve_interval"
        or batch.complete_batch_fingerprint
        != _digest(
            _canonical(batch.model_dump(mode="json", exclude={"complete_batch_fingerprint"}))
        )
        or not isinstance(batch.authentication, WorkerAuthentication)
        or batch.authentication.applicability_schema != ISSUANCE_SCHEMA
    ):
        raise ValueError("selected resolution envelope differs")
    auth = batch.authentication
    evidence = ResolutionIssuanceEvidence.model_validate_json(auth.applicability_bytes)
    if (
        evidence.canonical_bytes() != auth.applicability_bytes
        or auth.applicability_fingerprint != _digest(auth.applicability_bytes)
        or auth.invocation.issuance_fingerprint
        != _digest(
            _canonical(
                {
                    "nonce": auth.invocation.issuance_id,
                    "identity": batch.identity.model_dump(mode="json"),
                    "command": batch.command.model_dump(mode="json"),
                    "evidence": auth.applicability_fingerprint,
                }
            )
        )
    ):
        raise ValueError("resolution issuance commitment differs")
    preview = adopted(evidence.adoption)
    generations = json.loads(evidence.generation_preimages)
    authentication = generations["authentication"]
    if evidence.trust_preimages != _canonical(authentication):
        raise ValueError("resolution trust preimages differ")
    trust_request, trust_response = authentication["request"], authentication["response"]
    trust_result = json.loads(base64.b64decode(authentication["result_base64"], validate=True))
    trust = generations["trust_reference"]
    if (
        trust_request["operation_id"] != "deployment_trust.authenticate"
        or trust_response["request_id"] != trust_request["request_id"]
        or trust_response["responder"] != trust_request["callee"]
        or base64.b64decode(trust_response["canonical_payload_base64"], validate=True)
        != base64.b64decode(authentication["result_base64"], validate=True)
        or trust_result["disposition"] != "VALID"
        or json.loads(base64.b64decode(trust_result["reference_bytes"], validate=True)) != trust
        or (trust["tenant_id"], trust["principal_id"], trust["contour"], trust["source_head"])
        != ("hermetic-tenant", "hermetic-principal", "CLI", "local")
    ):
        raise ValueError("resolution trust invocation differs")
    worker = generations["broker_generation_preimage"]["agent_session"]
    if (
        auth.invocation.broker_epoch != str(worker["broker_epoch"])
        or auth.invocation.broker_session != worker["session_id"]
        or auth.invocation.runtime_generation != worker["generation_id"]
        or auth.invocation.operation_subject != batch.identity.command_id
    ):
        raise ValueError("resolution worker invocation differs")
    request = evidence.preparation
    from chiplog.composition.r15_resolution_runtime import _manifest

    if batch.expected != _manifest(request):
        raise ValueError("resolution complete read manifest differs")
    command = ResolveIntervalCommand.model_validate_json(request.command_bytes)
    context = context_for(
        evidence.adoption,
        evidence.generation_preimages,
        evidence.source_cut,
        evidence.issuance_nonce,
    )
    cut = request.snapshot.cut
    if (
        request.operation != batch.operation
        or request.context != context
        or cut.authorized_context != context
        or cut.authority_proof != cut_proof(context, evidence.source_cut)
        or cut.authorized_command_fingerprint != _digest(request.command_bytes)
        or (cut.tenant_id, cut.tenant_frontier, cut.materialization_commitment)
        != (
            evidence.source_cut.tenant_id,
            evidence.source_cut.tenant_frontier,
            evidence.source_cut.materialization_commitment,
        )
        or cut.registry_head != batch.expected.registry_head
        or cut.registry_fingerprint != batch.expected.registry_fingerprint
        or evidence.source_cut.tenant_id != batch.identity.tenant_id
        or evidence.source_cut.deletion_fence_generation != "r6"
        or evidence.source_cut.deletion_fence_frontier != 0
        or not evidence.source_cut.independent_journal_head
        or not evidence.source_cut.database_path
        or command.canonical_bytes() != request.command_bytes
        or command.identity.command_id != resolution_id(evidence.adoption.adoption_act_id)
        or batch.identity.command_id != command.identity.command_id
        or batch.identity.command_fingerprint != _digest(request.command_bytes)
        or command.operator_proof != operator_proof(evidence.adoption, context)
        or request.snapshot.operator_proof != command.operator_proof
        or command.active_hold.hold != preview.active_hold
        or command.active_hold.boundary != preview.held_boundary
        or command.boundary != preview.held_boundary
        or command.successor_bound != preview.successor_bound
        or command.boundary.schedule_definition_head != preview.schedule
        or command.boundary.missed_occurrence_policy_head != preview.missed_policy
        or batch.command.owner != "agent_loop"
        or batch.command.schema_id != PREPARATION_SCHEMA
        or batch.command.canonical_bytes != request.canonical_bytes()
        or batch.command.fingerprint != _digest(request.canonical_bytes())
    ):
        raise ValueError("resolution adopted policy, hold or request differs")
    sent, response = (
        json.loads(evidence.owner_request_frame),
        json.loads(evidence.owner_response_frame),
    )
    if (
        sent["operation_id"] != "scheduler.prepare_interval"
        or sent["schema_id"] != PREPARATION_SCHEMA
        or sent["callee"] != worker
        or response["request_id"] != sent["request_id"]
        or response["responder"] != sent["callee"]
        or response["schema_id"] != BATCH_SCHEMA
        or base64.b64decode(sent["canonical_payload_base64"], validate=True)
        != request.canonical_bytes()
    ):
        raise ValueError("resolution owner IPC differs")
    prepared = prepare_interval_request(request)
    if base64.b64decode(
        response["canonical_payload_base64"], validate=True
    ) != prepared.canonical_bytes() or tuple(
        (
            record.owner,
            record.record_kind,
            record.record_id,
            record.schema_id,
            record.canonical_bytes,
            record.fingerprint,
        )
        for record in batch.complete_records
    ) != tuple(
        (
            "agent_loop",
            record.record_kind,
            record.record_id,
            record.schema_id,
            base64.b64decode(record.canonical_base64, validate=True),
            record.fingerprint,
        )
        for record in prepared.records
    ):
        raise ValueError("resolution selected records differ from complete owner output")
    return evidence


def validate_selected_resolution(decision: object) -> None:
    from chiplog.platform.owner_publications import SelectedOwnerDecision

    if not isinstance(decision, SelectedOwnerDecision):
        raise ValueError("resolution selection must be independent")
    batch = decision.prepared.request
    if batch.operation != "scheduler.resolve_interval":
        return
    try:
        if not isinstance(batch, SingleOwnerBatch):
            raise ValueError("resolution is not single-owner")
        validate_resolution_batch(batch)
    except (ValueError, TypeError, KeyError) as error:
        raise OwnerJournalIntegrityError(
            "scheduler_resolution_provenance", batch.identity.tenant_id, batch.identity.command_id
        ) from error
