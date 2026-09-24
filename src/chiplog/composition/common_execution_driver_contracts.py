"""Public common-driver wire contracts; construction grants no source authority.

The driver retains original ingress evidence for a registered reader to reproduce.
It never carries authentication proof, acknowledgement/cursor permission, or a
provider-send outcome.  Runtime wiring remains responsible for those operations.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Final, Literal, Protocol

from pydantic import Field, model_validator

from chiplog.capabilities.agent_loop.contracts import RunState
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Digest, Present
from chiplog.platform._ingress_contracts import Head, Identity, IngressDTO, SourceBinding
from chiplog.platform.ingress_source_contracts import decode_ingress_source
from chiplog.platform.ingress_transition_contracts import (
    IngressCommandIdentity,
    RetainedIngressSource,
)

DRIVER_CANONICALIZATION_VERSION: Final = "chiplog.common-execution-driver.v1"
_RETAINED_CLI_SCHEMA: Final = "chiplog.ingress.retained-source-observation.v1"
_RETAINED_CLI_PLACEHOLDER: Final = "<root-deployed-retained-reader>"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


class DriverCommandIdentityV1(IngressDTO):
    tenant_id: Identity
    database_id: Identity
    driver_command_id: Identity
    canonicalization_version: Literal["chiplog.common-execution-driver.v1"] = (
        DRIVER_CANONICALIZATION_VERSION
    )
    original_ingress_identity: IngressCommandIdentity
    original_ingress_request_fingerprint: Digest

    @model_validator(mode="after")
    def _bind_original_ingress(self) -> DriverCommandIdentityV1:
        if (self.tenant_id, self.database_id) != (
            self.original_ingress_identity.tenant_id,
            self.original_ingress_identity.database_id,
        ):
            raise ValueError("driver and original ingress tenant/database differ")
        return self


class _SelectedSource(IngressDTO):
    original_ingress_identity: IngressCommandIdentity
    source_binding: SourceBinding
    selected_ingress_decision: Head
    source_head: Head
    retained_source: RetainedIngressSource

    source_class: str
    expected_reader_id: str
    expected_schema_id: str

    @model_validator(mode="after")
    def _bind_selected_source(self) -> _SelectedSource:
        if self.source_binding.source_class != self.source_class:
            raise ValueError("selected source class differs from source binding")
        if self.retained_source.source != self.source_head:
            raise ValueError("retained source head differs from selected source head")
        if self.retained_source.reader_id != self.expected_reader_id:
            raise ValueError("selected source reader is not registered for its route")
        if self.retained_source.schema_id != self.expected_schema_id:
            raise ValueError("selected source schema is not registered for its route")
        if (self.source_binding.tenant_id, self.source_binding.database_id) != (
            self.original_ingress_identity.tenant_id,
            self.original_ingress_identity.database_id,
        ):
            raise ValueError("source binding and original ingress tenant/database differ")
        decode_ingress_source(
            self.retained_source,
            expected_class=self.source_class,
            expected_binding=self.source_binding,
            registered_reader_id=self.expected_reader_id,
        )
        return self


class CliPeerSelectedSourceV1(_SelectedSource):
    kind: Literal["CLI_PEER_SELECTED_SOURCE_V1"] = "CLI_PEER_SELECTED_SOURCE_V1"
    source_class: Literal["CLI"] = "CLI"
    expected_reader_id: Literal["cli-peer-v1"] = "cli-peer-v1"
    expected_schema_id: Literal["chiplog.ingress.source.cli-peer.v1"] = (
        "chiplog.ingress.source.cli-peer.v1"
    )


class CliRetainedSelectedSourceV1(_SelectedSource):
    kind: Literal["CLI_RETAINED_SELECTED_SOURCE_V1"] = "CLI_RETAINED_SELECTED_SOURCE_V1"
    source_class: Literal["CLI"] = "CLI"
    expected_reader_id: Identity
    expected_schema_id: Literal["chiplog.ingress.retained-source-observation.v1"] = (
        _RETAINED_CLI_SCHEMA
    )

    @model_validator(mode="after")
    def _require_deployed_reader(self) -> CliRetainedSelectedSourceV1:
        if self.expected_reader_id == _RETAINED_CLI_PLACEHOLDER:
            raise ValueError("retained CLI reader must be a deployed registry identity")
        return self


class TelegramPushSelectedSourceV1(_SelectedSource):
    kind: Literal["TELEGRAM_PUSH_SELECTED_SOURCE_V1"] = "TELEGRAM_PUSH_SELECTED_SOURCE_V1"
    source_class: Literal["TELEGRAM_PUSH"] = "TELEGRAM_PUSH"
    expected_reader_id: Literal["telegram-push-v1"] = "telegram-push-v1"
    expected_schema_id: Literal["chiplog.ingress.source.telegram-push.v1"] = (
        "chiplog.ingress.source.telegram-push.v1"
    )


class TelegramPollSelectedSourceV1(_SelectedSource):
    kind: Literal["TELEGRAM_POLL_SELECTED_SOURCE_V1"] = "TELEGRAM_POLL_SELECTED_SOURCE_V1"
    source_class: Literal["TELEGRAM_POLL"] = "TELEGRAM_POLL"
    expected_reader_id: Literal["telegram-poll-v1"] = "telegram-poll-v1"
    expected_schema_id: Literal["chiplog.ingress.source.telegram-poll.v1"] = (
        "chiplog.ingress.source.telegram-poll.v1"
    )


DriverSelectedSourceV1 = Annotated[
    CliPeerSelectedSourceV1
    | CliRetainedSelectedSourceV1
    | TelegramPushSelectedSourceV1
    | TelegramPollSelectedSourceV1,
    Field(discriminator="kind"),
]


class DriveInputRequestV1(IngressDTO):
    kind: Literal["DRIVE_INPUT_REQUEST_V1"] = "DRIVE_INPUT_REQUEST_V1"
    schema_id: Literal["chiplog.common-execution-driver.drive-input-request.v1"] = (
        "chiplog.common-execution-driver.drive-input-request.v1"
    )
    identity: DriverCommandIdentityV1
    selected_source: DriverSelectedSourceV1

    @model_validator(mode="after")
    def _bind_source_identity(self) -> DriveInputRequestV1:
        if (
            self.selected_source.original_ingress_identity
            != self.identity.original_ingress_identity
        ):
            raise ValueError("selected source and driver original ingress identity differ")
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical(self.model_dump(mode="json"))

    def original_driver_command_fingerprint(self) -> str:
        return hashlib.sha256(
            b"chiplog.common-execution-driver.request.v1\x00" + self.canonical_bytes()
        ).hexdigest()


class LookupExecutionRequestV1(IngressDTO):
    kind: Literal["LOOKUP_EXECUTION_REQUEST_V1"] = "LOOKUP_EXECUTION_REQUEST_V1"
    schema_id: Literal["chiplog.common-execution-driver.lookup-execution-request.v1"] = (
        "chiplog.common-execution-driver.lookup-execution-request.v1"
    )
    identity: DriverCommandIdentityV1
    original_driver_command_fingerprint: Digest


class AcceptedTerminalDetailV1(IngressDTO):
    kind: Literal["ACCEPTED"] = "ACCEPTED"
    acceptance_head: Head
    delivery_manifest_head: Head
    committed_conversation_projection_head: Head


ConversationProjectionAtRejectV1 = Annotated[Absent | Present, Field(discriminator="kind")]


class SemanticRejectedTerminalDetailV1(IngressDTO):
    kind: Literal["SEMANTIC_REJECTED"] = "SEMANTIC_REJECTED"
    preserved_trace_head: Head
    conversation_projection_before_and_after: ConversationProjectionAtRejectV1
    no_conversation_change_commitment: Digest
    ordered_members: tuple[()] = ()


class OtherTerminalDetailV1(IngressDTO):
    """A selected non-completion terminal branch with no conversation assertion."""

    kind: Literal["ABORTED", "CANCELLED"]
    terminal_source_head: Head
    terminal_manifest_head: Head | None = None


TerminalDetailV1 = Annotated[
    AcceptedTerminalDetailV1 | SemanticRejectedTerminalDetailV1 | OtherTerminalDetailV1,
    Field(discriminator="kind"),
]


class SelectedExecutionReceiptV1(IngressDTO):
    kind: Literal["SELECTED_EXECUTION_RECEIPT_V1"] = "SELECTED_EXECUTION_RECEIPT_V1"
    schema_id: Literal["chiplog.common-execution-driver.selected-execution-receipt.v1"] = (
        "chiplog.common-execution-driver.selected-execution-receipt.v1"
    )
    disposition: Literal["COMMITTED", "EXACT_REPLAY"]
    identity: DriverCommandIdentityV1
    original_driver_command_fingerprint: Digest
    selected_ingress_decision: Head
    selected_custody: Head
    selected_admitted_input: Head
    stable_run_lineage_id: Identity
    selected_run_head: Head
    selected_run_state: RunState
    selected_journal_decision: Head
    commit_sequence: int = Field(ge=0, le=2**64 - 1)
    phase: Literal["INITIALIZED", "RUNNING", "SUSPENDED", "TERMINAL"]
    terminal_detail: TerminalDetailV1 | None = None

    @model_validator(mode="after")
    def _require_terminal_detail(self) -> SelectedExecutionReceiptV1:
        if (self.phase == "TERMINAL") != (self.terminal_detail is not None):
            raise ValueError("terminal detail is required exactly for terminal selected receipts")
        permitted_states = {
            "INITIALIZED": {"CREATED"},
            "RUNNING": {"ACTIVE"},
            "SUSPENDED": {"SUSPENDED"},
            "TERMINAL": {"SUCCEEDED", "ABORTED", "CANCELLED"},
        }
        if self.selected_run_state not in permitted_states[self.phase]:
            raise ValueError("selected Run state is not valid for receipt phase")
        if self.terminal_detail is None:
            return self
        if isinstance(self.terminal_detail, AcceptedTerminalDetailV1):
            if self.selected_run_state != "SUCCEEDED":
                raise ValueError("accepted terminal detail requires a succeeded Run")
        elif isinstance(self.terminal_detail, SemanticRejectedTerminalDetailV1):
            if self.selected_run_state != "ABORTED":
                raise ValueError("semantic rejection detail requires an aborted Run")
        elif self.selected_run_state != self.terminal_detail.kind:
            raise ValueError("generic terminal detail must match the selected Run state")
        return self


class ExecutionPendingReceiptV1(IngressDTO):
    kind: Literal["EXECUTION_PENDING_RECEIPT_V1"] = "EXECUTION_PENDING_RECEIPT_V1"
    schema_id: Literal["chiplog.common-execution-driver.execution-pending-receipt.v1"] = (
        "chiplog.common-execution-driver.execution-pending-receipt.v1"
    )
    identity: DriverCommandIdentityV1
    original_driver_command_fingerprint: Digest
    phase: Literal["NO_RUN", "RUNNING", "SUSPENDED"]
    selected_ingress_decision: Head | None = None
    selected_admitted_input: Head | None = None
    selected_run_head: Head | None = None

    @model_validator(mode="after")
    def _bind_pending_phase(self) -> ExecutionPendingReceiptV1:
        if self.phase == "NO_RUN":
            if self.selected_run_head is not None:
                raise ValueError("no-run pending receipt cannot retain a selected Run head")
        elif (
            self.selected_ingress_decision is None
            or self.selected_admitted_input is None
            or self.selected_run_head is None
        ):
            raise ValueError(
                "running or suspended pending receipt requires ingress, admitted and Run heads"
            )
        return self


class UncertainExecutionPublicationV1(IngressDTO):
    kind: Literal["UNCERTAIN_EXECUTION_PUBLICATION_V1"] = "UNCERTAIN_EXECUTION_PUBLICATION_V1"
    schema_id: Literal["chiplog.common-execution-driver.uncertain-execution-publication.v1"] = (
        "chiplog.common-execution-driver.uncertain-execution-publication.v1"
    )
    identity: DriverCommandIdentityV1
    original_driver_command_fingerprint: Digest
    operation: Identity
    reason: Identity


class ExecutionDriverRejectedV1(IngressDTO):
    kind: Literal["EXECUTION_DRIVER_REJECTED_V1"] = "EXECUTION_DRIVER_REJECTED_V1"
    schema_id: Literal["chiplog.common-execution-driver.execution-driver-rejected.v1"] = (
        "chiplog.common-execution-driver.execution-driver-rejected.v1"
    )
    identity: DriverCommandIdentityV1
    original_driver_command_fingerprint: Digest
    code: Literal["HOLD", "CONFLICT", "STALE", "DENIED", "INVALID_INPUT", "INTEGRITY_FAULT"]
    reason: Identity


CommonExecutionResultV1 = Annotated[
    SelectedExecutionReceiptV1
    | ExecutionPendingReceiptV1
    | UncertainExecutionPublicationV1
    | ExecutionDriverRejectedV1,
    Field(discriminator="kind"),
]


class CommonExecutionDriverPort(Protocol):
    async def drive_input(self, request: DriveInputRequestV1) -> CommonExecutionResultV1: ...

    async def lookup_execution(
        self, request: LookupExecutionRequestV1
    ) -> CommonExecutionResultV1: ...
