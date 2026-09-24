"""Inert conversation-preparation wire contracts.

These values describe proposed conversation members.  Decoding them does not
authenticate a source, select a journal decision, or publish a history row.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol, Self, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.delivery_contracts import EndpointSelection, ExactHead
from chiplog.capabilities.agent_loop.delivery_preparation import DeliveryAcceptanceProposal
from chiplog.capabilities.agent_loop.execution_completion_contracts import (
    PreparedExecutionCompletion,
    PreparedExecutionCompletionReject,
    PrepareExecutionCompletion,
)
from chiplog.capabilities.agent_loop.execution_first_path_completion_contracts import (
    decode_completion_request,
)
from chiplog.capabilities.agent_loop.execution_initialization_contracts import (
    SelectedAdmittedRunInput,
)
from chiplog.capabilities.agent_loop.execution_recovery_observations import (
    ExecutionTerminalManifest,
)
from chiplog.capabilities.agent_loop.execution_run_versions import ExecutionRun
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present

from .r9_boundary import ConversationEntry
from .workspace_boundary import DisclosureEnvelope


class _Frozen(BaseModel):
    """Strict Pydantic-v2 base; ``V1`` in names identifies the wire version."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )

    def canonical_json_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


OWNER: Literal["conversation"] = "conversation"
ACCEPTED_ENTRY_SCHEMA: Literal["chiplog.conversation.accepted-entry.v2"] = (
    "chiplog.conversation.accepted-entry.v2"
)
PREPARATION_DOMAIN: Literal["chiplog.conversation.preparation.v1"] = (
    "chiplog.conversation.preparation.v1"
)
NO_CHANGE_DOMAIN: Literal["chiplog.conversation.no-change.v1"] = (
    "chiplog.conversation.no-change.v1"
)
ADMITTED_INPUT_SOURCE_SCHEMA: Literal["chiplog.agent-loop.selected-admitted-run-input.v1"] = (
    "chiplog.agent-loop.selected-admitted-run-input.v1"
)
COMPLETION_SOURCE_SCHEMA_V2: Literal["chiplog.agent-loop.execution-record.v2"] = (
    "chiplog.agent-loop.execution-record.v2"
)
COMPLETION_SOURCE_SCHEMA_V3: Literal["chiplog.agent-loop.execution-record.v3"] = (
    "chiplog.agent-loop.execution-record.v3"
)


class ConversationCanonicalMemberV2(_Frozen):
    """Exact proposed physical row wrapper for one accepted conversation entry."""

    owner: Literal["conversation"] = OWNER
    record_kind: Literal["ACCEPTED_ENTRY"] = "ACCEPTED_ENTRY"
    schema_id: Literal["chiplog.conversation.accepted-entry.v2"] = ACCEPTED_ENTRY_SCHEMA
    record_id: str = Field(min_length=1)
    canonical_bytes: bytes = Field(min_length=1)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class ConversationPreparationIntegrityError(ValueError):
    """A bounded local failure interpreting one proposed v2 conversation row."""


_PREVIOUS_ENTRY_ADAPTER: TypeAdapter[Annotated[Absent | Present, Field(discriminator="kind")]] = (
    TypeAdapter(Annotated[Absent | Present, Field(discriminator="kind")])
)
_EXECUTION_RUN_ADAPTER: TypeAdapter[ExecutionRun] = TypeAdapter(ExecutionRun)


def conversation_source_heads_json(
    source_selected_decision: CallSubjectHead,
    source_physical_record: CallSubjectHead,
    expected_previous_entry: Absent | Present,
) -> str:
    """Encode source heads in their fixed record-payload order."""
    return _canonical_json(
        [
            source_selected_decision.model_dump(mode="json"),
            source_physical_record.model_dump(mode="json"),
            expected_previous_entry.model_dump(mode="json"),
        ]
    ).decode("utf-8")


def _decode_source_heads_json(raw: str) -> None:
    try:
        values = json.loads(raw)
        if (
            not isinstance(values, list)
            or len(values) != 3
            or _canonical_json(values).decode() != raw
        ):
            raise ValueError("source heads are not canonical fixed-order JSON")
        CallSubjectHead.model_validate(values[0])
        CallSubjectHead.model_validate(values[1])
        _PREVIOUS_ENTRY_ADAPTER.validate_python(values[2])
    except Exception as error:
        raise ConversationPreparationIntegrityError("malformed source heads JSON") from error


