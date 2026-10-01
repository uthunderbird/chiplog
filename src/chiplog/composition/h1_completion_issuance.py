"""Strict, non-authorizing H1 completion issuance decoder.

This seam proves only that a retained H1 applicability value is canonical and
reconstructs the fixed owner batch from its typed assembly.  Historical source
selection remains an authority operation and deliberately fails closed until
the runtime exposes a raw selected-source reader.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Literal, Self, cast

from pydantic import Field, ValidationError, model_validator

from chiplog.capabilities.agent_loop.execution_first_path_completion_contracts import (
    PrepareExecutionCompletionFirstPathV2,
)
from chiplog.capabilities.deployment_trust.h1_broker_evidence_contracts import (
    H1OwnerCandidateCallV1,
    H1OwnerCandidateV1,
    H1OwnerCurrentCallV1,
    H1OwnerCurrentCandidateV1,
    H1RetainedSelectedWrapperV1,
)
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    ReadCurrentHermeticExecutionScopeV1,
)
from chiplog.capabilities.effects.dispatch_authority_contracts import (
    Digest,
    DispatchObservationDTO,
    Identity,
)
from chiplog.capabilities.effects.h1_scoped_preparation_contracts import (
    H1ScopedDeliveryOwnerCallV1,
    PreparedH1ScopedDeliveryV1,
)
from chiplog.capabilities.effects.h1_scoped_preparation_v2_contracts import (
    H1ScopedDeliveryOwnerCallV2,
    PreparedH1ScopedDeliveryV2,
)
from chiplog.capabilities.effects.scoped_intent_contracts import PreparedDeliveryAuthority
from chiplog.composition.common_execution_driver_contracts import DriveInputRequestV1
from chiplog.composition.completion_publication_contracts import (
    PrepareCompleteAcceptanceAssemblyV1,
    PrepareH1CompleteAcceptanceAssemblyV1,
    acceptance_commands,
    completion_batch_fingerprint,
    expected_completion_records,
    validate_complete_acceptance_batch,
    validate_h1_complete_acceptance_batch,
    validate_h1_scoped_v3_complete_acceptance_batch,
)
from chiplog.composition.h1_historical_selected_sources import verify_h1_historical_sources
from chiplog.composition.r7_planning import ObservedTrustCall
from chiplog.composition.r14_execution_completion_records import (
    RetainedCompleteAcceptanceExchangeV1,
    RetainedH1ScopedV3CompleteAcceptanceExchangeV1,
    RetainedH1ScopedV4CompleteAcceptanceExchangeV1,
)
from chiplog.composition.r14_execution_inbox_records import RetainedInboxExecutionInitialization
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    CompleteDeliveryBatchV2,
    InvocationProofRef,
    OwnerCommandBytes,
)
from chiplog.platform.authority_checkpoint import AuthorityCheckpointRefV1
from chiplog.platform.broker import (
    BrokerSession,
    PublicPortCall,
    PublicPortResult,
    PublicPortSuccess,
)
from chiplog.platform.r7_trust import decode_trust_owner_call_canonical

if TYPE_CHECKING:
    from chiplog.composition.r14_runtime import R14PlanningRuntime


SCHEMA = "chiplog.composition.h1-completion-issuance.v1"
V2_SCHEMA = "chiplog.composition.h1-completion-issuance.v2"
V3_SCHEMA = "chiplog.composition.h1-completion-issuance.v3"
V4_SCHEMA = "chiplog.composition.h1-completion-issuance.v4"


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


class H1CompletionRecoveryRefV1(DispatchObservationDTO):
    """Closed reference to the selected native recovery prefix."""

    root_id: Digest
    journal_instance_id: Identity
    completed_chain_head: Identity


class H1CompletionTerminalAdmissionWitnessV1(DispatchObservationDTO):
    """The retained preterminal CURRENT claim for one V2 candidate."""

    terminal_call_fingerprint: Digest
    preterminal_current_exchange: H1CompletionOwnerExchangeV1


class H1CompletionReadPlanEvidenceV1(DispatchObservationDTO):
    """Closed V2 predecessor/read-plan evidence from the installed owner."""

    predecessor_checkpoint: AuthorityCheckpointRefV1
    predecessor_owner_head: Identity | None
    registry_bytes: bytes = Field(min_length=1)


class H1CompletionIssuanceV2(DispatchObservationDTO):
    """V2 evidence wire; it remains non-authorizing until installed validation."""

    schema_id: Literal["chiplog.composition.h1-completion-issuance.v2"] = (
        "chiplog.composition.h1-completion-issuance.v2"
    )
    assembly: PrepareH1CompleteAcceptanceAssemblyV1
    capture: H1CompletionCaptureV1
    owner_exchanges: tuple[H1CompletionOwnerExchangeV1, ...] = Field(min_length=4, max_length=4)
    scope_issue_exchange: H1CompletionOwnerExchangeV1
    scope_current_exchange: H1CompletionOwnerExchangeV1
    final_current_exchange: H1CompletionOwnerExchangeV1
    recovery: H1CompletionRecoveryRefV1
    terminal_admission: H1CompletionTerminalAdmissionWitnessV1
    read_plan: H1CompletionReadPlanEvidenceV1


class H1CompletionIssuanceV3(DispatchObservationDTO):
    """Scoped producer evidence; no runtime dispatch or authority is implied."""

    schema_id: Literal["chiplog.composition.h1-completion-issuance.v3"] = (
        "chiplog.composition.h1-completion-issuance.v3"
    )
    assembly: PrepareCompleteAcceptanceAssemblyV1
    capture: H1CompletionCaptureV1
    owner_exchanges: tuple[H1CompletionOwnerExchangeV1, ...] = Field(min_length=4, max_length=4)
    scope_issue_exchange: H1CompletionOwnerExchangeV1
    scope_current_exchange: H1CompletionOwnerExchangeV1
    final_current_exchange: H1CompletionOwnerExchangeV1
    recovery: H1CompletionRecoveryRefV1
    terminal_admission: H1CompletionTerminalAdmissionWitnessV1
    read_plan: H1CompletionReadPlanEvidenceV1
    effects_call: H1ScopedDeliveryOwnerCallV1
    effects_result: PreparedH1ScopedDeliveryV1

    @model_validator(mode="after")
    def exact_scoped_exchange(self) -> Self:
        assembly = self.assembly
        if (
            type(assembly.original_completion_request) is not PrepareExecutionCompletionFirstPathV2
            or len(assembly.ordered_effects) != 1
            or len(assembly.prepared_completion.delivery.manifest.ordered_deliveries) != 1
            or tuple(exchange.role for exchange in self.owner_exchanges)
            != ("completion", "conversation", "effects", "terminal_work")
        ):
            raise ValueError("H1 V3 requires the sole first-path scoped delivery exchange")
        call = self.effects_call
        result = self.effects_result
        request = call.request
        exchange = assembly.ordered_effects[0]
        if (
            request.original_completion_request != assembly.original_completion_request
            or request.prepared_completion != assembly.prepared_completion
            or result.source_request_fingerprint != call.request_digest
            or result.precursor_request != call.request.precursor_request
            or any(
                getattr(result, field) != getattr(exchange, field)
                for field in type(exchange).model_fields
            )
        ):
            raise ValueError("H1 V3 scoped owner exchange differs from assembly")
        authority = result.intent_request.intent.acquisition.authority
        if (
            not isinstance(authority, PreparedDeliveryAuthority)
            or authority.preexisting_communication_authority
            != call.request.preexisting_communication_authority
            or authority.current_disclosure_authority != call.request.current_disclosure_authority
            or result.intent_request.current != call.request.current
            or result.intent_request.complete_current_origin_sources
            != call.request.complete_current_origin_sources
            or result.intent_request.intent.acquisition.original_sources
            != call.request.original_sources
            or result.retained_sources != call.request.retained_sources
            or result.intent_request.identity != request.identity
            or result.intent_request.expected_intent != request.expected_intent
            or result.intent_request.fence != request.fence
            or result.intent_request.intent.intent_id != request.intent_id
        ):
            raise ValueError("H1 V3 scoped result differs from pinned authority inputs")
        expected_completion_records(assembly)
        wire = self.owner_exchanges[2]
        if (
            wire.sent.operation_id != call.route.operation
            or wire.sent.schema_id != call.schema_id
            or wire.sent.canonical_payload != call.canonical_bytes()
            or not isinstance(wire.returned, PublicPortSuccess)
        ):
            raise ValueError("H1 V3 effects wire differs from scoped owner call")
        if (
            wire.returned.schema_id != result.schema_id
            or wire.returned.canonical_payload != result.canonical_bytes()
            or wire.returned.request_id != wire.sent.request_id
            or wire.returned.responder != wire.sent.callee
            or wire.sent.caller.owner_id != "broker"
            or wire.sent.callee.owner_id != "effects"
            or wire.sent.request_id != call.route.request_id
            or wire.sent.caller.tenant_id != call.route.tenant_id
            or wire.sent.callee.tenant_id != call.route.tenant_id
            or wire.sent.caller.broker_epoch != call.route.broker_epoch
            or wire.sent.callee.broker_epoch != call.route.broker_epoch
            or wire.sent.caller.generation_id != call.route.runtime_generation
            or wire.sent.callee.generation_id != call.route.runtime_generation
            or wire.sent.caller.session_id != call.route.broker_session_id
            or wire.sent.callee.session_id != call.route.owner_session_id
            or wire.sent.caller.broker_epoch != self.capture.broker_epoch
            or wire.sent.caller.generation_id != self.capture.runtime_generation
            or wire.sent.caller.session_id != self.capture.broker_session_id
            or call.route.database_id != self.capture.database_id
            or call.route.worker_session_id != self.capture.worker_session_id
            or wire.sent.caller not in self.capture.sessions
            or wire.sent.callee not in self.capture.sessions
        ):
            raise ValueError("H1 V3 effects wire differs from scoped owner result")
        return self


class H1CompletionIssuanceV4(DispatchObservationDTO):
    """Dispatch-V2 scoped producer evidence with a distinct retention schema."""

    schema_id: Literal["chiplog.composition.h1-completion-issuance.v4"] = (
        "chiplog.composition.h1-completion-issuance.v4"
    )
    assembly: PrepareCompleteAcceptanceAssemblyV1
    capture: H1CompletionCaptureV1
    owner_exchanges: tuple[H1CompletionOwnerExchangeV1, ...] = Field(min_length=4, max_length=4)
    scope_issue_exchange: H1CompletionOwnerExchangeV1
    scope_current_exchange: H1CompletionOwnerExchangeV1
    final_current_exchange: H1CompletionOwnerExchangeV1
    recovery: H1CompletionRecoveryRefV1
    terminal_admission: H1CompletionTerminalAdmissionWitnessV1
    read_plan: H1CompletionReadPlanEvidenceV1
    effects_call: H1ScopedDeliveryOwnerCallV2
    effects_result: PreparedH1ScopedDeliveryV2

    @model_validator(mode="after")
    def exact_scoped_exchange(self) -> Self:
        assembly = self.assembly
        if (
            type(assembly.original_completion_request) is not PrepareExecutionCompletionFirstPathV2
            or len(assembly.ordered_effects) != 1
            or len(assembly.prepared_completion.delivery.manifest.ordered_deliveries) != 1
            or tuple(exchange.role for exchange in self.owner_exchanges)
            != ("completion", "conversation", "effects", "terminal_work")
        ):
            raise ValueError("H1 V4 requires the sole first-path scoped delivery exchange")
        call = self.effects_call
        result = self.effects_result
        request = call.request
        exchange = assembly.ordered_effects[0]
        if (
            call.schema_id != "chiplog.effects.h1-scoped-delivery-owner-call.v2"
            or call.route.operation != "effects.prepare_h1_scoped_delivery_v2"
            or request.schema_id != "chiplog.effects.prepare-h1-scoped-delivery.v2"
            or result.schema_id != "chiplog.effects.prepared-h1-scoped-delivery.v2"
            or request.original_completion_request != assembly.original_completion_request
            or request.prepared_completion != assembly.prepared_completion
            or result.source_request_fingerprint != call.request_digest
            or result.precursor_request != call.request.precursor_request
            or any(
                getattr(result, field) != getattr(exchange, field)
                for field in type(exchange).model_fields
            )
        ):
            raise ValueError("H1 V4 scoped owner exchange differs from V2 assembly")
        authority = result.intent_request.intent.acquisition.authority
        if (
            not isinstance(authority, PreparedDeliveryAuthority)
            or authority.preexisting_communication_authority
            != call.request.preexisting_communication_authority
            or authority.current_disclosure_authority != call.request.current_disclosure_authority
            or result.intent_request.current != call.request.current
            or result.intent_request.complete_current_origin_sources
            != call.request.complete_current_origin_sources
            or result.intent_request.intent.acquisition.original_sources
            != call.request.original_sources
            or result.retained_sources != call.request.retained_sources
            or result.intent_request.identity != request.identity
            or result.intent_request.expected_intent != request.expected_intent
            or result.intent_request.fence != request.fence
            or result.intent_request.intent.intent_id != request.intent_id
        ):
            raise ValueError("H1 V4 scoped result differs from pinned authority inputs")
        expected_completion_records(assembly)
        wire = self.owner_exchanges[2]
        if (
            wire.sent.operation_id != call.route.operation
            or wire.sent.schema_id != call.schema_id
            or wire.sent.canonical_payload != call.canonical_bytes()
            or not isinstance(wire.returned, PublicPortSuccess)
        ):
            raise ValueError("H1 V4 effects wire differs from scoped owner call")
        if (
            wire.returned.schema_id != result.schema_id
            or wire.returned.canonical_payload != result.canonical_bytes()
            or wire.returned.request_id != wire.sent.request_id
            or wire.returned.responder != wire.sent.callee
            or wire.sent.caller.owner_id != "broker"
            or wire.sent.callee.owner_id != "effects"
            or wire.sent.request_id != call.route.request_id
            or wire.sent.caller.tenant_id != call.route.tenant_id
            or wire.sent.callee.tenant_id != call.route.tenant_id
            or wire.sent.caller.broker_epoch != call.route.broker_epoch
            or wire.sent.callee.broker_epoch != call.route.broker_epoch
            or wire.sent.caller.generation_id != call.route.runtime_generation
            or wire.sent.callee.generation_id != call.route.runtime_generation
            or wire.sent.caller.session_id != call.route.broker_session_id
            or wire.sent.callee.session_id != call.route.owner_session_id
            or wire.sent.caller.broker_epoch != self.capture.broker_epoch
            or wire.sent.caller.generation_id != self.capture.runtime_generation
            or wire.sent.caller.session_id != self.capture.broker_session_id
            or call.route.database_id != self.capture.database_id
            or call.route.worker_session_id != self.capture.worker_session_id
            or wire.sent.caller not in self.capture.sessions
            or wire.sent.callee not in self.capture.sessions
        ):
            raise ValueError("H1 V4 effects wire differs from scoped owner result")
        return self


def _forbid_json_number(value: str) -> object:
    del value
    raise ValueError("H1 completion issuance does not permit non-integral JSON numbers")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("H1 completion issuance has a duplicate JSON key")
        result[key] = value
    return result


def _strict_canonical_json_object(raw: bytes) -> dict[str, object]:
    """Parse the complete wire before Pydantic can collapse duplicate keys."""
    if type(raw) is not bytes or not raw:
        raise ValueError("H1 completion issuance bytes are absent")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_float=_forbid_json_number,
            parse_constant=_forbid_json_number,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("H1 completion issuance is not strict JSON") from error
    if not isinstance(value, dict) or _canonical(value) != raw:
        raise ValueError("H1 completion issuance is noncanonical")
    return value


def decode_h1_completion_recovery_ref_v1(raw: bytes) -> H1CompletionRecoveryRefV1:
    """Decode only a closed, duplicate-safe canonical recovery reference."""
    _strict_canonical_json_object(raw)
    try:
        decoded = H1CompletionRecoveryRefV1.model_validate_json(raw)
    except ValidationError as error:
        raise ValueError("H1 completion recovery reference is malformed") from error
    if decoded.canonical_bytes() != raw:
        raise ValueError("H1 completion recovery reference is noncanonical")
    return decoded


def decode_h1_completion_terminal_admission_witness_v1(
    raw: bytes,
) -> H1CompletionTerminalAdmissionWitnessV1:
    """Decode only a closed, duplicate-safe canonical admission witness."""
    _strict_canonical_json_object(raw)
    try:
        decoded = H1CompletionTerminalAdmissionWitnessV1.model_validate_json(raw)
    except ValidationError as error:
        raise ValueError("H1 completion terminal admission witness is malformed") from error
    if (
        decoded.preterminal_current_exchange.role != "scope_current"
        or decoded.canonical_bytes() != raw
    ):
        raise ValueError("H1 completion terminal admission witness is noncanonical")
    return decoded


def dispatch_h1_completion_issuance_schema(
    outer_schema: str, raw: bytes
) -> Literal["V1", "V2", "V3", "V4"]:
    """Fail closed unless the retained outer and inner issuance versions agree.

    A schema-marker-only object is accepted for routing probes.  A complete
    issuance must have precisely the corresponding frozen member set.
    """
    value = _strict_canonical_json_object(raw)
    inner_schema = value.get("schema_id")
    if not isinstance(outer_schema, str) or inner_schema != outer_schema:
        raise ValueError("H1 completion issuance outer and inner schemas differ")
    if outer_schema == SCHEMA:
        fields = set(H1CompletionIssuanceV1.model_fields)
        version: Literal["V1", "V2", "V3", "V4"] = "V1"
    elif outer_schema == V2_SCHEMA:
        fields = set(H1CompletionIssuanceV2.model_fields)
        version = "V2"
    elif outer_schema == V3_SCHEMA:
        fields = set(H1CompletionIssuanceV3.model_fields)
        version = "V3"
    elif outer_schema == V4_SCHEMA:
        fields = set(H1CompletionIssuanceV4.model_fields)
        version = "V4"
    else:
        raise ValueError("H1 completion issuance schema is unsupported")
    if set(value) not in ({"schema_id"}, fields):
        raise ValueError("H1 completion issuance schema members differ")
    return version


def decode_h1_completion_issuance_v2(raw: bytes) -> H1CompletionIssuanceV2:
    """Decode the pure canonical V2 evidence wire without granting authority."""
    if dispatch_h1_completion_issuance_schema(V2_SCHEMA, raw) != "V2":
        raise ValueError("H1 completion issuance is not V2")
    _strict_canonical_json_object(raw)
    try:
        decoded = H1CompletionIssuanceV2.model_validate_json(raw)
    except ValidationError as error:
        raise ValueError("H1 completion issuance V2 is malformed") from error
    if decoded.canonical_bytes() != raw:
        raise ValueError("H1 completion issuance V2 is noncanonical")
    return decoded


def decode_h1_completion_issuance_v3(raw: bytes) -> H1CompletionIssuanceV3:
    """Decode the new canonical wire without admitting it to runtime dispatch."""
    if dispatch_h1_completion_issuance_schema(V3_SCHEMA, raw) != "V3":
        raise ValueError("H1 completion issuance is not V3")
    try:
        decoded = H1CompletionIssuanceV3.model_validate_json(raw)
    except ValidationError as error:
        raise ValueError("H1 completion issuance V3 is malformed") from error
    if decoded.canonical_bytes() != raw:
        raise ValueError("H1 completion issuance V3 is noncanonical")
    _require_v3_exchange_shape(decoded)
    return decoded


def decode_h1_completion_issuance_v4(raw: bytes) -> H1CompletionIssuanceV4:
    """Decode only the canonical retained V2 producer wire."""
    if dispatch_h1_completion_issuance_schema(V4_SCHEMA, raw) != "V4":
        raise ValueError("H1 completion issuance is not V4")
    try:
        decoded = H1CompletionIssuanceV4.model_validate_json(raw)
    except ValidationError as error:
        raise ValueError("H1 completion issuance V4 is malformed") from error
    if decoded.canonical_bytes() != raw:
        raise ValueError("H1 completion issuance V4 is noncanonical")
    _require_v4_exchange_shape(decoded)
    return decoded


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
    wire = decode_trust_owner_call_canonical(sent.canonical_payload)
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
    wire = decode_trust_owner_call_canonical(sent.canonical_payload)
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


def _require_historical_scope_issue_exchange_v2(
    exchange: H1CompletionOwnerExchangeV1,
    assembly: PrepareH1CompleteAcceptanceAssemblyV1,
) -> None:
    """Validate the retained ISSUE wire without substituting the final capture.

    Its trust-prefix join is deliberately performed by the selected historical
    reader, which is the only component with raw historical trust access.
    """
    sent = exchange.sent
    if (
        sent.operation_id != "deployment_trust.issue_hermetic_output_scope"
        or sent.schema_id != "chiplog.deployment-trust.owner-call.v1"
        or sent.caller.owner_id != "broker"
        or sent.callee.owner_id != "deployment_trust"
    ):
        raise ValueError("H1 V2 historical scope issue exchange has a substituted route")
    wire = decode_trust_owner_call_canonical(sent.canonical_payload)
    call = H1OwnerCandidateCallV1.model_validate_json(wire.request_bytes)
    selected = assembly.ordered_effects[0].owner_call.request.selected_scope
    retained = assembly.ordered_effects[0].owner_call.request.retained_origin
    returned = exchange.returned
    if (
        wire.canonical_bytes() != sent.canonical_payload
        or wire.mode != "ISSUE_HERMETIC_OUTPUT_SCOPE_V1"
        or call.canonical_bytes() != wire.request_bytes
        or hashlib.sha256(wire.snapshot_bytes).hexdigest() != call.evidence.trust_snapshot_digest
        or call.evidence.retained != retained
        or not isinstance(returned, PublicPortSuccess)
        or returned.request_id != sent.request_id
        or returned.responder != sent.callee
        or returned.schema_id != "chiplog.deployment-trust.issue-hermetic-output-scope-result.v1"
    ):
        raise ValueError("H1 V2 historical scope issue exchange differs from selected evidence")
    candidate = H1OwnerCandidateV1.model_validate_json(returned.canonical_payload)
    if (
        candidate.canonical_bytes() != returned.canonical_payload
        or candidate.scope != selected.scope
    ):
        raise ValueError("H1 V2 historical scope issue response differs from selected scope")
    candidate.check_pinned_call(call)


def _require_historical_scope_current_exchange_v2(
    exchange: H1CompletionOwnerExchangeV1,
    assembly: PrepareH1CompleteAcceptanceAssemblyV1,
) -> None:
    """Validate one historical CURRENT wire; its own prefix is checked by the reader."""
    sent = exchange.sent
    if (
        sent.operation_id != "deployment_trust.read_current_hermetic_output_scope"
        or sent.schema_id != "chiplog.deployment-trust.owner-call.v1"
        or sent.caller.owner_id != "broker"
        or sent.callee.owner_id != "deployment_trust"
    ):
        raise ValueError("H1 V2 historical scope current exchange has a substituted route")
    wire = decode_trust_owner_call_canonical(sent.canonical_payload)
    call = H1OwnerCurrentCallV1.model_validate_json(wire.request_bytes)
    selected = assembly.ordered_effects[0].owner_call.request.selected_scope
    returned = exchange.returned
    if (
        wire.canonical_bytes() != sent.canonical_payload
        or wire.mode != "READ_CURRENT_HERMETIC_OUTPUT_SCOPE_V1"
        or call.canonical_bytes() != wire.request_bytes
        or call.read_request_bytes != selected.current_request.canonical_bytes()
        or not isinstance(returned, PublicPortSuccess)
        or returned.request_id != sent.request_id
        or returned.responder != sent.callee
        or returned.schema_id != "chiplog.deployment-trust.current-hermetic-output-scope-result.v1"
    ):
        raise ValueError("H1 V2 historical scope current exchange differs from selected evidence")
    candidate = H1OwnerCurrentCandidateV1.model_validate_json(returned.canonical_payload)
    if (
        candidate.canonical_bytes() != returned.canonical_payload
        or candidate.current != selected.current_result
    ):
        raise ValueError("H1 V2 historical scope current response differs from selected scope")
    candidate.check_pinned_call(call)


def _require_fresh_scope_current_exchange_v2(
    exchange: H1CompletionOwnerExchangeV1,
    assembly: PrepareH1CompleteAcceptanceAssemblyV1,
    capture: H1CompletionCaptureV1,
) -> None:
    """A final-fence CURRENT is independently tied to the final capture."""
    _require_scope_current_exchange(exchange, assembly, capture)


def _require_v2_exchange_shape(value: H1CompletionIssuanceV2) -> None:
    roles = tuple(item.role for item in value.owner_exchanges)
    if roles != ("completion", "conversation", "effects", "terminal_work"):
        raise ValueError("H1 V2 completion owner exchanges have a noncanonical role order")
    if (
        value.scope_issue_exchange.role != "scope_issue"
        or value.scope_current_exchange.role != "scope_current"
        or value.terminal_admission.preterminal_current_exchange.role != "scope_current"
        or value.final_current_exchange.role != "scope_current"
    ):
        raise ValueError("H1 V2 scope exchanges have substituted roles")
    from chiplog.composition.h1_terminal_call_identity import _terminal_call_identity

    terminal = value.owner_exchanges[3]
    if (
        _terminal_call_identity(terminal.sent)[0]
        != value.terminal_admission.terminal_call_fingerprint
    ):
        raise ValueError("H1 V2 terminal fingerprint differs from terminal exchange")
    preterminal = value.terminal_admission.preterminal_current_exchange
    final = value.final_current_exchange
    if (
        preterminal.sent.request_id == final.sent.request_id
        or final.sent.request_id == value.scope_current_exchange.sent.request_id
        or preterminal.returned_at_ns >= terminal.sent_at_ns
        or terminal.returned_at_ns >= final.sent_at_ns
    ):
        raise ValueError("H1 V2 final-fence chronology or call identity differs")
    exchanges = (
        *value.owner_exchanges,
        value.scope_issue_exchange,
        value.scope_current_exchange,
        preterminal,
        final,
    )
    if any(
        exchange.returned_at_ns < exchange.sent_at_ns
        or exchange.returned_at_ns >= exchange.sent.budget.absolute_deadline_ns
        for exchange in exchanges
    ):
        raise ValueError("H1 V2 owner exchange timing differs")
    assembly = value.assembly
    completion, conversation, effects, terminal_work = value.owner_exchanges
    for exchange, operation, request_schema, request_bytes, response_schema, owner in (
        (
            completion,
            "agent_loop.prepare_first_path_completion",
            "chiplog.execution.first-path-completion.v2",
            assembly.original_completion_request.canonical_bytes(),
            "chiplog.agent-loop.prepared-execution-completion-result.v1",
            "agent_loop",
        ),
        (
            conversation,
            "projections.prepare_conversation_completion",
            "chiplog.conversation.prepare-completion.v1",
            assembly.conversation_request.canonical_json_bytes(),
            "chiplog.conversation.prepared-completion-result.v1",
            "projections",
        ),
        (
            effects,
            "effects.prepare_h1_local_commentary",
            "chiplog.effects.h1-local-commentary-owner-call.v1",
            assembly.ordered_effects[0].owner_call.canonical_bytes(),
            "chiplog.effects.prepared-h1-local-commentary.v1",
            "effects",
        ),
        (
            terminal_work,
            "agent_loop.prepare_terminal_work",
            "chiplog.agent-loop.prepare-terminal-work.v1",
            assembly.terminal_work_request.canonical_bytes(),
            "chiplog.agent-loop.prepared-post-terminal-work-result.v1",
            "agent_loop",
        ),
    ):
        _require_success_exchange(
            exchange,
            operation=operation,
            request_schema=request_schema,
            request_bytes=request_bytes,
            response_schema=response_schema,
            response_bytes=(
                assembly.prepared_completion.canonical_bytes()
                if exchange is completion
                else assembly.conversation_result.canonical_json_bytes()
                if exchange is conversation
                else assembly.ordered_effects[0].owner_result.canonical_bytes()
                if exchange is effects
                else assembly.terminal_work_result.canonical_bytes()
            ),
            callee_owner=owner,
            capture=value.capture,
        )
    _require_historical_scope_issue_exchange_v2(value.scope_issue_exchange, assembly)
    _require_historical_scope_current_exchange_v2(value.scope_current_exchange, assembly)
    _require_fresh_scope_current_exchange_v2(preterminal, assembly, value.capture)
    _require_fresh_scope_current_exchange_v2(final, assembly, value.capture)


def _require_v3_exchange_shape(value: H1CompletionIssuanceV3) -> None:
    """Close all four retained V3 owner frames without granting authority."""
    if tuple(item.role for item in value.owner_exchanges) != (
        "completion",
        "conversation",
        "effects",
        "terminal_work",
    ):
        raise ValueError("H1 V3 completion owner exchanges have a noncanonical role order")
    exchanges = (
        *value.owner_exchanges,
        value.scope_issue_exchange,
        value.scope_current_exchange,
        value.final_current_exchange,
        value.terminal_admission.preterminal_current_exchange,
    )
    if any(
        exchange.returned_at_ns < exchange.sent_at_ns
        or exchange.returned_at_ns >= exchange.sent.budget.absolute_deadline_ns
        for exchange in exchanges
    ):
        raise ValueError("H1 V3 owner exchange timing differs")
    assembly = value.assembly
    completion, conversation, effects, terminal_work = value.owner_exchanges
    for (
        exchange,
        operation,
        request_schema,
        request_bytes,
        response_schema,
        response_bytes,
        owner,
    ) in (
        (
            completion,
            "agent_loop.prepare_first_path_completion",
            "chiplog.execution.first-path-completion.v2",
            assembly.original_completion_request.canonical_bytes(),
            "chiplog.agent-loop.prepared-execution-completion-result.v1",
            assembly.prepared_completion.canonical_bytes(),
            "agent_loop",
        ),
        (
            conversation,
            "projections.prepare_conversation_completion",
            "chiplog.conversation.prepare-completion.v1",
            assembly.conversation_request.canonical_json_bytes(),
            "chiplog.conversation.prepared-completion-result.v1",
            assembly.conversation_result.canonical_json_bytes(),
            "projections",
        ),
        (
            effects,
            "effects.prepare_h1_scoped_delivery",
            value.effects_call.schema_id,
            value.effects_call.canonical_bytes(),
            value.effects_result.schema_id,
            value.effects_result.canonical_bytes(),
            "effects",
        ),
        (
            terminal_work,
            "agent_loop.prepare_terminal_work",
            "chiplog.agent-loop.prepare-terminal-work.v1",
            assembly.terminal_work_request.canonical_bytes(),
            "chiplog.agent-loop.prepared-post-terminal-work-result.v1",
            assembly.terminal_work_result.canonical_bytes(),
            "agent_loop",
        ),
    ):
        _require_success_exchange(
            exchange,
            operation=operation,
            request_schema=request_schema,
            request_bytes=request_bytes,
            response_schema=response_schema,
            response_bytes=response_bytes,
            callee_owner=owner,
            capture=value.capture,
        )
    from chiplog.composition.completion_publication_contracts import _validate_h1_scoped_v3_exchange

    _validate_h1_scoped_v3_exchange(assembly, value.effects_call, value.effects_result)


def _require_v4_exchange_shape(value: H1CompletionIssuanceV4) -> None:
    """Close all V4 frames to the exact dispatch-V2 contract."""
    if (
        type(value.effects_call) is not H1ScopedDeliveryOwnerCallV2
        or type(value.effects_result) is not PreparedH1ScopedDeliveryV2
        or tuple(item.role for item in value.owner_exchanges)
        != ("completion", "conversation", "effects", "terminal_work")
    ):
        raise ValueError("H1 V4 completion owner exchanges have a noncanonical shape")
    from chiplog.composition.h1_terminal_call_identity import _terminal_call_identity

    terminal = value.owner_exchanges[3]
    preterminal = value.terminal_admission.preterminal_current_exchange
    final = value.final_current_exchange
    if (
        _terminal_call_identity(terminal.sent)[0]
        != value.terminal_admission.terminal_call_fingerprint
        or preterminal.sent.request_id == final.sent.request_id
        or final.sent.request_id == value.scope_current_exchange.sent.request_id
        or preterminal.returned_at_ns >= terminal.sent_at_ns
        or terminal.returned_at_ns >= final.sent_at_ns
    ):
        raise ValueError("H1 V4 final-fence chronology or call identity differs")
    exchanges = (
        *value.owner_exchanges,
        value.scope_issue_exchange,
        value.scope_current_exchange,
        value.final_current_exchange,
        value.terminal_admission.preterminal_current_exchange,
    )
    if any(
        exchange.returned_at_ns < exchange.sent_at_ns
        or exchange.returned_at_ns >= exchange.sent.budget.absolute_deadline_ns
        for exchange in exchanges
    ):
        raise ValueError("H1 V4 owner exchange timing differs")
    assembly = value.assembly
    completion, conversation, effects, terminal_work = value.owner_exchanges
    for (
        exchange,
        operation,
        request_schema,
        request_bytes,
        response_schema,
        response_bytes,
        owner,
    ) in (
        (
            completion,
            "agent_loop.prepare_first_path_completion",
            "chiplog.execution.first-path-completion.v2",
            assembly.original_completion_request.canonical_bytes(),
            "chiplog.agent-loop.prepared-execution-completion-result.v1",
            assembly.prepared_completion.canonical_bytes(),
            "agent_loop",
        ),
        (
            conversation,
            "projections.prepare_conversation_completion",
            "chiplog.conversation.prepare-completion.v1",
            assembly.conversation_request.canonical_json_bytes(),
            "chiplog.conversation.prepared-completion-result.v1",
            assembly.conversation_result.canonical_json_bytes(),
            "projections",
        ),
        (
            effects,
            "effects.prepare_h1_scoped_delivery_v2",
            "chiplog.effects.h1-scoped-delivery-owner-call.v2",
            value.effects_call.canonical_bytes(),
            "chiplog.effects.prepared-h1-scoped-delivery.v2",
            value.effects_result.canonical_bytes(),
            "effects",
        ),
        (
            terminal_work,
            "agent_loop.prepare_terminal_work",
            "chiplog.agent-loop.prepare-terminal-work.v1",
            assembly.terminal_work_request.canonical_bytes(),
            "chiplog.agent-loop.prepared-post-terminal-work-result.v1",
            assembly.terminal_work_result.canonical_bytes(),
            "agent_loop",
        ),
    ):
        _require_success_exchange(
            exchange,
            operation=operation,
            request_schema=request_schema,
            request_bytes=request_bytes,
            response_schema=response_schema,
            response_bytes=response_bytes,
            callee_owner=owner,
            capture=value.capture,
        )
    _require_v4_scope_issue_exchange(value.scope_issue_exchange, value.effects_call)
    _require_v4_scope_current_exchange(value.scope_current_exchange, value.effects_call)
    _require_v4_scope_current_exchange(
        value.terminal_admission.preterminal_current_exchange,
        value.effects_call,
        capture=value.capture,
    )
    _require_v4_scope_current_exchange(
        value.final_current_exchange, value.effects_call, capture=value.capture
    )


def _require_v4_scope_issue_exchange(
    exchange: H1CompletionOwnerExchangeV1, call: H1ScopedDeliveryOwnerCallV2
) -> None:
    """Bind V4's historical ISSUE frame to the retained V2 origin."""
    sent = exchange.sent
    if (
        exchange.role != "scope_issue"
        or sent.operation_id != "deployment_trust.issue_hermetic_output_scope"
        or sent.schema_id != "chiplog.deployment-trust.owner-call.v1"
        or sent.caller.owner_id != "broker"
        or sent.callee.owner_id != "deployment_trust"
    ):
        raise ValueError("H1 V4 scope issue exchange has a substituted route")
    wire = decode_trust_owner_call_canonical(sent.canonical_payload)
    candidate_call = H1OwnerCandidateCallV1.model_validate_json(wire.request_bytes)
    returned = exchange.returned
    if (
        wire.canonical_bytes() != sent.canonical_payload
        or wire.mode != "ISSUE_HERMETIC_OUTPUT_SCOPE_V1"
        or candidate_call.canonical_bytes() != wire.request_bytes
        or hashlib.sha256(wire.snapshot_bytes).hexdigest()
        != candidate_call.evidence.trust_snapshot_digest
        or candidate_call.evidence.retained != call.request.retained_origin
        or not isinstance(returned, PublicPortSuccess)
        or returned.request_id != sent.request_id
        or returned.responder != sent.callee
        or returned.schema_id != "chiplog.deployment-trust.issue-hermetic-output-scope-result.v1"
    ):
        raise ValueError("H1 V4 scope issue exchange differs from selected evidence")
    candidate = H1OwnerCandidateV1.model_validate_json(returned.canonical_payload)
    if (
        candidate.canonical_bytes() != returned.canonical_payload
        or candidate.scope != call.request.selected_scope.scope
    ):
        raise ValueError("H1 V4 scope issue response differs from selected scope")
    candidate.check_pinned_call(candidate_call)


