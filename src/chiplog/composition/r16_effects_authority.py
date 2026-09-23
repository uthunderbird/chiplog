"""Private, invocation-local authority for the registered PlanEffect operation."""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.composition.r7_planning import ObservedTrustCall
from chiplog.composition.r16_effects import PreparedEffectAdoption
from chiplog.composition.r16_effects_inputs import EffectSources, canonical, capture_sources, digest
from chiplog.composition.r16_effects_registry import HermeticPlanEffectRegistry
from chiplog.platform._owner_publication_contracts import (
    ExactReplayQuery,
    InvocationProofRef,
    OwnerCommandBytes,
    PlanEffectBatch,
    PublicationIdentity,
    PublicationRejected,
    RegisteredPublication,
)
from chiplog.platform.authority_reads import capture_authority_storage_state
from chiplog.platform.broker import PublicPortCall, PublicPortResult
from chiplog.platform.owner_publications import PreparedOwnerPublication, SelectedOwnerDecision
from chiplog.platform.workspace_snapshot import workspace_snapshot

if TYPE_CHECKING:
    from chiplog.composition.r14_runtime import R14PlanningRuntime


@dataclass(frozen=True)
class _Invocation:
    reference: InvocationProofRef
    identity: PublicationIdentity
    commands: tuple[OwnerCommandBytes, ...]
    observed: ObservedTrustCall


@dataclass(frozen=True)
class _IssuedPreparation:
    publication: PreparedOwnerPublication
    adoption: PreparedEffectAdoption
    sources: EffectSources
    sent: PublicPortCall
    returned: PublicPortResult


class R16PlanEffectAuthority:
    """Only canonical orchestration creates entries; input DTOs cannot create them."""

    def __init__(self, runtime: R14PlanningRuntime) -> None:
        self._runtime = runtime
        self._registry = HermeticPlanEffectRegistry()
        self._invocations: dict[str, _Invocation] = {}
        self._prepared: dict[str, _IssuedPreparation] = {}

    def issue_invocation(
        self,
        identity: PublicationIdentity,
        commands: tuple[OwnerCommandBytes, ...],
        observed: ObservedTrustCall,
    ) -> InvocationProofRef:
        runtime = self._runtime
        with runtime._authority_gate().hold():
            if runtime._trust_observation_guard(observed) is not None:
                raise LoopRejected("current authenticated effect invocation required")
            if observed.result.reference_bytes is None:
                raise LoopRejected("effect invocation lacks authenticated identity")
            principal = json.loads(observed.result.reference_bytes)
            if (
                identity.tenant_id,
                principal["tenant_id"],
                principal["principal_id"],
                principal["contour"],
            ) != (runtime._tenant_id, runtime._tenant_id, "hermetic-principal", "CLI"):
                raise LoopRejected("effect invocation belongs to another principal")
            caller = observed.request.caller
            nonce = secrets.token_hex(32)
            proof = InvocationProofRef(
                issuance_id=nonce,
                issuance_fingerprint=digest(canonical((nonce, identity, commands, observed))),
                broker_epoch=str(caller.broker_epoch),
                broker_session=caller.session_id,
                runtime_generation=caller.generation_id,
                operation_subject=identity.command_id,
            )
            self._invocations[nonce] = _Invocation(proof, identity, commands, observed)
            return proof

    def register_prepared(
        self,
        batch: PlanEffectBatch,
        adoption: PreparedEffectAdoption,
        sources: EffectSources,
        sent: PublicPortCall,
        returned: PublicPortResult,
    ) -> None:
        runtime = self._runtime
        with runtime._authority_gate().hold():
            proof = batch.authentication.invocation
            invocation = self._invocations.get(proof.issuance_id)
            if invocation is None or invocation.reference != proof:
                raise LoopRejected("unissued effect invocation")
            prepared = PreparedOwnerPublication(
                batch,
                secrets.token_hex(32),
                "r6",
                0,
                sources.cut.materialization_commitment,
            )
            self._prepared[prepared.issuance_id] = _IssuedPreparation(
                prepared,
                adoption,
                sources,
                sent,
                returned,
            )

    def authenticate_replay(self, query: ExactReplayQuery) -> PublicationRejected | None:
        with self._runtime._authority_gate().hold():
            entry = self._invocations.get(query.current_invocation.issuance_id)
            if (
                entry is None
                or entry.reference != query.current_invocation
                or entry.identity != query.identity
                or entry.commands != query.original_commands
                or query.operation != "effects.publish_plan_effect"
                or self._runtime._trust_observation_guard(entry.observed) is not None
            ):
                return PublicationRejected(
                    kind="DENIED",
                    tenant_id=query.identity.tenant_id,
                    command_id=query.identity.command_id,
                    reason="no exact current issued invocation",
                )
        return None

    async def prepare(
        self,
        request: RegisteredPublication,
    ) -> PreparedOwnerPublication | PublicationRejected:
        matches = tuple(
            value for value in self._prepared.values() if value.publication.request == request
        )
        if len(matches) == 1:
            return matches[0].publication
        return PublicationRejected(
            kind="DENIED",
            tenant_id=request.identity.tenant_id,
            command_id=request.identity.command_id,
            reason="publication was not issued by the registered preparation",
        )

    def check_prepared(
        self,
        prepared: PreparedOwnerPublication,
    ) -> Literal["DENIED", "STALE", "INDETERMINATE"] | None:
        with self._runtime._authority_gate().hold():
            entry = self._prepared.get(prepared.issuance_id)
            if entry is None or entry.publication != prepared:
                return "DENIED"
            if time.monotonic_ns() >= entry.sent.budget.absolute_deadline_ns:
                return "STALE"
            try:
                if capture_sources(self._runtime, entry.adoption, self._registry) != entry.sources:
                    return "STALE"
            except LoopRejected, ValueError, RuntimeError:
                return "STALE"
            if time.monotonic_ns() >= entry.sent.budget.absolute_deadline_ns:
                return "STALE"
            return None

    def materialization_state(
        self, decision: SelectedOwnerDecision
    ) -> Literal["ABSENT", "COMPLETE", "CONFLICT"]:
        return effect_materialization_state(self._runtime, decision)

    def check_selected_predecessor(self, decision: SelectedOwnerDecision) -> bool:
        with self._runtime._authority_gate().hold():
            return self.materialization_state(decision) == "ABSENT"