def conversation_recipient_binding_json(selection: EndpointSelection) -> str:
    return _canonical_json(selection.model_dump(mode="json")).decode("utf-8")


def _decode_recipient_binding_json(raw: str) -> None:
    try:
        value = json.loads(raw)
        if _canonical_json(value).decode() != raw:
            raise ValueError("recipient binding is not canonical JSON")
        TypeAdapter(EndpointSelection).validate_json(raw)
    except Exception as error:
        raise ConversationPreparationIntegrityError("malformed recipient binding JSON") from error


@dataclass(frozen=True)
class DecodedConversationCanonicalMemberV2:
    member: ConversationCanonicalMemberV2
    entry: ConversationEntry
    source_kind: Literal["ADMITTED_INPUT", "COMPLETION"]
    source_request_fingerprint: str
    delivery_id: str | None
    recipient_binding_json: str | None
    source_heads_json: str


def conversation_member_payload(
    *,
    source_kind: Literal["ADMITTED_INPUT", "COMPLETION"],
    source_request_fingerprint: str,
    entry: ConversationEntry,
    delivery_id: str | None,
    recipient_binding_json: str | None,
    source_heads_json: str,
) -> dict[str, object]:
    """Build the fixed v2 record preimage without a self-hash field."""
    if source_kind == "ADMITTED_INPUT" and (
        delivery_id is not None or recipient_binding_json is not None
    ):
        raise ConversationPreparationIntegrityError(
            "admitted input does not carry delivery or recipient binding"
        )
    if source_kind == "COMPLETION" and (
        not delivery_id or not recipient_binding_json
    ):
        raise ConversationPreparationIntegrityError(
            "completion requires delivery and recipient binding"
        )
    _decode_source_heads_json(source_heads_json)
    if recipient_binding_json is not None:
        _decode_recipient_binding_json(recipient_binding_json)
    return {
        "schema_id": ACCEPTED_ENTRY_SCHEMA,
        "source_kind": source_kind,
        "source_request_fingerprint": source_request_fingerprint,
        "entry_json": entry.model_dump_json(),
        "delivery_id": delivery_id,
        "recipient_binding_json": recipient_binding_json,
        "source_heads_json": source_heads_json,
    }


def make_conversation_canonical_member(
    *,
    source_kind: Literal["ADMITTED_INPUT", "COMPLETION"],
    source_request_fingerprint: str,
    entry: ConversationEntry,
    delivery_id: str | None,
    recipient_binding_json: str | None,
    source_heads_json: str,
) -> ConversationCanonicalMemberV2:
    """Encode exactly one immutable v2 member; this does not validate its sources."""
    payload = conversation_member_payload(
        source_kind=source_kind,
        source_request_fingerprint=source_request_fingerprint,
        entry=entry,
        delivery_id=delivery_id,
        recipient_binding_json=recipient_binding_json,
        source_heads_json=source_heads_json,
    )
    canonical_bytes = _canonical_json(payload)
    return ConversationCanonicalMemberV2(
        record_id=entry.entry_id,
        canonical_bytes=canonical_bytes,
        fingerprint=hashlib.sha256(canonical_bytes).hexdigest(),
    )


