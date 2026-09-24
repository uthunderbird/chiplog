"""Independent H1 workspace issuance retention and exact-byte reopening."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from chiplog.adapters.driven.calendar_reads import decode_h1_original_calendar_read
from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.adapters.driven.workspace_issuance import WorkspaceIssuanceJournal
from chiplog.capabilities.agent_loop.contracts import VisibilityMember
from chiplog.capabilities.evidence_journal.commands import TrustedIngress
from chiplog.capabilities.projections.batch_boundary import ProposalContext
from chiplog.capabilities.projections.r9_boundary import WorkspaceRejected
from chiplog.capabilities.projections.workspace_boundary import SourceReference
from chiplog.platform.authority_gate import AuthorityGate

from .r14_h1_workspace_issuance_contracts import (
    H1OriginalWorkspaceIssuanceV1,
    H1VerifiedWorkspaceClosure,
    H1WorkspaceIssuanceRefV1,
)


class H1WorkspaceIssuanceJournal:
    @classmethod
    def open(
        cls, path: Path, authority_gate: AuthorityGate, tenant: str
    ) -> H1WorkspaceIssuanceJournal:
        return cls(
            IndependentTenantDecisionJournal.for_authority_bundle(
                path, authority_gate=authority_gate
            ),
            tenant,
        )

    def __init__(self, journal: IndependentTenantDecisionJournal, tenant: str) -> None:
        if not tenant or journal.authority_gate is None:
            raise ValueError("H1 workspace issuance requires tenant-bound authority")
        self._journal, self._tenant, self._gate = journal, tenant, journal.authority_gate
        self._scan()

    def _scan(self) -> dict[str, H1OriginalWorkspaceIssuanceV1]:
        result: dict[str, H1OriginalWorkspaceIssuanceV1] = {}
        for entry_id, _, payload in self._journal.entries():
            issued = H1OriginalWorkspaceIssuanceV1.model_validate_json(payload)
            if issued.canonical_bytes() != payload or issued.tenant != self._tenant:
                raise WorkspaceRejected("invalid original workspace issuance")
            if entry_id in result:
                raise WorkspaceRejected("duplicate original workspace issuance")
            result[entry_id] = issued
        return result

    def append(self, issuance: H1OriginalWorkspaceIssuanceV1) -> H1WorkspaceIssuanceRefV1:
        if issuance.tenant != self._tenant:
            raise WorkspaceRejected("foreign original workspace issuance")
        payload = issuance.canonical_bytes()
        with self._gate.hold():
            entries = self._scan()
            for entry_id, prior in entries.items():
                if (
                    prior.tenant == issuance.tenant
                    and prior.proposal_context_json == issuance.proposal_context_json
                ):
                    if prior.canonical_bytes() != payload:
                        raise WorkspaceRejected(
                            "original workspace batch already issued different bytes"
                        )
                    return H1WorkspaceIssuanceRefV1(
                        tenant=self._tenant,
                        batch_id=_batch_id(prior),
                        entry_id=entry_id,
                        payload_digest=_digest(payload),
                    )
            predecessor = self._journal.entries()[-1][0] if entries else None
            entry_id = self._journal.append(payload, predecessor)
            return H1WorkspaceIssuanceRefV1(
                tenant=self._tenant,
                batch_id=_batch_id(issuance),
                entry_id=entry_id,
                payload_digest=_digest(payload),
            )

    def load(self, ref: H1WorkspaceIssuanceRefV1) -> H1OriginalWorkspaceIssuanceV1:
        if ref.tenant != self._tenant:
            raise WorkspaceRejected("foreign original workspace issuance reference")
        with self._gate.hold():
            try:
                issued = self._scan()[ref.entry_id]
            except KeyError as error:
                raise WorkspaceRejected("missing original workspace issuance") from error
            if (
                _batch_id(issued) != ref.batch_id
                or _digest(issued.canonical_bytes()) != ref.payload_digest
            ):
                raise WorkspaceRejected("original workspace issuance reference differs")
            return issued

    def find_exact(
        self, workspace_member_bytes: bytes, proposal_context_bytes: bytes
    ) -> tuple[H1WorkspaceIssuanceRefV1, H1OriginalWorkspaceIssuanceV1]:
        with self._gate.hold():
            matches = [
                (entry_id, value)
                for entry_id, value in self._scan().items()
                if value.workspace_member_json.encode() == workspace_member_bytes
                and value.proposal_context_json.encode() == proposal_context_bytes
            ]
        if len(matches) != 1:
            raise WorkspaceRejected("original workspace issuance is absent or ambiguous")
        entry_id, issued = matches[0]
        return H1WorkspaceIssuanceRefV1(
            tenant=self._tenant,
            batch_id=_batch_id(issued),
            entry_id=entry_id,
            payload_digest=_digest(issued.canonical_bytes()),
        ), issued


def verify_h1_original_workspace(
    ref: H1WorkspaceIssuanceRefV1,
    workspace_member_bytes: bytes,
    proposal_context_bytes: bytes,
    journal: H1WorkspaceIssuanceJournal,
    dashboard_issuance: WorkspaceIssuanceJournal,
) -> H1VerifiedWorkspaceClosure:
    issued = journal.load(ref)
    if (
        issued.workspace_member_json.encode() != workspace_member_bytes
        or issued.proposal_context_json.encode() != proposal_context_bytes
    ):
        raise WorkspaceRejected("selected workspace bytes differ from original issuance")
    _validate_retained_original(issued)
    _validate_dashboard(issued, dashboard_issuance)
    decode_h1_original_calendar_read(issued.calendar)
    return H1VerifiedWorkspaceClosure(
        issuance=ref,
        workspace_member_bytes=workspace_member_bytes,
        proposal_context_bytes=proposal_context_bytes,
        snapshot=issued.snapshot,
        queries=issued.queries,
        calendar=issued.calendar,
        sources=issued.sources,
        dashboard=issued.dashboard,
    )


def _validate_dashboard(
    issued: H1OriginalWorkspaceIssuanceV1, dashboard_issuance: WorkspaceIssuanceJournal
) -> None:
    dashboard = issued.dashboard
    entry_id, payload_digest = dashboard_issuance.raw_entry(
        dashboard.channel_id, dashboard.sequence
    )
    state = dashboard_issuance.load(dashboard.channel_id, dashboard.sequence)
    batch = ProposalContext.model_validate_json(issued.proposal_context_json).batch
    if (
        entry_id != dashboard.entry_id
        or payload_digest != dashboard.payload_digest
        or state.model_dump_json() != batch.dashboard.model_dump_json()
    ):
        raise WorkspaceRejected("original workspace dashboard issuance differs")


def _validate_retained_original(issued: H1OriginalWorkspaceIssuanceV1) -> None:
    """Validate every relationship recoverable from immutable retained bytes."""
    member = VisibilityMember.model_validate_json(issued.workspace_member_json)
    context = ProposalContext.model_validate_json(issued.proposal_context_json)
    batch = context.batch
    if (
        member.record_id != "workspace/" + batch.batch_id
        or member.revision_head != batch.batch_id
        or member.content != issued.proposal_context_json
        or member.provenance_head != batch.policy_binding_digest
        or member.label.model_dump_json() != context.label.model_dump_json()
        or member.label_head != batch.context.principal_contour_head
        or member.producer != "projections"
        or member.surface != "workspace"
        or issued.tenant != batch.context.tenant_id
    ):
        raise WorkspaceRejected("original workspace member/context join differs")
    expected_results = (batch.history, batch.planning, batch.journal, batch.calendar)
    if tuple(proof.result_json for proof in issued.queries) != tuple(
        result.model_dump_json() for result in expected_results
    ):
        raise WorkspaceRejected("original workspace query result differs")
    if context.history_record_ids != tuple(row.row_id for row in batch.history.rows):
        raise WorkspaceRejected("original workspace history join differs")
    if not batch.history_complete or batch.history.next_cursor is not None:
        raise WorkspaceRejected("original workspace history is incomplete")
    _validate_snapshot(issued)
    ingress = TrustedIngress.model_validate_json(issued.sources.trusted_ingress_json)
    if (
        ingress.tenant != issued.tenant
        or ingress.endpoint != issued.sources.endpoint
        or batch.context.principal_id != ingress.principal
        or batch.context.channel_id != ingress.endpoint
        or issued.sources.policy_payload_base64 == ""
    ):
        raise WorkspaceRejected("original workspace source identity differs")
    if issued.sources.conversation_bindings_json and not issued.sources.selected_loop_decisions:
        raise WorkspaceRejected("original conversation decision proof is missing")
    records = {row.record_id: row for row in issued.snapshot.records}
    for decision in issued.sources.selected_loop_decisions:
        try:
            decoded = base64.b64decode(decision.payload_base64.encode("ascii"), validate=True)
            entry = json.loads(decoded)
            if entry.get("kind") != "DECIDED" or not isinstance(entry.get("records"), list):
                raise ValueError("not selected")
            for row in entry["records"]:
                record = records.get(row["record_id"])
                payload = base64.b64decode(row["payload"], validate=True)
                if (
                    record is None
                    or record.owner != row["owner"]
                    or record.schema_id != row["schema"]
                    or record.canonical_record_bytes() != payload
                    or hashlib.sha256(payload).hexdigest() != row["digest"]
                ):
                    raise ValueError("selected decision physical record differs")
        except (KeyError, TypeError, ValueError) as error:
            raise WorkspaceRejected("invalid original conversation decision") from error
    policy = records.get(issued.sources.policy_record_id)
    if policy is None or policy.canonical_record_bytes() != base64.b64decode(
        issued.sources.policy_payload_base64.encode("ascii"), validate=True
    ):
        raise WorkspaceRejected("original workspace policy record differs")
    for planning in issued.sources.planning_sources:
        for raw in planning.source_reference_json:
            source = SourceReference.model_validate_json(raw)
            record = records.get(source.record_id)
            if (
                record is None
                or record.owner != source.owner
                or record.tenant_id != source.tenant_id
                or hashlib.sha256(record.canonical_record_bytes()).hexdigest()
                != source.content_digest
            ):
                raise WorkspaceRejected("original workspace planning source differs")


def _validate_snapshot(issued: H1OriginalWorkspaceIssuanceV1) -> None:
    snapshot = issued.snapshot
    if tuple(row.commit_sequence for row in snapshot.publications) != tuple(
        range(1, snapshot.tenant_head + 1)
    ):
        raise WorkspaceRejected("original workspace publication frontier differs")
    records = {row.record_id: row for row in snapshot.records}
    if len(records) != len(snapshot.records) or any(
        row.tenant_id != issued.tenant for row in records.values()
    ):
        raise WorkspaceRejected("original workspace record inventory differs")
    seen: set[str] = set()
    for publication in snapshot.publications:
        if publication.tenant_id != issued.tenant:
            raise WorkspaceRejected("foreign original workspace publication")
        # `_sqlite.py` stores a newline-delimited physical list.  Retain its
        # exact text and only decode that closed representation here.
        record_ids = publication.record_ids_json.splitlines()
        if not record_ids or any(not item for item in record_ids):
            raise WorkspaceRejected("invalid original workspace record membership")
        for record_id in record_ids:
            record = records.get(record_id)
            if (
                record is None
                or record.commit_sequence != publication.commit_sequence
                or record_id in seen
            ):
                raise WorkspaceRejected("original workspace publication membership differs")
            seen.add(record_id)
    if seen != set(records):
        raise WorkspaceRejected("unpublished original workspace record")


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _batch_id(issued: H1OriginalWorkspaceIssuanceV1) -> str:

    return str(json.loads(issued.proposal_context_json)["batch"]["batch_id"])
