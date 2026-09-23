"""Exact hermetic effect preview/adoption producer on the existing R13 runtime.

Preparation grants no storage or transport authority. The canonical broker must
validate this exact composite at its registered atomic publication boundary.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from chiplog.adapters.driven.effects_broker import EffectsIntegrityError
from chiplog.adapters.driven.effects_queries import StoredEffectRow
from chiplog.adapters.driven.loop_sqlite import SQLiteLoopStore
from chiplog.capabilities.agent_loop.contracts import LoopRejected, ProposalDisplay, RunRecord
from chiplog.capabilities.agent_loop.delivery_preparation import (
    DeliveryObservation,
    DeliveryPrepareRequest,
)
from chiplog.capabilities.agent_loop.domain import validate_record
from chiplog.capabilities.effects.fences import NonSchedulerFence, NotApplicable
from chiplog.capabilities.planning import CreateIntentionLine
from chiplog.capabilities.planning._r8_authority import decode_trace
from chiplog.capabilities.planning.r8_boundary import R8PlanningRequest
from chiplog.composition.r7_planning import ObservedTrustCall, PreparedPlanningCandidate
from chiplog.composition.r8 import R8PlanningRuntime, command_bytes
from chiplog.composition.r13_planning import R13PlanningRuntime, _trust_payload
from chiplog.domain_primitives import RecordId, TenantId
from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatch
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.broker import BrokerSession
from chiplog.platform.owner_decision_journal import (
    IndependentOwnerDecisionJournal,
    OwnerJournalSnapshot,
)
from chiplog.platform.workspace_snapshot import workspace_snapshot


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


class _Wire(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    def canonical_bytes(self) -> bytes:
        return _canonical(self.model_dump(mode="json"))


class HermeticEffectProposal(_Wire):
    schema_id: Literal["chiplog.hermetic-effect-proposal.v1"]
    purpose: str = Field(min_length=1, max_length=4096)
    payload_base64: str = Field(min_length=1)
    bundle_members: tuple[str, ...] = Field(min_length=1, max_length=32)

    def payload(self) -> bytes:
        raw = base64.b64decode(self.payload_base64, validate=True)
        if (
            not raw
            or len(raw) > 65536
            or base64.b64encode(raw).decode() != self.payload_base64
            or len(set(self.bundle_members)) != len(self.bundle_members)
            or any(not member for member in self.bundle_members)
        ):
            raise LoopRejected("effect proposal has invalid exact payload or bundle")
        return raw


class EffectPreviewBinding(_Wire):
    schema_id: Literal["chiplog.hermetic-effect-preview.v1"]
    proposal: HermeticEffectProposal
    proposal_id: str
    run_id: str
    run_head: str
    principal: str
    tenant: str
    planning_command_base64: str
    planning_result_base64: str
    planning_request_base64: str
    expected_tenant_head: int = Field(ge=0)
    provider: Literal["hermetic-effects"]
    account: Literal["hermetic-account"]
    recipient: Literal["hermetic-principal"]
    canonical_address: Literal["hermetic://effects/hermetic-principal"]
    adapter_contract: Literal["chiplog.hermetic-effects.v1"]
    policy: Literal["chiplog.hermetic-self-effect-policy.v1"]
    valid_until_ns: int = Field(gt=0)


class EffectAdoptionIngress(_Wire):
    """Exact observed CLI inputs; only the broker may issue invocation authority."""

    schema_id: Literal["chiplog.hermetic-effect-adoption-observation.v1"]
    display_id: str
    display_digest: str
    adoption_act_id: str
    tenant: str
    principal: str
    session_head: str
    peer_credential: str
    contour: Literal["CLI"]
    trust_reference_base64: str


@dataclass(frozen=True)
class PreparedEffectAdoption:
    display: ProposalDisplay
    binding: EffectPreviewBinding
    planning: PreparedPlanningCandidate
    ingress: EffectAdoptionIngress
    observed_trust: ObservedTrustCall
    disposition: Literal["PREPARED"] = "PREPARED"


class R16EffectsProducer:
    """Composition-only producer; no independent appender, writer or authority port."""

    def __init__(self, runtime: R13PlanningRuntime) -> None:
        self._runtime = runtime

    def _proposal(self, proposal_id: str) -> tuple[RunRecord, HermeticEffectProposal]:
        runtime = self._runtime
        rows = (
            SQLiteLoopStore(runtime._database, runtime._appender, runtime._tenant_id, "r6")
            .snapshot()
            .records
        )
        latest = {row.run_id: row for row in rows}
        matches = [
            (run, outcome.call.text)
            for run in latest.values()
            for turn in run.turns
            for outcome in (turn.sealed_calls or ())
            if outcome.proposal_id == proposal_id
            and outcome.call.tool == "propose_intent"
            and outcome.state == "TERMINAL"
        ]
        if len(matches) != 1:
            raise LoopRejected("absent or ambiguous sealed effect interpretation")
        run, text = matches[0]
        proposal = HermeticEffectProposal.model_validate_json(text)
        if proposal.canonical_bytes() != text.encode():
            raise LoopRejected("noncanonical hermetic effect interpretation")
        proposal.payload()
        if run.state != "ACTIVE" or run.principal != "hermetic-principal":
            raise LoopRejected("effect interpretation has no active registered principal")
        return run, proposal

    @staticmethod
    def _command(
        display_id: str, proposal_id: str, purpose: str, tenant: str
    ) -> CreateIntentionLine:
        scope = TenantId(tenant)
        return CreateIntentionLine(
            RecordId(scope, display_id + "/command"),
            RecordId(scope, "effect-intention:" + hashlib.sha256(proposal_id.encode()).hexdigest()),
            RecordId(scope, display_id + "/revision"),
            purpose,
            display_id + "/adoption",
        )

    async def _prepare(self, command: CreateIntentionLine) -> PreparedPlanningCandidate:
        candidate, _ = await self._prepare_observed(command)
        return candidate

    async def _prepare_observed(
        self, command: CreateIntentionLine, original: R8PlanningRequest | None = None
    ) -> tuple[PreparedPlanningCandidate, ObservedTrustCall]:
        runtime = self._runtime
        observed = await runtime._observed_trust_call(
            "AUTHENTICATE",
            {
                "contour": "CLI",
                "credential_id": "hermetic-credential",
                "peer_credential": f"uid:{os.getuid()}",
                "session_id": "hermetic-session",
            },
        )
        decision = observed.result
        if decision.disposition != "VALID" or decision.reference_bytes is None:
            raise LoopRejected("effect preparation has no authenticated principal")
        reference = json.loads(decision.reference_bytes)
        if (
            reference["tenant_id"],
            reference["principal_id"],
            reference["contour"],
            reference["peer_credential"],
        ) != (runtime._tenant_id, "hermetic-principal", "CLI", f"uid:{os.getuid()}"):
            raise LoopRejected("foreign effect preparation principal")
        with runtime._authority_gate().hold():
            self._require_observed_trust(observed)
            now = time.monotonic_ns()
            # Explicit base implementation: no ambient R13 adoption or shared trace slots.
            request = original
            if request is None:
                trace = R8PlanningRuntime._trace(
                    runtime, "hermetic-principal", _trust_payload(reference), now + 5_000_000_000
                )
                request = R8PlanningRequest(
                    command_bytes=command_bytes(command),
                    authority_trace_bytes=trace,
                    observed_time_ns=now,
                )
            if request.command_bytes != command_bytes(command):
                raise LoopRejected("effect original command differs")
            self._require_current_request(request, decision.reference_bytes)
        candidate = await runtime._prepare_planning_request(
            command, request, decision.reference_bytes
        )
        with runtime._authority_gate().hold():
            self._require_observed_trust(observed)
            if not isinstance(candidate, PreparedPlanningCandidate):
                raise LoopRejected("effect composite preparation: " + candidate.disposition)
            if candidate.request_bytes != request.canonical_bytes():
                raise LoopRejected("effect prepared request differs from sent request")
            self._require_current_preparation(candidate)
        return candidate, observed

    def _require_observed_trust(self, observed: ObservedTrustCall) -> None:
        if self._runtime._trust_observation_guard(observed) is not None:
            raise LoopRejected("effect authentication source changed or expired during IPC")

    def _require_current_preparation(self, prepared: PreparedPlanningCandidate) -> None:
        self._require_current_request(
            _decode_request(prepared.request_bytes), prepared.trust_reference_bytes
        )

    def _require_current_request(
        self, request: R8PlanningRequest, trust_reference_bytes: bytes
    ) -> None:
        original = decode_trace(request.authority_trace_bytes)
        deadline = min(read.valid_until_ns for read in original.reads)
        if time.monotonic_ns() >= deadline:
            raise LoopRejected("effect preparation expired; redisplay required")
        reference = json.loads(trust_reference_bytes)
        trust = self._runtime._trust.verify()
        if trust is None or (
            trust.tenant_id,
            trust.trust_head,
            trust.materialization_head,
            trust.phase,
        ) != (
            self._runtime._tenant_id,
            reference["trust_head"],
            reference["materialization_head"],
            "ACTIVE",
        ):
            raise LoopRejected("effect trust source changed during IPC")
        current = decode_trace(
            R8PlanningRuntime._trace(
                self._runtime, "hermetic-principal", _trust_payload(reference), deadline
            )
        )
        if current != original:
            raise LoopRejected("effect preparation source changed during IPC")

    async def display_effect(self, proposal_id: str) -> ProposalDisplay:
        runtime = self._runtime
        run, proposal = self._proposal(proposal_id)
        existing = [d for d in runtime._displays() if d.proposal_id == proposal_id]
        revision = len(existing) + 1
        display_id = f"{proposal_id}/effect-display/{revision}"
        command = self._command(display_id, proposal_id, proposal.purpose, run.tenant)
        prepared, observed = await self._prepare_observed(command)
        async with runtime._planning_lane:
            with runtime._authority_gate().hold():
                self._require_observed_trust(observed)
                if (
                    self._proposal(proposal_id) != (run, proposal)
                    or [d for d in runtime._displays() if d.proposal_id == proposal_id] != existing
                ):
                    raise LoopRejected("effect preview source changed during IPC")
                self._require_current_preparation(prepared)
                request = _decode_request(prepared.request_bytes)
                trace = decode_trace(request.authority_trace_bytes)
                deadline = min(read.valid_until_ns for read in trace.reads)
                binding = EffectPreviewBinding(
                    schema_id="chiplog.hermetic-effect-preview.v1",
                    proposal=proposal,
                    proposal_id=proposal_id,
                    run_id=run.run_id,
                    run_head=run.head,
                    principal=run.principal,
                    tenant=run.tenant,
                    planning_command_base64=base64.b64encode(prepared.command_bytes).decode(),
                    planning_result_base64=base64.b64encode(prepared.owner_result_bytes).decode(),
                    planning_request_base64=base64.b64encode(prepared.request_bytes).decode(),
                    expected_tenant_head=prepared.expected_tenant_head,
                    provider="hermetic-effects",
                    account="hermetic-account",
                    recipient="hermetic-principal",
                    canonical_address="hermetic://effects/hermetic-principal",
                    adapter_contract="chiplog.hermetic-effects.v1",
                    policy="chiplog.hermetic-self-effect-policy.v1",
                    valid_until_ns=deadline,
                )
                rendered = _canonical(
                    {
                        "operation": "CREATE_PLAN_AND_HERMETIC_EFFECT",
                        "binding": binding.model_dump(mode="json"),
                        "consequence": "Create a planning intention and emit the exact payload "
                        "to the independent hermetic provider account. "
                        "Delivery may become unknown; "
                        "adoption does not authorize a blind retry or a replacement intent.",
                    }
                ).decode()
                display = ProposalDisplay(
                    display_id=display_id,
                    proposal_id=proposal_id,
                    revision=revision,
                    run_id=run.run_id,
                    tenant=run.tenant,
                    principal=run.principal,
                    adoption_act_id=command.authority_act_id,
                    canonical_command=binding.canonical_bytes().decode(),
                    display_text=rendered,
                    display_digest=hashlib.sha256(rendered.encode()).hexdigest(),
                    original_binding_base64=base64.b64encode(binding.canonical_bytes()).decode(),
                )
                runtime._append_decision(
                    {
                        "version": 1,
                        "kind": "DISPLAY",
                        "operation_id": display_id,
                        "display": display.model_dump_json(),
                    }
                )
                return display

    async def adopt_effect(
        self, peer: str, display_id: str, digest: str, adoption_act_id: str
    ) -> PreparedEffectAdoption:
        runtime = self._runtime
        matches = [d for d in runtime._displays() if d.display_id == display_id]
        if len(matches) != 1:
            raise LoopRejected("missing exact effect display")
        display = matches[0]
        binding = EffectPreviewBinding.model_validate_json(display.canonical_command)
        if (
            peer != "hermetic-ingress"
            or digest != display.display_digest
            or adoption_act_id != display.adoption_act_id
            or binding.canonical_bytes() != display.canonical_command.encode()
            or base64.b64decode(display.original_binding_base64, validate=True)
            != binding.canonical_bytes()
            or hashlib.sha256(display.display_text.encode()).hexdigest() != digest
            or time.monotonic_ns() >= binding.valid_until_ns
        ):
            raise LoopRejected("missing exact current authenticated effect adoption")
        run, proposal = self._proposal(display.proposal_id)
        if (run.run_id, run.head, run.tenant, run.principal, proposal) != (
            binding.run_id,
            binding.run_head,
            binding.tenant,
            binding.principal,
            binding.proposal,
        ):
            raise LoopRejected("effect interpretation changed; redisplay required")
        command = self._command(display_id, display.proposal_id, proposal.purpose, run.tenant)
        original = _decode_request(base64.b64decode(binding.planning_request_base64, validate=True))
        prepared, observed = await self._prepare_observed(command, original)
        async with runtime._planning_lane:
            with runtime._authority_gate().hold():
                self._require_observed_trust(observed)
                if self._proposal(display.proposal_id) != (run, proposal) or [
                    d for d in runtime._displays() if d.display_id == display_id
                ] != [display]:
                    raise LoopRejected("effect adoption source changed during IPC")
                self._require_current_preparation(prepared)
                original = _decode_request(
                    base64.b64decode(binding.planning_request_base64, validate=True)
                )
                original_trace = decode_trace(original.authority_trace_bytes)
                if time.monotonic_ns() >= min(
                    binding.valid_until_ns, *(read.valid_until_ns for read in original_trace.reads)
                ):
                    raise LoopRejected("effect display expired during IPC; redisplay required")
                if (
                    prepared.request_bytes != original.canonical_bytes()
                    or prepared.command_bytes
                    != base64.b64decode(binding.planning_command_base64, validate=True)
                    or prepared.owner_result_bytes
                    != base64.b64decode(binding.planning_result_base64, validate=True)
                    or prepared.expected_tenant_head != binding.expected_tenant_head
                ):
                    raise LoopRejected("effect authority or result changed; redisplay required")
                reference = json.loads(prepared.trust_reference_bytes)
                ingress = EffectAdoptionIngress(
                    schema_id="chiplog.hermetic-effect-adoption-observation.v1",
                    display_id=display.display_id,
                    display_digest=digest,
                    adoption_act_id=adoption_act_id,
                    tenant=reference["tenant_id"],
                    principal=reference["principal_id"],
                    session_head=reference["session_head"],
                    peer_credential=reference["peer_credential"],
                    contour="CLI",
                    trust_reference_base64=base64.b64encode(
                        prepared.trust_reference_bytes
                    ).decode(),
                )
                return PreparedEffectAdoption(display, binding, prepared, ingress, observed)


def _decode_request(raw: bytes) -> R8PlanningRequest:
    value = json.loads(raw)
    for key in (
        "command_bytes",
        "authority_trace_bytes",
        "proposal_binding_bytes",
        "display_bytes",
    ):
        if value.get(key) is not None:
            value[key] = base64.b64decode(value[key], validate=True)
    request = R8PlanningRequest.model_validate(value)
    if request.canonical_bytes() != raw:
        raise LoopRejected("noncanonical planning preparation request")
    return request


class EffectsCurrentWorkerHold(LoopRejected):
    """No currently supported live worker; this observation issues no authority."""


@dataclass(frozen=True)
class CurrentEffectsWorker:
    run: RunRecord
    fence: NonSchedulerFence
    owner_session: BrokerSession


@dataclass(frozen=True)
class MaterializedEffectsCut:
    """Authenticated materialization evidence; not current effect authorization."""

    tenant_id: str
    tenant_frontier: int
    materialization_commitment: str
    owner_journal_head: str | None
    physical_path: str
    physical_device: int
    physical_inode: int
    rows: tuple[StoredEffectRow, ...]
    worker: CurrentEffectsWorker | None
    latest_runs: tuple[RunRecord, ...]


def read_materialized_effects(
    runtime: R13PlanningRuntime,
    journal: IndependentOwnerDecisionJournal,
    *,
    run_id: str | None = None,
) -> MaterializedEffectsCut:
    """Prove complete effects membership against independently selected decisions."""
    tenant = runtime._tenant_id
    identity = "full-effects-manifest"
    try:
        before = journal.snapshot()
        from chiplog.composition.r16_denial_history import validate_selected_denials

        validate_selected_denials(before)
        if before.tenant_id != tenant:
            raise ValueError("foreign independent owner journal")
        if {d.prepared.request.identity.command_id for d in before.decisions} != set(
            before.materialized_command_ids
        ):
            raise ValueError("pending selected owner decision requires recovery")
        committed = runtime._commitment_journal.load(tenant)
        with workspace_snapshot(runtime._database) as physical:
            connection = physical.connection
            commitment = capture_authority_snapshot_commitment(connection, tenant)
            if commitment != committed:
                raise ValueError("physical authority differs from independent commitment")
            head = connection.execute(
                "SELECT head FROM main.tenant_heads WHERE tenant_id = ?", (tenant,)
            ).fetchone()
            frontier = 0 if head is None else head[0]
            if type(frontier) is not int or frontier < 0:
                raise ValueError("invalid physical tenant frontier")
            actual_effect_ids = {
                row[0]
                for row in connection.execute(
                    "SELECT record_id FROM main.records WHERE tenant_id = ? AND owner = 'effects'",
                    (tenant,),
                )
            }
            expected_effect_ids: set[str] = set()
            rows: list[StoredEffectRow] = []
            for decision in before.decisions:
                request = decision.prepared.request
                identity = request.identity.command_id
                manifest = tuple(row.record_id for row in request.complete_records)
                publication = connection.execute(
                    "SELECT request_fingerprint, commit_sequence, record_ids "
                    "FROM main.publications WHERE tenant_id = ? AND operation_kind = ? "
                    "AND idempotency_key = ?",
                    (tenant, request.operation, identity),
                ).fetchone()
                if (
                    publication
                    != (
                        request.identity.command_fingerprint,
                        decision.tenant_commit_sequence,
                        "\n".join(manifest),
                    )
                    or decision.tenant_commit_sequence > frontier
                ):
                    raise ValueError("independent decision differs from physical publication")
                physical_ids = {
                    row[0]
                    for row in connection.execute(
                        "SELECT record_id FROM main.records WHERE tenant_id = ? "
                        "AND commit_sequence = ?",
                        (tenant, decision.tenant_commit_sequence),
                    )
                }
                if physical_ids != set(manifest):
                    raise ValueError("physical commit omits or adds an owner batch member")
                for ordinal, record in enumerate(request.complete_records):
                    identity = record.record_id
                    actual = connection.execute(
                        "SELECT owner, schema_id, canonical_bytes, commit_sequence "
                        "FROM main.records WHERE tenant_id = ? AND record_id = ?",
                        (tenant, identity),
                    ).fetchone()
                    if actual != (
                        record.owner,
                        record.schema_id,
                        record.canonical_bytes,
                        decision.tenant_commit_sequence,
                    ):
                        raise ValueError("physical record differs from independent exact bytes")
                    if record.owner == "effects":
                        if identity in expected_effect_ids:
                            raise ValueError("duplicate effect across selected batches")
                        expected_effect_ids.add(identity)
                        rows.append(
                            StoredEffectRow(
                                decision.tenant_commit_sequence, ordinal, manifest, record
                            )
                        )
            if expected_effect_ids != actual_effect_ids:
                raise ValueError("complete effects scope has missing tail or unknown rows")
            latest_runs = _reconstruct_runs(connection, tenant, before)
            if (
                journal.snapshot() != before
                or runtime._commitment_journal.load(tenant) != committed
            ):
                raise ValueError("independent authority changed while acquiring physical cut")
            worker = None if run_id is None else _current_worker(runtime, latest_runs, run_id)
            stat = runtime._database.stat()
            if (
                runtime._database.resolve(strict=True) != physical.path
                or (stat.st_dev, stat.st_ino) != physical.identity
            ):
                raise ValueError("physical authority database changed during acquisition")
            return MaterializedEffectsCut(
                tenant,
                frontier,
                commitment,
                before.head,
                str(physical.path),
                physical.identity[0],
                physical.identity[1],
                tuple(rows),
                worker,
                latest_runs,
            )
    except EffectsCurrentWorkerHold, EffectsIntegrityError:
        raise
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        raise EffectsIntegrityError(
            f"effects integrity: operation=read_materialized_effects "
            f"tenant={tenant} record={identity}"
        ) from error


def _reconstruct_runs(
    connection: sqlite3.Connection, tenant: str, journal: OwnerJournalSnapshot
) -> tuple[RunRecord, ...]:
    """Historical semantics from selected original inputs, never current authority."""
    try:
        return _reconstruct_run_rows(connection, tenant, journal)
    except EffectsIntegrityError:
        raise
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        raise EffectsIntegrityError(
            f"effects integrity: operation=reconstruct_runs tenant={tenant} record=Run-history"
        ) from error


def _check_legacy_run_bytes(previous_raw: bytes | None, raw: bytes) -> None:
    record = RunRecord.model_validate_json(raw)
    previous = None if previous_raw is None else RunRecord.model_validate_json(previous_raw)
    if (
        record.accepted_delivery_binding != "LEGACY_R13"
        or record.canonical_bytes() != raw
        or (previous is not None and previous.canonical_bytes() != previous_raw)
    ):
        raise ValueError("legacy Run validation requires canonical legacy bytes")
    validate_record(previous, record)


_cached_legacy_run_check = lru_cache(maxsize=32)(_check_legacy_run_bytes)


def _validate_legacy_run_bytes(previous_raw: bytes | None, raw: bytes) -> None:
    # Only pure historical semantics are reused, keyed by the entire pair. Large
    # records still validate, but cannot retain an unbounded amount of cache memory.
    check = (
        _cached_legacy_run_check
        if len(raw) + (0 if previous_raw is None else len(previous_raw)) <= 256 * 1024
        else _check_legacy_run_bytes
    )
    check(previous_raw, raw)


def _reconstruct_run_rows(
    connection: sqlite3.Connection, tenant: str, journal: OwnerJournalSnapshot
) -> tuple[RunRecord, ...]:
    # Complete schema enumeration, including Runs committed in registered atomic
    # batches; legacy operation_kind='agent_loop' is not a membership oracle.
    physical = connection.execute(
        "SELECT record_id, canonical_bytes, commit_sequence FROM main.records "
        "WHERE tenant_id = ? AND owner = 'agent_loop' "
        "AND schema_id = 'chiplog.agent-loop.record.v1'",
        (tenant,),
    ).fetchall()
    physical_ids = {row[0] for row in physical}
    for decision in journal.decisions:
        batch = decision.prepared.request
        if isinstance(batch, CompleteDeliveryBatch):
            selected_runs = [
                member
                for member in batch.complete_records
                if member.owner == "agent_loop"
                and member.schema_id == "chiplog.agent-loop.record.v1"
            ]
            if len(selected_runs) != 1 or selected_runs[0].record_id not in physical_ids:
                raise EffectsIntegrityError(
                    f"effects integrity: operation=historical_delivery tenant={tenant} "
                    f"record={batch.identity.command_id}: selected Run missing or unknown schema"
                ) from ValueError("complete-delivery publisher requires exactly one present Run")
            selected_run = RunRecord.model_validate_json(selected_runs[0].canonical_bytes)
            if (
                selected_run.event != "CompleteAcceptance"
                or selected_run.accepted_delivery_binding == "LEGACY_R13"
            ):
                raise EffectsIntegrityError(
                    f"effects integrity: operation=historical_delivery tenant={tenant} "
                    f"record={selected_run.head}: selected Run is not expanded acceptance"
                ) from ValueError("complete-delivery publisher cannot downgrade to legacy")
    positions: dict[str, tuple[int, int]] = {}
    for sequence, manifest in connection.execute(
        "SELECT commit_sequence, record_ids FROM main.publications WHERE tenant_id = ?",
        (tenant,),
    ):
        for ordinal, identity in enumerate(manifest.split("\n")):
            if identity in positions:
                raise ValueError("Run physical identity appears in multiple publications")
            positions[identity] = (sequence, ordinal)
    for identity, _, _ in physical:
        if identity not in positions:
            raise ValueError("Run lacks physical publication membership")
    latest: dict[str, RunRecord] = {}
    latest_bytes: dict[str, bytes] = {}
    for identity, raw, sequence in sorted(physical, key=lambda row: positions[row[0]]):
        record = RunRecord.model_validate_json(raw)
        if record.head != identity or record.tenant != tenant or positions[identity][0] != sequence:
            raise ValueError("Run physical/logical identity differs")
        previous = latest.get(record.run_id)
        if record.accepted_delivery_binding == "LEGACY_R13":
            _validate_legacy_run_bytes(latest_bytes.get(record.run_id), raw)
        else:
            if record.canonical_bytes() != raw:
                raise ValueError("Run physical/logical identity differs")
            observation = _historical_delivery_observation(
                connection, journal, previous, record, positions[identity]
            )
            validate_record(previous, record, observation)
        latest[record.run_id] = record
        latest_bytes[record.run_id] = raw
    return tuple(latest.values())


def _historical_delivery_observation(
    connection: sqlite3.Connection,
    journal: OwnerJournalSnapshot,
    previous: RunRecord | None,
    record: RunRecord,
    position: tuple[int, int],
) -> DeliveryObservation:
    try:
        matches = [
            decision
            for decision in journal.decisions
            if any(
                row.record_id == record.head for row in decision.prepared.request.complete_records
            )
        ]
        if len(matches) != 1:
            raise ValueError("expanded Run lacks unique independently selected batch")
        decision = matches[0]
        batch = decision.prepared.request
        if (
            not isinstance(batch, CompleteDeliveryBatch)
            or batch.identity.tenant_id != record.tenant
            or journal.tenant_id != record.tenant
            or batch.identity.command_id not in journal.materialized_command_ids
        ):
            raise ValueError("expanded Run has no materialized complete-delivery publisher")
        command = batch.loop_command
        if (
            command.owner != "agent_loop"
            or command.schema_id != "chiplog.delivery.prepare-completion.v1"
            or hashlib.sha256(command.canonical_bytes).hexdigest() != command.fingerprint
        ):
            raise ValueError("unknown or changed historical delivery publisher command")
        request = DeliveryPrepareRequest.model_validate_json(command.canonical_bytes)
        if request.canonical_bytes() != command.canonical_bytes or request.previous != previous:
            raise ValueError("historical command differs from exact predecessor or canonical codec")
        manifest = tuple(row.record_id for row in batch.complete_records)
        if len(set(manifest)) != len(manifest) or position != (
            decision.tenant_commit_sequence,
            manifest.index(record.head),
        ):
            raise ValueError("historical Run has wrong physical batch ordinal")
        actual = connection.execute(
            "SELECT request_fingerprint, commit_sequence, record_ids FROM main.publications "
            "WHERE tenant_id = ? AND operation_kind = ? AND idempotency_key = ?",
            (record.tenant, batch.operation, batch.identity.command_id),
        ).fetchone()
        if actual != (
            batch.identity.command_fingerprint,
            decision.tenant_commit_sequence,
            "\n".join(manifest),
        ):
            raise ValueError("historical complete publication manifest differs")
        physical = connection.execute(
            "SELECT record_id, owner, schema_id, canonical_bytes FROM main.records "
            "WHERE tenant_id = ? AND commit_sequence = ?",
            (record.tenant, decision.tenant_commit_sequence),
        ).fetchall()
        if {row[0] for row in physical} != set(manifest):
            raise ValueError("historical delivery companion omitted or added")
        physical_by_id = {row[0]: row[1:] for row in physical}
        for member in batch.complete_records:
            if hashlib.sha256(
                member.canonical_bytes
            ).hexdigest() != member.fingerprint or physical_by_id[member.record_id] != (
                member.owner,
                member.schema_id,
                member.canonical_bytes,
            ):
                raise ValueError("historical delivery companion exact bytes differ")
        own = batch.complete_records[position[1]]
        if (own.owner, own.schema_id, own.canonical_bytes) != (
            "agent_loop",
            "chiplog.agent-loop.record.v1",
            record.canonical_bytes(),
        ):
            raise ValueError("historical selected Run differs from physical Run")
        # The admitted owner validator recomputes the entire embedded acceptance
        # and observation digest. Fresh effect companion semantics remain the
        # registered atomic publisher's obligation, not inferred from row names.
        validate_record(previous, record, request.observation)
        return request.observation
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        raise EffectsIntegrityError(
            f"effects integrity: operation=historical_delivery tenant={record.tenant} "
            f"record={record.head}"
        ) from error


def _current_worker(
    runtime: R13PlanningRuntime, records: tuple[RunRecord, ...], run_id: str
) -> CurrentEffectsWorker:
    tenant = runtime._tenant_id
    latest = {record.run_id: record for record in records}
    run = latest.get(run_id)
    session = runtime._supervisor.runtime().session("agent_loop")
    expected_worker = f"{session.broker_epoch}:{session.generation_id}:{session.session_id}"
    if (
        run is None
        or run.state != "ACTIVE"
        or run.root_binding != "NOT_APPLICABLE"
        or run.worker_session != expected_worker
        or runtime.current_worker() != expected_worker
        or session.tenant_id != tenant
        or session.owner_id != "agent_loop"
    ):
        raise EffectsCurrentWorkerHold(
            "current non-scheduler ACTIVE Run unavailable; scheduler/post-terminal authority HOLD"
        )
    return CurrentEffectsWorker(
        run,
        NonSchedulerFence(
            lineage=NotApplicable(),
            physical_root=NotApplicable(),
            lease=NotApplicable(),
            clock_proof=NotApplicable(),
            run_id=run.run_id,
            run_head=run.head,
            worker_session_id=run.worker_session,
            runtime_generation=session.generation_id,
        ),
        session,
    )