def decode_conversation_canonical_member(
    member: ConversationCanonicalMemberV2,
) -> DecodedConversationCanonicalMemberV2:
    """Strictly decode one fixed v2 wrapper while retaining its supplied bytes."""
    if member.owner != OWNER or member.record_kind != "ACCEPTED_ENTRY":
        raise ConversationPreparationIntegrityError("unexpected conversation member envelope")
    if member.schema_id != ACCEPTED_ENTRY_SCHEMA:
        raise ConversationPreparationIntegrityError("unknown conversation record schema")
    if hashlib.sha256(member.canonical_bytes).hexdigest() != member.fingerprint:
        raise ConversationPreparationIntegrityError("member fingerprint differs from bytes")
    try:
        payload = json.loads(member.canonical_bytes)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ConversationPreparationIntegrityError(
            "malformed conversation canonical JSON"
        ) from error
    if not isinstance(payload, dict) or set(payload) != {
        "schema_id",
        "source_kind",
        "source_request_fingerprint",
        "entry_json",
        "delivery_id",
        "recipient_binding_json",
        "source_heads_json",
    }:
        raise ConversationPreparationIntegrityError(
            "conversation payload keys differ from v2 schema"
        )
    if _canonical_json(payload) != member.canonical_bytes:
        raise ConversationPreparationIntegrityError("conversation payload is not canonical JSON")
    try:
        schema_id = payload["schema_id"]
        source_kind = payload["source_kind"]
        fingerprint = payload["source_request_fingerprint"]
        entry_json = payload["entry_json"]
        delivery_id = payload["delivery_id"]
        recipient_binding_json = payload["recipient_binding_json"]
        source_heads_json = payload["source_heads_json"]
        if (
            schema_id != ACCEPTED_ENTRY_SCHEMA
            or source_kind not in {"ADMITTED_INPUT", "COMPLETION"}
            or not isinstance(fingerprint, str)
            or len(fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in fingerprint)
            or not isinstance(entry_json, str)
            or not isinstance(source_heads_json, str)
            or (delivery_id is not None and not isinstance(delivery_id, str))
            or (recipient_binding_json is not None and not isinstance(recipient_binding_json, str))
        ):
            raise ValueError("payload field differs from v2 schema")
        entry = ConversationEntry.model_validate_json(entry_json)
    except Exception as error:
        raise ConversationPreparationIntegrityError("malformed conversation payload") from error
    if entry.entry_id != member.record_id:
        raise ConversationPreparationIntegrityError("member record id differs from entry")
    if entry.model_dump_json() != entry_json:
        raise ConversationPreparationIntegrityError("entry JSON is not canonical model JSON")
    _decode_source_heads_json(source_heads_json)
    if recipient_binding_json is not None:
        _decode_recipient_binding_json(recipient_binding_json)
    if source_kind == "ADMITTED_INPUT":
        if delivery_id is not None or recipient_binding_json is not None:
            raise ConversationPreparationIntegrityError("admitted input has delivery metadata")
    elif not delivery_id or not recipient_binding_json:
        raise ConversationPreparationIntegrityError("completion lacks delivery metadata")
    return DecodedConversationCanonicalMemberV2(
        member=member,
        entry=entry,
        source_kind=source_kind,
        source_request_fingerprint=fingerprint,
        delivery_id=delivery_id,
        recipient_binding_json=recipient_binding_json,
        source_heads_json=source_heads_json,
    )


class ConversationAdmittedInputSourceV1(_Frozen):
    kind: Literal["ADMITTED_INPUT"] = "ADMITTED_INPUT"
    selected_admitted_input: CallSubjectHead


class ConversationCompletionSourceV1(_Frozen):
    kind: Literal["COMPLETION"] = "COMPLETION"
    captured_run: CallSubjectHead
    selected_attempt: CallSubjectHead


ConversationSourcePayloadV1 = Annotated[
    ConversationAdmittedInputSourceV1 | ConversationCompletionSourceV1,
    Field(discriminator="kind"),
]


