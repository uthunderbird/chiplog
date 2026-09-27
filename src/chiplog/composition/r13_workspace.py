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
from chiplog.adapters.driven.workspace_issuance import WorkspaceIssuanceJournal
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
from chiplog.composition.h1_preissuance_registration import H1PreissuanceRegistrationSource
from chiplog.composition.h1_workspace_policy_v2 import (
    H1OriginalWorkspaceIssuanceV2,
    H1WorkspacePolicyV2,
)
from chiplog.composition.r10 import HermeticIngressRegistry
from chiplog.composition.r12 import (
    R12Workspace,
    _install_policy,
    _open_calendar_ledger,
    install_h1_workspace_policy_v2,
)
from chiplog.composition.r13_workspace_provenance import accepted_sources, conversation_bindings
from chiplog.composition.r14_h1_workspace_issuance import H1WorkspaceIssuanceJournal
from chiplog.composition.r14_h1_workspace_issuance_contracts import (
    H1RetainedDecisionV1,
    H1WorkspaceIssuanceRefV1,
)
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


def _history(runtime: R13Runtime, tenant: str = TENANT) -> tuple[ConversationEntry, ...]:
    rows = runtime._appender._materializer.guarded_records(tenant, "r6", 0)
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
                or entry.tenant_id != tenant
            ):
                raise ValueError("conversation physical identity mismatch")
            entries.append(entry)
        except (ValueError, TypeError, KeyError) as error:
            raise WorkspaceIntegrityError("loop.history", tenant, str(row[1])) from error
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
    *,
    tenant: str = TENANT,
    channel: str = CHANNEL,
    contour: str = CONTOUR,
) -> ConversationEntry:
    history = _history(runtime, tenant)
    existing = [entry for entry in history if entry.entry_id == identity]
    return ConversationEntry.model_validate(
        {
            "tenant_id": tenant,
            "conversation_id": "conversation:" + tenant,
            "entry_id": identity,
            "sequence": existing[0].sequence if existing else len(history) + 1,
            "origin_channel_id": channel,
            "visible_channels": (channel,),
            "role": role,
            "accepted_bytes": content,
            "envelope": DisclosureEnvelope(
                tenant_id=tenant,
                content_digest=_digest(content),
                sources=sources,
                label=label,
                policy_head=POLICY,
                contour_head=contour,
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
    sources = accepted_sources(run)
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
        self._h1_workspace_issuance: H1WorkspaceIssuanceJournal | None = None
        self._last_h1_workspace_issuance: H1WorkspaceIssuanceRefV1 | None = None

    async def _workspace(
        self,
        extra: tuple[SourceReference, ...] = (),
        *,
        h1_workspace_policy: H1WorkspacePolicyV2 | None = None,
        h1_preissuance_selection: object | None = None,
        h1_source: H1PreissuanceRegistrationSource | None = None,
    ) -> R12Workspace:
        runtime = self.runtime
        h1_source_provider = h1_source
        h1_port: object | None = None
        tenant, principal, channel, database_id, contour = (
            TENANT,
            PRINCIPAL,
            CHANNEL,
            "hermetic-database",
            CONTOUR,
        )
        credential_head = "hermetic-credential"
        session_head = "hermetic-session"
        if h1_preissuance_selection is not None:
            h1_source_provider = h1_source_provider or H1PreissuanceRegistrationSource(runtime)
            h1_port = getattr(runtime, "_h1_preissuance_registration_source_port", None)
            selected_workspace_identity = getattr(h1_port, "selected_workspace_identity", None)
            if not callable(selected_workspace_identity):
                raise LoopRejected("H1 V2 workspace binding mount is unavailable")
            with runtime._authority_gate().hold():
                selected = selected_workspace_identity(h1_preissuance_selection)
            if any(
                type(value) is not str or not value
                for value in (
                    selected.tenant,
                    selected.principal,
                    selected.channel,
                    selected.database_id,
                    selected.credential_head,
                    selected.session_head,
                    selected.contour_head,
                )
            ):
                raise LoopRejected("H1 V2 workspace identity is unavailable")
            tenant, principal, channel, database_id, contour = (
                selected.tenant,
                selected.principal,
                selected.channel,
                selected.database_id,
                selected.contour_head,
            )
            credential_head, session_head = selected.credential_head, selected.session_head
        # Verify the broker commitment before source enumeration; registry is not
        # reconstructed from model-supplied provenance metadata.
        runtime.refresh_derivative_observation()
        repository = SQLitePlanningRepository(
            runtime._database, runtime._appender, asyncio.get_running_loop()
        )
        planning = {}
        for publication in repository.committed_publications(TenantId(tenant)):
            planning[publication.result.intention_line_id.value] = tuple(
                sorted(
                    (
                        SourceReference(
                            tenant_id=tenant,
                            owner="planning",
                            record_id=record.record_id.value,
                            record_version=str(publication.commit_sequence),
                            content_digest=_digest(record.canonical_bytes),
                            label_head=contour,
                            label=DisclosureLabel(
                                lattice_version="chiplog.disclosure.v1",
                                value="ENDPOINT_RESTRICTED",
                                allowed_endpoints=(channel,),
                            ),
                        )
                        for record in publication.records
                    ),
                    key=lambda source: source.record_id,
                )
            )
        bindings = conversation_bindings(
            runtime, _history(runtime, tenant), extra, channel=channel, contour=contour
        )
        selected_decisions = tuple(
            H1RetainedDecisionV1(
                entry_id=entry_id,
                predecessor=predecessor,
                payload_base64=base64.b64encode(payload).decode("ascii"),
            )
            for entry_id, predecessor, payload in runtime._loop_decisions().entries()
            if json.loads(payload).get("kind") == "DECIDED"
        )
        all_sources = (
            *(source for binding in bindings for source in binding.sources),
            *(source for sources in planning.values() for source in sources),
        )
        source_map: dict[tuple[str, str, str], SourceReference] = {}
        for source in all_sources:
            key = (source.owner, source.record_id, source.record_version)
            if key in source_map and source_map[key] != source:
                raise LoopRejected("conflicting authenticated source identity")
            source_map[key] = source
        identity = TrustedIngress(
            tenant=tenant,
            principal=principal,
            heads=Heads(
                journal=self.storage.snapshot(tenant).head,
                policy=POLICY,
                credential=credential_head,
                session=session_head,
                contour=contour,
                deletion="r6",
            ),
            items=(),
            sources=tuple(
                JournalSource.model_validate_json(source.model_dump_json())
                for source in source_map.values()
            ),
            endpoint=channel,
        )
        policy = h1_workspace_policy
        if h1_preissuance_selection is not None:
            assert h1_source_provider is not None
            bind_workspace_cut = getattr(h1_port, "bind_workspace_cut", None)
            if not callable(bind_workspace_cut):
                raise LoopRejected("H1 V2 workspace binding mount is unavailable")
            # The opaque selection is only bound to the final authenticated R13
            # inventory.  In particular, it is never bound to a caller DTO.
            with runtime._authority_gate().hold():
                bind_workspace_cut(
                    h1_preissuance_selection,
                    identity=identity,
                    channel=channel,
                    database_id=database_id,
                )
            resolved = h1_source_provider.resolved_workspace_policy(h1_preissuance_selection)
            if type(resolved) is not H1WorkspacePolicyV2:
                raise LoopRejected("H1 V2 workspace binding returned an unsupported policy")
            policy = resolved
        elif policy is not None:
            raise LoopRejected("H1 V2 workspace policy requires a selected source")

        # One current transport registry is shared by all issued batches. Old
        # R12 instances observe this replacement and reject stale source cuts.
        # A selected H1 identity reaches this registry only after its issuer
        # has bound the same authenticated workspace cut.
        self.ingress._peers[PEER] = identity
        if self._issued is not None:
            self._issued._closed = True
            self._issued._issued = None

        if policy is None:
            await _install_policy(
                runtime._appender, self.storage, identity, channel, database_id, 0
            )
        else:
            await install_h1_workspace_policy_v2(
                runtime._appender,
                self.storage,
                identity,
                channel,
                database_id,
                0,
                policy,
            )
        if h1_preissuance_selection is not None:
            assert h1_source_provider is not None
            assert h1_port is not None
            check_current = getattr(h1_port, "check_current", None)
            with runtime._authority_gate().hold():
                if (
                    not callable(check_current)
                    or h1_source_provider.check_current(h1_preissuance_selection) is not True
                    or check_current(h1_preissuance_selection) is not True
                    or h1_source_provider.resolved_workspace_policy(h1_preissuance_selection)
                    is not policy
                ):
                    raise LoopRejected("H1 V2 workspace source changed during policy publication")
        batch = CalendarBatch(revision="r13-empty-calendar-v1", observations=())
        state = CalendarReadState(
            tenant_id=tenant,
            broker_epoch=1,
            credential_session_head=identity.heads.session,
            endpoint_channel_head=channel,
            file_wal_observation="hermetic-empty",
            journal_head="hermetic-empty",
            materialization_commitment="hermetic-empty",
            owner_generation="hermetic-calendar-v1",
            owner_draining=False,
            principal_contour_head=contour,
            storage_mutation_generation=1,
            trust_transition_head="hermetic-only",
            authority_surface_digest="r13-empty-calendar-v1",
            amr_fingerprint="evidence-only",
            database_instance_id=database_id,
            principal_id=principal,
            channel_id=channel,
            policy_head=POLICY,
            deletion_fence_head="r6",
            provider_revision=batch.revision,
            source_heads_digest=source_heads_digest(batch),
        )
        ledger = _open_calendar_ledger(runtime._database.with_suffix(".calendar.sqlite"), state)
        self._issued = R12Workspace(
            runtime._database,
            runtime._database.with_suffix(".screens.v2.sqlite"),
            runtime._appender._materializer,
            runtime._appender,
            self.ingress,
            PEER,
            channel,
            database_id,
            0,
            CalendarReadBroker(ledger, HermeticCalendarProvider(batch)),
            ledger,
            planning,
            conversation_provenance=bindings,
            selected_loop_decisions=selected_decisions,
            issuance=WorkspaceIssuanceJournal.open(
                runtime._database.with_suffix(".workspace-issuance"),
                runtime._authority_gate(),
                tenant,
            ),
            h1_workspace_policy=policy,
        )
        self._h1_workspace_issuance = H1WorkspaceIssuanceJournal.open(
            runtime._database.with_suffix(".h1-workspace-issuance"),
            runtime._authority_gate(),
            tenant,
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

    async def context(
        self,
        run: RunRecord | ExecutionRunRecord,
        *,
        h1_preissuance_selection: object | None = None,
    ) -> VisibilityMember:
        source = (
            H1PreissuanceRegistrationSource(self.runtime)
            if h1_preissuance_selection is not None
            else None
        )
        workspace = await self._workspace(
            h1_preissuance_selection=h1_preissuance_selection,
            h1_source=source,
        )
        batch = await workspace.read_batch()
        context = workspace.proposal_context(batch)
        self.runtime.refresh_derivative_observation()
        member = VisibilityMember(
            record_id="workspace/" + batch.batch_id,
            revision_head=batch.batch_id,
            content=context.model_dump_json(),
            provenance_head=batch.policy_binding_digest,
            label_head=batch.context.principal_contour_head,
            label=LoopLabel.model_validate_json(context.label.model_dump_json()),
            producer="projections",
            surface="workspace",
        )
        # Legacy R13 contexts remain structurally unchanged.  Only the native
        # executable first-turn path may create H1 evidence, and it does so
        # before this member can reach Accumulate/model selection.
        if isinstance(run, ExecutionRunRecord):
            self.issue_h1_workspace_issuance(
                run,
                member,
                h1_preissuance_selection=h1_preissuance_selection,
                source=source,
            )
        return member

    def issue_h1_workspace_issuance(
        self,
        run: RunRecord | ExecutionRunRecord,
        member: VisibilityMember,
        *,
        h1_preissuance_selection: object | None = None,
        source: H1PreissuanceRegistrationSource | None = None,
    ) -> H1WorkspaceIssuanceRefV1:
        """Append an original cut under the runtime authority gate before selection.

        V3 must retain the returned typed ref in its selected transition; this
        method deliberately does not infer a ref during reopening.
        """
        if (
            self._issued is None
            or self._issued._issued is None
            or self._h1_workspace_issuance is None
        ):
            raise LoopRejected("H1 original workspace issuance lacks an issued cut")
        batch = self._issued._issued
        context = self._issued.proposal_context(batch)
        turn_id = run.turns[-1].turn_id if run.turns else run.run_id + "/initial"
        issuance_args = {
            "run_id": run.run_id,
            "started_run_head": run.head,
            "turn_id": turn_id,
            "worker_session": getattr(run, "worker_session", "legacy-worker"),
            "workspace_member_json": member.model_dump_json(),
            "proposal_context_json": context.model_dump_json(),
        }
        if self._issued._h1_workspace_policy is None:
            issuance = self._issued.h1_original_issuance(**issuance_args)
            with self.runtime._authority_gate().hold():
                ref = self._h1_workspace_issuance.append(issuance)
            self._last_h1_workspace_issuance = ref
            return ref

        with self.runtime._authority_gate().hold():
            if (
                source is None
                or h1_preissuance_selection is None
                or source.check_current(h1_preissuance_selection) is not True
                or source.resolved_workspace_policy(h1_preissuance_selection)
                is not self._issued._h1_workspace_policy
                or member.content != context.model_dump_json()
            ):
                raise LoopRejected("H1 V2 original issuance selection is unavailable or stale")
            issuance = self._issued.h1_original_issuance(**issuance_args)
            if (
                type(issuance) is not H1OriginalWorkspaceIssuanceV2
                or issuance.workspace_member_json != member.model_dump_json()
                or issuance.proposal_context_json != context.model_dump_json()
            ):
                raise LoopRejected("H1 V2 original issuance differs from selected workspace")
            ref = self._h1_workspace_issuance.append(issuance)
            reloaded = self._h1_workspace_issuance.load(ref)
            if type(reloaded) is not H1OriginalWorkspaceIssuanceV2 or reloaded != issuance:
                raise LoopRejected("H1 V2 original issuance reload differs")
        self._last_h1_workspace_issuance = ref
        return ref

    def open_h1_workspace_issuance(self) -> H1WorkspaceIssuanceJournal:
        """Read-only verifier seam; callers still need a retained typed ref."""
        if self._h1_workspace_issuance is None:
            tenant = self._issued._identity.tenant if self._issued is not None else TENANT
            self._h1_workspace_issuance = H1WorkspaceIssuanceJournal.open(
                self.runtime._database.with_suffix(".h1-workspace-issuance"),
                self.runtime._authority_gate(),
                tenant,
            )
        return self._h1_workspace_issuance

    def open_dashboard_issuance(self) -> WorkspaceIssuanceJournal:
        """Open the runtime-configured dashboard authority, never a caller path."""
        tenant = self._issued._identity.tenant if self._issued is not None else TENANT
        return WorkspaceIssuanceJournal.open(
            self.runtime._database.with_suffix(".workspace-issuance"),
            self.runtime._authority_gate(),
            tenant,
        )
