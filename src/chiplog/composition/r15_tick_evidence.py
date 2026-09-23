"""Closed tick preimages and historical interpretation; never fresh authority."""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    DecideIntervalCommand,
    SchedulerContextRef,
)
from chiplog.capabilities.agent_loop.scheduler_preparation import (
    BATCH_SCHEMA,
    prepare_interval_request,
)
from chiplog.composition.r15_scheduler_registry import _canonical, _digest, _reference
from chiplog.composition.r15_tick_contracts import (
    TickClockObservation,
    TickClockSource,
    TickIssuanceEvidence,
    TickPolicy,
    TickPolicyAdoption,
    TickPolicyPreview,
    TickSourceCut,
)
from chiplog.platform._owner_publication_contracts import SingleOwnerBatch, WorkerAuthentication
from chiplog.platform.owner_decision_journal import OwnerJournalIntegrityError
from chiplog.platform.owner_publications import source_commands

if TYPE_CHECKING:
    from chiplog.platform.owner_publications import SelectedOwnerDecision

ISSUANCE_SCHEMA = "chiplog.scheduler.tick-issuance.v1"
PREPARATION_SCHEMA = "chiplog.scheduler.interval-preparation.v1"
SERVICE = "r15-hermetic-one-tick-v1"


def tick_id(act: str) -> str:
    return _reference(
        "r15-tick-act-v1",
        ("hermetic-tenant", "hermetic-principal", "scheduler.decide_interval", act),
    )


def tick_policy(source: TickClockSource) -> TickPolicy:
    return TickPolicy(
        schema_id="chiplog.scheduler.hermetic-tick-policy.v1",
        operation="scheduler.decide_interval",
        scope="ONE_DELIVERY_PRE_ROOT_MATERIALIZATION_NO_EXECUTION_NO_SEND",
        tenant_id="hermetic-tenant",
        principal_id="hermetic-principal",
        service_identity=SERVICE,
        registered_ingress="hermetic-ingress",
        clock_source=source,
    )


def adopted(adoption: TickPolicyAdoption) -> TickPolicyPreview:
    policy = TickPolicyPreview.model_validate_json(adoption.policy_bytes)
    if (
        policy.canonical_bytes() != adoption.policy_bytes
        or policy.adoption_act_id != adoption.adoption_act_id
    ):
        raise LoopRejected("noncanonical or mismatched exact tick adoption")
    if policy.policy != tick_policy(policy.policy.clock_source):
        raise LoopRejected("unregistered tick policy")
    return policy


def authority_head(adoption: TickPolicyAdoption, generations: bytes) -> str:
    evidence = json.loads(generations)
    return _reference(
        "r15-tick-authority-v1",
        {
            "adoption": adoption.model_dump(mode="json"),
            "trust_reference": evidence["trust_reference"],
            "authority_epoch_preimage": evidence["authority_epoch_preimage"],
            "broker_generation_preimage": evidence["broker_generation_preimage"],
            "runtime_graph_preimage": evidence["runtime_graph_preimage"],
        },
    )


def context_for(
    adoption: TickPolicyAdoption,
    generations: bytes,
    cut: TickSourceCut,
    clock: TickClockObservation,
    nonce: str,
) -> SchedulerContextRef:
    return SchedulerContextRef(
        tenant_id=cut.tenant_id,
        service_identity=SERVICE,
        session_id="hermetic-session",
        mandate_head=authority_head(adoption, generations),
        issuance_id="r15-tick-context:" + nonce,
        issuance_fingerprint=_digest(
            _canonical(
                {
                    "adoption": adoption.model_dump(mode="json"),
                    "generations": json.loads(generations),
                    "cut": cut.model_dump(mode="json"),
                    "clock": clock.model_dump(mode="json"),
                    "nonce": nonce,
                }
            )
        ),
    )


def enumeration(cut: TickSourceCut, preview: TickPolicyPreview) -> tuple[str, Present]:
    body = {"cut": cut.model_dump(mode="json"), "policy": preview.model_dump(mode="json")}
    frontier = _reference("r15-tick-enumeration-v1", body)
    return frontier, Present(head=frontier, fingerprint=_digest(_canonical(body)))


def cut_proof(context: SchedulerContextRef, cut: TickSourceCut) -> Present:
    body = {"context": context.model_dump(mode="json"), "cut": cut.model_dump(mode="json")}
    return Present(head=_reference("r15-tick-cut-v1", body), fingerprint=_digest(_canonical(body)))


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise ValueError("selected tick provenance: " + reason)


