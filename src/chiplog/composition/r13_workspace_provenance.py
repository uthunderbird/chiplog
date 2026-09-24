"""Complete conversation closures from original independently selected publications."""

import base64
import hashlib
import json
import sqlite3
from typing import TYPE_CHECKING

from chiplog.adapters.driven.loop_sqlite import OWNER, SCHEMA
from chiplog.adapters.driven.r9_fence import CONVERSATION_OWNER, CONVERSATION_SCHEMA
from chiplog.capabilities.agent_loop.contracts import RunRecord
from chiplog.capabilities.projections.provenance import ProvenanceBinding, ProvenanceSubject
from chiplog.capabilities.projections.r9_boundary import ConversationEntry, WorkspaceIntegrityError
from chiplog.capabilities.projections.workspace_boundary import DisclosureLabel, SourceReference
from chiplog.composition.r14_fanout_contracts import FANOUT_OPERATION, RetainedFanOutPreparation
from chiplog.composition.r14_fanout_verification import build_envelope
from chiplog.platform.publication_inspection import (
    ExpectedPublicationRecord,
    PublicationExpectation,
    inspect_publication,
)
from chiplog.platform.workspace_snapshot import read_connection

if TYPE_CHECKING:
    from chiplog.composition.r13_runtime import R13Runtime


def accepted_sources(run: RunRecord) -> tuple[SourceReference, ...]:
    members = tuple(
        member
        for turn in run.turns
        for attempt in turn.attempts
        for member in attempt.manifest.members
    )
    return tuple(
        SourceReference(
            tenant_id=run.tenant,
            owner="agent_loop",
            record_id=member.record_id,
            record_version=member.revision_head
            + ":"
            + hashlib.sha256(member.content.encode()).hexdigest(),
            content_digest=hashlib.sha256(member.content.encode()).hexdigest(),
            label_head=member.label_head,
            label=DisclosureLabel.model_validate_json(member.label.model_dump_json()),
        )
        for member in {
            (item.record_id, item.revision_head, item.content): item for item in members
        }.values()
    )


