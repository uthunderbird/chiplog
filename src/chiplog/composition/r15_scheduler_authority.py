"""Composition-private issuance and physical readback for scheduler configuration.

The publisher retains the actual authentication invocation and owner IPC result.
Constructible source/proof DTOs are descriptions; only objects retained here can
enter fresh publication. Historical selections use today's authentication and
the original complete bytes, never a reconstructed current owner request.
"""

from __future__ import annotations

import secrets
import sqlite3
from contextlib import closing, nullcontext
from dataclasses import dataclass
from typing import Literal

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.scheduler_preparation import ConfigurationPreparationRequest
from chiplog.composition.r7_planning import ObservedTrustCall
from chiplog.composition.r14_runtime import R14PlanningRuntime
from chiplog.composition.r15_scheduler_history import inspect_publication, read_loop_snapshot
from chiplog.composition.r15_scheduler_registry import (
    AMEND_POLICY_OPERATION,
    AMEND_SCHEDULE_OPERATION,
    GENESIS_OPERATION,
    REPLACE_BOUND_OPERATION,
    SchedulerConfigurationAdoption,
    SchedulerGenerationSources,
    SchedulerGenesisAdoption,
    _canonical,
    _command_id,
    _digest,
    capture_generation_sources,
    configuration_command_id,
)
from chiplog.composition.scheduler_source_registry import (
    AdmittedSchedulerStartup,
    read_admitted_scheduler_startup,
)
from chiplog.platform._owner_publication_contracts import (
    ExactReplayQuery,
    InvocationProofRef,
    OwnerCommandBytes,
    PublicationIdentity,
    PublicationRejected,
    RegisteredPublication,
    SingleOwnerBatch,
)
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.owner_publications import PreparedOwnerPublication, SelectedOwnerDecision
from chiplog.platform.workspace_snapshot import _CURRENT, read_connection, workspace_snapshot


@dataclass(frozen=True)
class SchedulerFreshSources:
    generations: SchedulerGenerationSources
    startup: AdmittedSchedulerStartup
    loop_entries: tuple[tuple[str, str | None, bytes], ...]
    fence_generation: str
    fence_frontier: int


