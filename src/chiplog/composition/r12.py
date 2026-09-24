"""Canonical hermetic Stage-1 composition. No model loop or external exposure."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import sqlite3
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, closing
from pathlib import Path
from time import time_ns
from types import FunctionType, ModuleType
from typing import cast

from chiplog.adapters.driven import calendar_reads as calendar_reads_module
from chiplog.adapters.driven.calendar_hermetic import HermeticCalendarProvider
from chiplog.adapters.driven.calendar_reads import CalendarReadBroker, _BoundCalendarPort
from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.adapters.driven.journal_sqlite import OWNER, SCHEMA, SQLiteJournal
from chiplog.adapters.driven.planning_sqlite import SQLitePlanningRepository
from chiplog.adapters.driven.r9_fence import (
    CONVERSATION_OWNER,
    CONVERSATION_SCHEMA,
    SCREEN_SINK,
    R3ConversationStore,
    R3ScreenDerivatives,
)
from chiplog.adapters.driven.r9_planning import PlanningWorkspaceQueries
from chiplog.adapters.driven.workspace_issuance import WorkspaceIssuanceJournal
from chiplog.adapters.driven.workspace_journal import JournalWorkspaceQueries
from chiplog.adapters.driven.workspace_sqlite import SQLiteWorkspaceStore, latest_workspace_state
from chiplog.capabilities.calendar_observations import CalendarBatch
from chiplog.capabilities.calendar_observations import observations as observations_module
from chiplog.capabilities.calendar_observations.boundary import (
    WorkspaceReadRequest as CalendarRequest,
)
from chiplog.capabilities.evidence_journal import journal as journal_module
from chiplog.capabilities.evidence_journal.commands import (
    Command,
    JournalPort,
    JournalRecord,
    Outcome,
    TrustedIngress,
)
from chiplog.capabilities.evidence_journal.journal import Journal
from chiplog.capabilities.projections import (
    conversation as conversation_module,
)
from chiplog.capabilities.projections import (
    disclosure as disclosure_module,
)
from chiplog.capabilities.projections import (
    workspace as workspace_module,
)
from chiplog.capabilities.projections._planning import (
    _PlanningProjectionRebuilder,
    _PlanningReadStore,
)
from chiplog.capabilities.projections.batch_boundary import ProposalContext, WorkspaceBatch
from chiplog.capabilities.projections.conversation import ConversationHistory
from chiplog.capabilities.projections.disclosure import (
    CurrentDisclosureGuard,
    join,
    label,
    verify_payload,
)
from chiplog.capabilities.projections.provenance import ProvenanceBinding, ProvenanceClosures
from chiplog.capabilities.projections.r9_boundary import (
    BudgetPolicy,
    ConversationEntry,
    NavigationCommand,
    ScreenLocation,
    ScreenSnapshot,
    WorkspaceIntegrityError,
    WorkspaceRejected,
    WorkspaceState,
)
from chiplog.capabilities.projections.workspace import (
    DashboardFamilySpec,
    DashboardRegistry,
    QueryDashboardBuilder,
    Workspace,
)
from chiplog.capabilities.projections.workspace_boundary import (
    DisclosureEnvelope,
    ProvenanceSubject,
    SourceReference,
    WorkspaceQuery,
    WorkspaceReadContext,
    WorkspaceReadRequest,
    WorkspaceReadResult,
)
from chiplog.composition.r10 import HermeticIngressRegistry
from chiplog.composition.r14_h1_workspace_issuance_contracts import (
    H1CalendarOriginalReadV1,
    H1DashboardIssuanceRefV1,
    H1OriginalWorkspaceIssuanceV1,
    H1PlanningSourcesV1,
    H1PublicationRowV1,
    H1QueryProofV1,
    H1RecordRowV1,
    H1RetainedDecisionV1,
    H1WorkspaceSnapshotV1,
    H1WorkspaceSourcesV1,
)
from chiplog.domain_primitives import TenantId
from chiplog.platform._sqlite import (
    EventAppender,
    FenceAdvanceCommand,
    PhysicalPublicationCommand,
    PhysicalRecord,
    SQLiteMaterializer,
)
from chiplog.platform.calendar_read_ledger import CalendarReadLedger, CalendarReadState
from chiplog.platform.workspace_snapshot import ReadSnapshot, workspace_snapshot


class _BatchContexts:
    def __init__(self, runtime: R12Workspace, context: WorkspaceReadContext) -> None:
        self.runtime, self.context = runtime, context

    def validate(self, context: WorkspaceReadContext) -> None:
        if context != self.context or self.runtime._active != context:
            raise WorkspaceRejected("not the exact active broker-issued workspace cut")
        self.runtime._validate_peer()


class _Sources:
    def __init__(
        self,
        identity: TrustedIngress,
        contexts: _BatchContexts,
        bindings: tuple[ProvenanceBinding, ...] = (),
    ) -> None:
        self._sources = {
            (s.owner, s.record_id): SourceReference.model_validate_json(s.model_dump_json())
            for s in identity.sources
        }
        self._contexts = contexts
        if len(self._sources) != len(identity.sources):
            raise WorkspaceRejected("duplicate independent source identity")
        self._manifests: dict[str, tuple[SourceReference, ...]] = {}
        self._bindings = bindings
        self._closures = ProvenanceClosures(bindings)

    def register(self, result: WorkspaceReadResult, producer: str | None = None) -> None:
        for row in result.rows:
            verify_payload(row.canonical_payload, row.envelope)
            if producer is not None:
                subject = ProvenanceSubject(
                    tenant_id=result.context.tenant_id,
                    producer=producer,
                    record_id=row.row_id,
                    revision=row.row_version,
                )
                if producer == "core.conversation":
                    # History must already be bound to original selected ingress
                    # or CompleteAcceptance, before the history guard runs.
                    self._closures.check(subject, row.envelope)
                else:
                    self._bindings += (
                        ProvenanceBinding(
                            subject=subject,
                            content_digest=row.envelope.content_digest,
                            sources=row.envelope.sources,
                        ),
                    )
                    self._closures = ProvenanceClosures(self._bindings)
                continue
            previous = self._manifests.setdefault(row.envelope.content_digest, row.envelope.sources)
            if previous != row.envelope.sources:
                raise WorkspaceRejected("ambiguous composed source closure")

    def validate_subject(
        self,
        subject: ProvenanceSubject,
        envelope: DisclosureEnvelope,
        context: WorkspaceReadContext,
    ) -> None:
        self._contexts.validate(context)
        self._closures.check(subject, envelope)

    def validate(self, source: SourceReference, context: WorkspaceReadContext) -> None:
        self._contexts.validate(context)
        if self._sources.get((source.owner, source.record_id)) != source:
            raise WorkspaceRejected("source differs from independent ingress registry")

    def validate_manifest(
        self, envelope: DisclosureEnvelope, context: WorkspaceReadContext
    ) -> None:
        self._contexts.validate(context)
        expected = self._manifests.get(envelope.content_digest)
        if expected is None:
            direct = tuple(
                s for s in self._sources.values() if s.content_digest == envelope.content_digest
            )
            if len(direct) != 1:
                raise WorkspaceRejected("missing complete independent provenance")
            expected = direct
        if envelope.sources != expected:
            raise WorkspaceRejected("incomplete composed provenance")


class _CachedQuery:
    def __init__(self, result: WorkspaceReadResult) -> None:
        self.result = result

    async def read(self, request: WorkspaceReadRequest) -> WorkspaceReadResult:
        if request.context != self.result.context:
            raise WorkspaceRejected("cached family outside its batch")
        result = self.result
        if request.detail_id is not None:
            if result.next_cursor is not None:
                raise WorkspaceRejected("detail requires complete family source closure")
            result = result.model_copy(
                update={
                    "rows": tuple(row for row in result.rows if row.row_id == request.detail_id),
                }
            )
        return result.model_copy(
            update={
                "request_id": request.request_id,
                "read_attempt_id": request.read_attempt_id,
            }
        )


class _Derivatives:
    def __init__(self, local: R3ScreenDerivatives, contexts: _BatchContexts) -> None:
        self._local, self._contexts = local, contexts

    async def register(self, snapshot: ScreenSnapshot) -> None:
        self._contexts.validate(snapshot.ref.context)
        if snapshot.ref.location.dashboard_id == "core.calendar":
            # External observations have no canonical tenant record to register in
            # R3. Their full envelopes remain in the immutable display store; R12
            # has no unguarded replay/read path and checks fence + external state
            # at every release, including proposal-context reuse.
            return
        await self._local.register(snapshot)


class _JournalCommands:
    def __init__(self, runtime: R12Workspace) -> None:
        self._runtime = runtime

    async def execute(self, peer: str, command: Command) -> Outcome:
        self._runtime._check_dispatch()
        outcome = await self._runtime._journal.execute(peer, command)
        self._runtime._check_dispatch()
        return outcome

    async def prepare(self, peer: str, command: Command) -> Outcome:
        self._runtime._check_dispatch()
        outcome = await self._runtime._journal.prepare(peer, command)
        self._runtime._check_dispatch()
        return outcome

    def lineage(self, peer: str, tenant: str, family: str) -> tuple[JournalRecord, ...]:
        self._runtime._check_dispatch()
        result = self._runtime._journal.lineage(peer, tenant, family)
        self._runtime._check_dispatch()
        return result


class R12Workspace:
    def __init__(
        self,
        database: Path,
        screens: Path,
        materializer: SQLiteMaterializer,
        appender: EventAppender,
        ingress: HermeticIngressRegistry,
        peer: str,
        channel: str,
        database_id: str,
        fence_frontier: int,
        calendar: CalendarReadBroker,
        calendar_ledger: CalendarReadLedger,
        planning_sources: Mapping[str, tuple[SourceReference, ...]],
        *,
        conversation_provenance: tuple[ProvenanceBinding, ...] = (),
        selected_loop_decisions: tuple[H1RetainedDecisionV1, ...] = (),
        issuance: WorkspaceIssuanceJournal | None = None,
    ) -> None:
        _validate_paths(database, screens, calendar_ledger._path)
        self._database, self._screens = database, screens
        self._materializer, self._appender = materializer, appender
        self.ingress, self._peer = ingress, peer
        identity = ingress.authenticate(peer)
        if identity is None:
            raise WorkspaceRejected("unknown authenticated peer")
        self._identity = identity
        self._policy_payload = _policy_bytes(identity, channel, database_id)
        self._policy_id = (
            _policy_id(identity.tenant, identity.principal, channel)
            + ":"
            + hashlib.sha256(self._policy_payload).hexdigest()
        )
        self._channel, self._database_id = channel, database_id
        self._fence_frontier = fence_frontier
        self._calendar, self._calendar_ledger = calendar, calendar_ledger
        self._planning_sources = dict(planning_sources)
        self._conversation_provenance = conversation_provenance
        self._selected_loop_decisions = selected_loop_decisions
        self._issuance = issuance
        self._pinned_issuance = issuance
        self._calendar_peer = calendar.authenticate_transport(
            tenant_id=identity.tenant,
            principal_id=identity.principal,
            channel_id=channel,
        )
        self._calendar_port = calendar.connect(self._calendar_peer)
        self._storage = SQLiteJournal(
            database,
            appender,
            fence_generation=identity.heads.deletion,
            fence_frontier=fence_frontier,
        )
        self._journal = Journal(self._storage, ingress)
        self._journal_port = _JournalCommands(self)
        self.journal: JournalPort = self._journal_port
        stat = database.stat()
        self._physical_identity = (stat.st_dev, stat.st_ino)
        self._active: WorkspaceReadContext | None = None
        self._issued: WorkspaceBatch | None = None
        self._state_fingerprint: str | None = None
        self._calendar_state: CalendarReadState | None = None
        self._h1_original: (
            tuple[
                H1WorkspaceSnapshotV1,
                tuple[H1QueryProofV1, ...],
                H1CalendarOriginalReadV1,
                H1WorkspaceSourcesV1,
                H1DashboardIssuanceRefV1,
            ]
            | None
        ) = None
        self._closed = False
        store = SQLiteWorkspaceStore(screens)
        self._screen_state: WorkspaceState | None = (
            issuance.recover_cache(store, channel)
            if issuance is not None
            else latest_workspace_state(store, identity.tenant, channel)
        )

    def _validate_peer(self) -> TrustedIngress:
        identity = self.ingress.authenticate(self._peer)
        if (
            self._closed
            or identity is None
            or identity.model_copy(
                update={
                    "heads": identity.heads.model_copy(
                        update={"journal": self._identity.heads.journal}
                    ),
                    "confirmations": self._identity.confirmations,
                }
            )
            != self._identity
        ):
            raise WorkspaceRejected("workspace peer/policy/source binding changed")
        stat = self._database.stat()
        if (stat.st_dev, stat.st_ino) != self._physical_identity:
            raise WorkspaceRejected("workspace physical database changed")
        return identity

    @staticmethod
    def _state(connection: sqlite3.Connection, tenant: str) -> tuple[int, tuple[object, ...], str]:
        try:
            return R12Workspace._read_state(connection, tenant)
        except (ValueError, TypeError, sqlite3.Error) as error:
            raise WorkspaceIntegrityError("workspace.read", tenant, "tenant-frontier") from error

    @staticmethod
    def _read_state(
        connection: sqlite3.Connection, tenant: str
    ) -> tuple[int, tuple[object, ...], str]:
        head_row = connection.execute(
            "SELECT head FROM tenant_heads WHERE tenant_id = ?", (tenant,)
        ).fetchone()
        head = 0 if head_row is None else int(head_row[0])
        if head < 0:
            raise ValueError("negative tenant head")
        fence = connection.execute(
            "SELECT generation, frontier FROM deletion_fences WHERE tenant_id = ?", (tenant,)
        ).fetchone()
        if fence is None:
            raise WorkspaceRejected("missing workspace deletion fence")
        publications = connection.execute(
            "SELECT commit_sequence FROM publications WHERE tenant_id = ? ORDER BY commit_sequence",
            (tenant,),
        ).fetchall()
        if tuple(row[0] for row in publications) != tuple(range(1, head + 1)):
            raise WorkspaceRejected("incomplete tenant publication frontier")
        digest = hashlib.sha256()
        for table in ("tenant_heads", "deletion_fences", "publications", "records"):
            rows = connection.execute(
                f"SELECT * FROM {table} WHERE tenant_id = ? ORDER BY rowid", (tenant,)
            ).fetchall()
            digest.update(repr((table, rows)).encode())
        return head, tuple(fence), digest.hexdigest()

    def _context(self, snapshot: ReadSnapshot, observed_at_ns: int) -> WorkspaceReadContext:
        identity = self._validate_peer()
        head, fence, _ = self._state(snapshot.connection, identity.tenant)
        if fence != (identity.heads.deletion, self._fence_frontier):
            raise WorkspaceRejected("workspace fence changed")
        policy = snapshot.connection.execute(
            "SELECT owner, schema_id, canonical_bytes FROM records "
            "WHERE tenant_id=? AND record_id=?",
            (identity.tenant, self._policy_id),
        ).fetchone()
        if policy != ("workspace_policy", "chiplog.workspace.policy.v1", self._policy_payload):
            error = ValueError(
                "policy binding differs from independent current transport/policy state"
            )
            raise WorkspaceIntegrityError(
                "workspace.policy", identity.tenant, self._policy_id
            ) from error
        return WorkspaceReadContext(
            tenant_id=identity.tenant,
            database_instance_id=self._database_id,
            principal_id=identity.principal,
            principal_contour_head=identity.heads.contour,
            channel_id=self._channel,
            endpoint_binding_head=identity.heads.session,
            broker_epoch=1,
            owner_id="projections",
            generation_id="r12.workspace.v1",
            session_id=secrets.token_hex(24),
            snapshot_id=secrets.token_hex(24),
            snapshot_frontier=head,
            verified_snapshot_bytes=secrets.token_bytes(32),
            invalidator_registry_digest="r12.workspace.read.v1",
            policy_head=identity.heads.policy,
            deletion_fence_head=identity.heads.deletion,
            observed_at_ns=observed_at_ns,
        )

    @staticmethod
    def _request(context: WorkspaceReadContext, max_rows: int) -> WorkspaceReadRequest:
        return WorkspaceReadRequest(
            context=context,
            query="CONVERSATION_HISTORY",
            request_id=secrets.token_hex(16),
            read_attempt_id=secrets.token_hex(16),
            response_slot_id=secrets.token_hex(16),
            max_rows=max_rows,
            after_cursor=None,
            detail_id=None,
            range_start_ns=None,
            range_end_ns=None,
        )

    def _history(
        self, contexts: _BatchContexts, guard: CurrentDisclosureGuard
    ) -> ConversationHistory:
        return ConversationHistory(
            R3ConversationStore(
                self._materializer,
                self._appender,
                contexts,
                contexts.context,
                self._fence_frontier,
            ),
            contexts,
            guard,
            self._identity.endpoint,
            subject_bound=self._issuance is not None,
        )

    async def accept(self, entry: ConversationEntry) -> str:
        self._check_dispatch()
        with workspace_snapshot(self._database) as snapshot:
            context = self._context(snapshot, 0)
        self._active = context
        contexts = _BatchContexts(self, context)
        sources = _Sources(self._validate_peer(), contexts, self._conversation_provenance)
        guard = CurrentDisclosureGuard(contexts, sources)
        try:
            return await self._history(contexts, guard).accept(entry, self._request(context, 100))
        finally:
            self._active = None
            self._issued = None

    async def read_batch(
        self,
        *,
        max_rows: int = 100,
        navigation: NavigationCommand | None = None,
        budget: BudgetPolicy | None = None,
    ) -> WorkspaceBatch:
        self._check_dispatch()
        if self._active is not None:
            raise WorkspaceRejected("workspace batch already active")
        self._issued = None
        calendar_context = await self._calendar.acquire(
            self._calendar_peer, observed_at_ns=time_ns()
        )
        calendar_state = self._calendar_ledger.current_state(self._identity.tenant)
        try:
            with workspace_snapshot(self._database) as snapshot:
                context = self._context(snapshot, calendar_context.observed_at_ns)
                self._active = context
                contexts = _BatchContexts(self, context)
                identity = self._validate_peer()
                sources = _Sources(identity, contexts, self._conversation_provenance)
                guard = CurrentDisclosureGuard(contexts, sources)
                request = self._request(context, max_rows)
                history_store = R3ConversationStore(
                    self._materializer, self._appender, contexts, context, self._fence_frontier
                )
                if (
                    len(history_store.entries(identity.tenant, context.snapshot_frontier))
                    > max_rows
                ):
                    raise WorkspaceRejected("complete relevant history exceeds batch bound")
                history = await ConversationHistory(
                    history_store,
                    contexts,
                    guard,
                    identity.endpoint,
                    subject_bound=self._issuance is not None,
                ).context_read(request)
                sources.register(history, "core.conversation" if self._issuance else None)
                journal = await JournalWorkspaceQueries(
                    self._journal, self._peer, identity.heads, contexts
                ).read(request.model_copy(update={"query": "JOURNAL_CLAIMS"}))
                sources.register(journal, "core.journal" if self._issuance else None)
                repository = SQLitePlanningRepository(
                    self._database, self._appender, asyncio.get_running_loop()
                )
                publications = repository.committed_publications(TenantId(identity.tenant))
                if set(self._planning_sources) != {
                    p.result.intention_line_id.value for p in publications
                }:
                    raise WorkspaceRejected("incomplete planning source inventory")
                for publication in publications:
                    supplied = self._planning_sources[publication.result.intention_line_id.value]
                    records = {record.record_id.value: record for record in publication.records}
                    if tuple(source.record_id for source in supplied) != tuple(sorted(records)):
                        raise WorkspaceRejected(
                            "planning requires complete owner publication provenance"
                        )
                    for source in supplied:
                        record = records[source.record_id]
                        if (
                            source.owner != "planning"
                            or source.tenant_id != identity.tenant
                            or source.record_version != str(publication.commit_sequence)
                            or source.content_digest
                            != hashlib.sha256(record.canonical_bytes).hexdigest()
                        ):
                            raise WorkspaceRejected("planning physical/source identity mismatch")
                projection = _PlanningProjectionRebuilder(
                    cast(_PlanningReadStore, repository)
                ).rebuild(
                    TenantId(identity.tenant), verified_tenant_frontier=context.snapshot_frontier
                )
                planning = await PlanningWorkspaceQueries(
                    projection, contexts, self._planning_sources
                ).read(request.model_copy(update={"query": "PLANNING_VIEW"}))
                sources.register(planning, "core.planning" if self._issuance else None)
                calendar_request = CalendarRequest.model_validate_json(
                    request.model_copy(
                        update={
                            "context": calendar_context,
                            "query": "CALENDAR_AGENDA",
                            "range_start_ns": 0,
                            "range_end_ns": 2**63 - 1,
                        }
                    ).model_dump_json()
                )
                external = await self._calendar_port.read(calendar_request)
                calendar = WorkspaceReadResult.model_validate_json(external.model_dump_json())
                original_calendar = self._calendar._original_read(
                    self._calendar_peer, calendar_request, external
                )
                self._calendar._verify_original_read(self._calendar_peer, original_calendar)
                for field in (
                    "tenant_id",
                    "database_instance_id",
                    "principal_id",
                    "channel_id",
                    "principal_contour_head",
                    "policy_head",
                    "deletion_fence_head",
                ):
                    if getattr(calendar.context, field) != getattr(context, field):
                        raise WorkspaceRejected("external calendar scope differs from workspace")
                if calendar.disposition not in ("CURRENT", "LAGGING"):
                    raise WorkspaceRejected("external calendar observation indeterminate")
                # Calendar remains an explicit external cut in the batch. Dashboard
                # framing uses the internal cut but can only be LAGGING/display-only.
                calendar_display = calendar.model_copy(
                    update={"context": context, "disposition": "LAGGING"}
                )
                sources.register(calendar_display, "core.calendar" if self._issuance else None)
                for producer, result in (
                    ("core.conversation", history),
                    ("core.planning", planning),
                    ("core.journal", journal),
                    ("core.calendar", calendar_display),
                ):
                    if result.context != context or result.disposition not in (
                        "CURRENT",
                        "LAGGING",
                    ):
                        raise WorkspaceRejected("family failed the shared read cut")
                    for row in result.rows:
                        if self._issuance is not None:
                            guard.check_subject(
                                ProvenanceSubject(
                                    tenant_id=context.tenant_id,
                                    producer=producer,
                                    record_id=row.row_id,
                                    revision=row.row_version,
                                ),
                                row.envelope,
                                context,
                                identity.endpoint,
                            )
                        else:
                            guard.check(row.envelope, context, identity.endpoint)
                families = tuple(
                    DashboardFamilySpec(
                        name,
                        name + ".r12",
                        "1",
                        QueryDashboardBuilder(_CachedQuery(result), cast(WorkspaceQuery, query)),
                        ("overview",) if name == "core.conversation" else ("overview", "detail"),
                    )
                    for name, result, query in (
                        ("core.conversation", history, "CONVERSATION_HISTORY"),
                        ("core.planning", planning, "PLANNING_VIEW"),
                        ("core.journal", journal, "JOURNAL_CLAIMS"),
                        ("core.calendar", calendar_display, "CALENDAR_AGENDA"),
                    )
                )
                workspace = Workspace(
                    DashboardRegistry(families),
                    SQLiteWorkspaceStore(self._screens),
                    contexts,
                    guard,
                    identity.endpoint,
                    _Derivatives(
                        R3ScreenDerivatives(self._appender, contexts, self._fence_frontier),
                        contexts,
                    ),
                    issuance=self._issuance,
                    subject_bound=self._issuance is not None,
                )
                policy = budget or BudgetPolicy(
                    total=16000, fixed=100, output=100, conversation_floor=256
                )
                state = self._screen_state
                if state is None:
                    for family in ("core.planning", "core.journal", "core.calendar"):
                        state = await workspace.refresh(
                            request,
                            policy,
                            state,
                            NavigationCommand(
                                operation="focus", location=ScreenLocation(dashboard_id=family)
                            ),
                        )
                        self._screen_state = state
                state = await workspace.refresh(request, policy, state, navigation)
                self._screen_state = state
                assert state is not None
                envelopes = tuple(
                    row.envelope
                    for result in (history, planning, journal, calendar)
                    for row in result.rows
                )
                batch = WorkspaceBatch(
                    batch_id=secrets.token_hex(24),
                    context=context,
                    policy_binding_digest=hashlib.sha256(self._policy_payload).hexdigest(),
                    history=history,
                    planning=planning,
                    journal=journal,
                    calendar=calendar,
                    external_context=calendar.context,
                    dashboard=state,
                    label=join(tuple(e.label for e in envelopes))
                    if envelopes
                    else label("UNRESTRICTED"),
                    history_complete=history.next_cursor is None,
                )
                _, _, expected = self._state(snapshot.connection, identity.tenant)
                publications_rows = tuple(
                    H1PublicationRowV1(
                        tenant_id=str(row[0]),
                        operation_kind=str(row[1]),
                        idempotency_key=str(row[2]),
                        request_fingerprint=str(row[3]),
                        commit_sequence=int(row[4]),
                        record_ids_json=str(row[5]),
                    )
                    for row in snapshot.connection.execute(
                        "SELECT tenant_id, operation_kind, idempotency_key, request_fingerprint, "
                        "commit_sequence, record_ids FROM publications WHERE tenant_id=? "
                        "ORDER BY commit_sequence",
                        (identity.tenant,),
                    )
                )
                record_rows = tuple(
                    H1RecordRowV1(
                        tenant_id=str(row[0]),
                        record_id=str(row[1]),
                        owner=str(row[2]),
                        schema_id=str(row[3]),
                        canonical_bytes_base64=base64.b64encode(bytes(row[4])).decode("ascii"),
                        commit_sequence=int(row[5]),
                    )
                    for row in snapshot.connection.execute(
                        "SELECT tenant_id, record_id, owner, schema_id, canonical_bytes, "
                        "commit_sequence FROM records WHERE tenant_id=? ORDER BY record_id",
                        (identity.tenant,),
                    )
                )
                fence = snapshot.connection.execute(
                    "SELECT generation, frontier FROM deletion_fences WHERE tenant_id=?",
                    (identity.tenant,),
                ).fetchone()
                if fence is None:
                    raise WorkspaceRejected("missing H1 deletion fence")
                h1_snapshot = H1WorkspaceSnapshotV1(
                    tenant_head=context.snapshot_frontier,
                    deletion_generation=str(fence[0]),
                    deletion_frontier=int(fence[1]),
                    publications=publications_rows,
                    records=record_rows,
                    release_fingerprint=expected,
                )
                h1_queries = (
                    H1QueryProofV1(
                        slot="history",
                        reader_id="conversation.context_read.v1",
                        request_json=request.model_dump_json(),
                        result_json=history.model_dump_json(),
                    ),
                    H1QueryProofV1(
                        slot="planning",
                        reader_id="planning.workspace.read.v1",
                        request_json=request.model_copy(
                            update={"query": "PLANNING_VIEW"}
                        ).model_dump_json(),
                        result_json=planning.model_dump_json(),
                    ),
                    H1QueryProofV1(
                        slot="journal",
                        reader_id="journal.workspace.read.v1",
                        request_json=request.model_copy(
                            update={"query": "JOURNAL_CLAIMS"}
                        ).model_dump_json(),
                        result_json=journal.model_dump_json(),
                    ),
                    H1QueryProofV1(
                        slot="calendar",
                        reader_id="calendar.workspace.read.v1",
                        request_json=calendar_request.model_dump_json(),
                        result_json=calendar.model_dump_json(),
                    ),
                )
                h1_calendar = H1CalendarOriginalReadV1(
                    provider_batch_json=original_calendar.provider_batch.model_dump_json(),
                    state_json=original_calendar.state.model_dump_json(),
                    operation_json=original_calendar.operation.model_dump_json(),
                    result_json=original_calendar.result.model_dump_json(),
                    operation_fingerprint=original_calendar.operation.fingerprint(),
                    recipient_fingerprint=original_calendar.operation.recipient_fingerprint(),
                    proof_fingerprint=original_calendar.receipt.proof_fingerprint or "0" * 64,
                    result_digest=original_calendar.receipt.result_digest or "0" * 64,
                    cursor_token=(
                        None
                        if original_calendar.cursor_binding is None
                        else original_calendar.cursor_binding.token
                    ),
                    cursor_snapshot_id=(
                        None
                        if original_calendar.cursor_binding is None
                        else original_calendar.cursor_binding.snapshot_id
                    ),
                    cursor_query_binding_base64=(
                        None
                        if original_calendar.cursor_binding is None
                        else base64.b64encode(
                            original_calendar.cursor_binding.query_binding
                        ).decode("ascii")
                    ),
                    cursor_last_order_key=(
                        None
                        if original_calendar.cursor_binding is None
                        else original_calendar.cursor_binding.last_order_key
                    ),
                    display_result_json=calendar_display.model_dump_json(),
                )
                h1_sources = H1WorkspaceSourcesV1(
                    trusted_ingress_json=identity.model_dump_json(),
                    conversation_bindings_json=tuple(
                        binding.model_dump_json() for binding in self._conversation_provenance
                    ),
                    selected_loop_decisions=self._selected_loop_decisions,
                    planning_sources=tuple(
                        H1PlanningSourcesV1(
                            intention_line_id=line_id,
                            source_reference_json=tuple(
                                source.model_dump_json() for source in sources
                            ),
                        )
                        for line_id, sources in sorted(self._planning_sources.items())
                    ),
                    policy_record_id=self._policy_id,
                    policy_payload_base64=base64.b64encode(self._policy_payload).decode("ascii"),
                    endpoint=identity.endpoint,
                )
                if self._issuance is None:
                    raise WorkspaceRejected("H1 requires original dashboard issuance")
                dashboard_entry_id, dashboard_payload_digest = self._issuance.raw_entry(
                    state.channel_id, state.sequence
                )
                h1_dashboard = H1DashboardIssuanceRefV1(
                    channel_id=state.channel_id,
                    sequence=state.sequence,
                    entry_id=dashboard_entry_id,
                    payload_digest=dashboard_payload_digest,
                )
            self._release(expected, calendar_state)
            self._check_dispatch()
            self._issued, self._state_fingerprint, self._calendar_state = (
                batch,
                expected,
                calendar_state,
            )
            self._h1_original = (h1_snapshot, h1_queries, h1_calendar, h1_sources, h1_dashboard)
            return batch
        finally:
            self._active = None

    def _release(self, expected: str, calendar_state: CalendarReadState) -> None:
        self._validate_peer()
        with closing(sqlite3.connect(self._database)) as connection:
            connection.execute("BEGIN")
            if self._state(connection, self._identity.tenant)[2] != expected:
                raise WorkspaceRejected("workspace invalidated before release")
            if self._calendar_ledger.current_state(self._identity.tenant) != calendar_state:
                raise WorkspaceRejected("calendar invalidated before workspace release")
            if self._calendar._physical_identity() != self._calendar._identity:
                raise WorkspaceRejected("calendar physical database changed")

    def proposal_context(self, batch: WorkspaceBatch) -> ProposalContext:
        self._check_dispatch()
        if (
            self._issued is None
            or batch != self._issued
            or not batch.history_complete
            or self._state_fingerprint is None
            or self._calendar_state is None
        ):
            raise WorkspaceRejected("consequential proposal requires complete issued history")
        self._release(self._state_fingerprint, self._calendar_state)
        return ProposalContext(
            batch=batch,
            history_record_ids=tuple(row.row_id for row in batch.history.rows),
            label=batch.label,
        )

    def h1_original_issuance(
        self,
        *,
        run_id: str,
        started_run_head: str,
        turn_id: str,
        worker_session: str,
        workspace_member_json: str,
        proposal_context_json: str,
    ) -> H1OriginalWorkspaceIssuanceV1:
        """Materialize only the exact read cut captured within the original snapshot."""
        if self._issued is None or self._h1_original is None:
            raise WorkspaceRejected("H1 original issuance requires an issued original workspace")
        context = self.proposal_context(self._issued)
        if context.model_dump_json() != proposal_context_json:
            raise WorkspaceRejected("H1 proposal context differs from issued workspace")
        snapshot, queries, calendar, sources, dashboard = self._h1_original
        # R13's pre-snapshot enumeration cannot authenticate a nonempty
        # conversation lineage at this original R12 cut.  Until it supplies
        # exact selected decision rows captured under this snapshot, issuing
        # would turn a binding summary into retrospective proof.
        if sources.conversation_bindings_json and not sources.selected_loop_decisions:
            raise WorkspaceRejected("H1_WORKSPACE_ORIGINAL_READ_UNPROVEN")
        return H1OriginalWorkspaceIssuanceV1(
            tenant=self._identity.tenant,
            run_id=run_id,
            started_run_head=started_run_head,
            turn_id=turn_id,
            worker_session=worker_session,
            workspace_member_json=workspace_member_json,
            proposal_context_json=proposal_context_json,
            snapshot=snapshot,
            queries=queries,
            calendar=calendar,
            sources=sources,
            dashboard=dashboard,
        )

    async def history(
        self,
        batch: WorkspaceBatch,
        *,
        max_rows: int = 100,
        after_cursor: str | None = None,
    ) -> WorkspaceReadResult:
        self.proposal_context(batch)
        if self._active is not None:
            raise WorkspaceRejected("workspace batch already active")
        try:
            with workspace_snapshot(self._database) as snapshot:
                if (
                    self._state(snapshot.connection, batch.context.tenant_id)[2]
                    != self._state_fingerprint
                ):
                    raise WorkspaceRejected("history tool cut is no longer current")
                self._active = batch.context
                contexts = _BatchContexts(self, batch.context)
                guard = CurrentDisclosureGuard(
                    contexts,
                    _Sources(self._validate_peer(), contexts, self._conversation_provenance),
                )
                request = self._request(batch.context, max_rows).model_copy(
                    update={"after_cursor": after_cursor}
                )
                result = await self._history(contexts, guard).read(request)
            self.proposal_context(batch)
            return result
        finally:
            self._active = None

    async def calendar_detail(self, batch: WorkspaceBatch, record_id: str) -> WorkspaceReadResult:
        self.proposal_context(batch)
        request = self._request(batch.context, 1).model_copy(
            update={
                "context": batch.external_context,
                "query": "CALENDAR_DETAIL",
                "detail_id": record_id,
            }
        )
        result = await self._calendar_port.read(
            CalendarRequest.model_validate_json(request.model_dump_json())
        )
        self.proposal_context(batch)
        if result.context.model_dump_json() != batch.external_context.model_dump_json():
            raise WorkspaceRejected("calendar detail substituted the issued cut")
        return WorkspaceReadResult.model_validate_json(result.model_dump_json())

    def _check_dispatch(self) -> None:
        for module, expected in _MODULE_BINDINGS.items():
            current = {
                name: value
                for name, value in vars(module).items()
                if not name.startswith("__") and not isinstance(value, ModuleType)
            }
            if current.keys() != expected.keys() or any(
                current[name] is not value for name, value in expected.items()
            ):
                raise WorkspaceRejected("substituted owner disclosure/semantic helper")
        if any(globals().get(name) is not value for name, value in _GLOBAL_BINDINGS.items()):
            raise WorkspaceRejected("substituted workspace composition dependency")
        for cls, expected in _DISPATCH.items():
            current = {
                name: value
                for name, value in vars(cls).items()
                if isinstance(value, (FunctionType, staticmethod, classmethod))
            }
            if current != expected:
                raise WorkspaceRejected("unregistered workspace class dispatch")
        if (
            self.journal is not self._journal_port
            or self._journal_port._runtime is not self
            or self._journal._store is not self._storage
            or self._journal._identities is not self.ingress
            or self._storage._appender is not self._appender
            or self._appender._materializer is not self._materializer
            or self._calendar._ledger is not self._calendar_ledger
            or self._storage._database != self._database
            or self.ingress._store._database != self._database
            or self.ingress._store._appender is not self._appender
            or type(self._calendar_port) is not _BoundCalendarPort
            or self._calendar_port._broker is not self._calendar
            or self._calendar_port._peer is not self._calendar_peer
        ):
            raise WorkspaceRejected("substituted workspace owner/storage graph")
        if self._issuance is not self._pinned_issuance:
            raise WorkspaceRejected("substituted workspace issuance journal")
        if self._issuance is not None and (
            type(self._issuance) is not WorkspaceIssuanceJournal
            or type(self._issuance._journal) is not IndependentTenantDecisionJournal
            or self._issuance._tenant != self._identity.tenant
            or self._issuance._gate != self._materializer.authority_gate
            or self._issuance._journal.authority_gate != self._materializer.authority_gate
            or self._issuance._journal._path
            != self._database.with_suffix(".workspace-issuance").resolve()
        ):
            raise WorkspaceRejected("workspace issuance differs from canonical authority bundle")
        for instance in (
            self,
            self._journal,
            self._storage,
            self._calendar,
            self.ingress,
            self._appender,
            self._materializer,
            self.ingress._store,
            self._calendar_ledger,
            self._calendar._provider,
            self._calendar_port,
            self._journal_port,
            *((self._issuance, self._issuance._journal) if self._issuance is not None else ()),
        ):
            if type(instance) not in _DISPATCH or any(
                name in _DISPATCH[type(instance)] for name in vars(instance)
            ):
                raise WorkspaceRejected("unregistered workspace instance dispatch")


_DISPATCH: dict[type[object], dict[str, object]] = {
    cls: {
        name: value
        for name, value in vars(cls).items()
        if isinstance(value, (FunctionType, staticmethod, classmethod))
    }
    for cls in (
        R12Workspace,
        Journal,
        SQLiteJournal,
        CalendarReadBroker,
        HermeticIngressRegistry,
        ConversationHistory,
        R3ConversationStore,
        JournalWorkspaceQueries,
        PlanningWorkspaceQueries,
        Workspace,
        _Sources,
        _CachedQuery,
        _BatchContexts,
        _Derivatives,
        EventAppender,
        SQLiteMaterializer,
        CalendarReadLedger,
        HermeticCalendarProvider,
        CurrentDisclosureGuard,
        SQLiteWorkspaceStore,
        R3ScreenDerivatives,
        WorkspaceIssuanceJournal,
        IndependentTenantDecisionJournal,
        ProvenanceClosures,
        DashboardRegistry,
        QueryDashboardBuilder,
        _BoundCalendarPort,
        _JournalCommands,
    )
}


def _validate_paths(database: Path, screens: Path, calendar_ledger_path: Path) -> None:
    paths = (database, screens, calendar_ledger_path)
    if len({path.resolve() for path in paths}) != len(paths):
        raise WorkspaceRejected("authority, display and calendar ledger paths must differ")
    existing = [path.stat() for path in paths if path.exists()]
    if len({(stat.st_dev, stat.st_ino) for stat in existing}) != len(existing):
        raise WorkspaceRejected("aliased workspace physical databases")


def _open_calendar_ledger(path: Path, state: CalendarReadState) -> CalendarReadLedger:
    existing = path.exists()
    ledger = CalendarReadLedger(path)
    if existing:
        if ledger.current_state(state.tenant_id) != state:
            raise WorkspaceRejected("restart calendar state differs from independent current state")
    else:
        ledger.publish_initial_state(state)
    return ledger


def _policy_id(tenant: str, principal: str, channel: str) -> str:
    return (
        "workspace-policy:"
        + hashlib.sha256(json.dumps([tenant, principal, channel]).encode()).hexdigest()
    )


def _policy_bytes(identity: TrustedIngress, channel: str, database_id: str) -> bytes:
    # Broker-authored binding metadata only. This does not reconstruct a policy
    # decision or grant production authority; the independent ingress state must
    # still match at admission and release.
    return json.dumps(
        {
            "tenant": identity.tenant,
            "principal": identity.principal,
            "channel": channel,
            "database": database_id,
            "endpoint": identity.endpoint,
            "heads": identity.heads.model_dump(exclude={"journal"}),
            "sources": [source.model_dump(mode="json") for source in identity.sources],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


async def _install_policy(
    appender: EventAppender,
    storage: SQLiteJournal,
    identity: TrustedIngress,
    channel: str,
    database_id: str,
    fence_frontier: int,
) -> None:
    payload = _policy_bytes(identity, channel, database_id)
    record_id = (
        _policy_id(identity.tenant, identity.principal, channel)
        + ":"
        + hashlib.sha256(payload).hexdigest()
    )
    fingerprint = hashlib.sha256(payload).hexdigest()
    head = storage.snapshot(identity.tenant).head
    result = await appender.submit(
        PhysicalPublicationCommand(
            identity.tenant,
            "workspace.policy",
            record_id,
            fingerprint,
            head,
            identity.heads.deletion,
            fence_frontier,
            fence_frontier,
            (
                PhysicalRecord(
                    record_id,
                    "workspace_policy",
                    "chiplog.workspace.policy.v1",
                    payload,
                    fingerprint,
                ),
            ),
        )
    )
    if result.disposition not in ("COMMITTED", "REPLAY"):
        raise WorkspaceRejected("workspace policy binding publication " + result.disposition)


@asynccontextmanager
async def open_r12_workspace(
    database: Path,
    *,
    screens: Path,
    peers: Mapping[str, TrustedIngress],
    peer: str,
    channel: str,
    database_id: str,
    calendar_batch: CalendarBatch,
    calendar_state: CalendarReadState,
    calendar_ledger_path: Path,
    planning_sources: Mapping[str, tuple[SourceReference, ...]] | None = None,
    fence_frontier: int = 0,
) -> AsyncIterator[R12Workspace]:
    identity = peers.get(peer)
    if identity is None or {p.tenant for p in peers.values()} != {identity.tenant}:
        raise WorkspaceRejected("one authenticated tenant is required")
    if {p.principal for p in peers.values()} != {identity.principal}:
        raise WorkspaceRejected("one authenticated principal is required")
    _validate_paths(database, screens, calendar_ledger_path)
    with SQLiteMaterializer(
        database,
        record_contracts={
            OWNER: SCHEMA,
            CONVERSATION_OWNER: CONVERSATION_SCHEMA,
            "planning": "chiplog.planning.record.v1",
            "workspace_policy": "chiplog.workspace.policy.v1",
        },
        derivative_contracts=(SCREEN_SINK,),
        managed_derivative_sinks=(SCREEN_SINK,),
    ) as materializer:
        async with EventAppender(materializer, capacity=32) as appender:
            await appender.advance_fence(
                FenceAdvanceCommand(
                    identity.tenant,
                    identity.heads.deletion,
                    fence_frontier,
                    allow_exact_replay=True,
                )
            )
            storage = SQLiteJournal(
                database,
                appender,
                fence_generation=identity.heads.deletion,
                fence_frontier=fence_frontier,
            )
            ingress = HermeticIngressRegistry(peers, storage)
            await _install_policy(appender, storage, identity, channel, database_id, fence_frontier)
            ledger = _open_calendar_ledger(calendar_ledger_path, calendar_state)
            runtime = R12Workspace(
                database,
                screens,
                materializer,
                appender,
                ingress,
                peer,
                channel,
                database_id,
                fence_frontier,
                CalendarReadBroker(ledger, HermeticCalendarProvider(calendar_batch)),
                ledger,
                planning_sources or {},
            )
            try:
                yield runtime
            finally:
                runtime._closed = True
                runtime._issued = None


_MODULE_BINDINGS = {
    module: {
        name: value
        for name, value in vars(module).items()
        if not name.startswith("__") and not isinstance(value, ModuleType)
    }
    for module in (
        calendar_reads_module,
        observations_module,
        journal_module,
        conversation_module,
        disclosure_module,
        workspace_module,
    )
}

_GLOBAL_BINDINGS = {
    name: value
    for name, value in globals().copy().items()
    if isinstance(value, (type, FunctionType)) or name == "time_ns"
}

__all__ = ["R12Workspace", "open_r12_workspace"]
