"""Frozen V2b historical evaluator; no publication API."""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Literal, cast

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.scheduler_contracts import SchedulerLineageView
from chiplog.capabilities.agent_loop.scheduler_execution_contracts import (
    SelectedScheduledSystemMandateV2,
)
from chiplog.capabilities.agent_loop.scheduler_materialization import (
    ScheduledBatchPrimitiveDomainV1,
)
from chiplog.capabilities.deployment_trust._r7_process import _evaluate, _TrustOwnerCall
from chiplog.composition.r15_lease_acquire import (
    EXECUTION_SERVICE,
    LeaseAcquireEvidence,
    validate_historical_acquire,
)
from chiplog.composition.r15_lease_policy import (
    PreparedLeasePolicyAdoption,
    policy_for_execution,
    validate_historical_lease_policy,
)
from chiplog.composition.r15_lease_v2b_canonical import _canonical, _digest
from chiplog.composition.r15_tick_clock_v1 import _clock_source
from chiplog.platform._owner_publication_contracts import (
    OwnerRecordBytes,
    RegisteredPublication,
    SingleOwnerBatch,
    WorkerAuthentication,
)

if TYPE_CHECKING:
    from chiplog.platform.owner_publications import SelectedOwnerDecision

_POLICY_OPERATION: Literal["r15.adopt_lease_execution_policy"] = "r15.adopt_lease_execution_policy"

_POLICY_SCHEMA = "chiplog.r15.lease-policy-adoption.v1"

_EVIDENCE_SCHEMA = "chiplog.r15.lease-publication-evidence.v1"

_EXECUTION_SESSION = "r15-hermetic-execution-session-v1"

_ADMIN_POLICY_BYTES = _canonical(
    {
        "schema": "chiplog.r15.lease-admin-policy.v1",
        "tenant": "hermetic-tenant",
        "principal": "hermetic-principal",
        "operation": _POLICY_OPERATION,
        "service": EXECUTION_SERVICE,
        "session": _EXECUTION_SESSION,
        "max_lease_ns": 60_000_000_000,
        "authentication_budget_ns": 30_000_000_000,
    }
)


def _validate_root_frames(
    frames: tuple[bytes, ...],
    current: SchedulerLineageView,
    mandate: SelectedScheduledSystemMandateV2,
) -> None:
    matches = []
    for raw in frames:
        batch = SingleOwnerBatch.model_validate_json(raw)
        if (
            _canonical(batch.model_dump(mode="json")) != raw
            or batch.operation != "scheduler.decide_interval"
        ):
            raise LoopRejected("lease root source is not canonical selected tick")
        for row in batch.complete_records:
            if row.record_kind == "batch-primitive":
                primitive = ScheduledBatchPrimitiveDomainV1.model_validate_json(row.canonical_bytes)
                if primitive.root_id == current.lineage.root_id:
                    matches.append(primitive)
    if len(matches) != 1:
        raise LoopRejected("lease root primitive provenance differs")
    primitive = matches[0]
    scope = mandate.mandate.scope
    schedule, missed = (
        primitive.boundary.schedule_definition_head,
        primitive.boundary.missed_occurrence_policy_head,
    )
    coordinates = [
        member.due_coordinate
        for member in primitive.manifest.members
        if primitive.subject.kind == "COALESCED"
        or member.occurrence_id == primitive.subject.occurrence_id
    ]
    if (
        primitive.subject != current.lineage.subject
        or primitive.initial_run_id != current.lineage.initial_run_id
        or (schedule.head, schedule.fingerprint)
        != (scope.schedule_head.revision.head, scope.schedule_head.revision.fingerprint)
        or (missed.head, missed.fingerprint)
        != (scope.missed_policy_head.revision.head, scope.missed_policy_head.revision.fingerprint)
        or (primitive.bound_head.head_id, _digest(primitive.bound_head.canonical_bytes()))
        != (scope.bound_head.revision.head, scope.bound_head.revision.fingerprint)
        or not coordinates
        or any(
            not int(scope.horizon.first_eligible_due_coordinate)
            <= int(coordinate.canonical_coordinate)
            <= int(scope.horizon.last_eligible_due_coordinate)
            for coordinate in coordinates
        )
    ):
        raise LoopRejected("lease root primitive exceeds selected mandate applicability")