def effect_materialization_state(
    runtime: R14PlanningRuntime, decision: SelectedOwnerDecision
) -> Literal["ABSENT", "COMPLETE", "CONFLICT"]:
    """Historical SQL equality, including the complete physical batch and anchor."""
    request = decision.prepared.request
    with runtime._authority_gate().hold():
        runtime._check_database_identity()
        journal = runtime._owner_decisions()
        if journal.lookup(runtime._tenant_id, request.identity.command_id) != decision:
            return "CONFLICT"
        history = journal.snapshot()
        historical = request.identity.command_id in history.materialized_command_ids
        if not historical and (
            not history.decisions
            or history.decisions[-1] != decision
            or runtime._pending_owners() != (decision,)
            or runtime._pending()
            or runtime._pending_gate_publications()
        ):
            return "CONFLICT"
        actual, _ = capture_authority_storage_state(runtime._database)
        anchor = runtime._commitment_journal.load(runtime._tenant_id)
        if historical:
            if actual != anchor:
                return "CONFLICT"
        elif anchor not in (
            decision.prepared.predecessor_commitment,
            decision.resulting_commitment,
        ):
            return "CONFLICT"
        with workspace_snapshot(runtime._database) as snapshot:
            connection = snapshot.connection
            publication = connection.execute(
                "SELECT request_fingerprint, commit_sequence, record_ids "
                "FROM main.publications "
                "WHERE tenant_id=? AND operation_kind=? AND idempotency_key=?",
                (runtime._tenant_id, request.operation, request.identity.command_id),
            ).fetchone()
            records = tuple(
                connection.execute(
                    "SELECT record_id, owner, schema_id, canonical_bytes, commit_sequence "
                    "FROM main.records WHERE tenant_id=? AND record_id=?",
                    (runtime._tenant_id, row.record_id),
                ).fetchone()
                for row in request.complete_records
            )
            if publication is None and all(row is None for row in records):
                return (
                    "ABSENT"
                    if actual == decision.prepared.predecessor_commitment and not historical
                    else "CONFLICT"
                )
            expected = tuple(
                (
                    row.record_id,
                    row.owner,
                    row.schema_id,
                    row.canonical_bytes,
                    decision.tenant_commit_sequence,
                )
                for row in request.complete_records
            )
            ids = {
                row[0]
                for row in connection.execute(
                    "SELECT record_id FROM main.records WHERE tenant_id=? AND commit_sequence=?",
                    (runtime._tenant_id, decision.tenant_commit_sequence),
                )
            }
            if (
                records != expected
                or ids != {row.record_id for row in request.complete_records}
                or publication
                != (
                    request.identity.command_fingerprint,
                    decision.tenant_commit_sequence,
                    "\n".join(row.record_id for row in request.complete_records),
                )
                or (not historical and actual != decision.resulting_commitment)
            ):
                return "CONFLICT"
        return "COMPLETE"