class ConversationSourceCutV1(_Frozen):
    """Preexisting observations at a conversation preparation cut, never outputs."""

    schema_id: Literal["chiplog.conversation.source-cut.v1"] = "chiplog.conversation.source-cut.v1"
    tenant_id: str = Field(min_length=1)
    database_id: str = Field(min_length=1)
    tenant_commit_sequence: int = Field(ge=0, le=2**64 - 1)
    materialization_commitment: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_previous_entry: Annotated[Absent | Present, Field(discriminator="kind")]
    source_selected_decision: CallSubjectHead
    source_physical_record: CallSubjectHead
    source_schema_id: Literal[
        "chiplog.agent-loop.selected-admitted-run-input.v1",
        "chiplog.agent-loop.execution-record.v2",
        "chiplog.agent-loop.execution-record.v3",
    ]
    source_bytes: bytes = Field(min_length=1)
    source: ConversationSourcePayloadV1

    @model_validator(mode="after")
    def exact_preexisting_source(self) -> Self:
        if isinstance(self.source, ConversationAdmittedInputSourceV1):
            if self.source_schema_id != ADMITTED_INPUT_SOURCE_SCHEMA:
                raise ValueError("admitted input source uses the wrong registered schema")
            decoded = cast(
                SelectedAdmittedRunInput,
                _decode_exact_canonical_bytes(self.source_bytes, SelectedAdmittedRunInput),
            )
            if decoded.kind != "SELECTED_ADMITTED_RUN_INPUT_V1":
                raise ValueError("admitted input source has the wrong body tag")
        elif self.source_schema_id in {
            COMPLETION_SOURCE_SCHEMA_V2,
            COMPLETION_SOURCE_SCHEMA_V3,
        }:
            decoded_run = _decode_exact_execution_run(self.source_bytes)
            if decoded_run.schema_id != self.source_schema_id:
                raise ValueError("completion source schema differs from its body")
        else:
            raise ValueError("completion source uses the wrong registered schema")
        return self


class ConversationCompletionEntryV1(_Frozen):
    delivery_id: str = Field(min_length=1)
    recipient_binding: EndpointSelection
    entry: ConversationEntry

    @model_validator(mode="after")
    def assistant_entry(self) -> Self:
        if self.entry.role != "assistant":
            raise ValueError("completion conversation entry must have assistant role")
        return self


def _decode_exact_canonical_bytes(raw: bytes, decoder: type[BaseModel]) -> BaseModel:
    try:
        decoded = decoder.model_validate_json(raw)
    except Exception as error:
        raise ValueError("original typed bytes do not decode with their required schema") from error
    canonical_bytes = getattr(decoded, "canonical_bytes", None)
    if not callable(canonical_bytes) or canonical_bytes() != raw:
        raise ValueError("original typed bytes are not the exact canonical owner bytes")
    return decoded


def _decode_exact_execution_run(raw: bytes) -> ExecutionRun:
    try:
        decoded: ExecutionRun = _EXECUTION_RUN_ADAPTER.validate_json(raw)
    except Exception as error:
        raise ValueError(
            "original execution Run bytes do not decode with a registered schema"
        ) from error
    canonical_bytes = getattr(decoded, "canonical_bytes", None)
    if not callable(canonical_bytes) or canonical_bytes() != raw:
        raise ValueError("original execution Run bytes are not exact canonical owner bytes")
    return decoded


def _decode_exact_completion_request(raw: bytes) -> None:
    try:
        decoded = decode_completion_request(raw)
    except Exception as error:
        raise ValueError("original completion request bytes do not decode") from error
    if decoded.canonical_bytes() != raw:
        raise ValueError("original completion request bytes are not canonical")


class PrepareConversationAdmittedInputV1(_Frozen):
    kind: Literal["PREPARE_CONVERSATION_ADMITTED_INPUT_V1"] = (
        "PREPARE_CONVERSATION_ADMITTED_INPUT_V1"
    )
    schema_id: Literal["chiplog.conversation.prepare-admitted-input.v1"] = (
        "chiplog.conversation.prepare-admitted-input.v1"
    )
    command_id: str = Field(min_length=1)
    source_cut: ConversationSourceCutV1
    original_selected_admitted_input_bytes: bytes = Field(min_length=1)
    original_selected_admitted_input_head: CallSubjectHead
    original_normalized_principal_text_bytes: bytes = Field(min_length=1)
    origin_channel_id: str = Field(min_length=1)
    visible_channels: tuple[str, ...] = Field(min_length=1)
    envelope: DisclosureEnvelope
    proposed_entry: ConversationEntry

    @model_validator(mode="after")
    def admitted_input_shape(self) -> Self:
        if not isinstance(self.source_cut.source, ConversationAdmittedInputSourceV1):
            raise ValueError("admitted input requires admitted source cut")
        if self.proposed_entry.role != "principal":
            raise ValueError("admitted input conversation entry must have principal role")
        _decode_exact_canonical_bytes(
            self.original_selected_admitted_input_bytes, SelectedAdmittedRunInput
        )
        return self


