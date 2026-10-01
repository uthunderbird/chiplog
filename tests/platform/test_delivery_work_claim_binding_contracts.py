"""The first delivery-work claim binds a canonical authority-free command."""

import hashlib

import pytest
from pydantic import TypeAdapter

from chiplog.capabilities.agent_loop.delivery_work_contracts import (
    DELIVERY_WORK_CLAIM_COMMAND_SCHEMA,
    DELIVERY_WORK_OWNER,
    DELIVERY_WORK_SCHEMA,
    DeliveryWorkSourceMemberV1,
    DeliveryWorkSourceV1,
    PrepareDeliveryWorkClaimV1,
    prepare_delivery_work_claim,
    prepare_delivery_work_claim_projection,
    prepare_delivery_work_genesis,
)
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    DeliveryWorkClaimBatchV1,
    InvocationProofRef,
    OwnerCommandBytes,
    OwnerRecordBytes,
    PublicationIdentity,
    RegisteredPublication,
    WorkerAuthentication,
)
from chiplog.platform.delivery_work_claim_binding_contracts import (
    DeliveryWorkClaimantClockBindingV1,
    DeliveryWorkClaimBindingV1,
    canonical_delivery_work_claim_batch_bytes,
    validate_delivery_work_claim_binding,
)


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _source() -> DeliveryWorkSourceV1:
    def member(owner: str, kind: str, record_id: str, raw: bytes) -> DeliveryWorkSourceMemberV1:
        return DeliveryWorkSourceMemberV1(
            owner=owner,
            record_kind=kind,
            record_id=record_id,
            schema_id="test." + kind + ".v1",
            canonical_record_bytes=raw,
            fingerprint=_digest(raw),
        )

    return DeliveryWorkSourceV1(
        tenant_id="tenant",
        intent_id="intent",
        selected_decision_id="v4-decision",
        selected_decision_fingerprint=_digest(b"v4-decision"),
        terminal_run=member("agent_loop", "Run", "run", b"run"),
        terminal_manifest=member("agent_loop", "Manifest", "manifest", b"manifest"),
        intent=member("effects", "Intent", "intent", b"intent"),
    )


def _batch() -> tuple[DeliveryWorkClaimBatchV1, DeliveryWorkClaimBindingV1]:
    source = _source()
    predecessor = prepare_delivery_work_genesis(source)
    projection = prepare_delivery_work_claim_projection(source, predecessor)
    command = PrepareDeliveryWorkClaimV1(projection=projection)
    command_bytes = command.canonical_bytes()
    claimant_clock = DeliveryWorkClaimantClockBindingV1(
        holder_id="holder",
        session_id="session",
        broker_epoch="broker-epoch",
        runtime_generation="runtime-generation",
        clock_epoch="clock-epoch",
        trusted_clock_ns=10,
        lease_expires_at_ns=20,
    )
    state = prepare_delivery_work_claim(
        source,
        predecessor,
        holder_id=claimant_clock.holder_id,
        session_id=claimant_clock.session_id,
        trusted_clock_ns=claimant_clock.trusted_clock_ns,
        lease_expires_at_ns=claimant_clock.lease_expires_at_ns,
    )
    batch = DeliveryWorkClaimBatchV1(
        identity=PublicationIdentity(
            tenant_id="tenant",
            command_id="claim-command",
            command_fingerprint=_digest(command_bytes),
            canonicalization_version="chiplog.owner-publication.v1",
        ),
        authentication=WorkerAuthentication(
            invocation=InvocationProofRef(
                issuance_id="issue",
                issuance_fingerprint=_digest(b"issue"),
                broker_epoch="epoch",
                broker_session="session",
                runtime_generation="generation",
                operation_subject="claim-command",
            ),
            applicability_schema="test.claim.v1",
            applicability_bytes=b"applicability",
            applicability_fingerprint=_digest(b"applicability"),
        ),
        expected=AuthoritativeReadManifest(
            tenant_id="tenant",
            tenant_frontier=1,
            expected_materialization_commitment=_digest(b"before"),
            registry_head="registry",
            registry_fingerprint=_digest(b"registry"),
            ordered_heads=(),
            complete_manifest_fingerprint=_digest(b"manifest"),
        ),
        claim_command=OwnerCommandBytes(
            owner=DELIVERY_WORK_OWNER,
            schema_id=DELIVERY_WORK_CLAIM_COMMAND_SCHEMA,
            canonical_bytes=command_bytes,
            fingerprint=_digest(command_bytes),
        ),
        complete_records=(
            OwnerRecordBytes(
                owner=DELIVERY_WORK_OWNER,
                record_kind="DELIVERY_WORK_STATE",
                record_id=state.record_id,
                schema_id=DELIVERY_WORK_SCHEMA,
                canonical_bytes=state.canonical_bytes(),
                fingerprint=_digest(state.canonical_bytes()),
            ),
        ),
        complete_batch_fingerprint=_digest(b"complete"),
    )
    binding = DeliveryWorkClaimBindingV1(
        genesis_decision_id="g0-decision",
        genesis_decision_fingerprint=_digest(b"g0-decision"),
        selected_source=source,
        claim_projection=projection,
        claimant_clock=claimant_clock,
        request_digest=_digest(canonical_delivery_work_claim_batch_bytes(batch)),
    )
    return batch, binding


def test_claim_binding_reproduces_the_complete_g1_record() -> None:
    batch, binding = _batch()

    validate_delivery_work_claim_binding(batch, binding)
    assert TypeAdapter(RegisteredPublication).validate_json(batch.model_dump_json()) == batch


@pytest.mark.parametrize("mutation", ("schema", "record", "digest"))
def test_claim_batch_or_binding_rejects_schema_and_exact_projection_mutations(
    mutation: str,
) -> None:
    batch, binding = _batch()
    if mutation == "schema":
        raw = batch.model_dump(mode="python")
        raw["claim_command"] = batch.claim_command.model_copy(update={"schema_id": "unknown.v1"})
        with pytest.raises(ValueError, match="route, role, schema, or digest"):
            DeliveryWorkClaimBatchV1.model_validate(raw)
    elif mutation == "record":
        raw = batch.model_dump(mode="python")
        raw["complete_records"] = (
            batch.complete_records[0].model_copy(update={"record_id": "foreign-g1"}),
        )
        with pytest.raises(ValueError, match="projection differs"):
            DeliveryWorkClaimBatchV1.model_validate(raw)
    else:
        with pytest.raises(ValueError, match="selected binding differs"):
            validate_delivery_work_claim_binding(
                batch, binding.model_copy(update={"request_digest": _digest(b"foreign")})
            )