def _require_v4_scope_current_exchange(
    exchange: H1CompletionOwnerExchangeV1,
    call: H1ScopedDeliveryOwnerCallV2,
    *,
    capture: H1CompletionCaptureV1 | None = None,
) -> None:
    """Bind V4's retained CURRENT frames to the selected V2 scope request."""
    sent = exchange.sent
    if (
        exchange.role != "scope_current"
        or sent.operation_id != "deployment_trust.read_current_hermetic_output_scope"
        or sent.schema_id != "chiplog.deployment-trust.owner-call.v1"
        or sent.caller.owner_id != "broker"
        or sent.callee.owner_id != "deployment_trust"
        or (capture is not None and sent.callee not in capture.sessions)
    ):
        raise ValueError("H1 V4 scope current exchange has a substituted route")
    wire = decode_trust_owner_call_canonical(sent.canonical_payload)
    current_call = H1OwnerCurrentCallV1.model_validate_json(wire.request_bytes)
    request = ReadCurrentHermeticExecutionScopeV1.model_validate_json(
        current_call.read_request_bytes
    )
    selected_request = call.request.selected_scope.current_request
    expected_request = selected_request.model_copy(
        update={"expected_trust_observation": request.expected_trust_observation}
    )
    if capture is not None:
        physical = request.expected_trust_observation.physical_journal_head
        if (
            physical.identity != "deployment-trust/journal"
            or physical.head != capture.observed.observation.journal_head
        ):
            raise ValueError("H1 V4 fresh scope current trust head differs from capture")
    returned = exchange.returned
    if (
        wire.canonical_bytes() != sent.canonical_payload
        or wire.mode != "READ_CURRENT_HERMETIC_OUTPUT_SCOPE_V1"
        or (
            capture is not None
            and wire.snapshot_bytes != capture.observed.observation.snapshot_bytes
        )
        or current_call.canonical_bytes() != wire.request_bytes
        or request.canonical_bytes() != current_call.read_request_bytes
        or request.canonical_bytes() != expected_request.canonical_bytes()
        or not isinstance(returned, PublicPortSuccess)
        or returned.request_id != sent.request_id
        or returned.responder != sent.callee
        or returned.schema_id != "chiplog.deployment-trust.current-hermetic-output-scope-result.v1"
    ):
        raise ValueError("H1 V4 scope current exchange differs from selected evidence")
    candidate = H1OwnerCurrentCandidateV1.model_validate_json(returned.canonical_payload)
    if (
        candidate.canonical_bytes() != returned.canonical_payload
        or candidate.current != call.request.selected_scope.current_result
    ):
        raise ValueError("H1 V4 scope current response differs from selected scope")
    candidate.check_pinned_call(current_call)