def _validate_authentication(batch: SingleOwnerBatch, body: dict[str, object]) -> None:
    auth = batch.authentication
    if not isinstance(auth, WorkerAuthentication):
        raise LoopRejected("lease historical worker authentication absent")
    generation = json.loads(
        base64.b64decode(cast(str, body["generation_preimages"]), validate=True)
    )
    authentication = generation["authentication"]
    sent, response = authentication["request"], authentication["response"]
    call_raw = base64.b64decode(sent["canonical_payload_base64"], validate=True)
    call_body = json.loads(call_raw)
    observation = authentication["observation"]
    call = _TrustOwnerCall(
        mode=call_body["mode"],
        request_bytes=base64.b64decode(call_body["request_bytes"], validate=True),
        snapshot_bytes=base64.b64decode(call_body["snapshot_bytes"], validate=True),
    )
    result = _evaluate(call)
    trust = generation["trust_reference"]
    worker = generation["broker_generation_preimage"]["agent_session"]
    proof = auth.invocation
    request = json.loads(call.request_bytes)
    if (
        call.mode != "AUTHENTICATE"
        or call.canonical_bytes() != call_raw
        or call.snapshot_bytes != base64.b64decode(observation["snapshot_base64"], validate=True)
        or sent["operation_id"] != "deployment_trust.authenticate"
        or response["disposition"] != "SUCCESS"
        or response["request_id"] != sent["request_id"]
        or response["responder"] != sent["callee"]
        or sent["callee"]["owner_id"] != "deployment_trust"
        or result.disposition != "VALID"
        or result.reference_bytes is None
        or result.canonical_bytes()
        != base64.b64decode(response["canonical_payload_base64"], validate=True)
        or result.canonical_bytes()
        != base64.b64decode(authentication["result_base64"], validate=True)
        or json.loads(result.reference_bytes) != trust
        or (trust["tenant_id"], trust["principal_id"], trust["contour"], trust["source_head"])
        != ("hermetic-tenant", "hermetic-principal", "CLI", "local")
        or request["session_id"] != "hermetic-session"
        or request["credential_id"] != "hermetic-credential"
        or (
            proof.broker_epoch,
            proof.broker_session,
            proof.runtime_generation,
            proof.operation_subject,
        )
        != (
            str(worker["broker_epoch"]),
            worker["session_id"],
            worker["generation_id"],
            batch.identity.command_id,
        )
        or proof.issuance_fingerprint
        != _digest(
            _canonical(
                [
                    proof.issuance_id,
                    batch.identity.model_dump(mode="json"),
                    batch.command.model_dump(mode="json"),
                    auth.applicability_fingerprint,
                ]
            )
        )
        or batch.complete_batch_fingerprint
        != _digest(
            _canonical(batch.model_dump(mode="json", exclude={"complete_batch_fingerprint"}))
        )
    ):
        raise LoopRejected("lease historical trust IPC or invocation provenance differs")
    if batch.operation == _POLICY_OPERATION:
        prepared = PreparedLeasePolicyAdoption.model_validate_json(_canonical(body["prepared"]))
        record = validate_historical_lease_policy(prepared)
        source = record.source
        if (
            source.trust_request_frame != _canonical(sent)
            or source.trust_response_frame != _canonical(response)
            or source.trust_observation_bytes != _canonical(observation)
            or record.policy.clock_source != _clock_source(record.policy.clock_source.source_id)
        ):
            raise LoopRejected("lease selected policy original trust frames differ")
    else:
        evidence = validate_historical_acquire(
            base64.b64decode(cast(str, body["acquire"]), validate=True)
        )
        execution = {
            "execution_session": _EXECUTION_SESSION,
            "broker_epoch": worker["broker_epoch"],
            "generation": worker["generation_id"],
            "policy": evidence.source.policy.adoption.model_dump(mode="json"),
        }
        if evidence.source.authentication_frames != (
            _canonical(sent),
            _canonical(response),
            _canonical(execution),
        ):
            raise LoopRejected("lease selected execution authentication frames differ")


