"""Additive owner contract for complete ordered accounting of one response seal.

This exchange produces inert terminal accounting only. It does not authorize a
continuation, dispatch, or publication.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .call_acceptance_contracts import CallSubjectHead
from .execution_recovery_observations import ExecutionRecoveryDTO
from .recovery_contracts import Digest, Identity
from .recovery_frontier_contracts import TerminalCallFrontier

NATIVE_SEALED_ACCOUNTING_V3_REQUEST_SCHEMA = (
    "chiplog.agent-loop.prepare-native-sealed-accounting.v3"
)
NATIVE_SEALED_ACCOUNTING_V3_RESULT_SCHEMA = (
    "chiplog.agent-loop.prepared-native-sealed-accounting.v3"
)


class PrepareNativeSealedAccountingV3(ExecutionRecoveryDTO):
    """Exact response seal selection and its complete ordered terminal members."""

    kind: Literal["PREPARE_NATIVE_SEALED_ACCOUNTING_V3"] = "PREPARE_NATIVE_SEALED_ACCOUNTING_V3"
    command_id: Identity
    selected_response: CallSubjectHead
    sealed_manifest_fingerprint: Digest
    ordered_sealed_call_ids: tuple[Identity, ...] = Field(min_length=1)
    complete_ordered_frontiers: tuple[TerminalCallFrontier, ...] = Field(min_length=1)


class PreparedNativeSealedAccountingV3(ExecutionRecoveryDTO):
    """Inert, exhaustive per-seal terminal accounting; grants no next-step authority."""

    kind: Literal["PREPARED_NATIVE_SEALED_ACCOUNTING_V3"] = "PREPARED_NATIVE_SEALED_ACCOUNTING_V3"
    source_request_fingerprint: Digest
    selected_response: CallSubjectHead
    sealed_manifest_fingerprint: Digest
    complete_ordered_calls: tuple[TerminalCallFrontier, ...] = Field(min_length=1)
