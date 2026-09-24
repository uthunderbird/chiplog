"""Strict, non-authorizing H1 completion issuance decoder.

This seam proves only that a retained H1 applicability value is canonical and
reconstructs the fixed owner batch from its typed assembly.  Historical source
selection remains an authority operation and deliberately fails closed until
the runtime exposes a raw selected-source reader.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from chiplog.capabilities.deployment_trust.h1_broker_evidence_contracts import (
    H1OwnerCandidateCallV1,
    H1OwnerCandidateV1,
    H1OwnerCurrentCallV1,
    H1OwnerCurrentCandidateV1,
)
from chiplog.capabilities.effects.dispatch_authority_contracts import DispatchObservationDTO
from chiplog.composition.common_execution_driver_contracts import DriveInputRequestV1
from chiplog.composition.completion_publication_contracts import (
    PrepareH1CompleteAcceptanceAssemblyV1,
    validate_h1_complete_acceptance_batch,
)
from chiplog.composition.h1_historical_selected_sources import verify_h1_historical_sources
from chiplog.composition.r7_planning import ObservedTrustCall
from chiplog.composition.r14_execution_completion_records import (
    RetainedCompleteAcceptanceExchangeV1,
)
from chiplog.composition.r14_execution_inbox_records import RetainedInboxExecutionInitialization
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    CompleteDeliveryBatchV2,
    InvocationProofRef,
)
from chiplog.platform.broker import (
    BrokerSession,
    PublicPortCall,
    PublicPortResult,
    PublicPortSuccess,
)
from chiplog.platform.r7_trust import TrustOwnerCall

if TYPE_CHECKING:
    from chiplog.composition.r14_runtime import R14PlanningRuntime


SCHEMA = "chiplog.composition.h1-completion-issuance.v1"


class H1CompletionOwnerExchangeV1(DispatchObservationDTO):
    """One retained public owner exchange, including its actual wire frames."""

    role: Literal[
        "completion", "conversation", "effects", "terminal_work", "scope_issue", "scope_current"
    ]
    sent: PublicPortCall
    returned: PublicPortResult
    sent_at_ns: int = Field(ge=0, le=2**64 - 1)
    returned_at_ns: int = Field(ge=0, le=2**64 - 1)


class H1CompletionCaptureV1(DispatchObservationDTO):
    """Invocation-local observations retained by a private issuer.

    Its fields are evidence claims.  They are not an authority substitute and
    are intentionally not consulted as historical source selection.
    """

    principal_bytes: bytes = Field(min_length=1)
    invocation: InvocationProofRef
    observed: ObservedTrustCall
    database_id: str = Field(min_length=1)
    physical_path: str = Field(min_length=1)
    physical_device: int = Field(ge=0, le=2**64 - 1)
    physical_inode: int = Field(ge=0, le=2**64 - 1)
    broker_epoch: int = Field(ge=0, le=2**64 - 1)
    runtime_generation: str = Field(min_length=1)
    broker_session_id: str = Field(min_length=1)
    worker_session_id: str = Field(min_length=1)
    sessions: tuple[BrokerSession, ...] = Field(min_length=1)
    observed_time_ns: int = Field(ge=0, le=2**64 - 1)
    clock_epoch: str = Field(min_length=1)
    valid_until_ns: int = Field(ge=0, le=2**64 - 1)
    expected: AuthoritativeReadManifest


class H1CompletionIssuanceV1(DispatchObservationDTO):
    schema_id: Literal["chiplog.composition.h1-completion-issuance.v1"] = (
        "chiplog.composition.h1-completion-issuance.v1"
    )
    assembly: PrepareH1CompleteAcceptanceAssemblyV1
    capture: H1CompletionCaptureV1
    owner_exchanges: tuple[H1CompletionOwnerExchangeV1, ...] = Field(min_length=4, max_length=4)
    scope_issue_exchange: H1CompletionOwnerExchangeV1
    scope_current_exchange: H1CompletionOwnerExchangeV1


def _require_success_exchange(
    exchange: H1CompletionOwnerExchangeV1,
    *,
    operation: str,
    request_schema: str,
    request_bytes: bytes,
    response_schema: str,
    response_bytes: bytes,
    callee_owner: str,
    capture: H1CompletionCaptureV1,
) -> None:
    sent = exchange.sent
    if (
        sent.operation_id != operation
        or sent.schema_id != request_schema
        or sent.canonical_payload != request_bytes
        or sent.caller.owner_id != "broker"
        or sent.callee.owner_id != callee_owner
        or sent.callee not in capture.sessions
    ):
        raise ValueError("H1 completion owner exchange differs from its pinned owner input")
    returned = exchange.returned
    if not isinstance(returned, PublicPortSuccess):
        raise ValueError("H1 completion owner exchange did not return success")
    if (
        returned.request_id != sent.request_id
        or returned.responder != sent.callee
        or returned.schema_id != response_schema
        or returned.canonical_payload != response_bytes
    ):
        raise ValueError("H1 completion owner exchange response differs from pinned result")


def _require_scope_issue_exchange(
    exchange: H1CompletionOwnerExchangeV1,
    assembly: PrepareH1CompleteAcceptanceAssemblyV1,
    capture: H1CompletionCaptureV1,
) -> None:
    sent = exchange.sent
    if (
        sent.operation_id != "deployment_trust.issue_hermetic_output_scope"
        or sent.schema_id != "chiplog.deployment-trust.owner-call.v1"
        or sent.caller.owner_id != "broker"
        or sent.callee.owner_id != "deployment_trust"
        or sent.callee not in capture.sessions
    ):
        raise ValueError("H1 scope issue exchange has a substituted route")
    wire = TrustOwnerCall.model_validate_json(sent.canonical_payload)
    if (
        wire.canonical_bytes() != sent.canonical_payload
        or wire.mode != "ISSUE_HERMETIC_OUTPUT_SCOPE_V1"
        or wire.snapshot_bytes != capture.observed.observation.snapshot_bytes
    ):
        raise ValueError("H1 scope issue exchange has a noncanonical outer request")
    call = H1OwnerCandidateCallV1.model_validate_json(wire.request_bytes)
    if (
        call.canonical_bytes() != wire.request_bytes
        or hashlib.sha256(wire.snapshot_bytes).hexdigest() != call.evidence.trust_snapshot_digest
    ):
        raise ValueError("H1 scope issue exchange has a noncanonical candidate request")
    selected = assembly.ordered_effects[0].owner_call.request.selected_scope
    retained = assembly.ordered_effects[0].owner_call.request.retained_origin
    if call.evidence.retained != retained:
        raise ValueError("H1 scope issue exchange differs from retained H0/R17 origin")
    returned = exchange.returned
    if not isinstance(returned, PublicPortSuccess):
        raise ValueError("H1 scope issue exchange did not return success")
    if (
        returned.request_id != sent.request_id
        or returned.responder != sent.callee
        or returned.schema_id != "chiplog.deployment-trust.issue-hermetic-output-scope-result.v1"
    ):
        raise ValueError("H1 scope issue exchange response correlation differs")
    candidate = H1OwnerCandidateV1.model_validate_json(returned.canonical_payload)
    if (
        candidate.canonical_bytes() != returned.canonical_payload
        or candidate.scope != selected.scope
    ):
        raise ValueError("H1 scope issue exchange response differs from selected scope")
    candidate.check_pinned_call(call)


def _require_scope_current_exchange(
    exchange: H1CompletionOwnerExchangeV1,
    assembly: PrepareH1CompleteAcceptanceAssemblyV1,
    capture: H1CompletionCaptureV1,
) -> None:
    sent = exchange.sent
    if (
        sent.operation_id != "deployment_trust.read_current_hermetic_output_scope"
        or sent.schema_id != "chiplog.deployment-trust.owner-call.v1"
        or sent.caller.owner_id != "broker"
        or sent.callee.owner_id != "deployment_trust"
        or sent.callee not in capture.sessions
    ):
        raise ValueError("H1 scope current exchange has a substituted route")
    wire = TrustOwnerCall.model_validate_json(sent.canonical_payload)
    if (
        wire.canonical_bytes() != sent.canonical_payload
        or wire.mode != "READ_CURRENT_HERMETIC_OUTPUT_SCOPE_V1"
        or wire.snapshot_bytes != capture.observed.observation.snapshot_bytes
    ):
        raise ValueError("H1 scope current exchange has a noncanonical outer request")
    call = H1OwnerCurrentCallV1.model_validate_json(wire.request_bytes)
    if call.canonical_bytes() != wire.request_bytes:
        raise ValueError("H1 scope current exchange has a noncanonical candidate request")
    selected = assembly.ordered_effects[0].owner_call.request.selected_scope
    if call.read_request_bytes != selected.current_request.canonical_bytes():
        raise ValueError("H1 scope current exchange differs from selected scope request")
    returned = exchange.returned
    if not isinstance(returned, PublicPortSuccess):
        raise ValueError("H1 scope current exchange did not return success")
    if (
        returned.request_id != sent.request_id
        or returned.responder != sent.callee
        or returned.schema_id != "chiplog.deployment-trust.current-hermetic-output-scope-result.v1"
    ):
        raise ValueError("H1 scope current exchange response correlation differs")
    candidate = H1OwnerCurrentCandidateV1.model_validate_json(returned.canonical_payload)
    if (
        candidate.canonical_bytes() != returned.canonical_payload
        or candidate.current != selected.current_result
    ):
        raise ValueError("H1 scope current exchange response differs from selected scope")
    candidate.check_pinned_call(call)


def _require_exchange_shape(value: H1CompletionIssuanceV1) -> None:
    roles = tuple(item.role for item in value.owner_exchanges)
    if roles != ("completion", "conversation", "effects", "terminal_work"):
        raise ValueError("H1 completion owner exchanges have a noncanonical role order")
    if (
        value.scope_issue_exchange.role != "scope_issue"
        or value.scope_current_exchange.role != "scope_current"
    ):
        raise ValueError("H1 completion scope exchanges have substituted roles")
    exchanges = (*value.owner_exchanges, value.scope_issue_exchange, value.scope_current_exchange)
    for exchange in exchanges:
        if exchange.returned_at_ns < exchange.sent_at_ns:
            raise ValueError("H1 completion owner exchange returns before it is sent")
    assembly = value.assembly
    completion, conversation, effects, terminal_work = value.owner_exchanges
    _require_success_exchange(
        completion,
        operation="agent_loop.prepare_first_path_completion",
        request_schema="chiplog.execution.first-path-completion.v2",
        request_bytes=assembly.original_completion_request.canonical_bytes(),
        response_schema="chiplog.agent-loop.prepared-execution-completion-result.v1",
        response_bytes=assembly.prepared_completion.canonical_bytes(),
        callee_owner="agent_loop",
        capture=value.capture,
    )
    _require_success_exchange(
        conversation,
        operation="projections.prepare_conversation_completion",
        request_schema="chiplog.conversation.prepare-completion.v1",
        request_bytes=assembly.conversation_request.canonical_json_bytes(),
        response_schema="chiplog.conversation.prepared-completion-result.v1",
        response_bytes=assembly.conversation_result.canonical_json_bytes(),
        callee_owner="projections",
        capture=value.capture,
    )
    _require_success_exchange(
        effects,
        operation="effects.prepare_h1_local_commentary",
        request_schema="chiplog.effects.h1-local-commentary-owner-call.v1",
        request_bytes=assembly.ordered_effects[0].owner_call.canonical_bytes(),
        response_schema="chiplog.effects.prepared-h1-local-commentary.v1",
        response_bytes=assembly.ordered_effects[0].owner_result.canonical_bytes(),
        callee_owner="effects",
        capture=value.capture,
    )
    _require_success_exchange(
        terminal_work,
        operation="agent_loop.prepare_terminal_work",
        request_schema="chiplog.agent-loop.prepare-terminal-work.v1",
        request_bytes=assembly.terminal_work_request.canonical_bytes(),
        response_schema="chiplog.agent-loop.prepared-post-terminal-work-result.v1",
        response_bytes=assembly.terminal_work_result.canonical_bytes(),
        callee_owner="agent_loop",
        capture=value.capture,
    )
    _require_scope_issue_exchange(value.scope_issue_exchange, assembly, value.capture)
    _require_scope_current_exchange(value.scope_current_exchange, assembly, value.capture)


def _h1_identity(assembly: PrepareH1CompleteAcceptanceAssemblyV1) -> tuple[str, str]:
    """Derive the stable completion identity from the retained H0 envelope."""
    retained = assembly.ordered_effects[0].owner_call.request.retained_origin
    try:
        envelope = json.loads(retained.initialization_envelope_bytes)
        initialization = RetainedInboxExecutionInitialization.model_validate_json(
            envelope["inbox_initialization"]
        )
        driver = DriveInputRequestV1.model_validate_json(initialization.driver_request_bytes)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("H1 completion retained H0 initialization is not decodable") from error
    if (
        initialization.canonical_bytes() != envelope["inbox_initialization"].encode()
        or driver.canonical_bytes() != initialization.driver_request_bytes
        or driver.original_driver_command_fingerprint() != initialization.driver_request_fingerprint
        or driver.identity.tenant_id != assembly.original_completion_request.source.tenant_id
    ):
        raise ValueError("H1 completion retained H0 driver identity differs")
    source = assembly.original_completion_request.source
    preimage = {
        "domain": "chiplog.composition.h1-completion-identity.v1",
        "tenant_id": source.tenant_id,
        "principal_id": source.selected_admitted_input.principal_id,
        "original_driver_identity": driver.identity.model_dump(mode="json"),
        "original_driver_fingerprint": initialization.driver_request_fingerprint,
        "run_id": assembly.original_completion_request.run.run_id,
        "selected_seal": source.selected_response_seal.model_dump(mode="json"),
        "operation": "agent_loop.complete_acceptance.v2",
    }
    fingerprint = hashlib.sha256(
        json.dumps(preimage, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    return "h1-completion:" + fingerprint, fingerprint


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _require_invocation(
    capture: H1CompletionCaptureV1,
    batch: CompleteDeliveryBatchV2,
    *,
    principal_id: str,
) -> None:
    proof = capture.invocation
    observed = capture.observed
    reference = observed.result.reference_bytes
    if reference is None or capture.principal_bytes != reference:
        raise ValueError("H1 completion invocation differs from authenticated principal")
    try:
        principal = json.loads(reference)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("H1 completion authenticated principal is not decodable") from error
    if _canonical(principal) != reference or not isinstance(principal, dict):
        raise ValueError("H1 completion authenticated principal is noncanonical")
    caller = observed.request.caller
    if (
        principal.get("tenant_id") != batch.identity.tenant_id
        or principal.get("principal_id") != principal_id
        or batch.authentication.invocation != proof
        or proof.operation_subject != batch.identity.command_id
        or proof.broker_epoch != str(capture.broker_epoch)
        or proof.broker_session != capture.broker_session_id
        or proof.runtime_generation != capture.runtime_generation
        or (caller.broker_epoch, caller.session_id, caller.generation_id)
        != (capture.broker_epoch, capture.broker_session_id, capture.runtime_generation)
    ):
        raise ValueError("H1 completion invocation binding differs")
    observed_json = capture.model_dump(mode="json")["observed"]
    expected_fingerprint = hashlib.sha256(
        _canonical(
            {
                "domain": "chiplog.composition.h1-completion-invocation.v1",
                "issuance_id": proof.issuance_id,
                "identity": batch.identity.model_dump(mode="json"),
                "principal_fingerprint": hashlib.sha256(capture.principal_bytes).hexdigest(),
                "observed_fingerprint": hashlib.sha256(_canonical(observed_json)).hexdigest(),
            }
        )
    ).hexdigest()
    if proof.issuance_fingerprint != expected_fingerprint:
        raise ValueError("H1 completion invocation issuance fingerprint differs")


def _decode_value(batch: CompleteDeliveryBatchV2) -> H1CompletionIssuanceV1:
    if type(batch) is not CompleteDeliveryBatchV2:
        raise TypeError("H1 completion issuance requires CompleteDeliveryBatchV2")
    authentication = batch.authentication
    if (
        authentication.kind != "WORKER"
        or authentication.applicability_schema != SCHEMA
        or hashlib.sha256(authentication.applicability_bytes).hexdigest()
        != authentication.applicability_fingerprint
    ):
        raise ValueError("H1 completion batch lacks its exact issuance applicability")
    value = H1CompletionIssuanceV1.model_validate_json(authentication.applicability_bytes)
    if value.canonical_bytes() != authentication.applicability_bytes:
        raise ValueError("H1 completion issuance is noncanonical")
    _require_exchange_shape(value)
    command_id, command_fingerprint = _h1_identity(value.assembly)
    failure = validate_h1_complete_acceptance_batch(value.assembly, batch)
    if failure is not None:
        raise ValueError("H1 completion batch differs from canonical assembly: " + failure.code)
    if batch.expected != value.capture.expected:
        raise ValueError("H1 completion batch read manifest differs from captured issuance")
    if (
        batch.identity.tenant_id != value.assembly.original_completion_request.source.tenant_id
        or batch.identity.command_id != command_id
        or batch.identity.command_fingerprint != command_fingerprint
    ):
        raise ValueError("H1 completion batch identity differs from retained H0 identity")
    _require_invocation(
        value.capture,
        batch,
        principal_id=value.assembly.original_completion_request.source.selected_admitted_input.principal_id,
    )
    return value


def decode_h1_completion_issuance(batch: CompleteDeliveryBatchV2) -> H1CompletionIssuanceV1:
    """Decode only a canonical issuance whose assembly exactly reproduces *batch*.

    Source selection and independent manifest derivation are intentionally not
    implied by this pure decoder; callers requiring those guarantees use
    :func:`validate_h1_completion_issuance`.
    """
    return _decode_value(batch)


def validate_h1_completion_issuance(
    batch: CompleteDeliveryBatchV2, runtime: R14PlanningRuntime
) -> H1CompletionIssuanceV1:
    """Validate raw selected H0/R16/R17 sources through the historical seam.

    The reader currently fails closed until its registered raw ports are
    mounted.  The current H1 reader is never used as a substitute.
    """
    value = _decode_value(batch)
    verify_h1_historical_sources(batch, value, runtime)
    return value


def h1_completion_exchange(batch: CompleteDeliveryBatchV2) -> RetainedCompleteAcceptanceExchangeV1:
    """Project a structurally verified issuance into the transient R14 exchange."""
    value = _decode_value(batch)
    return RetainedCompleteAcceptanceExchangeV1(
        assembly=value.assembly,
        batch=batch,
        expected_head=batch.expected.tenant_frontier,
        predecessor_commitment=batch.expected.expected_materialization_commitment,
    )
