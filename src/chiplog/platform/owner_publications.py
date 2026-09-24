"""Journal-first publication mechanics beneath registered broker owner adapters.

This component owns no semantic authority. Its private authority/journal ports
are provisioned by canonical composition, never by a public command or worker.
"""

from dataclasses import dataclass
from typing import Literal, Protocol

from chiplog.platform._owner_publication_contracts import (
    BrokerPublicationResult,
    ExactReplayQuery,
    JournalSelectedPublication,
    NoSelectedDecision,
    OwnerCommandBytes,
    PublicationRejected,
    RegisteredPublication,
)
from chiplog.platform._sqlite import EventAppender, PhysicalPublicationCommand, PhysicalRecord


class OwnerPublicationIntegrityError(RuntimeError):
    pass


class OwnerPublicationPending(RuntimeError):
    """An earlier selected batch must materialize before a fresh selection."""


class OwnerPublicationUncertain(RuntimeError):
    """A selected decision exists; retry may only materialize that exact decision."""

    def __init__(self, command_id: str) -> None:
        super().__init__(f"durable publication decision requires recovery: command={command_id}")
        self.command_id = command_id


@dataclass(frozen=True)
class PreparedOwnerPublication:
    request: RegisteredPublication
    issuance_id: str
    fence_generation: str
    fence_frontier: int
    predecessor_commitment: str


@dataclass(frozen=True)
class SelectedOwnerDecision:
    prepared: PreparedOwnerPublication
    decision_id: str
    decision_head: str
    decision_fingerprint: str
    resulting_commitment: str
    tenant_commit_sequence: int


class OwnerDecisionJournal(Protocol):
    """Bridge to the existing independently authoritative R13 decision journal."""

    def lookup(self, tenant_id: str, command_id: str) -> SelectedOwnerDecision | None: ...

    def select(
        self, prepared: PreparedOwnerPublication, resulting_commitment: str
    ) -> SelectedOwnerDecision: ...

    def materialized(self, decision: SelectedOwnerDecision) -> None: ...


class BrokerPublicationAuthority(Protocol):
    """Canonical broker implementation supplies registry/auth/snapshot verification.

    prepare may use isolated owners. check_prepared and materialization_state run
    locally; they must never call another owner while the writer lock is held.
    """

    def authenticate_replay(self, query: ExactReplayQuery) -> PublicationRejected | None: ...

    async def prepare(
        self, request: RegisteredPublication
    ) -> PreparedOwnerPublication | PublicationRejected: ...

    def check_prepared(
        self, prepared: PreparedOwnerPublication
    ) -> Literal["DENIED", "STALE", "INDETERMINATE"] | None: ...

    def materialization_state(
        self, decision: SelectedOwnerDecision
    ) -> Literal["ABSENT", "EXACT_PREFIX", "COMPLETE", "CONFLICT"]: ...

    def check_selected_predecessor(self, decision: SelectedOwnerDecision) -> bool:
        """Reprove exact physical predecessor, not a historical worker's live lease."""
        ...


def source_commands(request: RegisteredPublication) -> tuple[OwnerCommandBytes, ...]:
    if request.kind == "SINGLE_OWNER":
        return (request.command,)
    if request.kind == "PLAN_EFFECT_ATOMIC":
        return (request.planning_command, request.effects_command)
    if request.kind == "CALL_EFFECT_ATOMIC":
        return (
            request.loop_command,
            request.effects_command,
            *(
                (request.planning.command,)
                if request.planning.kind == "PLANNING_PUBLICATION"
                else ()
            ),
        )
    if request.kind == "COMPLETE_DELIVERY_ATOMIC_V2":
        return (
            request.loop_command,
            request.conversation_command,
            *request.prepared_effects_commands,
            request.terminal_work_command,
        )
    if request.kind == "REJECTED_COMPLETION_ATOMIC_V1":
        return (
            request.loop_rejection_command,
            request.rejected_terminalization_command,
            request.conversation_no_change_command,
            request.terminal_work_command,
        )
    return (request.loop_command, *request.prepared_effects_commands)


