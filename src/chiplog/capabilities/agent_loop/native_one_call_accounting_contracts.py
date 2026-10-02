"""Bounded owner exchange for one cancelled native call.

The exchange deliberately carries the original canonical records.  It is an
accounting preparation only: the caller has not acquired a recovery cut,
publication authority, or permission to continue a Run.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .call_acceptance_contracts import CallSubjectHead
from .execution_recovery_observations import ExecutionRecoveryDTO
from .recovery_contracts import Digest, Identity
from .recovery_frontier_contracts import InitializedCall, TerminalCallFrontier

NATIVE_ONE_CALL_ACCOUNTING_REQUEST_SCHEMA = (
    "chiplog.agent-loop.prepare-native-one-call-accounting.v1"
)
NATIVE_ONE_CALL_ACCOUNTING_RESULT_SCHEMA = (
    "chiplog.agent-loop.prepared-native-one-call-accounting.v1"
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
