"""Broker-private ingress wire shapes. They do not authenticate or admit bytes.

The sole broker writer resolves registered paths, validates independent source
proofs, and owns all token/custody/admission CAS transitions. No worker lease is
required for independently authenticated late evidence.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Identity = Annotated[str, Field(min_length=1)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
UInt64 = Annotated[int, Field(ge=0, le=2**64 - 1)]
Positive = Annotated[int, Field(gt=0, le=2**64 - 1)]
SourceClass = Literal[
    "TELEGRAM_PUSH",
    "TELEGRAM_POLL",
    "CLI",
    "PROVIDER_CALLBACK",
    "PROVIDER_POLL",
    "RECONCILIATION",
    "TOOL_RESULT",
]


class IngressDTO(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, ser_json_bytes="base64", val_json_bytes="base64"
    )


class Head(IngressDTO):
    identity: Identity
    head: Identity
    fingerprint: Digest


class KnownEndpoint(IngressDTO):
    kind: Literal["KNOWN"] = "KNOWN"
    binding: Head


class UnknownEndpoint(IngressDTO):
    kind: Literal["UNKNOWN_PRE_AUTH"] = "UNKNOWN_PRE_AUTH"


class SourceBinding(IngressDTO):
    manifest_row: Head
    source_class: SourceClass
    tenant_id: Identity
    database_id: Identity
    source_identity: Identity
    endpoint_account_binding: Annotated[
        KnownEndpoint | UnknownEndpoint, Field(discriminator="kind")
    ]
    broker_epoch: Identity
    broker_session: Identity
    admission_epoch: Head
    admission_fence: UInt64
    transport_version: Identity


class ReceiptToken(IngressDTO):
    token_id: Identity
    source: SourceBinding
    receive_slot: Identity
    maximum_bytes: Positive
    predecessor: Head | None
    command_fingerprint: Digest


class LossSlot(IngressDTO):
    kind: Literal["ALLOCATED_LOSS_SLOT"] = "ALLOCATED_LOSS_SLOT"
    token: ReceiptToken
    authentication_stage: Literal["PRE_AUTH"] = "PRE_AUTH"


class RawStaged(IngressDTO):
    kind: Literal["RAW_STAGED"] = "RAW_STAGED"
    token: ReceiptToken
    raw_bytes: bytes
    raw_digest: Digest
    predecessor: Head
    authentication_stage: Literal["PRE_AUTH"] = "PRE_AUTH"


class RetainedToken(IngressDTO):
    kind: Literal["PROVEN_SOURCE_RETENTION"] = "PROVEN_SOURCE_RETENTION"
    token: ReceiptToken
    retention_proof: Head


TokenState = Annotated[LossSlot | RawStaged | RetainedToken, Field(discriminator="kind")]


class AuthenticationBase(IngressDTO):
    proof: Head
    source: SourceBinding
    raw_digest: Digest
    principal_contour: Head
    freshness: Head
    replay_identity: Identity
    original_subject: Identity
    contract_version: Identity
    canonicalization_version: Identity


class ProviderAuthentication(AuthenticationBase):
    kind: Literal["PROVIDER_TRANSPORT"] = "PROVIDER_TRANSPORT"
    provider_identity: Identity
    account_id: Identity
    endpoint_binding: Head
    credential_key: Head
    audience: Identity


class CliAuthentication(AuthenticationBase):
    kind: Literal["CLI_OS_PEER"] = "CLI_OS_PEER"
    unix_endpoint: Head
    os_peer: Head
    authenticated_cli_session: Head
    credential_binding: Head
    policy_head: Head


class ToolAuthentication(AuthenticationBase):
    kind: Literal["BROKER_TOOL_RESULT"] = "BROKER_TOOL_RESULT"
    authenticated_owner_session: Head
    accepted_call: Head
    issued_result_slot: Head
    runtime_generation: Head


AuthenticationBinding = Annotated[
    ProviderAuthentication | CliAuthentication | ToolAuthentication, Field(discriminator="kind")
]


class DurableCustody(IngressDTO):
    kind: Literal["ADMITTED_DURABLE"] = "ADMITTED_DURABLE"
    token: Head
    raw_bytes: bytes
    raw_digest: Digest
    authentication: AuthenticationBinding
    inbox: Head


class QuarantineCustody(IngressDTO):
    kind: Literal["QUARANTINED_RAW_EVIDENCE"] = "QUARANTINED_RAW_EVIDENCE"
    token: Head
    raw_bytes: bytes
    raw_digest: Digest
    authentication: AuthenticationBinding
    parser_id: Identity
    parser_version: Identity
    error_type: Identity
    inspection_retry_policy: Head
    quarantine_budget: Head


class RetainedRetry(IngressDTO):
    kind: Literal["RETRY_WITH_SOURCE_CUSTODY"] = "RETRY_WITH_SOURCE_CUSTODY"
    token: Head
    continuing_retention_proof: Head


class TerminalReject(IngressDTO):
    kind: Literal["TERMINAL_REJECT"] = "TERMINAL_REJECT"
    token: Head
    deterministic_non_evidence_reason: Head


class LossObligation(IngressDTO):
    kind: Literal["LOSS_OBLIGATION"] = "LOSS_OBLIGATION"
    token: Head
    receive_slot: Identity
    custody_loss_cut: Identity


CustodyState = Annotated[
    DurableCustody | QuarantineCustody | RetainedRetry | TerminalReject | LossObligation,
    Field(discriminator="kind"),
]


class ClassReserve(IngressDTO):
    source_class: SourceClass | Literal["ORDINARY"]
    byte_quantum: Positive
    maximum_item_bytes: Positive
    deficit_cap: Positive
    physical_item_reserve: Positive
    physical_byte_reserve: Positive
    physical_quarantine_reserve: Positive
    ready_depth_limit: Positive
    descendant_snapshot_byte_limit: Positive


class ReadyGeneration(IngressDTO):
    stable_work_id: Identity
    durable_admission_commit_seq: UInt64
    stable_tie_identity: Identity
    exact_head: Head
    accounted_bytes: Positive


class AdmissionBound(IngressDTO):
    bound_id: Identity
    bound_lineage_id: Identity
    epoch: Head
    fairness_version: Identity
    formula_version: Identity
    canonical_class_order: tuple[SourceClass | Literal["ORDINARY"], ...]
    reserve: ClassReserve
    initial_deficit: UInt64
    origin_scheduler_slot: UInt64
    complete_ordered_predecessors: tuple[ReadyGeneration, ...]
    target: ReadyGeneration
    absolute_selection_deadline_slot: UInt64
    # A rebase retains immutable origin/deadline but records its exact remaining cut.
    evaluation_scheduler_slot: UInt64 | None = None


class BlockedRebase(IngressDTO):
    blocked_generation: ReadyGeneration
    blocked_head: Head
    consumed_skip_slot: UInt64
    resulting_deficit: UInt64
    closed_descendant_bounds: tuple[Head, ...]
    replacement_descendant_bounds: tuple[AdmissionBound, ...]
    batch_fingerprint: Digest


class DrainManifest(IngressDTO):
    epoch: Head
    state: Literal["QUIESCING", "DRAINING", "CLOSED"]
    fence: UInt64
    canonical_reserves: tuple[ClassReserve, ...]
    scheduler_trace: tuple[Head, ...]
    all_tokens: tuple[Head, ...]
    all_successors: tuple[Head, ...]
    ready_fifo: tuple[ReadyGeneration, ...]
    blocked_holds: tuple[Head, ...]
    bound_snapshots: tuple[AdmissionBound, ...]
    deficit_heads: tuple[Head, ...]
    producer_quiescence: Head | None
    complete_inventory_fingerprint: Digest
    quarantine_heads: tuple[Head, ...]
    parser_remainder_fences: tuple[tuple[Head, Head], ...]
    settled_token_heads: tuple[tuple[Head, Head], ...]
    physical_occupancy: tuple[Head, ...]
    ready_token_ids: tuple[str | None, ...]
    blocked_token_ids: tuple[str | None, ...]


class PollMember(IngressDTO):
    identity: Identity
    digest: Digest


class PollPageManifest(IngressDTO):
    page_id: Identity
    authentication: AuthenticationBinding
    transport_contract: Literal["DURABLE_CURSOR_BEFORE_REQUEST"]
    request_attempt: Head
    predecessor_cursor: Head
    candidate_cursor: bytes
    raw_page_digest: Digest
    complete_ordered_members: tuple[PollMember, ...]
    manifest_digest: Digest
    schema_version: Identity
    canonicalization_version: Identity


class PollMaterialized(IngressDTO):
    kind: Literal["MATERIALIZED"] = "MATERIALIZED"
    member: PollMember
    inbox: Head


class PollDuplicate(IngressDTO):
    kind: Literal["DUPLICATE_OF"] = "DUPLICATE_OF"
    member: PollMember
    exact_durable_inbox: Head


class PollTerminalReject(IngressDTO):
    kind: Literal["TERMINAL_NON_EVIDENCE_REJECT"] = "TERMINAL_NON_EVIDENCE_REJECT"
    member: PollMember
    deterministic_registered_reason: Head


class PollQuarantined(IngressDTO):
    kind: Literal["QUARANTINED_RAW_EVIDENCE"] = "QUARANTINED_RAW_EVIDENCE"
    member: PollMember
    exact_custody: Head


PollMemberDisposition = Annotated[
    PollMaterialized | PollDuplicate | PollTerminalReject | PollQuarantined,
    Field(discriminator="kind"),
]


class QuarantineMaterialized(IngressDTO):
    kind: Literal["MATERIALIZED"] = "MATERIALIZED"
    custody: Head
    stable_inbox_id: Identity
    exact_inbox_head: Head


class QuarantineTerminalProof(IngressDTO):
    kind: Literal["VERSION_INDEPENDENT_TERMINAL_NON_EVIDENCE"] = (
        "VERSION_INDEPENDENT_TERMINAL_NON_EVIDENCE"
    )
    custody: Head
    byte_level_proof_kind: Identity
    proof_version: Identity
    proof_digest: Digest


class QuarantineRetry(IngressDTO):
    kind: Literal["RETRY_OR_HOLD"] = "RETRY_OR_HOLD"
    custody: Head
    failure_or_unknown: Identity
    next_parser_constraints: Head


QuarantineResult = Annotated[
    QuarantineMaterialized | QuarantineTerminalProof | QuarantineRetry,
    Field(discriminator="kind"),
]


class ParserAttempt(IngressDTO):
    attempt_id: Identity
    exact_prior_retry_head: Head
    custody: Head
    parser_id: Identity
    parser_version: Identity
    original_authentication: AuthenticationBinding
    result: QuarantineResult
