"""First-path completion observations; canonical consistency is not authority.

The broker must independently enumerate selected history and frontier families at
acceptance. These codecs neither authenticate selection nor grant model emission,
completion, or delivery permission. V1 recovery requests remain unchanged.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Annotated, Literal, Self

from pydantic import Field, TypeAdapter, model_validator

from .call_acceptance_contracts import CallSubjectHead, SealedResponseRecord
from .delivery_contracts import ExactHead
from .delivery_preparation import DeliveryObservation
from .execution_completion_contracts import PrepareExecutionCompletion
from .execution_contracts import ExecutionRunRecord
from .execution_initialization_contracts import SelectedAdmittedRunInput
from .execution_recovery_observations import ExecutionRecoveryDTO, RecoverySourceRecord
from .recovery_contracts import Digest, Identity, NonSchedulerFence, Present, UInt64
from .recovery_frontier_contracts import FrontierMember, RecoveryFrontier
from .recovery_frontier_registry_contracts import (
    RECOVERY_FRONTIER_REGISTRY_SCHEMA,
    execution_zero_call_frontier_registry,
    frontier_registry_reference,
)

FIRST_PATH_CUT_SCHEMA: Literal["chiplog.execution.first-path-completion-cut.v2"] = (
    "chiplog.execution.first-path-completion-cut.v2"
)
FIRST_PATH_COMPLETION_SCHEMA: Literal["chiplog.execution.first-path-completion.v2"] = (
    "chiplog.execution.first-path-completion.v2"
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _content_fingerprint(domain: str, value: dict[str, object], excluded: str) -> str:
    content = {key: item for key, item in value.items() if key != excluded}
    return _sha(
        json.dumps(
            {"domain": domain, "content": content},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    )


def first_path_frontier_member_fingerprint(member: FrontierMember) -> str:
    """First-path profile only; never reinterpret an existing recovery preimage."""
    return _content_fingerprint(
        "chiplog.execution.first-path-frontier-member.v2",
        member.model_dump(mode="json"),
        "fingerprint",
    )


def first_path_frontier_fingerprint(frontier: RecoveryFrontier) -> str:
    return _content_fingerprint(
        "chiplog.execution.first-path-frontier.v2",
        frontier.model_dump(mode="json"),
        "fingerprint",
    )


def _ref(subject: str, raw: bytes, head: str | None = None) -> CallSubjectHead:
    digest = _sha(raw)
    return CallSubjectHead(
        subject_id=subject, revision=Present(head=head or "record:" + digest, fingerprint=digest)
    )


def _run_ref(run: ExecutionRunRecord) -> CallSubjectHead:
    return _ref(run.run_id, run.canonical_bytes(), run.head)


def _require(value: bool, reason: str) -> None:
    if not value:
        raise ValueError(reason)


class FirstPathCompletionCutV2(ExecutionRecoveryDTO):
    kind: Literal["FIRST_PATH_COMPLETION_CUT_V2"] = "FIRST_PATH_COMPLETION_CUT_V2"
    schema_id: Literal["chiplog.execution.first-path-completion-cut.v2"] = FIRST_PATH_CUT_SCHEMA
    tenant_id: Identity
    database_id: Identity
    tenant_commit_sequence: UInt64
    materialization_commitment: Digest
    selected_admitted_input: SelectedAdmittedRunInput
    complete_ordered_run_lineage: tuple[ExecutionRunRecord, ...] = Field(min_length=3)
    current_run: CallSubjectHead
    selected_capture: CallSubjectHead
    selected_response_seal: CallSubjectHead
    seal: SealedResponseRecord
    frontier: RecoveryFrontier
    complete_sources: tuple[RecoverySourceRecord, ...] = Field(min_length=1)
    complete_inventory_fingerprint: Digest

    def require_source(self, schema: str, subject: CallSubjectHead, raw: bytes) -> None:
        matches = tuple(
            row
            for row in self.complete_sources
            if (
                row.owner == "agent_loop"
                and row.schema_id == schema
                and row.subject == subject
                and row.canonical_record_bytes == raw
            )
        )
        _require(len(matches) == 1, "missing or duplicate exact retained source")

    @model_validator(mode="after")
    def exact_first_path(self) -> Self:
        lineage = self.complete_ordered_run_lineage
        root, captured, run = lineage[0], lineage[-2], lineage[-1]
        admitted = self.selected_admitted_input
        _require(
            admitted.source_class == "CLI"
            and admitted.tenant_id == self.tenant_id
            and admitted.database_id == self.database_id
            and admitted.commit_sequence <= self.tenant_commit_sequence
            and (
                admitted.principal_id,
                admitted.normalized_prompt,
                admitted.origin,
                admitted.contour_head,
            )
            == (root.principal, root.prompt, root.origin, root.contour_head),
            "admitted CLI input differs from first-path root",
        )
        _require(
            root.state == "CREATED"
            and root.event == "RunCreated"
            and root.predecessor is None
            and not root.turns,
            "invalid first-path root",
        )
        _require(len({row.head for row in lineage}) == len(lineage), "duplicate lineage head")
        for index, row in enumerate(lineage):
            _require(
                (row.tenant, row.principal, row.run_id, row.prompt, row.origin, row.worker_session)
                == (
                    self.tenant_id,
                    root.principal,
                    root.run_id,
                    root.prompt,
                    root.origin,
                    root.worker_session,
                )
                and row.root_binding == "NOT_APPLICABLE"
                and row.suspension_baseline is None
                and not row.original_obligations
                and not row.no_retry_references
                and row.delivery_acceptance is None
                and (index == 0 or row.state == "ACTIVE")
                and (index == 0 or row.predecessor == lineage[index - 1].head)
                and row.head == "loop:" + row.model_copy(update={"head": "pending"}).digest(),
                "foreign, stale or unsupported native lineage",
            )
            _require(len(row.turns) <= 1, "first path has earlier Turn")
            for item in row.turns:
                _require(
                    item.head
                    == "execution-turn:" + item.model_copy(update={"head": "pending"}).digest(),
                    "native Turn head differs",
                )
                for attempt in item.attempts:
                    _require(
                        attempt.head
                        == "execution-attempt:"
                        + attempt.model_copy(update={"head": "pending"}).digest(),
                        "native attempt head differs",
                    )
            if index < len(lineage) - 1:
                _require(
                    all(
                        turn.response_seal is None and turn.initialized_calls is None
                        for turn in row.turns
                    ),
                    "first path has earlier sealed response",
                )
            self.require_source(row.schema_id, _run_ref(row), row.canonical_bytes())
        _require(
            self.current_run == _run_ref(run)
            and self.selected_capture == _run_ref(captured)
            and captured.event == "ModelResponseCaptured"
            and run.event == "ModelCompletionPrepared"
            and len(run.turns) == len(captured.turns) == 1,
            "selected seal Run or capture differs",
        )
        turn, old = run.turns[0], captured.turns[0]
        _require(
            turn.state == old.state == "RESPONSE_AVAILABLE"
            and turn.turn_id == old.turn_id
            and turn.attempts == old.attempts
            and turn.selector == old.selector == 0
            and len(turn.attempts) == 1
            and turn.initialized_calls == (),
            "seal changed captured attempt or calls",
        )
        attempt = turn.attempts[0]
        _require(
            attempt.state == "RESPONSE_CAPTURED"
            and attempt.generation == 0
            and attempt.provider_contract == "hermetic-model.v1"
            and attempt.recipient == "hermetic-model"
            and attempt.live_model is None
            and attempt.worker_session == run.worker_session,
            "unsupported captured attempt",
        )
        seal_ref = _ref(self.seal.response_seal_id, self.seal.canonical_bytes())
        _require(
            self.selected_response_seal == turn.response_seal == seal_ref
            and (self.seal.tenant_id, self.seal.original_run_id, self.seal.original_turn_id)
            == (run.tenant, run.run_id, turn.turn_id)
            and self.seal.captured_response == self.selected_capture
            and self.seal.complete_ordered_initialized == (),
            "foreign or nonzero-call seal",
        )
        self.require_source("chiplog.call.response-seal.v1", seal_ref, self.seal.canonical_bytes())
        sources = self.complete_sources
        _require(
            len({row.physical_record.canonical_bytes() for row in sources}) == len(sources),
            "duplicate physical source",
        )
        for source in sources:
            digest = _sha(source.canonical_record_bytes)
            _require(
                isinstance(source.subject.revision, Present)
                and source.subject.revision.fingerprint == digest
                and isinstance(source.physical_record.revision, Present)
                and source.physical_record.revision.fingerprint == digest
                and isinstance(source.selected_decision.revision, Present),
                "retained source fingerprint or selected marker differs",
            )
        self._validate_frontier(run)
        _require(
            self.complete_inventory_fingerprint == first_path_inventory_fingerprint(self),
            "first-path inventory fingerprint differs",
        )
        return self

    def _validate_frontier(self, run: ExecutionRunRecord) -> None:
        frontier = self.frontier
        _require(
            (frontier.tenant_id, frontier.run_id, frontier.tenant_commit_sequence)
            == (self.tenant_id, run.run_id, self.tenant_commit_sequence)
            and not frontier.ordered_calls,
            "foreign or nonzero-call frontier",
        )
        registry = execution_zero_call_frontier_registry()
        _require(frontier.registry == registry, "first-path registry differs")
        self.require_source(
            RECOVERY_FRONTIER_REGISTRY_SCHEMA,
            frontier_registry_reference(registry),
            registry.canonical_bytes(),
        )
        members = frontier.ordered_members
        turn = run.turns[0]
        turn_ref = _ref(turn.turn_id, turn.canonical_bytes(), turn.head)
        families = tuple(row.family for row in registry.ordered_rows)
        _require(
            set(row.family for row in members) == set(families), "frontier family coverage differs"
        )
        keys = tuple((families.index(row.family), row.subject) for row in members)
        _require(keys == tuple(sorted(set(keys))), "frontier members duplicate or unordered")
        for member in members:
            _require(
                member.fingerprint == first_path_frontier_member_fingerprint(member),
                "frontier member fingerprint differs",
            )
            for marker in member.ordered_heads:
                if isinstance(marker, Present):
                    _require(
                        marker == turn_ref.revision
                        or any(
                            source.subject.revision == marker for source in self.complete_sources
                        ),
                        "frontier head lacks retained source",
                    )
        for family, subject in (
            ("RUN", self.current_run),
            ("TURN", turn_ref),
            ("SEALED_RESPONSE", self.selected_response_seal),
        ):
            matched = tuple(row for row in members if row.family == family)
            _require(
                len(matched) == 1
                and matched[0].subject == subject.subject_id
                and matched[0].ordered_heads == (subject.revision,),
                "frontier selected Run or seal differs",
            )
        for member in members:
            if member.family in {"CALL", "OBLIGATION"}:
                _require(
                    all(head.kind == "ABSENT" for head in member.ordered_heads),
                    "zero-call frontier contains call or obligation",
                )
            if member.family == "EXECUTION_LINEAGE":
                _require(
                    all(head.kind == "NOT_APPLICABLE" for head in member.ordered_heads),
                    "first-path frontier has scheduler lineage",
                )
        _require(
            frontier.fingerprint == first_path_frontier_fingerprint(frontier),
            "frontier fingerprint differs",
        )


def first_path_inventory_fingerprint(source: FirstPathCompletionCutV2) -> str:
    """Hash every cut field except its own embedded fingerprint."""
    return _content_fingerprint(
        FIRST_PATH_CUT_SCHEMA, source.model_dump(mode="json"), "complete_inventory_fingerprint"
    )


class PrepareExecutionCompletionFirstPathV2(ExecutionRecoveryDTO):
    kind: Literal["PREPARE_EXECUTION_COMPLETION_FIRST_PATH_V2"] = (
        "PREPARE_EXECUTION_COMPLETION_FIRST_PATH_V2"
    )
    schema_id: Literal["chiplog.execution.first-path-completion.v2"] = FIRST_PATH_COMPLETION_SCHEMA
    command_id: Identity
    run: ExecutionRunRecord
    selected_attempt: CallSubjectHead
    selector_generation: UInt64
    visibility_manifest: CallSubjectHead
    exact_captured_response: bytes = Field(min_length=1)
    source: FirstPathCompletionCutV2
    delivery: DeliveryObservation
    fence: NonSchedulerFence

    @model_validator(mode="after")
    def exact_request(self) -> Self:
        _require(self.run == self.source.complete_ordered_run_lineage[-1], "request Run differs")
        turn = self.run.turns[0]
        attempt = turn.attempts[0]
        _require(
            self.selected_attempt
            == _ref(attempt.attempt_id, attempt.canonical_bytes(), attempt.head)
            and self.selector_generation == attempt.generation,
            "selected attempt differs",
        )
        _require(
            attempt.response_base64 is not None
            and base64.b64decode(attempt.response_base64, validate=True)
            == self.exact_captured_response,
            "captured response differs",
        )
        manifest = attempt.manifest
        _require(
            self.visibility_manifest.revision
            == Present(head="record:" + manifest.digest(), fingerprint=manifest.digest()),
            "visibility manifest differs",
        )
        _require(
            (
                manifest.tenant,
                manifest.principal,
                manifest.run_id,
                manifest.turn_id,
                manifest.generation,
                manifest.worker_session,
            )
            == (
                self.run.tenant,
                self.run.principal,
                self.run.run_id,
                turn.turn_id,
                attempt.generation,
                self.run.worker_session,
            ),
            "visibility identity differs",
        )
        _require(
            (
                self.delivery.tenant,
                self.delivery.run,
                self.delivery.turn_id,
                self.delivery.captured_response,
                self.delivery.origin,
            )
            == (
                self.run.tenant,
                ExactHead(
                    identity=self.run.run_id, head=self.run.head, fingerprint=self.run.digest()
                ),
                turn.turn_id,
                self.exact_captured_response,
                self.run.origin,
            ),
            "delivery source differs",
        )
        _require(
            (self.fence.run_id, self.fence.run_head, self.fence.worker_session_id)
            == (self.run.run_id, self.run.head, self.run.worker_session),
            "fence source differs",
        )
        return self


ExecutionCompletionRequest = Annotated[
    PrepareExecutionCompletion | PrepareExecutionCompletionFirstPathV2, Field(discriminator="kind")
]


def decode_first_path_completion_request(raw: bytes) -> PrepareExecutionCompletionFirstPathV2:
    """Reject noncanonical encodings and revalidate copies before any use."""
    value = PrepareExecutionCompletionFirstPathV2.model_validate_json(raw)
    _require(value.canonical_bytes() == raw, "noncanonical first-path request")
    return value


def first_path_completion_request_fingerprint(
    request: PrepareExecutionCompletionFirstPathV2,
) -> str:
    return _sha(decode_first_path_completion_request(request.canonical_bytes()).canonical_bytes())


def decode_completion_request(
    raw: bytes,
) -> PrepareExecutionCompletion | PrepareExecutionCompletionFirstPathV2:
    adapter: TypeAdapter[PrepareExecutionCompletion | PrepareExecutionCompletionFirstPathV2] = (
        TypeAdapter(ExecutionCompletionRequest)
    )
    value = adapter.validate_json(raw)
    _require(value.canonical_bytes() == raw, "noncanonical completion request")
    return value