def _validate_h1_scoped_v4_complete_acceptance_batch(
    issuance: H1CompletionIssuanceV4, batch: CompleteDeliveryBatchV2
) -> None:
    """Reproduce a V4 batch while retaining the exact V2 owner-call command.

    The frozen V3 validator deliberately re-decodes V1 outer bytes.  V4 first
    validates the same generic assembly against its ordinary effects command,
    then replaces that one command with the retained V2 owner call and checks
    the actual batch fingerprint.
    """
    assembly = issuance.assembly
    generic_commands = acceptance_commands(assembly)
    generic_batch = batch.model_copy(
        update={
            "prepared_effects_commands": (generic_commands[2],),
            "complete_batch_fingerprint": "0" * 64,
        }
    )
    generic_batch = generic_batch.model_copy(
        update={"complete_batch_fingerprint": completion_batch_fingerprint(generic_batch)}
    )
    failure = validate_complete_acceptance_batch(assembly, generic_batch)
    if failure is not None:
        raise ValueError("H1 V4 generic batch differs from canonical assembly: " + failure.code)
    v2_command = OwnerCommandBytes(
        owner="effects",
        schema_id=issuance.effects_call.schema_id,
        canonical_bytes=issuance.effects_call.canonical_bytes(),
        fingerprint=hashlib.sha256(issuance.effects_call.canonical_bytes()).hexdigest(),
    )
    actual = (
        batch.loop_command,
        batch.conversation_command,
        *batch.prepared_effects_commands,
        batch.terminal_work_command,
    )
    expected = (generic_commands[0], generic_commands[1], v2_command, generic_commands[3])
    if actual != expected:
        raise ValueError("H1 V4 commands differ from exact V2 owner exchange")
    if batch.complete_batch_fingerprint != completion_batch_fingerprint(batch):
        raise ValueError("H1 V4 batch fingerprint differs")


