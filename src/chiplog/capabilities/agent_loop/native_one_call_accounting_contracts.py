"""Bounded owner exchange for one cancelled native call.

The exchange deliberately carries the original canonical records.  It is an
accounting preparation only: the caller has not acquired a recovery cut,
publication authority, or permission to continue a Run.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .call_acceptance_contracts import CallSubjectHead
from .execution_preparation_bindings_contracts import NativeExecutionPreparationBindingsV2
from .execution_recovery_observations import ExecutionRecoveryDTO
from .recovery_contracts import Digest, Identity
from .recovery_frontier_contracts import InitializedCall, TerminalCallFrontier

NATIVE_ONE_CALL_ACCOUNTING_REQUEST_SCHEMA = (
    "chiplog.agent-loop.prepare-native-one-call-accounting.v1"
)
NATIVE_ONE_CALL_ACCOUNTING_RESULT_SCHEMA = (
    "chiplog.agent-loop.prepared-native-one-call-accounting.v1"
)
NATIVE_ONE_CALL_ACCOUNTING_V2_REQUEST_SCHEMA = (
    "chiplog.agent-loop.prepare-native-one-call-accounting.v2"
)
NATIVE_ONE_CALL_ACCOUNTING_V2_RESULT_SCHEMA = (
    "chiplog.agent-loop.prepared-native-one-call-accounting.v2"
)


class PrepareNativeOneCallCancelledAccounting(ExecutionRecoveryDTO):
    """Exact retained wire for a single pre-accept ``request_self_effect`` call."""

    kind: Literal["PREPARE_NATIVE_ONE_CALL_CANCELLED_ACCOUNTING_V1"] = (
        "PREPARE_NATIVE_ONE_CALL_CANCELLED_ACCOUNTING_V1"
    )
    command_id: Identity
    tenant_id: Identity
    captured_run: CallSubjectHead
    captured_run_bytes: bytes = Field(min_length=1)
    # Retained selected fanout uses a ``record:`` content reference; the
    # physical Run locator below deliberately retains the distinct ``loop:`` head.
    selected_sealed_run: CallSubjectHead
    physical_sealed_run: CallSubjectHead
    selected_sealed_run_bytes: bytes = Field(min_length=1)
    selected_response: CallSubjectHead
    selected_response_bytes: bytes = Field(min_length=1)
    initialized: CallSubjectHead
    initialized_record_bytes: bytes = Field(min_length=1)
    acceptance: InitializedCall
    terminal: CallSubjectHead
    terminal_record_bytes: bytes = Field(min_length=1)
    result: CallSubjectHead
    result_record_bytes: bytes = Field(min_length=1)
    frontier_registry: CallSubjectHead
    frontier_registry_bytes: bytes = Field(min_length=1)


class PreparedNativeOneCallCancelledAccounting(ExecutionRecoveryDTO):
    """Inert owner result for the single validated terminal frontier."""

    kind: Literal["PREPARED_NATIVE_ONE_CALL_CANCELLED_ACCOUNTING_V1"] = (
        "PREPARED_NATIVE_ONE_CALL_CANCELLED_ACCOUNTING_V1"
    )
    source_request_fingerprint: Digest
    selected_response: CallSubjectHead
    sealed_manifest_fingerprint: Digest
    frontier_registry: CallSubjectHead
    complete_ordered_calls: tuple[TerminalCallFrontier, ...] = Field(min_length=1, max_length=1)


class NativeOneCallAccountingSourceStampV2(ExecutionRecoveryDTO):
    """Physical authority snapshot retained with the V24 request."""

    tenant_id: Identity
    database_identity: tuple[Identity, int, int]
    tenant_commit_sequence: int = Field(ge=0)
    authority_materialization_commitment: Digest


class PrepareNativeOneCallCancelledAccountingV2(ExecutionRecoveryDTO):
    """V24 wrapper binding the retained V1 wire to its original Prepare."""

    kind: Literal["PREPARE_NATIVE_ONE_CALL_CANCELLED_ACCOUNTING_V2"] = (
        "PREPARE_NATIVE_ONE_CALL_CANCELLED_ACCOUNTING_V2"
    )
    request: PrepareNativeOneCallCancelledAccounting
    preparation_bindings: NativeExecutionPreparationBindingsV2
    source_stamp: NativeOneCallAccountingSourceStampV2


class PreparedNativeOneCallCancelledAccountingV2(ExecutionRecoveryDTO):
    """Inert V24 result, fingerprinted over the complete canonical V2 request."""

    kind: Literal["PREPARED_NATIVE_ONE_CALL_CANCELLED_ACCOUNTING_V2"] = (
        "PREPARED_NATIVE_ONE_CALL_CANCELLED_ACCOUNTING_V2"
    )
    source_request_fingerprint: Digest
    result: PreparedNativeOneCallCancelledAccounting