def validate_tick_batch(batch: SingleOwnerBatch) -> TickIssuanceEvidence:
    """Interpret only authenticated journal-selected input; construction grants nothing."""
    _require(
        batch.complete_batch_fingerprint
        == _digest(
            _canonical(batch.model_dump(mode="json", exclude={"complete_batch_fingerprint"}))
        ),
        "complete batch commitment",
    )
    auth = batch.authentication
    _require(isinstance(auth, WorkerAuthentication), "worker authentication absent")
    assert isinstance(auth, WorkerAuthentication)
    _require(auth.applicability_schema == ISSUANCE_SCHEMA, "unknown issuance schema")
    evidence = TickIssuanceEvidence.model_validate_json(auth.applicability_bytes)
    _require(
        auth.invocation.issuance_fingerprint
        == _digest(
            _canonical(
                {
                    "nonce": auth.invocation.issuance_id,
                    "identity": batch.identity.model_dump(mode="json"),
                    "command": batch.command.model_dump(mode="json"),
                    "evidence": auth.applicability_fingerprint,
                }
            )
        ),
        "invocation evidence commitment",
    )
    _require(
        evidence.canonical_bytes() == auth.applicability_bytes
        and auth.applicability_fingerprint == _digest(auth.applicability_bytes),
        "evidence bytes",
    )
    preview = adopted(evidence.adoption)
    _require(evidence.registered_policy == preview.policy, "adopted policy differs")
    clock, cut, request = evidence.clock, evidence.source_cut, evidence.preparation
    from chiplog.composition.r15_tick_clock_v1 import _clock_source
    from chiplog.composition.r15_tick_runtime import _manifest

    _require(
        clock.source == _clock_source(clock.source.source_id), "clock implementation registration"
    )
    _require(batch.expected == _manifest(request), "complete original read manifest")
    _require(clock.source == preview.policy.clock_source, "clock source differs")
    _require(
        clock.source.source_id in ("r15-unix-clock-v1", "r15-evaluation-clock-v1")
        and clock.source.deployment_profile == "hermetic-offline",
        "unknown clock profile",
    )
    _require(
        clock.reading.monotonic_ns < clock.valid_until_monotonic_ns,
        "clock observation expired at capture",
    )
    generations = json.loads(evidence.generation_preimages)
    trust = generations["trust_reference"]
    authentication = generations["authentication"]
    _require(
        evidence.trust_request_frame == _canonical(authentication["request"])
        and evidence.trust_response_frame == _canonical(authentication["response"])
        and evidence.trust_observation_bytes == _canonical(authentication["observation"]),
        "original trust preimages differ",
    )
    trust_request, trust_response = authentication["request"], authentication["response"]
    _require(
        clock.valid_until_monotonic_ns <= trust_request["budget"]["absolute_deadline_ns"],
        "clock observation exceeds original authentication deadline",
    )
    trust_result = json.loads(base64.b64decode(authentication["result_base64"], validate=True))
    _require(
        trust_request["operation_id"] == "deployment_trust.authenticate"
        and trust_response["request_id"] == trust_request["request_id"]
        and trust_response["responder"] == trust_request["callee"],
        "trust IPC identity",
    )
    _require(
        base64.b64decode(trust_response["canonical_payload_base64"], validate=True)
        == base64.b64decode(authentication["result_base64"], validate=True),
        "trust response payload",
    )
    _require(
        trust_result["disposition"] == "VALID"
        and json.loads(base64.b64decode(trust_result["reference_bytes"], validate=True)) == trust,
        "trust decision/reference",
    )
    _require(
        (trust["tenant_id"], trust["principal_id"], trust["contour"], trust["source_head"])
        == ("hermetic-tenant", "hermetic-principal", "CLI", "local"),
        "principal scope",
    )
    worker = generations["broker_generation_preimage"]["agent_session"]
    _require(
        clock.broker_epoch == str(worker["broker_epoch"])
        and clock.broker_session == worker["session_id"]
        and auth.invocation.broker_epoch == str(worker["broker_epoch"])
        and auth.invocation.broker_session == worker["session_id"]
        and auth.invocation.runtime_generation == worker["generation_id"]
        and auth.invocation.operation_subject == batch.identity.command_id,
        "clock broker identity",
    )
    context = context_for(
        evidence.adoption, evidence.generation_preimages, cut, clock, evidence.issuance_nonce
    )
    _require(
        request.context == context and request.snapshot.cut.authorized_context == context,
        "context preimage",
    )
    command = DecideIntervalCommand.model_validate_json(request.command_bytes)
    _require(
        command.canonical_bytes() == request.command_bytes
        and command.identity.command_id == tick_id(evidence.adoption.adoption_act_id)
        and batch.identity.command_id == command.identity.command_id
        and batch.identity.command_fingerprint == _digest(request.command_bytes),
        "command identity",
    )
    _require(
        command.boundary.cutoff_due_coordinate.canonical_coordinate == str(clock.reading.unix_ns)
        and command.boundary.cutoff_due_coordinate.coordinate_policy_version
        == clock.source.coordinate_codec,
        "cutoff observation",
    )
    frontier, proof = enumeration(cut, preview)
    _require(
        command.boundary.enumeration_frontier == frontier
        and request.snapshot.enumeration_frontier == frontier
        and request.snapshot.enumeration_proof == proof,
        "enumeration provenance",
    )
    _require(
        command.boundary.schedule_definition_head == preview.schedule
        and command.boundary.missed_occurrence_policy_head == preview.missed_policy
        and command.bound_head == preview.bound,
        "adopted heads",
    )
    fence = command.publication_fence
    _require(
        fence == request.snapshot.publication_fence
        and fence.scheduler_authority_head == context.mandate_head
        and fence.broker_generation
        == _reference("r15-broker-generation-v1", generations["broker_generation_preimage"])
        and fence.runtime_generation
        == _reference("r15-runtime-graph-v1", generations["runtime_graph_preimage"]),
        "authority fence",
    )
    owner_cut = request.snapshot.cut
    _require(
        owner_cut.authority_proof == cut_proof(context, cut)
        and owner_cut.submission_id == "r15-tick-submission:" + evidence.issuance_nonce
        and owner_cut.authorized_command_fingerprint == _digest(request.command_bytes)
        and (owner_cut.tenant_id, owner_cut.tenant_frontier, owner_cut.materialization_commitment)
        == (cut.tenant_id, cut.tenant_frontier, cut.materialization_commitment),
        "writer cut",
    )
    _require(
        batch.command.owner == "agent_loop"
        and batch.command.schema_id == PREPARATION_SCHEMA
        and batch.command.canonical_bytes == request.canonical_bytes()
        and batch.command.fingerprint == _digest(request.canonical_bytes())
        and batch.operation == request.operation == "scheduler.decide_interval",
        "owner command",
    )
    sent, response = (
        json.loads(evidence.owner_request_frame),
        json.loads(evidence.owner_response_frame),
    )
    _require(
        sent["operation_id"] == "scheduler.prepare_interval"
        and sent["schema_id"] == PREPARATION_SCHEMA
        and sent["callee"] == worker
        and response["request_id"] == sent["request_id"]
        and response["responder"] == sent["callee"]
        and response["schema_id"] == BATCH_SCHEMA
        and base64.b64decode(sent["canonical_payload_base64"], validate=True)
        == request.canonical_bytes(),
        "owner IPC identity",
    )
    prepared = prepare_interval_request(request)
    _require(
        base64.b64decode(response["canonical_payload_base64"], validate=True)
        == prepared.canonical_bytes(),
        "complete original owner output",
    )
    _require(
        tuple(
            (r.owner, r.record_kind, r.record_id, r.schema_id, r.canonical_bytes, r.fingerprint)
            for r in batch.complete_records
        )
        == tuple(
            (
                "agent_loop",
                r.record_kind,
                r.record_id,
                r.schema_id,
                base64.b64decode(r.canonical_base64, validate=True),
                r.fingerprint,
            )
            for r in prepared.records
        ),
        "selected records",
    )
    return evidence