def validate_selected_lease(
    batch: RegisteredPublication,
) -> PreparedLeasePolicyAdoption | LeaseAcquireEvidence | None:
    if batch.operation not in (_POLICY_OPERATION, "scheduler.acquire"):
        return None
    if (
        not isinstance(batch, SingleOwnerBatch)
        or not isinstance(batch.authentication, WorkerAuthentication)
        or batch.authentication.applicability_schema != _EVIDENCE_SCHEMA
        or _digest(batch.authentication.applicability_bytes)
        != batch.authentication.applicability_fingerprint
    ):
        raise LoopRejected("lease publication evidence differs")
    body = json.loads(batch.authentication.applicability_bytes)
    if _canonical(body) != batch.authentication.applicability_bytes:
        raise LoopRejected("lease publication evidence is not canonical")
    _validate_authentication(batch, body)

    prepared: PreparedLeasePolicyAdoption | LeaseAcquireEvidence
    schema: str
    if batch.operation == _POLICY_OPERATION:
        prepared = PreparedLeasePolicyAdoption.model_validate_json(_canonical(body["prepared"]))
        record = validate_historical_lease_policy(prepared)
        if (
            record.policy.execution_session_id != _EXECUTION_SESSION
            or record.source.administrative_policy
            != Present(
                head="r15-lease-admin-policy-v1:" + _digest(_ADMIN_POLICY_BYTES),
                fingerprint=_digest(_ADMIN_POLICY_BYTES),
            )
        ):
            raise LoopRejected("lease administrative policy differs")
        raw = record.request.canonical_bytes()
        expected_record = OwnerRecordBytes(
            owner="broker_scheduler",
            record_kind="r15.lease-policy-adoption",
            record_id=prepared.record_id,
            schema_id=_POLICY_SCHEMA,
            canonical_bytes=prepared.canonical_record_bytes,
            fingerprint=prepared.fingerprint,
        )
        command_id, schema = prepared.command_id, record.request.schema_id
    else:
        prepared = validate_historical_acquire(
            base64.b64decode(cast(str, body["acquire"]), validate=True)
        )
        policy = PreparedLeasePolicyAdoption.model_validate_json(_canonical(body["policy"]))
        policy_for_execution(
            policy,
            prepared.source.policy.adoption,
            prepared.source.context,
            prepared.source.mandate,
        )
        _validate_root_frames(
            prepared.source.selected_root_frames, prepared.source.current, prepared.source.mandate
        )
        if prepared.request.context.session_id != _EXECUTION_SESSION:
            raise LoopRejected("lease selected execution session differs")
        raw = prepared.request.canonical_bytes()
        row = prepared.prepared
        expected_record = OwnerRecordBytes(
            owner="agent_loop",
            record_kind=row.record_kind,
            record_id=row.record_id,
            schema_id=row.schema_id,
            canonical_bytes=row.canonical_record_bytes,
            fingerprint=row.fingerprint,
        )
        if prepared.request.snapshot.issued is None:
            raise LoopRejected("lease selected clock issuance missing")
        command_id, schema = (
            prepared.request.snapshot.issued.proof.command_id,
            "chiplog.scheduler.lease-preparation.v1",
        )
        sent, response = body["owner_request"], body["owner_response"]
        if (
            sent["operation_id"] != "scheduler.prepare_lease"
            or response["request_id"] != sent["request_id"]
            or response["responder"] != sent["callee"]
            or base64.b64decode(response["canonical_payload_base64"], validate=True)
            != row.canonical_bytes()
            or base64.b64decode(sent["canonical_payload_base64"], validate=True) != raw
        ):
            raise LoopRejected("lease exact owner frames differ")
    if (
        batch.command.canonical_bytes != raw
        or batch.command.schema_id != schema
        or batch.command.fingerprint != _digest(raw)
        or batch.identity.command_id != command_id
        or batch.complete_records != (expected_record,)
    ):
        raise LoopRejected("lease selected command or exact output differs")
    return prepared


def validate_selected_lease_decision(
    decision: SelectedOwnerDecision, decisions: tuple[SelectedOwnerDecision, ...]
) -> None:
    prepared = validate_selected_lease(decision.prepared.request)
    if prepared is None:
        return
    earlier = tuple(
        item for item in decisions if item.tenant_commit_sequence < decision.tenant_commit_sequence
    )
    if isinstance(prepared, PreparedLeasePolicyAdoption):
        record = validate_historical_lease_policy(prepared)
        mandate = record.source.mandate
        if (
            record.source.cut.tenant_frontier != decision.prepared.request.expected.tenant_frontier
            or record.source.cut.materialization_commitment
            != decision.prepared.predecessor_commitment
            or record.source.authority_head
            != Present(
                head=decision.prepared.request.expected.registry_head,
                fingerprint=decision.prepared.request.expected.registry_fingerprint,
            )
        ):
            raise LoopRejected("selected lease policy source cut differs from selected predecessor")
    else:
        authentication = decision.prepared.request.authentication
        if not isinstance(authentication, WorkerAuthentication):
            raise LoopRejected("lease selected invocation type differs")
        evidence = json.loads(authentication.applicability_bytes)
        policy = PreparedLeasePolicyAdoption.model_validate_json(_canonical(evidence["policy"]))
        matches = [
            item
            for item in earlier
            if item.prepared.request.operation == _POLICY_OPERATION
            and validate_selected_lease(item.prepared.request) == policy
        ]
        if len(matches) != 1:
            raise LoopRejected("lease embedded policy has no exact preceding selected adoption")
        mandate = prepared.source.mandate
        for raw in prepared.source.selected_root_frames:
            matches = [
                item
                for item in earlier
                if item.prepared.request.operation == "scheduler.decide_interval"
                and _canonical(item.prepared.request.model_dump(mode="json")) == raw
            ]
            if len(matches) != 1:
                raise LoopRejected("lease root frame has no exact preceding selected tick")
    matches = [
        item
        for item in earlier
        if item.prepared.request.operation == "scheduler.issue_mandate"
        and Present(head=item.decision_head, fingerprint=item.decision_fingerprint)
        == mandate.issuance_observation.revision
        and any(
            row.record_id == mandate.mandate_head.head
            and row.fingerprint == mandate.mandate_head.fingerprint
            and row.canonical_bytes == mandate.canonical_mandate_bytes
            for row in item.prepared.request.complete_records
        )
    ]
    if len(matches) != 1:
        raise LoopRejected("lease mandate has no exact preceding selected issuance")
