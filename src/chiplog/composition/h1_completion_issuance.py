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

from pydantic import Field, ValidationError

from chiplog.capabilities.deployment_trust.h1_broker_evidence_contracts import (
    H1OwnerCandidateCallV1,
    H1OwnerCandidateV1,
    H1OwnerCurrentCallV1,
    H1OwnerCurrentCandidateV1,
)
from chiplog.capabilities.effects.dispatch_authority_contracts import (
    Digest,
    DispatchObservationDTO,
    Identity,
)
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


def dispatch_h1_completion_issuance_schema(outer_schema: str, raw: bytes) -> Literal["V1", "V2"]:
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
        version: Literal["V1", "V2"] = "V1"
    elif outer_schema == V2_SCHEMA:
        fields = set(H1CompletionIssuanceV2.model_fields)
        version = "V2"
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


def _decode_value(
    batch: CompleteDeliveryBatchV2,
) -> H1CompletionIssuanceV1 | H1CompletionIssuanceV2:
    if type(batch) is not CompleteDeliveryBatchV2:
        raise TypeError("H1 completion issuance requires CompleteDeliveryBatchV2")
    authentication = batch.authentication
    if (
        authentication.kind != "WORKER"
        or authentication.applicability_schema not in (SCHEMA, V2_SCHEMA)
        or hashlib.sha256(authentication.applicability_bytes).hexdigest()
        != authentication.applicability_fingerprint
    ):
        raise ValueError("H1 completion batch lacks its exact issuance applicability")
    version = dispatch_h1_completion_issuance_schema(
        authentication.applicability_schema, authentication.applicability_bytes
    )
    value: H1CompletionIssuanceV1 | H1CompletionIssuanceV2
    if version == "V1":
        value = H1CompletionIssuanceV1.model_validate_json(authentication.applicability_bytes)
        if value.canonical_bytes() != authentication.applicability_bytes:
            raise ValueError("H1 completion issuance is noncanonical")
        _require_exchange_shape(value)
    else:
        value = decode_h1_completion_issuance_v2(authentication.applicability_bytes)
        _require_v2_exchange_shape(value)
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


def decode_h1_completion_issuance(
    batch: CompleteDeliveryBatchV2,
) -> H1CompletionIssuanceV1 | H1CompletionIssuanceV2:
    """Decode only a canonical issuance whose assembly exactly reproduces *batch*.

    Source selection and independent manifest derivation are intentionally not
    implied by this pure decoder; callers requiring those guarantees use
    :func:`validate_h1_completion_issuance`.
    """
    return _decode_value(batch)


def validate_h1_completion_issuance(
    batch: CompleteDeliveryBatchV2, runtime: R14PlanningRuntime
) -> H1CompletionIssuanceV1 | H1CompletionIssuanceV2:
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