def _h1_identity_from_retained_origin(
    assembly: PrepareH1CompleteAcceptanceAssemblyV1 | PrepareCompleteAcceptanceAssemblyV1,
    retained: H1RetainedSelectedWrapperV1,
) -> tuple[str, str]:
    """Derive the stable completion identity from the retained H0 envelope."""
    try:
        envelope = json.loads(retained.initialization_envelope_bytes)
        initialization = RetainedInboxExecutionInitialization.model_validate_json(
            envelope["inbox_initialization"]
        )
        driver = DriveInputRequestV1.model_validate_json(initialization.driver_request_bytes)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("H1 completion retained H0 initialization is not decodable") from error
    original_request = assembly.original_completion_request
    if type(original_request) is not PrepareExecutionCompletionFirstPathV2:
        raise ValueError("H1 completion retained H0 request differs")
    if (
        initialization.canonical_bytes() != envelope["inbox_initialization"].encode()
        or driver.canonical_bytes() != initialization.driver_request_bytes
        or driver.original_driver_command_fingerprint() != initialization.driver_request_fingerprint
        or driver.identity.tenant_id != original_request.source.tenant_id
    ):
        raise ValueError("H1 completion retained H0 driver identity differs")
    source = original_request.source
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


def _h1_identity(assembly: PrepareH1CompleteAcceptanceAssemblyV1) -> tuple[str, str]:
    return _h1_identity_from_retained_origin(
        assembly, assembly.ordered_effects[0].owner_call.request.retained_origin
    )