def conversation_bindings(
    runtime: R13Runtime,
    history: tuple[ConversationEntry, ...],
    extra: tuple[SourceReference, ...],
    *,
    channel: str,
    contour: str,
) -> tuple[ProvenanceBinding, ...]:
    identity = "<conversation>"
    try:
        with runtime._authority_gate().hold(), read_connection(runtime._database) as connection:
            runtime._pending()
            by_id = {entry.entry_id: entry for entry in history}
            bindings: dict[str, ProvenanceBinding] = {}
            for _, _, raw in runtime._loop_decisions().entries():
                decision = json.loads(raw)
                if decision.get("kind") != "DECIDED":
                    continue
                command = runtime._publication(decision)
                conversations = tuple(
                    row for row in command.records if row.owner == CONVERSATION_OWNER
                )
                if not conversations:
                    continue
                identity = command.idempotency_key
                if (
                    len(conversations) != 1
                    or inspect_publication(
                        connection,
                        PublicationExpectation(
                            command.tenant_id,
                            command.operation_kind,
                            command.idempotency_key,
                            command.request_fingerprint,
                            command.expected_head,
                            tuple(
                                ExpectedPublicationRecord(
                                    row.record_id,
                                    row.owner,
                                    row.schema_id,
                                    row.canonical_bytes,
                                    row.fingerprint,
                                )
                                for row in command.records
                            ),
                        ),
                        command.expected_head + 1,
                    )
                    != "COMPLETE"
                    or not runtime._loop_decision_materialized(identity, decision)
                ):
                    raise ValueError("conversation publication is not exactly materialized")
                physical = conversations[0]
                retained = json.loads(physical.canonical_bytes)
                entry = ConversationEntry.model_validate_json(retained["entry_json"])
                if (
                    physical.schema_id != CONVERSATION_SCHEMA
                    or physical.record_id != entry.entry_id
                    or entry != by_id.get(entry.entry_id)
                    or entry.entry_id in bindings
                    or hashlib.sha256(retained["entry_json"].encode()).hexdigest()
                    != retained["fingerprint"]
                ):
                    raise ValueError("conversation differs from its original selected bytes")
                sources: tuple[SourceReference, ...]
                if entry.role == "principal":
                    if command.operation_kind != "conversation.accept":
                        raise ValueError("principal entry has no original ingress publication")
                    sources = (
                        SourceReference(
                            tenant_id=runtime._tenant_id,
                            owner="principal_ingress",
                            record_id=entry.entry_id,
                            record_version="1",
                            content_digest=hashlib.sha256(entry.accepted_bytes).hexdigest(),
                            label_head=contour,
                            label=DisclosureLabel(
                                lattice_version="chiplog.disclosure.v1",
                                value="ENDPOINT_RESTRICTED",
                                allowed_endpoints=(channel,),
                            ),
                        ),
                    )
                else:
                    owner = command.records[0]
                    if command.operation_kind == FANOUT_OPERATION:
                        evidence = RetainedFanOutPreparation.model_validate_json(
                            decision["fanout_preparation"]
                        )
                        envelope = build_envelope(evidence)
                        scalar_fields = (
                            "tenant_id",
                            "operation_kind",
                            "idempotency_key",
                            "request_fingerprint",
                            "expected_head",
                            "fence_generation",
                            "expected_fence_frontier",
                            "minimum_fence_frontier",
                        )
                        if (
                            any(
                                getattr(envelope, field) != getattr(command, field)
                                for field in scalar_fields
                            )
                            or command.fault != "none"
                            or command.admission_guard is not None
                            or command.decision_guard is not None
                            or tuple(
                                (
                                    member.record_id,
                                    member.owner,
                                    member.schema_id,
                                    base64.b64decode(
                                        member.canonical_payload_base64, validate=True
                                    ),
                                    member.fingerprint,
                                )
                                for member in envelope.records
                            )
                            != tuple(
                                (
                                    row.record_id,
                                    row.owner,
                                    row.schema_id,
                                    row.canonical_bytes,
                                    row.fingerprint,
                                )
                                for row in command.records
                            )
                        ):
                            raise ValueError(
                                "assistant fanout differs from original selected evidence"
                            )
                    elif command.operation_kind != "agent_loop" or len(command.records) != 2:
                        raise ValueError(
                            "assistant has no registered CompleteAcceptance publication"
                        )
                    if owner.owner != OWNER or owner.schema_id != SCHEMA:
                        raise ValueError(
                            "assistant requires a registered CompleteAcceptance schema"
                        )
                    run = RunRecord.model_validate_json(owner.canonical_bytes)
                    if (
                        run.event != "CompleteAcceptance"
                        or run.tenant != runtime._tenant_id
                        or owner.record_id != run.head
                        or entry.entry_id != run.run_id + "/accepted"
                        or entry.accepted_bytes != "\n".join(run.accepted_text).encode()
                    ):
                        raise ValueError("assistant differs from original CompleteAcceptance")
                    sources = accepted_sources(run)
                if entry.envelope.sources != sources:
                    raise ValueError(
                        "conversation closure differs from independent original inputs"
                    )
                bindings[entry.entry_id] = ProvenanceBinding(
                    subject=ProvenanceSubject(
                        tenant_id=runtime._tenant_id,
                        producer="core.conversation",
                        record_id=entry.entry_id,
                        revision=str(entry.sequence),
                    ),
                    content_digest=hashlib.sha256(entry.accepted_bytes).hexdigest(),
                    sources=tuple(
                        sorted(sources, key=lambda s: (s.owner, s.record_id, s.record_version))
                    ),
                )
            if bindings.keys() != by_id.keys():
                raise ValueError("conversation has missing original selected provenance")
            for source in extra:
                if source.owner != "principal_ingress" or source.tenant_id != runtime._tenant_id:
                    raise ValueError("new ingress source differs from authenticated principal")
                existing = bindings.get(source.record_id)
                if existing is not None:
                    if existing.sources != (source,):
                        raise ValueError("repeated ingress identity changes original source")
                    continue
                bindings[source.record_id] = ProvenanceBinding(
                    subject=ProvenanceSubject(
                        tenant_id=runtime._tenant_id,
                        producer="core.conversation",
                        record_id=source.record_id,
                        revision=str(len(history) + 1),
                    ),
                    content_digest=source.content_digest,
                    sources=(source,),
                )
            return tuple(bindings.values())
    except (ValueError, TypeError, KeyError, sqlite3.Error) as error:
        raise WorkspaceIntegrityError(
            "workspace.provenance", runtime._tenant_id, identity
        ) from error
