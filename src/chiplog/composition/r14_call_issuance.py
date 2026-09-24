"""Retained call preparation verification without current history acquisition."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from chiplog.capabilities.effects.dispatch_v2 import digest
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    CallEffectBatch,
    ExactRecordHead,
    InvocationProofRef,
    NoPlanningParticipant,
    ObservedPresence,
    OwnerCommandBytes,
    PublicationIdentity,
    WorkerAuthentication,
)

from .r14_call_acceptance_port import AcceptedCallReceipt
from .r14_call_batch import LOOP_PREPARATION_SCHEMA, acceptance_records, verify_call_batch
from .r14_call_dispatch_policy import policy_reference
from .r14_call_preparation import PreparedCallExchange, original_call_request
from .r14_call_preview import CallPreviewIssuance, validate_call_preview
from .r16_dispatch_authority import DispatchIssuance, validate_dispatch_issuance_value
from .r16_effects_inputs import canonical

if TYPE_CHECKING:
    from .r16_dispatch_runtime import ExecutionDispatchRuntime


SCHEMA = "chiplog.call.acceptance-issuance.v2"


class CallAcceptanceIssuance(PreparedCallExchange):
    schema_id: Literal["chiplog.call.acceptance-issuance.v2"] = (
        "chiplog.call.acceptance-issuance.v2"
    )


def call_identity(value: PreparedCallExchange) -> PublicationIdentity:
    return PublicationIdentity(
        tenant_id=value.captured.cut.tenant_id,
        command_id=value.retained.loop_request.command_id,
        command_fingerprint=digest(value.adoption.canonical_bytes()),
        canonicalization_version="chiplog.owner-publication.v1",
    )


def call_batch(value: CallAcceptanceIssuance, invocation: InvocationProofRef) -> CallEffectBatch:
    """Mechanical untrusted envelope; only the private writer authority can admit it."""
    records = acceptance_records(value.retained)
    loop, effects = value.retained.loop_request, value.retained.effects_request
    return CallEffectBatch(
        identity=call_identity(value),
        authentication=WorkerAuthentication(
            invocation=invocation,
            applicability_schema=SCHEMA,
            applicability_bytes=value.canonical_bytes(),
            applicability_fingerprint=digest(value.canonical_bytes()),
        ),
        expected=call_read_manifest(value),
        loop_command=OwnerCommandBytes(
            owner="agent_loop",
            schema_id=LOOP_PREPARATION_SCHEMA,
            canonical_bytes=loop.canonical_bytes(),
            fingerprint=digest(loop.canonical_bytes()),
        ),
        effects_command=OwnerCommandBytes(
            owner="effects",
            schema_id=effects.schema_id,
            canonical_bytes=effects.canonical_bytes(),
            fingerprint=digest(effects.canonical_bytes()),
        ),
        planning=NoPlanningParticipant(),
        complete_records=records,
        complete_batch_fingerprint=digest(canonical(records)),
    )


def call_read_manifest(value: PreparedCallExchange) -> AuthoritativeReadManifest:
    captured = value.captured
    worker = captured.cut.worker
    if worker is None:
        raise ValueError("call issuance lacks original worker")
    initialized = value.retained.loop_request.initialized_record
    policy = policy_reference()
    return AuthoritativeReadManifest(
        tenant_id=captured.cut.tenant_id,
        tenant_frontier=captured.cut.tenant_frontier,
        expected_materialization_commitment=captured.cut.materialization_commitment,
        registry_head=policy.head,
        registry_fingerprint=policy.fingerprint,
        ordered_heads=(
            ObservedPresence(
                head=ExactRecordHead(
                    owner="agent_loop",
                    record_kind="Run",
                    subject_id=worker.run.run_id,
                    record_id=worker.run.head,
                    fingerprint=digest(worker.run.canonical_bytes()),
                )
            ),
            ObservedPresence(
                head=ExactRecordHead(
                    owner="agent_loop",
                    record_kind=initialized.kind,
                    subject_id=initialized.original_call_id,
                    record_id=initialized.original_call_id,
                    fingerprint=digest(initialized.canonical_bytes()),
                )
            ),
        ),
        complete_manifest_fingerprint=digest(
            canonical(
                (
                    captured,
                    value.retained.loop_request.binding.cut.predecessor_inventory,
                )
            )
        ),
    )


def call_receipt(batch: CallEffectBatch, value: PreparedCallExchange) -> AcceptedCallReceipt:
    manifest = value.retained.loop_proposal.complete_acceptance_manifest
    return AcceptedCallReceipt(
        original_call_id=value.preview.preview.target.original_call_id,
        initialized=value.preview.preview.target.initialized,
        accepted=manifest[0],
        execution_intent=manifest[1],
        external_intent=manifest[2],
        publication_id=batch.identity.command_id,
        publication_fingerprint=batch.complete_batch_fingerprint,
    )


def decode_call_issuance(batch: CallEffectBatch) -> CallAcceptanceIssuance:
    auth = batch.authentication
    if (
        auth.applicability_schema != SCHEMA
        or digest(auth.applicability_bytes) != auth.applicability_fingerprint
    ):
        raise ValueError("call acceptance lacks exact original issuance")
    value = CallAcceptanceIssuance.model_validate_json(auth.applicability_bytes)
    if value.canonical_bytes() != auth.applicability_bytes:
        raise ValueError("noncanonical retained call issuance")
    verify_call_batch(batch, value.retained)
    return value


def validate_call_issuance(
    batch: CallEffectBatch, runtime: ExecutionDispatchRuntime
) -> CallAcceptanceIssuance:
    value = decode_call_issuance(batch)
    # Raw journal selection avoids recursive calls into joined execution history.
    selected = 0
    identities: set[str] = set()
    for _, _, raw in runtime._require_call_preview_journal().entries():
        preview = CallPreviewIssuance.model_validate_json(raw)
        if preview.canonical_bytes() != raw or preview.preview.preview_id in identities:
            raise ValueError("noncanonical or duplicate original preview selection")
        identities.add(preview.preview.preview_id)
        validate_call_preview(preview, runtime)
        selected += preview == value.preview
    if selected != 1:
        raise ValueError("acceptance preview is not independently selected")
    captured, retained = value.captured, value.retained
    if (
        value.adoption.preview_bytes != value.preview.preview.canonical_bytes()
        or captured.principal != value.preview.captured.principal
        or captured.cut.physical_path != value.preview.captured.cut.physical_path
        or captured.cut.physical_device != value.preview.captured.cut.physical_device
        or captured.cut.physical_inode != value.preview.captured.cut.physical_inode
        or batch.identity.command_fingerprint != digest(value.adoption.canonical_bytes())
        or retained.effects_request.command.identity.fingerprint
        != batch.identity.command_fingerprint
        or batch.expected != call_read_manifest(value)
    ):
        raise ValueError("call issuance adoption, physical cut or read manifest differs")
    expected_request, expected_intent = original_call_request(
        value.preview,
        value.adoption,
        captured,
        value.observed,
        retained.loop_request.binding.cut.predecessor_inventory,
    )
    if (
        expected_request != retained.loop_request
        or expected_intent != retained.effects_proposal.snapshot.intent
    ):
        raise ValueError("retained call sources or immutable intent interpretation differs")
    sent = value.loop_sent
    if (
        sent.callee != captured.sessions[2]
        or sent.operation_id != "agent_loop.prepare_consequential_acceptance"
        or sent.schema_id != LOOP_PREPARATION_SCHEMA
        or sent.canonical_payload != retained.loop_request.canonical_bytes()
        or sent.budget.absolute_deadline_ns <= captured.observed_time_ns
    ):
        raise ValueError("retained loop preparation exchange differs")
    validate_dispatch_issuance_value(
        DispatchIssuance(
            schema_id="chiplog.effects.dispatch-issuance.v2",
            captured=captured,
            observed=value.observed,
            sent=value.effects_sent,
            request=retained.effects_request,
            record=retained.effects_proposal,
        ),
        batch,
        runtime,
    )
    return value
