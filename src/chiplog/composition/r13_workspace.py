"""R12 workspace on the R13 broker's existing writer and authenticated source cut."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from typing import TYPE_CHECKING

from chiplog.adapters.driven.calendar_hermetic import HermeticCalendarProvider
from chiplog.adapters.driven.calendar_reads import CalendarReadBroker
from chiplog.adapters.driven.journal_sqlite import SQLiteJournal
from chiplog.adapters.driven.planning_sqlite import SQLitePlanningRepository
from chiplog.adapters.driven.r9_fence import CONVERSATION_OWNER, CONVERSATION_SCHEMA
from chiplog.capabilities.agent_loop.contracts import (
    DisclosureLabel as LoopLabel,
)
from chiplog.capabilities.agent_loop.contracts import (
    DurableCompanion,
    LoopRejected,
    RunRecord,
    VisibilityMember,
)
from chiplog.capabilities.agent_loop.domain import join_labels
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.calendar_observations.contracts import CalendarBatch
from chiplog.capabilities.calendar_observations.observations import source_heads_digest
from chiplog.capabilities.evidence_journal.boundary import SourceReference as JournalSource
from chiplog.capabilities.evidence_journal.commands import Heads, TrustedIngress
from chiplog.capabilities.projections.r9_boundary import ConversationEntry, WorkspaceIntegrityError
from chiplog.capabilities.projections.workspace_boundary import (
    DisclosureEnvelope,
    DisclosureLabel,
    SourceReference,
)
from chiplog.composition.r10 import HermeticIngressRegistry
from chiplog.composition.r12 import R12Workspace, _install_policy, _open_calendar_ledger
from chiplog.domain_primitives import TenantId
from chiplog.platform.calendar_read_ledger import CalendarReadState

if TYPE_CHECKING:
    from chiplog.composition.r13_runtime import R13Runtime

TENANT = "hermetic-tenant"
PRINCIPAL = "hermetic-principal"
CHANNEL = "hermetic-local"
PEER = "hermetic-ingress"
POLICY = "hermetic-policy-v1"
CONTOUR = "hermetic-contour-v1"


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _history(runtime: R13Runtime) -> tuple[ConversationEntry, ...]:
    rows = runtime._appender._materializer.guarded_records(TENANT, "r6", 0)
    entries = []
    for row in rows:
        if row[2] != CONVERSATION_OWNER:
            continue  # This read is the closed conversation owner slice.
        try:
            if not isinstance(row[4], bytes):
                raise ValueError("conversation payload must be bytes")
            value = json.loads(row[4])
            entry = ConversationEntry.model_validate_json(value["entry_json"])
            if (
                row[3] != CONVERSATION_SCHEMA
                or value["fingerprint"] != _digest(value["entry_json"].encode())
                or entry.entry_id != row[1]
                or entry.tenant_id != TENANT
            ):
                raise ValueError("conversation physical identity mismatch")
            entries.append(entry)
        except (ValueError, TypeError, KeyError) as error:
            raise WorkspaceIntegrityError("loop.history", TENANT, str(row[1])) from error
    entries.sort(key=lambda entry: entry.sequence)
    if [entry.sequence for entry in entries] != list(range(1, len(entries) + 1)):
        raise LoopRejected("conversation sequence gap or duplicate")
    return tuple(entries)


def _entry(
    runtime: R13Runtime,
    identity: str,
    content: bytes,
    role: str,
    sources: tuple[SourceReference, ...],
    label: DisclosureLabel,
) -> ConversationEntry:
    history = _history(runtime)
    existing = [entry for entry in history if entry.entry_id == identity]
    return ConversationEntry.model_validate(
        {
            "tenant_id": TENANT,
            "conversation_id": "conversation:" + TENANT,
            "entry_id": identity,
            "sequence": existing[0].sequence if existing else len(history) + 1,
            "origin_channel_id": CHANNEL,
            "visible_channels": (CHANNEL,),
            "role": role,
            "accepted_bytes": content,
            "envelope": DisclosureEnvelope(
                tenant_id=TENANT,
                content_digest=_digest(content),
                sources=sources,
                label=label,
                policy_head=POLICY,
                contour_head=CONTOUR,
                deletion_fence_head="r6",
            ),
        }
    )


def acceptance_companions(runtime: R13Runtime, run: RunRecord) -> tuple[DurableCompanion, ...]:
    if run.event != "CompleteAcceptance":
        return ()
    members = tuple(
        member
        for turn in run.turns
        for attempt in turn.attempts
        for member in attempt.manifest.members
    )
    sources = tuple(
        SourceReference(
            tenant_id=run.tenant,
            owner="agent_loop",
            record_id=member.record_id,
            record_version=member.revision_head + ":" + _digest(member.content.encode()),
            content_digest=_digest(member.content.encode()),
            label_head=member.label_head,
            label=DisclosureLabel.model_validate_json(member.label.model_dump_json()),
        )
        for member in {
            (item.record_id, item.revision_head, item.content): item for item in members
        }.values()
    )
    joined = join_labels(tuple(member.label for member in members))
    entry = _entry(
        runtime,
        run.run_id + "/accepted",
        "\n".join(run.accepted_text).encode(),
        "assistant",
        sources,
        DisclosureLabel.model_validate_json(joined.model_dump_json()),
    )
    raw = entry.model_dump_json().encode()
    encoded = json.dumps(
        {"entry_json": raw.decode(), "fingerprint": _digest(raw)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return (
        DurableCompanion(
            record_id=entry.entry_id,
            owner=CONVERSATION_OWNER,
            schema_id=CONVERSATION_SCHEMA,
            payload_base64=base64.b64encode(encoded).decode(),
        ),
    )


class R13Workspace:
    def __init__(self, runtime: R13Runtime) -> None:
        self.runtime = runtime
        self.storage = SQLiteJournal(runtime._database, runtime._appender, fence_generation="r6")
        self.ingress = HermeticIngressRegistry({}, self.storage)
        self._issued: R12Workspace | None = None

    async def _workspace(self, extra: tuple[SourceReference, ...] = ()) -> R12Workspace:
        runtime = self.runtime
        # Verify the broker commitment before source enumeration; registry is not
        # reconstructed from model-supplied provenance metadata.
        runtime.refresh_derivative_observation()
        repository = SQLitePlanningRepository(
            runtime._database, runtime._appender, asyncio.get_running_loop()
        )
        planning = {}
        for publication in repository.committed_publications(TenantId(TENANT)):
            planning[publication.result.intention_line_id.value] = tuple(
                sorted(
                    (
                        SourceReference(
                            tenant_id=TENANT,
                            owner="planning",
                            record_id=record.record_id.value,
                            record_version=str(publication.commit_sequence),
                            content_digest=_digest(record.canonical_bytes),
                            label_head=CONTOUR,
                            label=DisclosureLabel(
                                lattice_version="chiplog.disclosure.v1",
                                value="ENDPOINT_RESTRICTED",
                                allowed_endpoints=(CHANNEL,),
                            ),
                        )
                        for record in publication.records
                    ),
                    key=lambda source: source.record_id,
                )
            )
        all_sources = (
            *extra,
            *(source for entry in _history(runtime) for source in entry.envelope.sources),
            *(source for sources in planning.values() for source in sources),
        )
        source_map: dict[tuple[str, str, str], SourceReference] = {}
        for source in all_sources:
            key = (source.owner, source.record_id, source.record_version)
            if key in source_map and source_map[key] != source:
                raise LoopRejected("conflicting authenticated source identity")
            source_map[key] = source
        identity = TrustedIngress(
            tenant=TENANT,
            principal=PRINCIPAL,
            heads=Heads(
                journal=self.storage.snapshot(TENANT).head,
                policy=POLICY,
                credential="hermetic-credential",
                session="hermetic-session",
                contour=CONTOUR,
                deletion="r6",
            ),
            items=(),
            sources=tuple(
                JournalSource.model_validate_json(source.model_dump_json())
                for source in source_map.values()
            ),
            endpoint=CHANNEL,
        )
        # One current transport registry is shared by all issued batches. Old
        # R12 instances observe this replacement and reject stale source cuts.
        self.ingress._peers[PEER] = identity
        if self._issued is not None:
            self._issued._closed = True
            self._issued._issued = None
        await _install_policy(
            runtime._appender, self.storage, identity, CHANNEL, "hermetic-database", 0
        )
        batch = CalendarBatch(revision="r13-empty-calendar-v1", observations=())
        state = CalendarReadState(
            tenant_id=TENANT,
            broker_epoch=1,
            credential_session_head="hermetic-session",
            endpoint_channel_head=CHANNEL,
            file_wal_observation="hermetic-empty",
            journal_head="hermetic-empty",
            materialization_commitment="hermetic-empty",
            owner_generation="hermetic-calendar-v1",
            owner_draining=False,
            principal_contour_head=CONTOUR,
            storage_mutation_generation=1,
            trust_transition_head="hermetic-only",
            authority_surface_digest="r13-empty-calendar-v1",
            amr_fingerprint="evidence-only",
            database_instance_id="hermetic-database",
            principal_id=PRINCIPAL,
            channel_id=CHANNEL,
            policy_head=POLICY,
            deletion_fence_head="r6",
            provider_revision=batch.revision,
            source_heads_digest=source_heads_digest(batch),
        )
        ledger = _open_calendar_ledger(runtime._database.with_suffix(".calendar.sqlite"), state)
        self._issued = R12Workspace(
            runtime._database,
            runtime._database.with_suffix(".screens.sqlite"),
            runtime._appender._materializer,
            runtime._appender,
            self.ingress,
            PEER,
            CHANNEL,
            "hermetic-database",
            0,
            CalendarReadBroker(ledger, HermeticCalendarProvider(batch)),
            ledger,
            planning,
        )
        return self._issued

    async def ingest(self, run_id: str, prompt: str) -> None:
        content = prompt.encode()
        label = DisclosureLabel(
            lattice_version="chiplog.disclosure.v1",
            value="ENDPOINT_RESTRICTED",
            allowed_endpoints=(CHANNEL,),
        )
        source = SourceReference(
            tenant_id=TENANT,
            owner="principal_ingress",
            record_id=run_id + "/ingress",
            record_version="1",
            content_digest=_digest(content),
            label_head=CONTOUR,
            label=label,
        )
        workspace = await self._workspace((source,))
        result = await workspace.accept(
            _entry(self.runtime, source.record_id, content, "principal", (source,), label)
        )
        if result not in ("COMMITTED", "REPLAY"):
            raise LoopRejected("conversation ingress " + result)

    async def context(self, run: RunRecord | ExecutionRunRecord) -> VisibilityMember:
        workspace = await self._workspace()
        batch = await workspace.read_batch()
        context = workspace.proposal_context(batch)
        self.runtime.refresh_derivative_observation()
        return VisibilityMember(
            record_id="workspace/" + batch.batch_id,
            revision_head=batch.batch_id,
            content=context.model_dump_json(),
            provenance_head=batch.policy_binding_digest,
            label_head=batch.context.principal_contour_head,
            label=LoopLabel.model_validate_json(context.label.model_dump_json()),
            producer="projections",
            surface="workspace",
        )
