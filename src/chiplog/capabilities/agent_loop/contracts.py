"""Owner-authored immutable R13 boundary. Unsupported recovery remains explicit HOLD."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


class LoopRejected(ValueError):
    """An exact precondition is absent, stale, unsupported or invalid."""


RunState = Literal[
    "CREATED", "ACTIVE", "SUCCEEDED", "SUSPENDED", "SUPERSEDED", "ABORTED", "CANCELLED"
]
AttemptState = Literal[
    "PREPARED_NOT_EMITTED",
    "EMITTED_OUTCOME_UNKNOWN",
    "RESPONSE_CAPTURED",
    "TERMINAL_REJECTED",
    "TERMINAL_ACCEPTED",
]


class BudgetPolicy(Frozen):
    kind: Literal["MAX_TURNS", "NO_POLICY"] = "MAX_TURNS"
    max_turns: int = Field(default=16, gt=0)
    max_request_bytes: int = Field(default=65536, gt=0)
    max_response_bytes: int = Field(default=65536, gt=0)
    max_tool_calls: int = Field(default=8, gt=0)
    max_model_retries: int | None = Field(default=None, ge=0)


class ToolSpec(Frozen):
    name: Literal["propose_planning", "propose_intent"]
    version: Literal["1"] = "1"
    schema_id: str = Field(min_length=1)


class ToolCall(Frozen):
    call_id: str = Field(min_length=1)
    tool: Literal["propose_planning", "propose_intent"]
    text: str = Field(min_length=1, max_length=4096)


class Continue(Frozen):
    kind: Literal["Continue"]
    tool_calls: tuple[ToolCall, ...] = Field(min_length=1)


class EndpointSelection(Frozen):
    kind: Literal["ORIGIN_EXACT", "MODEL_SELECTED_EXACT"]
    ingress_binding_head: str = Field(min_length=1)
    endpoint_head: str = Field(min_length=1)
    endpoint_id: str = Field(min_length=1)
    provider: Literal["hermetic-local"]
    recipient: str = Field(min_length=1)
    canonical_address: str = Field(min_length=1)
    credential_binding_head: str = Field(min_length=1)


class Delivery(Frozen):
    kind: Literal["NonAuthoritativeText", "DeliveryAssertion"]
    text: str | None = Field(default=None, min_length=1, max_length=8192)
    endpoint: EndpointSelection | None = None
    assertion_code: Literal["LOCAL_PLANNING_COMMITTED"] | None = None
    evidence_id: str | None = None


class Complete(Frozen):
    kind: Literal["Complete"]
    deliveries: tuple[Delivery, ...] = Field(min_length=1, max_length=8)


class DisclosureLabel(Frozen):
    lattice_version: Literal["chiplog.disclosure.v1"] = "chiplog.disclosure.v1"
    value: Literal["UNRESTRICTED", "ENDPOINT_RESTRICTED", "DENY_ALL"]
    allowed_endpoints: tuple[str, ...]


class VisibilityMember(Frozen):
    record_id: str = Field(min_length=1)
    revision_head: str = Field(min_length=1)
    content: str
    provenance_head: str = Field(min_length=1)
    label_head: str = Field(min_length=1)
    label: DisclosureLabel
    producer: str = Field(min_length=1)
    surface: str = Field(min_length=1)
    schema_version: Literal["1"] = "1"


class RunView(Frozen):
    run_id: str
    state: RunState
    head: str
    turn_ordinal: int = Field(ge=0)
    turn_head: str | None


class PromptArtifact(Frozen):
    prompt_id: Literal["chiplog.agent-loop"] = "chiplog.agent-loop"
    version: Literal["1"] = "1"
    content_hash: str
    library_version: str
    generator_version: Literal["chiplog.turn-schema.v1"] = "chiplog.turn-schema.v1"
    tools: tuple[ToolSpec, ...]
    response_schema_json: str
    rendered: str


class VisibilityManifest(Frozen):
    tenant: str
    principal: str
    contour_head: str
    run_id: str
    turn_id: str
    call_slot: Literal[0] = 0
    generation: int = Field(ge=0)
    worker_session: str = Field(min_length=1)
    members: tuple[VisibilityMember, ...]
    joined_label: DisclosureLabel
    artifact: PromptArtifact


class ModelAttempt(Frozen):
    attempt_id: str
    lineage_id: str
    generation: int = Field(ge=0)
    state: AttemptState
    head: str
    manifest: VisibilityManifest
    request: str
    provider_contract: Literal["hermetic-model.v1"] = "hermetic-model.v1"
    recipient: Literal["hermetic-model"] = "hermetic-model"
    worker_session: str
    response_base64: str | None = None
    receipt: str | None = None
    rejection: str | None = None


class NoExposureProof(Frozen):
    kind: Literal["REGISTERED_PRE_EMISSION_CAS"] = "REGISTERED_PRE_EMISSION_CAS"
    attempt_id: str
    attempt_head: str
    run_head: str
    worker_session: str
    provider_contract: Literal["hermetic-model.v1"] = "hermetic-model.v1"


class ToolOutcome(Frozen):
    call: ToolCall
    state: Literal["INITIALIZED", "TERMINAL", "RECOVERY_REQUIRED"]
    result: str | None = None
    evidence_id: str | None = None
    proposal_id: str | None = None


class ProposalDisplay(Frozen):
    display_id: str
    proposal_id: str
    revision: int = Field(gt=0)
    run_id: str
    tenant: str
    principal: str
    adoption_act_id: str
    canonical_command: str
    display_text: str
    display_digest: str
    original_binding_base64: str


class LocalPlanningReceipt(Frozen):
    evidence_id: str
    proposal_id: str
    display_id: str
    command_id: str
    revision_id: str
    purpose: str
    canonical_result: str
    result_digest: str


class PlanningProposalPort(Protocol):
    async def display(self, proposal_id: str) -> ProposalDisplay: ...

    async def adopt(
        self, peer: str, display_id: str, digest: str, adoption_act_id: str
    ) -> LocalPlanningReceipt: ...


class Turn(Frozen):
    turn_id: str
    ordinal: int = Field(gt=0)
    head: str
    state: Literal["PREPARING", "CALL_ACTIVE", "RESPONSE_AVAILABLE", "ACCEPTED", "REJECTED"]
    accumulator: tuple[VisibilityMember, ...] = ()
    attempts: tuple[ModelAttempt, ...] = ()
    selector: int = Field(default=0, ge=0)
    sealed_calls: tuple[ToolOutcome, ...] | None = None


class AcceptedDelivery(Frozen):
    delivery_id: str
    payload: Delivery
    rendered: str
    visibility_digest: str
    state: Literal["PENDING_LOCAL"] = "PENDING_LOCAL"


class RunRecord(Frozen):
    tenant: str
    principal: str
    run_id: str
    state: RunState
    head: str
    predecessor: str | None
    prompt: str
    policy: BudgetPolicy
    origin: EndpointSelection
    contour_head: str
    policy_head: str
    worker_session: str
    root_binding: Literal["NOT_APPLICABLE"] = "NOT_APPLICABLE"
    turns: tuple[Turn, ...] = ()
    deliveries: tuple[AcceptedDelivery, ...] = ()
    accepted_text: tuple[str, ...] = ()
    planning_receipts: tuple[LocalPlanningReceipt, ...] = ()
    event: str


class LoopSnapshot(Frozen):
    tenant_head: int
    records: tuple[RunRecord, ...]


class TransitionRequest(Frozen):
    previous: RunRecord | None
    proposed: RunRecord


class TransitionAuthority(Protocol):
    def validate(self, previous: RunRecord | None, proposed: RunRecord) -> None: ...

    def companions(self, record: RunRecord) -> tuple[DurableCompanion, ...]: ...

    def decide(
        self,
        record: RunRecord,
        expected: LoopSnapshot,
        commitment: str,
        companions: tuple[DurableCompanion, ...],
    ) -> None: ...

    def committed(self, record: RunRecord) -> None: ...


class LoopStore(Protocol):
    def snapshot(self) -> LoopSnapshot: ...

    async def publish(
        self,
        record: RunRecord,
        expected: LoopSnapshot,
        validate: Callable[[LoopSnapshot], None] | None = None,
    ) -> None: ...


class ModelPort(Protocol):
    async def invoke(self, attempt: ModelAttempt) -> tuple[bytes, str]: ...


class DurableCompanion(Frozen):
    record_id: str
    owner: str
    schema_id: str
    payload_base64: str


class ContextPort(Protocol):
    async def ingest(self, run_id: str, prompt: str) -> None: ...
    async def context(self, run: RunRecord) -> VisibilityMember: ...


class WorkerSessionPort(Protocol):
    def current_worker(self) -> str: ...


class PromptPort(Protocol):
    async def render(self, context: str) -> PromptArtifact: ...

    def parse(self, raw: bytes, artifact: PromptArtifact) -> Continue | Complete: ...


class LoopPort(Protocol):
    async def create(self, run_id: str, prompt: str, policy: BudgetPolicy) -> RunView: ...

    async def activate(self, run_id: str, expected_head: str) -> RunView: ...

    async def step(self, run_id: str, expected_head: str) -> RunView: ...

    def status(self, run_id: str) -> RunView: ...