def validate_selected_tick(decision: SelectedOwnerDecision) -> None:
    batch = decision.prepared.request
    commands = source_commands(batch)
    embedded = []
    for command in commands:
        try:
            body = json.loads(command.canonical_bytes)
            embedded.append(
                isinstance(body, dict) and body.get("operation") == "scheduler.decide_interval"
            )
        except ValueError, UnicodeDecodeError:
            embedded.append(False)
    relevant = (
        batch.operation == "scheduler.decide_interval"
        or getattr(batch.authentication, "applicability_schema", None) == ISSUANCE_SCHEMA
        or any(command.schema_id == PREPARATION_SCHEMA for command in commands)
        or any(embedded)
        or any(
            record.schema_id
            in {
                "chiplog.scheduler.interval-parent.v1",
                "chiplog.scheduler.interval-result.v1",
                "chiplog.scheduler.overflow-hold.v1",
            }
            for record in batch.complete_records
        )
    )
    if not relevant:
        return
    try:
        _require(isinstance(batch, SingleOwnerBatch), "not single owner")
        assert isinstance(batch, SingleOwnerBatch)
        validate_tick_batch(batch)
    except (ValueError, TypeError, KeyError) as error:
        raise OwnerJournalIntegrityError(
            "scheduler_tick_provenance", batch.identity.tenant_id, batch.identity.command_id
        ) from error