def _h1_v3_identity(issuance: H1CompletionIssuanceV3) -> tuple[str, str]:
    return _h1_identity_from_retained_origin(
        issuance.assembly, issuance.effects_call.request.retained_origin
    )


def _h1_v4_identity(issuance: H1CompletionIssuanceV4) -> tuple[str, str]:
    return _h1_identity_from_retained_origin(
        issuance.assembly, issuance.effects_call.request.retained_origin
    )


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


def _decode_value(
    batch: CompleteDeliveryBatchV2,
) -> (
    H1CompletionIssuanceV1
    | H1CompletionIssuanceV2
    | H1CompletionIssuanceV3
    | H1CompletionIssuanceV4
):
    if type(batch) is not CompleteDeliveryBatchV2:
        raise TypeError("H1 completion issuance requires CompleteDeliveryBatchV2")
    authentication = batch.authentication
    if (
        authentication.kind != "WORKER"
        or authentication.applicability_schema not in (SCHEMA, V2_SCHEMA, V3_SCHEMA, V4_SCHEMA)
        or hashlib.sha256(authentication.applicability_bytes).hexdigest()
        != authentication.applicability_fingerprint
    ):
        raise ValueError("H1 completion batch lacks its exact issuance applicability")
    version = dispatch_h1_completion_issuance_schema(
        authentication.applicability_schema, authentication.applicability_bytes
    )
    value: (
        H1CompletionIssuanceV1
        | H1CompletionIssuanceV2
        | H1CompletionIssuanceV3
        | H1CompletionIssuanceV4
    )
    if version == "V1":
        value = H1CompletionIssuanceV1.model_validate_json(authentication.applicability_bytes)
        if value.canonical_bytes() != authentication.applicability_bytes:
            raise ValueError("H1 completion issuance is noncanonical")
        _require_exchange_shape(value)
    elif version == "V2":
        value = decode_h1_completion_issuance_v2(authentication.applicability_bytes)
        _require_v2_exchange_shape(value)
    elif version == "V3":
        value = decode_h1_completion_issuance_v3(authentication.applicability_bytes)
    else:
        value = decode_h1_completion_issuance_v4(authentication.applicability_bytes)
    if type(value) is H1CompletionIssuanceV3:
        command_id, command_fingerprint = _h1_v3_identity(value)
        failure = validate_h1_scoped_v3_complete_acceptance_batch(
            value.assembly, value.effects_call, value.effects_result, batch
        )
    elif type(value) is H1CompletionIssuanceV4:
        command_id, command_fingerprint = _h1_v4_identity(value)
        _validate_h1_scoped_v4_complete_acceptance_batch(value, batch)
        failure = None
    else:
        legacy = cast(H1CompletionIssuanceV1 | H1CompletionIssuanceV2, value)
        command_id, command_fingerprint = _h1_identity(legacy.assembly)
        failure = validate_h1_complete_acceptance_batch(legacy.assembly, batch)
    if failure is not None:
        raise ValueError("H1 completion batch differs from canonical assembly: " + failure.code)
    if batch.expected != value.capture.expected:
        raise ValueError("H1 completion batch read manifest differs from captured issuance")
    if (
        batch.identity.tenant_id
        != cast(PrepareExecutionCompletionFirstPathV2, value.assembly.original_completion_request)
        .source.tenant_id
        or batch.identity.command_id != command_id
        or batch.identity.command_fingerprint != command_fingerprint
    ):
        raise ValueError("H1 completion batch identity differs from retained H0 identity")
    _require_invocation(
        value.capture,
        batch,
        principal_id=cast(
            PrepareExecutionCompletionFirstPathV2, value.assembly.original_completion_request
        ).source.selected_admitted_input.principal_id,
    )
    return value