class PrepareConversationCompletionV1(_Frozen):
    kind: Literal["PREPARE_CONVERSATION_COMPLETION_V1"] = "PREPARE_CONVERSATION_COMPLETION_V1"
    schema_id: Literal["chiplog.conversation.prepare-completion.v1"] = (
        "chiplog.conversation.prepare-completion.v1"
    )
    command_id: str = Field(min_length=1)
    source_cut: ConversationSourceCutV1
    original_completion_request_bytes: bytes = Field(min_length=1)
    loop_preparation_bytes: bytes = Field(min_length=1)
    original_delivery_proposal_bytes: bytes = Field(min_length=1)
    proposed_terminal_run: ExecutionRun
    proposed_terminal_run_head: CallSubjectHead
    proposed_acceptance_head: ExactHead
    proposed_terminal_manifest: ExecutionTerminalManifest
    proposed_terminal_manifest_head: CallSubjectHead
    proposed_accepted_delivery_manifest_bytes: bytes = Field(min_length=1)
    ordered_assistant_entries: tuple[ConversationCompletionEntryV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def completion_shape(self) -> Self:
        if not isinstance(self.source_cut.source, ConversationCompletionSourceV1):
            raise ValueError("completion requires completion source cut")
        if self.source_cut.source_schema_id != self.proposed_terminal_run.schema_id:
            raise ValueError("completion source and proposed terminal Run schema differ")
        _decode_exact_completion_request(self.original_completion_request_bytes)
        _decode_exact_canonical_bytes(self.loop_preparation_bytes, PreparedExecutionCompletion)
        proposal = cast(
            DeliveryAcceptanceProposal,
            _decode_exact_canonical_bytes(
                self.original_delivery_proposal_bytes, DeliveryAcceptanceProposal
            ),
        )
        if proposal.manifest.canonical_bytes() != self.proposed_accepted_delivery_manifest_bytes:
            raise ValueError("accepted delivery manifest bytes differ from original proposal")
        return self


class PrepareConversationCompletionRejectedV1(_Frozen):
    kind: Literal["PREPARE_CONVERSATION_COMPLETION_REJECTED_V1"] = (
        "PREPARE_CONVERSATION_COMPLETION_REJECTED_V1"
    )
    schema_id: Literal["chiplog.conversation.prepare-completion-rejected.v1"] = (
        "chiplog.conversation.prepare-completion-rejected.v1"
    )
    command_id: str = Field(min_length=1)
    source_cut: ConversationSourceCutV1
    original_completion_request_bytes: bytes = Field(min_length=1)
    loop_rejection_bytes: bytes = Field(min_length=1)
    proposed_terminal_run: ExecutionRun
    proposed_terminal_run_head: CallSubjectHead
    preserved_trace: CallSubjectHead

    @model_validator(mode="after")
    def rejected_completion_shape(self) -> Self:
        if not isinstance(self.source_cut.source, ConversationCompletionSourceV1):
            raise ValueError("completion rejection requires completion source cut")
        if self.source_cut.source_schema_id != self.proposed_terminal_run.schema_id:
            raise ValueError("rejection source and proposed terminal Run schema differ")
        _decode_exact_canonical_bytes(
            self.original_completion_request_bytes, PrepareExecutionCompletion
        )
        rejected = cast(
            PreparedExecutionCompletionReject,
            _decode_exact_canonical_bytes(
                self.loop_rejection_bytes, PreparedExecutionCompletionReject
            ),
        )
        if rejected.preserved_trace != self.preserved_trace:
            raise ValueError("preserved trace differs from original rejection")
        return self


def conversation_source_request_fingerprint(
    request: PrepareConversationAdmittedInputV1
    | PrepareConversationCompletionV1
    | PrepareConversationCompletionRejectedV1,
) -> str:
    """Hash the complete canonical request under the new preparation domain."""
    return hashlib.sha256(
        _canonical_json(
            {
                "domain": PREPARATION_DOMAIN,
                "request": json.loads(request.canonical_json_bytes()),
            }
        )
    ).hexdigest()


def conversation_complete_owner_commitment(
    members: tuple[ConversationCanonicalMemberV2, ...],
) -> str:
    """Commit the ordered v2 member identities without feeding back into a member."""
    return hashlib.sha256(
        _canonical_json(
            [(member.record_id, member.schema_id, member.fingerprint) for member in members]
        )
    ).hexdigest()


def conversation_no_change_commitment() -> str:
    return hashlib.sha256(_canonical_json({"domain": NO_CHANGE_DOMAIN, "members": []})).hexdigest()


class PreparedConversationAdmittedInputV1(_Frozen):
    kind: Literal["PREPARED_CONVERSATION_ADMITTED_INPUT_V1"] = (
        "PREPARED_CONVERSATION_ADMITTED_INPUT_V1"
    )
    schema_id: Literal["chiplog.conversation.prepared-admitted-input.v1"] = (
        "chiplog.conversation.prepared-admitted-input.v1"
    )
    source_request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    member: ConversationCanonicalMemberV2
    complete_owner_commitment: str = Field(pattern=r"^[0-9a-f]{64}$")


class PreparedConversationCompletionV1(_Frozen):
    kind: Literal["PREPARED_CONVERSATION_COMPLETION_V1"] = "PREPARED_CONVERSATION_COMPLETION_V1"
    schema_id: Literal["chiplog.conversation.prepared-completion.v1"] = (
        "chiplog.conversation.prepared-completion.v1"
    )
    source_request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    ordered_members: tuple[ConversationCanonicalMemberV2, ...] = Field(min_length=1)
    complete_owner_commitment: str = Field(pattern=r"^[0-9a-f]{64}$")


class PreparedConversationCompletionRejectedV1(_Frozen):
    kind: Literal["PREPARED_CONVERSATION_COMPLETION_REJECTED_V1"] = (
        "PREPARED_CONVERSATION_COMPLETION_REJECTED_V1"
    )
    schema_id: Literal["chiplog.conversation.prepared-completion-rejected.v1"] = (
        "chiplog.conversation.prepared-completion-rejected.v1"
    )
    source_request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    ordered_members: tuple[ConversationCanonicalMemberV2, ...] = Field(default=(), max_length=0)
    no_conversation_change_commitment: str = Field(pattern=r"^[0-9a-f]{64}$")


class ConversationPreparationRejectedV1(_Frozen):
    kind: Literal["CONVERSATION_PREPARATION_REJECTED_V1"] = "CONVERSATION_PREPARATION_REJECTED_V1"
    schema_id: Literal["chiplog.conversation.preparation-rejected.v1"] = (
        "chiplog.conversation.preparation-rejected.v1"
    )
    operation: Literal["ADMITTED_INPUT", "COMPLETION", "COMPLETION_REJECTED"]
    code: Literal["SCHEMA", "STALE", "CONFLICT", "DENIED", "INTEGRITY_FAULT"]
    reason: str = Field(min_length=1)


ConversationPreparationRequestV1 = Annotated[
    PrepareConversationAdmittedInputV1
    | PrepareConversationCompletionV1
    | PrepareConversationCompletionRejectedV1,
    Field(discriminator="kind"),
]


AdmittedInputPreparationResultV1 = Annotated[
    PreparedConversationAdmittedInputV1 | ConversationPreparationRejectedV1,
    Field(discriminator="kind"),
]
CompletionPreparationResultV1 = Annotated[
    PreparedConversationCompletionV1 | ConversationPreparationRejectedV1,
    Field(discriminator="kind"),
]
CompletionRejectedPreparationResultV1 = Annotated[
    PreparedConversationCompletionRejectedV1 | ConversationPreparationRejectedV1,
    Field(discriminator="kind"),
]


class ConversationPreparationPort(Protocol):
    async def prepare_admitted_input(
        self, request: PrepareConversationAdmittedInputV1
    ) -> AdmittedInputPreparationResultV1: ...

    async def prepare_completion(
        self, request: PrepareConversationCompletionV1
    ) -> CompletionPreparationResultV1: ...

    async def prepare_completion_rejected(
        self, request: PrepareConversationCompletionRejectedV1
    ) -> CompletionRejectedPreparationResultV1: ...
