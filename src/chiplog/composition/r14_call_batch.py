"""Exact call-acceptance byte graph; no runtime reads or publication credentials."""

import base64

from chiplog.capabilities.effects.dispatch_v2 import canonical, digest
from chiplog.platform._owner_publication_contracts import (
    CallEffectBatch,
    NoPlanningParticipant,
    OwnerCommandBytes,
    OwnerRecordBytes,
)

from .r14_acceptance_v2_contracts import RetainedAcceptancePreparationV2
from .r14_acceptance_v2_records import build_acceptance_envelope
from .r14_call_dispatch_policy import policy_reference

LOOP_PREPARATION_SCHEMA = "chiplog.call.acceptance-preparation.v1"


def acceptance_records(
    retained: RetainedAcceptancePreparationV2,
) -> tuple[OwnerRecordBytes, ...]:
    envelope = build_acceptance_envelope(retained)
    kinds = (
        "agent_loop." + retained.loop_proposal.accepted.kind,
        "agent_loop." + retained.loop_proposal.execution_intent.kind,
        "effects." + retained.effects_proposal.kind,
    )
    return tuple(
        OwnerRecordBytes(
            owner=member.owner,
            record_kind=kind,
            record_id=member.record_id,
            schema_id=member.schema_id,
            canonical_bytes=base64.b64decode(member.canonical_payload_base64, validate=True),
            fingerprint=member.fingerprint,
        )
        for member, kind in zip(envelope.complete_records, kinds, strict=True)
    )


def verify_call_batch(batch: CallEffectBatch, retained: RetainedAcceptancePreparationV2) -> None:
    """Verify companions and cut headers; callers must authenticate retained issuance."""
    batch = CallEffectBatch.model_validate_json(batch.model_dump_json())
    records = acceptance_records(retained)
    loop = retained.loop_request
    effects = retained.effects_request
    cut = loop.binding.cut
    policy = policy_reference()
    expected_loop = OwnerCommandBytes(
        owner="agent_loop",
        schema_id=LOOP_PREPARATION_SCHEMA,
        canonical_bytes=loop.canonical_bytes(),
        fingerprint=digest(loop.canonical_bytes()),
    )
    expected_effects = OwnerCommandBytes(
        owner="effects",
        schema_id=effects.schema_id,
        canonical_bytes=effects.canonical_bytes(),
        fingerprint=digest(effects.canonical_bytes()),
    )
    if (
        not isinstance(batch.planning, NoPlanningParticipant)
        or batch.loop_command != expected_loop
        or batch.effects_command != expected_effects
        or batch.complete_records != records
        or batch.complete_batch_fingerprint
        != digest(canonical([row.model_dump(mode="json") for row in records]))
        or batch.identity.tenant_id != cut.tenant_id
        or batch.identity.command_id != loop.command_id
        or batch.identity.command_id != effects.command.identity.command_id
        or batch.expected.tenant_id != cut.tenant_id
        or batch.expected.tenant_frontier != cut.tenant_commit_sequence
        or batch.expected.expected_materialization_commitment != cut.materialization_commitment
        or batch.expected.registry_head != policy.head
        or batch.expected.registry_fingerprint != policy.fingerprint
    ):
        raise ValueError("call batch differs from exact retained owner graph or original cut")
