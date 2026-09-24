"""Pure completion-assembly representation and batch/exchange binding checks.

No function here authenticates a caller or selects a journal decision.  It makes
the otherwise broad broker command wrappers reproducible from typed owner
exchanges, so a caller cannot substitute a command blob for a named role.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field

from chiplog.capabilities.agent_loop.completion_owner_record_contracts import (
    CompletionCanonicalMember,
    make_completion_rejection_member,
    make_prepared_delivery_acceptance_member,
    make_terminal_manifest_member,
    make_terminal_run_member,
)
from chiplog.capabilities.agent_loop.completion_terminal_work_sources import (
    AcceptedCompletionWorkSourceV1,
    RejectedCompletionWorkSourceV1,
    completion_terminal_work_source_bytes,
    decode_completion_terminal_work_source,
)
from chiplog.capabilities.agent_loop.contracts import Frozen
from chiplog.capabilities.agent_loop.execution_completion_contracts import (
    PreparedExecutionCompletion,
    PreparedExecutionCompletionReject,
    PrepareExecutionCompletion,
)
from chiplog.capabilities.agent_loop.post_terminal_contracts import (
    PreparedPostTerminalWork,
    PrepareTerminalWork,
)
from chiplog.capabilities.agent_loop.post_terminal_record_contracts import (
    validate_prepared_post_terminal_work,
)
from chiplog.capabilities.agent_loop.rejected_completion_terminalization_contracts import (
    PreparedRejectedCompletionTerminalizationV1,
    PrepareRejectedCompletionTerminalizationV1,
    manifest_ref,
    rejected_terminalization_request_fingerprint,
)
from chiplog.capabilities.effects.scoped_intent_contracts import (
    PreparedDeliveryAuthority,
    PreparedDeliveryBasisV3,
    PreparedDeliveryOriginV3,
    PreparedScopedIntentPublication,
    PrepareScopedIntentPublication,
    ScopedPrecursorRequest,
    ScopedPrecursorResult,
)
from chiplog.capabilities.effects.scoped_intent_record_contracts import (
    RetainedOriginalSourceV3,
    ScopedIntentCanonicalMemberV3,
    ScopedIntentRetainedExchangeV3,
    make_scoped_intent_retained_exchange,
    validate_scoped_intent_publication,
)
from chiplog.capabilities.projections.conversation_preparation_contracts import (
    ConversationCanonicalMemberV2,
    PrepareConversationCompletionRejectedV1,
    PrepareConversationCompletionV1,
    PreparedConversationCompletionRejectedV1,
    PreparedConversationCompletionV1,
    conversation_complete_owner_commitment,
    conversation_no_change_commitment,
    conversation_source_request_fingerprint,
    decode_conversation_canonical_member,
)
from chiplog.platform._owner_publication_contracts import (
    CompleteDeliveryBatchV2,
    OwnerCommandBytes,
    OwnerRecordBytes,
    RejectedCompletionBatchV1,
)

LOOP_COMPLETION_SCHEMA = "chiplog.agent-loop.prepare-execution-completion.v1"
LOOP_REJECTION_SCHEMA = "chiplog.agent-loop.prepared-execution-completion-reject.v1"
LOOP_REJECT_TERMINALIZATION_SCHEMA = (
    "chiplog.agent-loop.prepare-rejected-completion-terminalization.v1"
)
WORK_SCHEMA = "chiplog.agent-loop.prepare-terminal-work.v1"
EFFECTS_SCHEMA = "chiplog.effects.prepare-scoped-intent-publication.v3"


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _command(
    owner: Literal["agent_loop", "effects", "conversation"], schema: str, raw: bytes
) -> OwnerCommandBytes:
    return OwnerCommandBytes(
        owner=owner, schema_id=schema, canonical_bytes=raw, fingerprint=_sha(raw)
    )


class DeliveryEffectsExchangeV1(Frozen):
    delivery_id: str = Field(min_length=1)
    basis: PreparedDeliveryBasisV3
    precursor_request: ScopedPrecursorRequest
    precursor_result: ScopedPrecursorResult
    intent_request: PrepareScopedIntentPublication
    intent_result: PreparedScopedIntentPublication
    acquisition_bytes: bytes = Field(min_length=1)
    mandate_bytes: bytes = Field(min_length=1)
    retained_sources: tuple[RetainedOriginalSourceV3, ...]


class PrepareCompleteAcceptanceAssemblyV1(Frozen):
    kind: Literal["PREPARE_COMPLETE_ACCEPTANCE_ASSEMBLY_V1"] = (
        "PREPARE_COMPLETE_ACCEPTANCE_ASSEMBLY_V1"
    )
    schema_id: Literal["chiplog.composition.complete-acceptance-assembly.v1"] = (
        "chiplog.composition.complete-acceptance-assembly.v1"
    )
    original_completion_request: PrepareExecutionCompletion
    prepared_completion: PreparedExecutionCompletion
    conversation_request: PrepareConversationCompletionV1
    conversation_result: PreparedConversationCompletionV1
    ordered_effects: tuple[DeliveryEffectsExchangeV1, ...] = Field(min_length=1)
    work_source: AcceptedCompletionWorkSourceV1
    terminal_work_request: PrepareTerminalWork
    terminal_work_result: PreparedPostTerminalWork


class PrepareRejectedCompletionAssemblyV1(Frozen):
    kind: Literal["PREPARE_REJECTED_COMPLETION_ASSEMBLY_V1"] = (
        "PREPARE_REJECTED_COMPLETION_ASSEMBLY_V1"
    )
    schema_id: Literal["chiplog.composition.rejected-completion-assembly.v1"] = (
        "chiplog.composition.rejected-completion-assembly.v1"
    )
    original_completion_request: PrepareExecutionCompletion
    completion_reject: PreparedExecutionCompletionReject
    rejected_terminalization_request: PrepareRejectedCompletionTerminalizationV1
    rejected_terminalization_result: PreparedRejectedCompletionTerminalizationV1
    conversation_request: PrepareConversationCompletionRejectedV1
    conversation_result: PreparedConversationCompletionRejectedV1
    work_source: RejectedCompletionWorkSourceV1
    terminal_work_request: PrepareTerminalWork
    terminal_work_result: PreparedPostTerminalWork


class CompletionPublicationAssemblyFailureV1(Frozen):
    kind: Literal["COMPLETION_PUBLICATION_ASSEMBLY_FAILURE_V1"] = (
        "COMPLETION_PUBLICATION_ASSEMBLY_FAILURE_V1"
    )
    code: Literal["SCHEMA", "ROLE", "SOURCE", "EXCHANGE", "RECORDS"]
    reason: str = Field(min_length=1)


class _CompletionExchangeError(ValueError):
    """A typed completion-exchange join differs before record reproduction."""


def _failure(
    code: Literal["SCHEMA", "ROLE", "SOURCE", "EXCHANGE", "RECORDS"], reason: str
) -> CompletionPublicationAssemblyFailureV1:
    return CompletionPublicationAssemblyFailureV1(code=code, reason=reason)


def acceptance_commands(
    assembly: PrepareCompleteAcceptanceAssemblyV1,
) -> tuple[OwnerCommandBytes, ...]:
    return (
        _command(
            "agent_loop",
            LOOP_COMPLETION_SCHEMA,
            assembly.original_completion_request.canonical_bytes(),
        ),
        _command(
            "conversation",
            assembly.conversation_request.schema_id,
            assembly.conversation_request.canonical_json_bytes(),
        ),
        *(
            _command("effects", EFFECTS_SCHEMA, item.intent_request.canonical_bytes())
            for item in assembly.ordered_effects
        ),
        _command("agent_loop", WORK_SCHEMA, assembly.terminal_work_request.canonical_bytes()),
    )


def rejected_commands(
    assembly: PrepareRejectedCompletionAssemblyV1,
) -> tuple[OwnerCommandBytes, ...]:
    return (
        _command(
            "agent_loop",
            LOOP_COMPLETION_SCHEMA,
            assembly.original_completion_request.canonical_bytes(),
        ),
        _command(
            "agent_loop",
            LOOP_REJECT_TERMINALIZATION_SCHEMA,
            assembly.rejected_terminalization_request.canonical_bytes(),
        ),
        _command(
            "conversation",
            assembly.conversation_request.schema_id,
            assembly.conversation_request.canonical_json_bytes(),
        ),
        _command("agent_loop", WORK_SCHEMA, assembly.terminal_work_request.canonical_bytes()),
    )


def _owner_record(
    owner: Literal["agent_loop", "effects", "conversation"],
    record_kind: str,
    record_id: str,
    schema_id: str,
    canonical_bytes: bytes,
    fingerprint: str,
) -> OwnerRecordBytes:
    return OwnerRecordBytes(
        owner=owner,
        record_kind=record_kind,
        record_id=record_id,
        schema_id=schema_id,
        canonical_bytes=canonical_bytes,
        fingerprint=fingerprint,
    )


def _completion_record(member: CompletionCanonicalMember) -> OwnerRecordBytes:
    return _owner_record(
        "agent_loop",
        member.record_kind,
        member.record_id,
        member.schema_id,
        member.canonical_record_bytes,
        member.fingerprint,
    )


def _conversation_record(member: ConversationCanonicalMemberV2) -> OwnerRecordBytes:
    decoded = decode_conversation_canonical_member(member)
    return _owner_record(
        "conversation",
        decoded.member.record_kind,
        decoded.member.record_id,
        decoded.member.schema_id,
        decoded.member.canonical_bytes,
        decoded.member.fingerprint,
    )


def _effects_record(member: ScopedIntentCanonicalMemberV3) -> OwnerRecordBytes:
    return _owner_record(
        "effects",
        member.record_kind,
        member.record_id,
        member.schema_id,
        member.canonical_record_bytes,
        member.fingerprint,
    )


def _work_records(
    request: PrepareTerminalWork, result: PreparedPostTerminalWork
) -> tuple[OwnerRecordBytes, ...]:
    return tuple(
        _owner_record(
            "agent_loop",
            item.member.record_kind,
            item.member.record_id,
            item.member.schema_id,
            item.member.canonical_record_bytes,
            item.member.fingerprint,
        )
        for item in validate_prepared_post_terminal_work(request, result)
    )


def _validate_terminal_work_source_join(
    source: AcceptedCompletionWorkSourceV1 | RejectedCompletionWorkSourceV1,
    request: PrepareTerminalWork,
) -> None:
    """Bind the canonical completion source directly to the terminal-work request."""
    raw = completion_terminal_work_source_bytes(source)
    decoded = decode_completion_terminal_work_source(raw)
    if decoded != source:
        raise ValueError("terminal work source differs after canonical re-decode")
    manifest_head = manifest_ref(decoded.terminal_manifest)
    if (
        request.terminal_run != decoded.terminal_run
        or request.terminal_manifest != decoded.terminal_manifest_head
        or request.terminal_manifest != manifest_head
        or request.ordered_open_obligations != decoded.ordered_open_obligations
        or request.ordered_open_obligations
        != decoded.terminal_manifest.complete_open_original_obligations
    ):
        raise ValueError("terminal work request differs from registered completion source")


def _accepted_effect_records(
    assembly: PrepareCompleteAcceptanceAssemblyV1,
) -> tuple[OwnerRecordBytes, ...]:
    deliveries = assembly.prepared_completion.delivery.manifest.ordered_deliveries
    if tuple(item.delivery_id for item in assembly.ordered_effects) != tuple(
        item.delivery_id for item in deliveries
    ):
        raise ValueError("effects exchanges differ from accepted delivery order")
    records: list[OwnerRecordBytes] = []
    proposal_bytes = assembly.prepared_completion.delivery.canonical_bytes()
    for exchange in assembly.ordered_effects:
        intent = exchange.intent_request.intent
        if not isinstance(intent.mandate.origin, PreparedDeliveryOriginV3):
            raise _CompletionExchangeError("effects intent has a non-delivery origin")
        if not isinstance(intent.acquisition.authority, PreparedDeliveryAuthority):
            raise _CompletionExchangeError("effects intent has a non-delivery authority")
        if exchange.delivery_id != intent.mandate.origin.binding.delivery_id:
            raise _CompletionExchangeError(
                "effects exchange delivery ID differs from intent binding"
            )
        if exchange.basis != intent.acquisition.authority.basis:
            raise _CompletionExchangeError("effects exchange basis differs from intent authority")
        if exchange.basis.loop_proposal_bytes != proposal_bytes:
            raise _CompletionExchangeError("effects exchange has a different loop proposal")
        retained = make_scoped_intent_retained_exchange(exchange.intent_request)
        if exchange.retained_sources != retained.original_sources:
            raise _CompletionExchangeError(
                "effects retained sources differ from the scoped request"
            )
        member = validate_scoped_intent_publication(
            exchange.intent_request,
            exchange.intent_result,
            ScopedIntentRetainedExchangeV3(
                precursor_request_bytes=exchange.precursor_request.canonical_bytes(),
                precursor_result_bytes=exchange.precursor_result.canonical_bytes(),
                mandate_bytes=exchange.mandate_bytes,
                acquisition_bytes=exchange.acquisition_bytes,
                original_sources=exchange.retained_sources,
            ),
        )
        records.append(_effects_record(member))
    return tuple(records)


def _validate_accepted_exchanges(assembly: PrepareCompleteAcceptanceAssemblyV1) -> None:
    request = assembly.original_completion_request
    prepared = assembly.prepared_completion
    if prepared.source_request_fingerprint != _sha(request.canonical_bytes()):
        raise ValueError("prepared completion source request fingerprint differs")
    if prepared.run != assembly.work_source.terminal_run:
        raise ValueError("prepared completion Run differs from work source")
    if prepared.terminal_manifest != assembly.work_source.terminal_manifest:
        raise ValueError("prepared completion manifest differs from work source")
    if assembly.work_source.original_completion_request_bytes != request.canonical_bytes():
        raise ValueError("accepted work source completion request bytes differ")
    if assembly.work_source.prepared_completion_bytes != prepared.canonical_bytes():
        raise ValueError("accepted work source completion result bytes differ")
    if (
        assembly.conversation_request.original_completion_request_bytes != request.canonical_bytes()
        or assembly.conversation_request.loop_preparation_bytes != prepared.canonical_bytes()
        or assembly.conversation_request.original_delivery_proposal_bytes
        != prepared.delivery.canonical_bytes()
        or assembly.conversation_request.proposed_terminal_run != prepared.run
        or assembly.conversation_request.proposed_terminal_manifest != prepared.terminal_manifest
    ):
        raise ValueError("conversation request differs from accepted loop exchange")
    conversation_fingerprint = conversation_source_request_fingerprint(
        assembly.conversation_request
    )
    if assembly.conversation_result.source_request_fingerprint != conversation_fingerprint:
        raise ValueError("conversation completion source request fingerprint differs")
    conversation_commitment = conversation_complete_owner_commitment(
        assembly.conversation_result.ordered_members
    )
    if assembly.conversation_result.complete_owner_commitment != conversation_commitment:
        raise ValueError("conversation completion owner commitment differs")
    accepted_ids = tuple(item.delivery_id for item in prepared.delivery.manifest.ordered_deliveries)
    members = tuple(
        decode_conversation_canonical_member(member)
        for member in assembly.conversation_result.ordered_members
    )
    member_fingerprints = tuple(member.source_request_fingerprint for member in members)
    member_kinds = tuple(member.source_kind for member in members)
    member_delivery_ids = tuple(member.delivery_id for member in members)
    if (
        member_fingerprints != (conversation_fingerprint,) * len(members)
        or member_kinds != ("COMPLETION",) * len(members)
        or member_delivery_ids != accepted_ids
    ):
        raise ValueError("conversation members differ from accepted delivery order")


def _validate_rejected_exchanges(assembly: PrepareRejectedCompletionAssemblyV1) -> None:
    request = assembly.original_completion_request
    rejected = assembly.completion_reject
    if rejected.source_request_fingerprint != _sha(request.canonical_bytes()):
        raise ValueError("completion rejection source request fingerprint differs")
    if assembly.rejected_terminalization_result.source_request_fingerprint != (
        rejected_terminalization_request_fingerprint(assembly.rejected_terminalization_request)
    ):
        raise ValueError("rejected terminalization source request fingerprint differs")
    conversation_fingerprint = conversation_source_request_fingerprint(
        assembly.conversation_request
    )
    if assembly.conversation_result.source_request_fingerprint != conversation_fingerprint:
        raise ValueError("rejected conversation source request fingerprint differs")
    no_change_commitment = conversation_no_change_commitment()
    if assembly.conversation_result.no_conversation_change_commitment != no_change_commitment:
        raise ValueError("rejected conversation no-change commitment differs")
    if (
        assembly.rejected_terminalization_request.original_completion_request_bytes
        != request.canonical_bytes()
        or assembly.rejected_terminalization_request.original_completion_reject_bytes
        != rejected.canonical_bytes()
        or assembly.conversation_request.original_completion_request_bytes
        != request.canonical_bytes()
        or assembly.conversation_request.loop_rejection_bytes != rejected.canonical_bytes()
        or assembly.conversation_request.proposed_terminal_run
        != assembly.rejected_terminalization_result.terminal_run
    ):
        raise ValueError("rejected requests differ from original loop exchanges")
    source = assembly.work_source
    if (
        source.original_completion_request_bytes != request.canonical_bytes()
        or source.original_completion_reject_bytes != rejected.canonical_bytes()
        or source.rejected_terminalization_request_bytes
        != assembly.rejected_terminalization_request.canonical_bytes()
        or source.rejected_terminalization_result_bytes
        != assembly.rejected_terminalization_result.canonical_bytes()
    ):
        raise ValueError("rejected work source exchange bytes differ")
    if (
        assembly.rejected_terminalization_result.terminal_manifest != source.terminal_manifest
        or assembly.rejected_terminalization_result.terminal_run != source.terminal_run
    ):
        raise ValueError("rejected terminalization differs from work source")


def expected_completion_records(
    assembly: PrepareCompleteAcceptanceAssemblyV1 | PrepareRejectedCompletionAssemblyV1,
) -> tuple[OwnerRecordBytes, ...]:
    """Reproduce the finite ordered owner records from typed completion exchanges."""
    if isinstance(assembly, PrepareCompleteAcceptanceAssemblyV1):
        _validate_accepted_exchanges(assembly)
        delivery = _completion_record(
            make_prepared_delivery_acceptance_member(
                assembly.original_completion_request, assembly.prepared_completion
            )
        )
        manifest = _completion_record(
            make_terminal_manifest_member(assembly.prepared_completion.terminal_manifest)
        )
        run = _completion_record(make_terminal_run_member(assembly.prepared_completion.run))
        conversation = tuple(
            _conversation_record(member) for member in assembly.conversation_result.ordered_members
        )
        effects = _accepted_effect_records(assembly)
        work = _work_records(assembly.terminal_work_request, assembly.terminal_work_result)
        return (delivery, manifest, run, *conversation, *effects, *work)

    _validate_rejected_exchanges(assembly)
    rejection = _completion_record(
        make_completion_rejection_member(
            assembly.original_completion_request, assembly.completion_reject
        )
    )
    manifest = _completion_record(
        make_terminal_manifest_member(assembly.rejected_terminalization_result.terminal_manifest)
    )
    run = _completion_record(
        make_terminal_run_member(assembly.rejected_terminalization_result.terminal_run)
    )
    work = _work_records(assembly.terminal_work_request, assembly.terminal_work_result)
    return (rejection, manifest, run, *work)


def validate_complete_acceptance_batch(
    assembly: PrepareCompleteAcceptanceAssemblyV1, batch: CompleteDeliveryBatchV2
) -> CompletionPublicationAssemblyFailureV1 | None:
    try:
        expected = acceptance_commands(assembly)
        actual = (
            batch.loop_command,
            batch.conversation_command,
            *batch.prepared_effects_commands,
            batch.terminal_work_command,
        )
        if actual != expected:
            return _failure(
                "ROLE", "complete acceptance commands differ from typed owner exchanges"
            )
        source_bytes = completion_terminal_work_source_bytes(assembly.work_source)
        if assembly.terminal_work_request.original_terminalization_request != source_bytes:
            return _failure(
                "SOURCE", "terminal work does not use the registered accepted completion source"
            )
        _validate_terminal_work_source_join(assembly.work_source, assembly.terminal_work_request)
        expected_records = expected_completion_records(assembly)
    except _CompletionExchangeError as error:
        return _failure("EXCHANGE", str(error))
    except ValueError:
        return _failure("SOURCE", "terminal work source is not a registered completion source")
    if batch.complete_records != expected_records:
        return _failure("RECORDS", "accepted completion records differ from typed owner exchanges")
    if batch.complete_batch_fingerprint != completion_batch_fingerprint(batch):
        return _failure("RECORDS", "accepted completion batch fingerprint differs")
    return None


def validate_rejected_completion_batch(
    assembly: PrepareRejectedCompletionAssemblyV1, batch: RejectedCompletionBatchV1
) -> CompletionPublicationAssemblyFailureV1 | None:
    try:
        expected = rejected_commands(assembly)
        actual = (
            batch.loop_rejection_command,
            batch.rejected_terminalization_command,
            batch.conversation_no_change_command,
            batch.terminal_work_command,
        )
        if actual != expected:
            return _failure(
                "ROLE", "rejected completion commands differ from typed owner exchanges"
            )
        if assembly.conversation_result.ordered_members:
            return _failure(
                "EXCHANGE", "rejected completion conversation exchange must be no-change"
            )
        source_bytes = completion_terminal_work_source_bytes(assembly.work_source)
        if assembly.terminal_work_request.original_terminalization_request != source_bytes:
            return _failure(
                "SOURCE", "terminal work does not use the registered rejected completion source"
            )
        _validate_terminal_work_source_join(assembly.work_source, assembly.terminal_work_request)
        expected_records = expected_completion_records(assembly)
    except _CompletionExchangeError as error:
        return _failure("EXCHANGE", str(error))
    except ValueError:
        return _failure("SOURCE", "terminal work source is not a registered completion source")
    if batch.complete_records != expected_records:
        return _failure("RECORDS", "rejected completion records differ from typed owner exchanges")
    if batch.complete_batch_fingerprint != completion_batch_fingerprint(batch):
        return _failure("RECORDS", "rejected completion batch fingerprint differs")
    return None


def completion_batch_fingerprint(
    batch: CompleteDeliveryBatchV2 | RejectedCompletionBatchV1,
) -> str:
    """The fixed command-order preimage; journal selection remains outside it."""
    if isinstance(batch, CompleteDeliveryBatchV2):
        commands = (
            batch.loop_command,
            batch.conversation_command,
            *batch.prepared_effects_commands,
            batch.terminal_work_command,
        )
    else:
        commands = (
            batch.loop_rejection_command,
            batch.rejected_terminalization_command,
            batch.conversation_no_change_command,
            batch.terminal_work_command,
        )
    return _sha(
        json.dumps(
            {
                "kind": batch.kind,
                "operation": batch.operation,
                "identity": batch.identity.model_dump(mode="json"),
                "expected": batch.expected.model_dump(mode="json"),
                "commands": [command.fingerprint for command in commands],
                "records": [
                    (
                        record.owner,
                        record.record_kind,
                        record.record_id,
                        record.schema_id,
                        record.fingerprint,
                    )
                    for record in batch.complete_records
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
