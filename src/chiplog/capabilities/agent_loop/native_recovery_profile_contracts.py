"""Closed native cancellation profile; selection belongs to the broker.

The physical profile commits the interpretation of the existing native registry.
Constructing this value neither selects it nor authenticates any recovery cut.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from .recovery_contracts import RecoveryDTO
from .recovery_frontier_contracts import RecoveryFrontierRegistry
from .recovery_frontier_registry_contracts import (
    execution_native_one_call_cancellation_frontier_registry_v1,
    execution_native_one_call_recovery_frontier_registry_v2,
)

SCHEMA: Literal["chiplog.execution.native-one-call-recovery-profile.v1"] = (
    "chiplog.execution.native-one-call-recovery-profile.v1"
)
PROFILE_OPERATION = "agent_loop.native-one-call-recovery-profile.v1"


class NativeOneCallRecoveryProfileV1(RecoveryDTO):
    schema_id: Literal["chiplog.execution.native-one-call-recovery-profile.v1"] = SCHEMA
    kind: Literal["NATIVE_ONE_CALL_RECOVERY_PROFILE_V1"] = "NATIVE_ONE_CALL_RECOVERY_PROFILE_V1"
    registry: RecoveryFrontierRegistry
    source_order: Literal["PHYSICAL_SEQUENCE_ID_THEN_CHILD_SCHEMA_SUBJECT_HEAD_V1"] = (
        "PHYSICAL_SEQUENCE_ID_THEN_CHILD_SCHEMA_SUBJECT_HEAD_V1"
    )
    member_order: Literal["REGISTRY_ORDINAL_THEN_SUBJECT_V1"] = "REGISTRY_ORDINAL_THEN_SUBJECT_V1"
    child_projection: Literal["EXACT_CANONICAL_CHILD_OF_AUTHENTICATED_CARRIER_V1"] = (
        "EXACT_CANONICAL_CHILD_OF_AUTHENTICATED_CARRIER_V1"
    )
    absence_proof: Literal["COMPLETE_DECODED_SELECTED_PHYSICAL_OCCURRENCES_V1"] = (
        "COMPLETE_DECODED_SELECTED_PHYSICAL_OCCURRENCES_V1"
    )
    causal_change_set: Literal["RUN_LINEAGE_AND_RESPONSE_CALL_DAG_V1"] = (
        "RUN_LINEAGE_AND_RESPONSE_CALL_DAG_V1"
    )
    evidence_relevance: Literal["EXACT_AUTHENTICATED_ORIGIN_TOKEN_CUSTODY_IS_CONTEXT_V1"] = (
        "EXACT_AUTHENTICATED_ORIGIN_TOKEN_CUSTODY_IS_CONTEXT_V1"
    )
    causal_transitions: tuple[str, ...]
    fingerprint_domain: Literal["chiplog.execution.native-one-call-recovery.v1"] = (
        "chiplog.execution.native-one-call-recovery.v1"
    )


def native_one_call_recovery_profile() -> NativeOneCallRecoveryProfileV1:
    return NativeOneCallRecoveryProfileV1(
        registry=execution_native_one_call_cancellation_frontier_registry_v1(),
        causal_transitions=(
            "RunCreated",
            "RunActivated",
            "TurnStarted",
            "TurnVisibilityAccumulated",
            "ModelAttemptPrepared",
            "ModelAttemptEmitted",
            "ModelResponseCaptured",
            "ModelResponseSealed",
            "CallInitialized",
            "CancelledBeforeAccept",
            "NotExecutedCallResult",
        ),
    )


def decode_native_one_call_recovery_profile(raw: bytes) -> NativeOneCallRecoveryProfileV1:
    profile = NativeOneCallRecoveryProfileV1.model_validate_json(raw)
    if profile.canonical_bytes() != raw or profile != native_one_call_recovery_profile():
        raise ValueError("native recovery profile differs from its closed interpretation")
    return profile


SCHEMA_V2: Literal["chiplog.execution.native-one-call-recovery-profile.v2"] = (
    "chiplog.execution.native-one-call-recovery-profile.v2"
)
PROFILE_OPERATION_V2 = "agent_loop.native-one-call-recovery-profile.v2"


class NativeOneCallRecoveryProfileV2(RecoveryDTO):
    """Immutable closed interpretation for cancellation and accepted-call recovery."""

    schema_id: Literal["chiplog.execution.native-one-call-recovery-profile.v2"] = SCHEMA_V2
    kind: Literal["NATIVE_ONE_CALL_RECOVERY_PROFILE_V2"] = "NATIVE_ONE_CALL_RECOVERY_PROFILE_V2"
    registry: RecoveryFrontierRegistry
    call_branches: tuple[
        Literal["CANCELLED_BEFORE_ACCEPT", "CONSEQUENTIAL_ACCEPTED_RECOVERY"], ...
    ]
    dispatch_outcomes: tuple[
        Literal[
            "CONFIRMED",
            "FAILED_NO_EFFECT",
            "OUTCOME_UNKNOWN",
            "PARTIAL",
            "PARTIAL_CONFIRMED",
        ], ...
    ]
    dispatch_record_kinds: tuple[
        Literal[
            "PLAN_EFFECT_PUBLISHED",
            "INTENT_ACCEPTED",
            "DISPATCH_AUTHORIZED",
            "SEND_COMMITTED",
        ], ...
    ]
    dispatch_outcome_record_kinds: tuple[Literal["EVIDENCE_RECORDED", "RECONCILED"], ...]
    obligation_states: tuple[Literal["OPEN", "CLOSED"], ...]
    source_order: Literal["PHYSICAL_SEQUENCE_ID_THEN_CHILD_SCHEMA_SUBJECT_HEAD_V1"] = (
        "PHYSICAL_SEQUENCE_ID_THEN_CHILD_SCHEMA_SUBJECT_HEAD_V1"
    )
    member_order: Literal["REGISTRY_ORDINAL_THEN_SUBJECT_V1"] = "REGISTRY_ORDINAL_THEN_SUBJECT_V1"
    child_projection: Literal["EXACT_CANONICAL_CHILD_OF_AUTHENTICATED_CARRIER_V1"] = (
        "EXACT_CANONICAL_CHILD_OF_AUTHENTICATED_CARRIER_V1"
    )
    absence_proof: Literal["COMPLETE_DECODED_SELECTED_PHYSICAL_OCCURRENCES_V1"] = (
        "COMPLETE_DECODED_SELECTED_PHYSICAL_OCCURRENCES_V1"
    )
    causal_change_set: Literal["RUN_LINEAGE_CALL_ACCEPTANCE_AND_DISPATCH_V2"] = (
        "RUN_LINEAGE_CALL_ACCEPTANCE_AND_DISPATCH_V2"
    )
    evidence_relevance: Literal["EXACT_AUTHENTICATED_ORIGIN_TOKEN_CUSTODY_IS_CONTEXT_V1"] = (
        "EXACT_AUTHENTICATED_ORIGIN_TOKEN_CUSTODY_IS_CONTEXT_V1"
    )
    causal_transitions: tuple[str, ...]
    fingerprint_domain: Literal["chiplog.execution.native-one-call-recovery.v2"] = (
        "chiplog.execution.native-one-call-recovery.v2"
    )


def native_one_call_recovery_profile_v2() -> NativeOneCallRecoveryProfileV2:
    return NativeOneCallRecoveryProfileV2(
        registry=execution_native_one_call_recovery_frontier_registry_v2(),
        call_branches=("CANCELLED_BEFORE_ACCEPT", "CONSEQUENTIAL_ACCEPTED_RECOVERY"),
        dispatch_outcomes=(
            "CONFIRMED",
            "FAILED_NO_EFFECT",
            "OUTCOME_UNKNOWN",
            "PARTIAL",
            "PARTIAL_CONFIRMED",
        ),
        dispatch_record_kinds=(
            "PLAN_EFFECT_PUBLISHED",
            "INTENT_ACCEPTED",
            "DISPATCH_AUTHORIZED",
            "SEND_COMMITTED",
        ),
        dispatch_outcome_record_kinds=("EVIDENCE_RECORDED", "RECONCILED"),
        obligation_states=("OPEN", "CLOSED"),
        causal_transitions=(
            "RunCreated",
            "RunActivated",
            "TurnStarted",
            "TurnVisibilityAccumulated",
            "ModelAttemptPrepared",
            "ModelAttemptEmitted",
            "ModelResponseCaptured",
            "ModelResponseSealed",
            "CallInitialized",
            "CancelledBeforeAccept",
            "NotExecutedCallResult",
        ),
    )


def decode_native_one_call_recovery_profile_v2(raw: bytes) -> NativeOneCallRecoveryProfileV2:
    profile = NativeOneCallRecoveryProfileV2.model_validate_json(raw)
    if profile.canonical_bytes() != raw or profile != native_one_call_recovery_profile_v2():
        raise ValueError("native recovery profile V2 differs from its closed interpretation")
    return profile


def native_recovery_fingerprint(domain: str, value: dict[str, object]) -> str:
    """Explicit preimage for native members, frontier and complete inventory."""
    raw = json.dumps(
        {"domain": "chiplog.execution.native-one-call-recovery.v1:" + domain, "content": value},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return hashlib.sha256(raw).hexdigest()