class SchedulerPublicationAuthority:
    """Private trusted composition, not a public authentication/issuance service."""

    def __init__(
        self,
        runtime: R14PlanningRuntime,
        observed: ObservedTrustCall,
        adoption: SchedulerGenesisAdoption | SchedulerConfigurationAdoption,
    ) -> None:
        self._runtime = runtime
        self._observed = observed
        self._adoption = adoption
        self._is_genesis = isinstance(adoption, SchedulerGenesisAdoption)
        self._operations = (
            (GENESIS_OPERATION,)
            if isinstance(adoption, SchedulerGenesisAdoption)
            else (AMEND_SCHEDULE_OPERATION, AMEND_POLICY_OPERATION, REPLACE_BOUND_OPERATION)
        )
        self._command_id = (
            _command_id(adoption.adoption_act_id)
            if isinstance(adoption, SchedulerGenesisAdoption)
            else configuration_command_id(adoption.adoption_act_id)
        )
        self._invocations: dict[
            str, tuple[InvocationProofRef, PublicationIdentity, OwnerCommandBytes, str]
        ] = {}
        self._sources: list[SchedulerFreshSources] = []
        self._prepared: list[tuple[PreparedOwnerPublication, SchedulerFreshSources]] = []

    def capture_fresh(self) -> SchedulerFreshSources:
        runtime = self._runtime
        with runtime._authority_gate().hold():
            runtime._require_no_pending()
            generations = capture_generation_sources(runtime, self._observed)
            entries = tuple(runtime._loop_decisions().entries())
            context = (
                nullcontext()
                if _CURRENT.get() is not None
                else workspace_snapshot(runtime._database)
            )
            with context, read_connection(runtime._database) as connection:
                # Validate ordinary shared history before selecting a new scheduler row.
                read_loop_snapshot(runtime)
                startup = read_admitted_scheduler_startup(
                    runtime._database,
                    runtime._tenant_id,
                    runtime._owner_decisions(),
                    runtime._commitment_journal,
                )
                if not isinstance(startup, AdmittedSchedulerStartup):
                    raise LoopRejected("scheduler source admission unresolved")
                cut = startup.cut
                if (
                    cut.physical_database_path,
                    cut.physical_device,
                    cut.physical_inode,
                ) != runtime._database_identity:
                    raise LoopRejected("scheduler cut belongs to another physical database")
                if self._is_genesis:
                    if cut.selected or cut.materialized or startup.index.selected_record_ids:
                        raise LoopRejected(
                            "scheduler GENESIS requires empty global scheduler history"
                        )
                else:
                    index = startup.index
                    if (
                        len(index.schedules) != 1
                        or len(index.policies) != 1
                        or len(index.bounds) != 1
                        or index.schedules[0].schedule_id != index.policies[0].schedule_id
                        or index.schedules[0].schedule_id != index.bounds[0][0]
                    ):
                        raise LoopRejected(
                            "scheduler configuration requires one complete global schedule"
                        )
                fence = connection.execute(
                    "SELECT generation, frontier FROM main.deletion_fences WHERE tenant_id=?",
                    (runtime._tenant_id,),
                ).fetchone()
                if fence != ("r6", 0):
                    raise LoopRejected("scheduler GENESIS requires the registered r6/0 fence")
                if (
                    capture_authority_snapshot_commitment(connection, runtime._tenant_id)
                    != cut.materialization_commitment
                    or runtime._commitment_journal.load(runtime._tenant_id)
                    != cut.materialization_commitment
                    or tuple(runtime._loop_decisions().entries()) != entries
                ):
                    raise LoopRejected("scheduler independent source cut changed")
                runtime._check_database_identity()
                result = SchedulerFreshSources(generations, startup, entries, fence[0], fence[1])
            self._sources.append(result)
            return result

    def issue_invocation(
        self, identity: PublicationIdentity, command: OwnerCommandBytes
    ) -> InvocationProofRef:
        sources = capture_generation_sources(self._runtime, self._observed)
        original = ConfigurationPreparationRequest.model_validate_json(command.canonical_bytes)
        if (
            identity.tenant_id != self._runtime._tenant_id
            or identity.command_id != self._command_id
            or identity.command_fingerprint != _digest(self._adoption.command_bytes)
            or command.owner != "agent_loop"
            or command.schema_id != "chiplog.scheduler.configuration-preparation.v1"
            or command.fingerprint != _digest(command.canonical_bytes)
            or original.canonical_bytes() != command.canonical_bytes
            or original.command_bytes != self._adoption.command_bytes
            or original.operation not in self._operations
        ):
            raise LoopRejected("scheduler invocation differs from exact adopted command")
        issuance = secrets.token_hex(32)
        session = sources.agent_session
        proof = InvocationProofRef(
            issuance_id=issuance,
            issuance_fingerprint=_digest(
                _canonical(
                    {
                        "issuance": issuance,
                        "identity": identity.model_dump(mode="json"),
                        "command": command.model_dump(mode="json"),
                        "generation_evidence": _digest(sources.evidence_bytes),
                    }
                )
            ),
            broker_epoch=str(session.broker_epoch),
            broker_session=session.session_id,
            runtime_generation=session.generation_id,
            operation_subject=identity.command_id,
        )
        self._invocations[issuance] = (proof, identity, command, original.operation)
        return proof

    def _matches_original(self, request: SingleOwnerBatch) -> bool:
        """Closed configuration interpretation; other issuers override explicitly."""
        original = ConfigurationPreparationRequest.model_validate_json(
            request.command.canonical_bytes
        )
        return (
            request.identity.command_fingerprint == _digest(self._adoption.command_bytes)
            and original.operation == request.operation
            and original.command_bytes == self._adoption.command_bytes
            and original.canonical_bytes() == request.command.canonical_bytes
            and request.command.owner == "agent_loop"
            and request.command.schema_id == "chiplog.scheduler.configuration-preparation.v1"
            and request.command.fingerprint == _digest(request.command.canonical_bytes)
        )

    def authenticate_replay(self, query: ExactReplayQuery) -> PublicationRejected | None:
        issued = self._invocations.get(query.current_invocation.issuance_id)
        if (
            issued is None
            or issued[0] is not query.current_invocation
            or issued[1] != query.identity
            or query.original_commands != (issued[2],)
            or query.operation != issued[3]
        ):
            return self._rejected(query.identity, "DENIED", "unknown private scheduler invocation")
        # No fresh absence/old-worker test: this authorizes the current caller,
        # including replay of an original selected request from an earlier worker.
        try:
            capture_generation_sources(self._runtime, self._observed)
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
            return self._rejected(query.identity, "DENIED", str(error) or type(error).__name__)
        return None

    def register_prepared(
        self, request: SingleOwnerBatch, sources: SchedulerFreshSources
    ) -> PreparedOwnerPublication:
        if not any(item is sources for item in self._sources):
            raise LoopRejected("scheduler source description was not captured by this issuer")
        failure = self.authenticate_replay(
            ExactReplayQuery(
                identity=request.identity,
                operation=request.operation,
                current_invocation=request.authentication.invocation,
                original_commands=(request.command,),
            )
        )
        cut = sources.startup.cut
        registry = sources.startup.registration.reference
        if (
            failure is not None
            or request.authentication.kind != "WORKER"
            or request.expected.tenant_id != self._runtime._tenant_id
            or request.expected.tenant_frontier != cut.tenant_frontier
            or request.expected.expected_materialization_commitment
            != cut.materialization_commitment
            or request.expected.registry_head != registry.head
            or request.expected.registry_fingerprint != registry.fingerprint
        ):
            raise LoopRejected("scheduler prepared publication differs from issued source cut")
        prepared = PreparedOwnerPublication(
            request,
            secrets.token_hex(32),
            sources.fence_generation,
            sources.fence_frontier,
            cut.materialization_commitment,
        )
        self._prepared.append((prepared, sources))
        return prepared

    async def prepare(
        self, request: RegisteredPublication
    ) -> PreparedOwnerPublication | PublicationRejected:
        for prepared, _ in self._prepared:
            if prepared.request is request:
                return prepared
        return self._rejected(request.identity, "DENIED", "unissued scheduler prepared request")

    def check_prepared(
        self, prepared: PreparedOwnerPublication
    ) -> Literal["DENIED", "STALE", "INDETERMINATE"] | None:
        for issued, sources in self._prepared:
            if issued is prepared:
                try:
                    return None if self.capture_fresh() == sources else "STALE"
                except OSError, RuntimeError, ValueError, TypeError, KeyError, sqlite3.Error:
                    return "INDETERMINATE"
        return "DENIED"

    def materialization_state(
        self, decision: SelectedOwnerDecision
    ) -> Literal["ABSENT", "EXACT_PREFIX", "COMPLETE", "CONFLICT"]:
        runtime = self._runtime
        with runtime._authority_gate().hold():
            runtime._check_database_identity()
            request = decision.prepared.request
            journal = runtime._owner_decisions()
            if (
                request.identity.tenant_id != runtime._tenant_id
                or request.operation not in self._operations
                or request.identity.command_id != self._command_id
                or journal.lookup(runtime._tenant_id, request.identity.command_id) != decision
            ):
                return "CONFLICT"
            if not isinstance(request, SingleOwnerBatch):
                return "CONFLICT"
            try:
                matches = self._matches_original(request)
            except ValueError:
                return "CONFLICT"
            if not matches:
                return "CONFLICT"
            snapshot = journal.snapshot()
            anchored = runtime._commitment_journal.load(runtime._tenant_id)
            command = runtime._owner_command(decision)
            # Never join an ambient workspace cut when proving post-commit state.
            uri = runtime._database.resolve(strict=True).as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True)) as connection:
                connection.execute("BEGIN")
                commitment = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
                state = inspect_publication(connection, command, decision.tenant_commit_sequence)
                head = connection.execute(
                    "SELECT head FROM main.tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
                ).fetchone()
                fence = connection.execute(
                    "SELECT generation, frontier FROM main.deletion_fences WHERE tenant_id=?",
                    (runtime._tenant_id,),
                ).fetchone()
                frontier = 0 if head is None else head[0]
                if (
                    type(frontier) is not int
                    or frontier < 0
                    or fence
                    != (decision.prepared.fence_generation, decision.prepared.fence_frontier)
                ):
                    return "CONFLICT"
            runtime._check_database_identity()
            if (
                journal.snapshot() != snapshot
                or runtime._commitment_journal.load(runtime._tenant_id) != anchored
            ):
                return "CONFLICT"
            marked = request.identity.command_id in snapshot.materialized_command_ids
            if state == "ABSENT":
                return (
                    "ABSENT"
                    if not marked
                    and commitment == anchored == decision.prepared.predecessor_commitment
                    and frontier == request.expected.tenant_frontier
                    else "CONFLICT"
                )
            if state != "COMPLETE" or frontier < decision.tenant_commit_sequence:
                return "CONFLICT"
            if marked:
                return "COMPLETE" if commitment == anchored else "CONFLICT"
            pending = tuple(
                item
                for item in snapshot.decisions
                if item.prepared.request.identity.command_id
                not in snapshot.materialized_command_ids
            )
            return (
                "COMPLETE"
                if pending == (decision,)
                and snapshot.decisions[-1] == decision
                and not runtime._pending()
                and not runtime._pending_gate_publications()
                and frontier == decision.tenant_commit_sequence
                and commitment == decision.resulting_commitment
                and anchored in (decision.prepared.predecessor_commitment, commitment)
                else "CONFLICT"
            )

    def check_selected_predecessor(self, decision: SelectedOwnerDecision) -> bool:
        runtime = self._runtime
        with runtime._authority_gate().hold():
            return (
                runtime._pending_owners() == (decision,)
                and not runtime._pending()
                and not runtime._pending_gate_publications()
                and self.materialization_state(decision) == "ABSENT"
            )

    @staticmethod
    def _rejected(
        identity: PublicationIdentity,
        kind: Literal["DENIED"],
        reason: str,
    ) -> PublicationRejected:
        return PublicationRejected(
            kind=kind,
            tenant_id=identity.tenant_id,
            command_id=identity.command_id,
            reason=reason,
        )