def decode_h1_completion_issuance(
    batch: CompleteDeliveryBatchV2,
) -> (
    H1CompletionIssuanceV1
    | H1CompletionIssuanceV2
    | H1CompletionIssuanceV3
    | H1CompletionIssuanceV4
):
    """Decode only a canonical issuance whose assembly exactly reproduces *batch*.

    Source selection and independent manifest derivation are intentionally not
    implied by this pure decoder; callers requiring those guarantees use
    :func:`validate_h1_completion_issuance`.
    """
    return _decode_value(batch)


def validate_h1_completion_issuance(
    batch: CompleteDeliveryBatchV2, runtime: R14PlanningRuntime
) -> (
    H1CompletionIssuanceV1
    | H1CompletionIssuanceV2
    | H1CompletionIssuanceV3
    | H1CompletionIssuanceV4
):
    """Validate raw selected H0/R16/R17 sources through the historical seam.

    The reader currently fails closed until its registered raw ports are
    mounted.  The current H1 reader is never used as a substitute.
    """
    value = _decode_value(batch)
    verify_h1_historical_sources(batch, value, runtime)
    return value


def h1_completion_exchange(
    batch: CompleteDeliveryBatchV2,
) -> (
    RetainedCompleteAcceptanceExchangeV1
    | RetainedH1ScopedV3CompleteAcceptanceExchangeV1
    | RetainedH1ScopedV4CompleteAcceptanceExchangeV1
):
    """Project a structurally verified issuance into the transient R14 exchange."""
    value = _decode_value(batch)
    if type(value) is H1CompletionIssuanceV3:
        return RetainedH1ScopedV3CompleteAcceptanceExchangeV1(
            issuance_bytes=value.canonical_bytes(),
            batch=batch,
            expected_head=batch.expected.tenant_frontier,
            predecessor_commitment=batch.expected.expected_materialization_commitment,
        )
    if type(value) is H1CompletionIssuanceV4:
        return RetainedH1ScopedV4CompleteAcceptanceExchangeV1(
            issuance_bytes=value.canonical_bytes(),
            batch=batch,
            expected_head=batch.expected.tenant_frontier,
            predecessor_commitment=batch.expected.expected_materialization_commitment,
        )
    return RetainedCompleteAcceptanceExchangeV1(
        assembly=value.assembly,
        batch=batch,
        expected_head=batch.expected.tenant_frontier,
        predecessor_commitment=batch.expected.expected_materialization_commitment,
    )
