"""Lean original-send custody and recovery records for the native client."""

import hashlib
from typing import Literal

from .contracts import ProviderRecipient, TransmissionAttempt
from .dispatch_authority_contracts import DispatchObservationDTO


class NativeEffectDTO(DispatchObservationDTO):
    schema_id: str
    head: str
    tenant_id: str
    principal_id: str
    original_call_id: str

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


class NativeEffectPreviewV1(NativeEffectDTO):
    schema_id: Literal["chiplog.effects.native-preview.v1"] = "chiplog.effects.native-preview.v1"
    initialized_head: str
    payload: bytes
    bundle_members: tuple[str, ...]
    target: ProviderRecipient
    expires_at_ns: int
    receipt_key_fingerprint: str


class NativeEffectIntentV1(NativeEffectDTO):
    schema_id: Literal["chiplog.effects.native-intent.v1"] = "chiplog.effects.native-intent.v1"
    intent_id: str
    run_id: str
    accepted_head: str
    authority_act_id: str
    preview: NativeEffectPreviewV1


class NativeEffectSendV1(NativeEffectDTO):
    schema_id: Literal["chiplog.effects.native-send.v1"] = "chiplog.effects.native-send.v1"
    intent_head: str
    transmission: TransmissionAttempt
    ticket_bytes: bytes
    gate_request_bytes: bytes


class NativeEffectClaimV1(NativeEffectDTO):
    schema_id: Literal["chiplog.effects.native-claim.v1"] = "chiplog.effects.native-claim.v1"
    send_head: str
    worker: str


class NativeEffectEvidenceV1(NativeEffectDTO):
    schema_id: Literal["chiplog.effects.native-evidence.v1"] = "chiplog.effects.native-evidence.v1"
    send_head: str
    predecessor_evidence: str | None
    raw_receipt: bytes


class NativeEffectInvocationV1(NativeEffectDTO):
    schema_id: Literal["chiplog.effects.native-resolver-invocation.v1"] = (
        "chiplog.effects.native-resolver-invocation.v1"
    )
    send_head: str
    credential_id: str
    session_id: str
    trust_materialization_head: str
    complete_evidence_heads: tuple[str, ...]


class NativeEffectClosureV1(NativeEffectDTO):
    schema_id: Literal["chiplog.effects.native-original-closure.v1"] = (
        "chiplog.effects.native-original-closure.v1"
    )
    obligation_id: str
    send_head: str
    invocation_head: str
    complete_evidence_heads: tuple[str, ...]
    outcome: Literal["CONFIRMED", "FAILED_NO_EFFECT", "PARTIAL_CONFIRMED"]


class NativeEffectReductionV1(NativeEffectDTO):
    schema_id: Literal["chiplog.effects.native-semantic-reduction.v1"] = (
        "chiplog.effects.native-semantic-reduction.v1"
    )
    send_head: str
    closure_head: str | None
    complete_evidence_heads: tuple[str, ...]
    outcome: Literal[
        "OUTCOME_UNKNOWN", "PARTIAL", "CONFIRMED", "FAILED_NO_EFFECT", "PARTIAL_CONFIRMED"
    ]