def _replay_query(request: RegisteredPublication) -> ExactReplayQuery:
    return ExactReplayQuery(
        identity=request.identity,
        operation=request.operation,
        current_invocation=request.authentication.invocation,
        original_commands=source_commands(request),
    )


def _result(decision: SelectedOwnerDecision, replay: bool) -> JournalSelectedPublication:
    request = decision.prepared.request
    return JournalSelectedPublication(
        kind="EXACT_REPLAY" if replay else "COMMITTED",
        tenant_id=request.identity.tenant_id,
        command_id=request.identity.command_id,
        decision_id=decision.decision_id,
        decision_head=decision.decision_head,
        decision_fingerprint=decision.decision_fingerprint,
        predecessor_commitment=decision.prepared.predecessor_commitment,
        resulting_commitment=decision.resulting_commitment,
        tenant_commit_sequence=decision.tenant_commit_sequence,
        complete_records=request.complete_records,
    )


class BrokerPublicationCoordinator:
    def __init__(
        self,
        appender: EventAppender,
        authority: BrokerPublicationAuthority,
        journal: OwnerDecisionJournal,
    ) -> None:
        self._appender = appender
        self._authority = authority
        self._journal = journal

    async def recover_selected(self, tenant_id: str, command_id: str) -> BrokerPublicationResult:
        """Broker startup path: materialize selected bytes, never rerun an owner."""
        decision = self._journal.lookup(tenant_id, command_id)
        if decision is None:
            return PublicationRejected(
                kind="HOLD",
                tenant_id=tenant_id,
                command_id=command_id,
                reason="no independently selected decision to materialize",
            )
        state = self._authority.materialization_state(decision)
        if state == "COMPLETE":
            self._journal.materialized(decision)
            return _result(decision, True)
        if state != "ABSENT":
            return PublicationRejected(
                kind="INTEGRITY_FAULT" if state == "CONFLICT" else "HOLD",
                tenant_id=tenant_id,
                command_id=command_id,
                reason="atomic materializer requires absent or complete selected batch; " + state,
            )
        prepared, request = decision.prepared, decision.prepared.request

        def guard() -> Literal["INDETERMINATE"] | None:
            if not self._authority.check_selected_predecessor(decision):
                return "INDETERMINATE"
            return None

        def exact_commitment(commitment: str) -> None:
            if commitment != decision.resulting_commitment:
                raise OwnerPublicationIntegrityError("selected materialization commitment differs")

        result = await self._appender.submit(
            PhysicalPublicationCommand(
                tenant_id=tenant_id,
                operation_kind=request.operation,
                idempotency_key=command_id,
                request_fingerprint=request.identity.command_fingerprint,
                expected_head=request.expected.tenant_frontier,
                fence_generation=prepared.fence_generation,
                expected_fence_frontier=prepared.fence_frontier,
                minimum_fence_frontier=prepared.fence_frontier,
                records=tuple(
                    PhysicalRecord(
                        row.record_id,
                        row.owner,
                        row.schema_id,
                        row.canonical_bytes,
                        row.fingerprint,
                    )
                    for row in request.complete_records
                ),
                admission_guard=guard,
                decision_guard=exact_commitment,
            )
        )
        if result.disposition not in ("COMMITTED", "REPLAY"):
            raise OwnerPublicationUncertain(command_id)
        if self._authority.materialization_state(decision) != "COMPLETE":
            raise OwnerPublicationIntegrityError("selected recovery produced an incomplete batch")
        self._journal.materialized(decision)
        return _result(decision, True)

    def lookup_exact(
        self, query: ExactReplayQuery
    ) -> JournalSelectedPublication | NoSelectedDecision | PublicationRejected:
        failure = self._authority.authenticate_replay(query)
        if failure is not None:
            return failure
        decision = self._journal.lookup(query.identity.tenant_id, query.identity.command_id)
        if decision is None:
            return NoSelectedDecision(
                tenant_id=query.identity.tenant_id, command_id=query.identity.command_id
            )
        original = decision.prepared.request
        if (
            original.identity != query.identity
            or original.operation != query.operation
            or source_commands(original) != query.original_commands
        ):
            return PublicationRejected(
                kind="CONFLICT",
                tenant_id=query.identity.tenant_id,
                command_id=query.identity.command_id,
                reason="changed exact-decision input",
            )
        state = self._authority.materialization_state(decision)
        if state == "COMPLETE":
            self._journal.materialized(decision)
            return _result(decision, True)
        return PublicationRejected(
            kind="INTEGRITY_FAULT" if state == "CONFLICT" else "HOLD",
            tenant_id=query.identity.tenant_id,
            command_id=query.identity.command_id,
            reason="selected decision has " + state + " materialization; recover exact decision",
        )

    async def commit(self, request: RegisteredPublication) -> BrokerPublicationResult:
        historical = self.lookup_exact(_replay_query(request))
        if not isinstance(historical, NoSelectedDecision):
            return historical
        prepared = await self._authority.prepare(request)
        if isinstance(prepared, PublicationRejected):
            return prepared
        if prepared.request != request:
            raise OwnerPublicationIntegrityError("broker prepared a different submitted request")
        selected: SelectedOwnerDecision | None = None

        def decide(commitment: str) -> None:
            nonlocal selected
            selected = self._journal.select(prepared, commitment)
            if selected.prepared != prepared or selected.resulting_commitment != commitment:
                raise OwnerPublicationIntegrityError("journal selected different complete bytes")

        command = PhysicalPublicationCommand(
            tenant_id=request.identity.tenant_id,
            operation_kind=request.operation,
            idempotency_key=request.identity.command_id,
            request_fingerprint=request.identity.command_fingerprint,
            expected_head=request.expected.tenant_frontier,
            fence_generation=prepared.fence_generation,
            expected_fence_frontier=prepared.fence_frontier,
            minimum_fence_frontier=prepared.fence_frontier,
            records=tuple(
                PhysicalRecord(
                    row.record_id, row.owner, row.schema_id, row.canonical_bytes, row.fingerprint
                )
                for row in request.complete_records
            ),
            admission_guard=lambda: self._authority.check_prepared(prepared),
            decision_guard=decide,
        )
        try:
            result = await self._appender.submit(command)
        except OwnerPublicationPending:
            return PublicationRejected(
                kind="HOLD",
                tenant_id=request.identity.tenant_id,
                command_id=request.identity.command_id,
                reason="an independently selected predecessor requires materialization",
            )
        except BaseException as error:
            if (
                self._journal.lookup(request.identity.tenant_id, request.identity.command_id)
                is not None
            ):
                raise OwnerPublicationUncertain(request.identity.command_id) from error
            raise
        if result.disposition not in ("COMMITTED", "REPLAY"):
            if selected is not None:
                raise OwnerPublicationUncertain(request.identity.command_id)
            rejection: Literal["HOLD", "CONFLICT", "STALE", "DENIED"]
            if result.disposition == "CONFLICT":
                rejection = "CONFLICT"
            elif result.disposition == "STALE":
                rejection = "STALE"
            elif result.disposition == "DENIED":
                rejection = "DENIED"
            else:
                rejection = "HOLD"
            return PublicationRejected(
                kind=rejection,
                tenant_id=request.identity.tenant_id,
                command_id=request.identity.command_id,
                reason="writer " + result.disposition,
            )
        if selected is None:
            # The physical replay path runs before guards. It cannot substitute
            # for a missing independently selected semantic decision.
            if result.disposition == "REPLAY":
                winner = self.lookup_exact(_replay_query(request))
                if isinstance(winner, (JournalSelectedPublication, PublicationRejected)):
                    return winner
            raise OwnerPublicationIntegrityError("physical replay without selected owner decision")
        if self._authority.materialization_state(selected) != "COMPLETE":
            raise OwnerPublicationIntegrityError(
                "selected decision materialized an incomplete batch"
            )
        self._journal.materialized(selected)
        return _result(selected, False)
