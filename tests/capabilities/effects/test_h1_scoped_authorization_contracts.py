"""Pure H1 request wires bind a real descriptive delivery-work g1 claim."""

from __future__ import annotations

import hashlib

import pytest
from pydantic import TypeAdapter

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead as LoopHead
from chiplog.capabilities.agent_loop.delivery_work_contracts import (
    DeliveryWorkSourceMemberV1,
    DeliveryWorkSourceV1,
    prepare_delivery_work_claim,
    prepare_delivery_work_genesis,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_contracts import (
    PreparedExternalDeliveryGrantAnchorV2,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_policy_contracts import (
    PreparedExternalSelfDeliveryPolicyAnchorV1,
)
from chiplog.capabilities.effects.contracts import ExactHead
from chiplog.capabilities.effects.dispatch_authority_contracts import CapturedSource
from chiplog.capabilities.effects.fences import NonSchedulerFence, NotApplicable, WorkerFence
from chiplog.capabilities.effects.h1_producer_source_contracts import (
    H1PreparedDeliveryMandateCandidateV1,
    H1ProducerNotApplicableV1,
    H1ProducerSourceInventoryV1,
)
from chiplog.capabilities.effects.h1_scoped_authorization_contracts import (
    H1DeliveryWorkClaimFenceV1,
    H1PrepareScopedAuthorizationV1,
    H1ScopedAuthorizationCurrentV1,
    decode_h1_prepare_scoped_authorization_v1,
)
from chiplog.composition.h1_scoped_authorization_issuance_v3 import (
    decode_scoped_authorization_issuance_v3,
)

_CURRENT = b"chiplog.effects.h1-scoped-authorization-current.v1\x00"
_FENCE = b"chiplog.effects.h1-delivery-work-claim-fence.v1\x00"
_REQUEST = b"chiplog.effects.h1-scoped-authorization-request.v1\x00"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _head(name: str) -> LoopHead:
    return LoopHead(identity=name, head=name + ":head", fingerprint=_sha(name.encode()))


def _candidate() -> H1PreparedDeliveryMandateCandidateV1:
    grant = PreparedExternalDeliveryGrantAnchorV2(
        owner_id="deployment_trust",
        decision=_head("decision"),
        record_ordinal=0,
        record_type_id="chiplog.deployment_trust.prepared_external_delivery_grant",
        schema_id="chiplog.deployment_trust.record.v1",
        record=_head("record"),
        grant=_head("grant"),
        revision=0,
    )
    policy = PreparedExternalSelfDeliveryPolicyAnchorV1(
        owner_id="deployment_trust",
        decision=_head("policy-decision"),
        record_ordinal=0,
        record_type_id="chiplog.deployment_trust.prepared_self_delivery_policy",
        schema_id="chiplog.deployment_trust.record.v1",
        record=_head("policy-record"),
        policy=_head("policy"),
        revision=0,
    )
    return H1PreparedDeliveryMandateCandidateV1(
        prepared_delivery_basis_bytes=b"basis",
        grant_anchor=grant,
        policy_anchor=policy,
        canonical_mandate_bytes=b"mandate",
        mandate_fingerprint=_sha(b"mandate"),
    )


def _source() -> DeliveryWorkSourceV1:
    def member(owner: str, kind: str, record: str) -> DeliveryWorkSourceMemberV1:
        raw = record.encode()
        return DeliveryWorkSourceMemberV1(
            owner=owner,
            record_kind=kind,
            record_id=record,
            schema_id="chiplog.test.v1",
            canonical_record_bytes=raw,
            fingerprint=_sha(raw),
        )

    return DeliveryWorkSourceV1(
        tenant_id="tenant",
        intent_id="intent",
        selected_decision_id="v4",
        selected_decision_fingerprint="a" * 64,
        terminal_run=member("agent_loop", "RUN", "run"),
        terminal_manifest=member("agent_loop", "MANIFEST", "manifest"),
        intent=member("effects", "INTENT", "intent"),
    )


def _inventory(candidate: H1PreparedDeliveryMandateCandidateV1) -> H1ProducerSourceInventoryV1:
    def captured(role: str) -> CapturedSource:
        raw = role.encode()
        value = ExactHead(subject_id=role, head=role + ":head", fingerprint=_sha(raw))
        return CapturedSource(
            source_id=role,
            source_version="v1",
            owner_id="reader",
            reader_id="reader",
            invalidation_manifest=value,
            head=value,
            canonical_value=raw,
            clock_contract="clock",
            clock_epoch="epoch",
            valid_until_ns=100,
        )

    values = {
        role: captured(role)
        for role in (
            "trust",
            "semantic_registry",
            "runtime_and_fence",
            "endpoint",
            "credential_lifecycle",
            "deployment_entitlement",
            "clock",
            "effects_history",
            "normative_conflict_generation",
        )
    }
    values["planning"] = H1ProducerNotApplicableV1(role="planning", candidate=candidate)
    values["original_adoption"] = H1ProducerNotApplicableV1(
        role="original_adoption", candidate=candidate
    )
    return H1ProducerSourceInventoryV1.model_validate(values)


def _request() -> H1PrepareScopedAuthorizationV1:
    candidate = _candidate()
    source = _source()
    g1 = prepare_delivery_work_claim(
        source,
        prepare_delivery_work_genesis(source),
        holder_id="holder",
        session_id="session",
        lease_expires_at_ns=90,
        trusted_clock_ns=10,
    )
    current = H1ScopedAuthorizationCurrentV1(
        tenant_id="tenant",
        intent_id="intent",
        command_id="command",
        command_bytes=b"command",
        command_fingerprint=_sha(_CURRENT + b"command"),
        mandate_candidate=candidate,
        sources=_inventory(candidate),
        broker_epoch="epoch",
        runtime_generation="generation",
        clock_epoch="clock",
        observed_at_ns=11,
        lease_expires_at_ns=80,
    )
    fence = H1DeliveryWorkClaimFenceV1(
        selected_claim_decision_id="g1-decision",
        selected_claim_decision_fingerprint="b" * 64,
        selected_g1_canonical_bytes=g1.canonical_bytes(),
        selected_g1_record_fingerprint=_sha(g1.canonical_bytes()),
        command_id="command",
        command_fingerprint=current.command_fingerprint,
        current_fingerprint=_sha(_CURRENT + current.canonical_bytes()),
        holder_id="holder",
        session_id="session",
        broker_epoch="epoch",
        runtime_generation="generation",
        clock_epoch="clock",
        claimed_at_ns=10,
        lease_expires_at_ns=90,
    )
    pre = b"".join(
        len(x).to_bytes(8, "big") + x
        for x in (b"tenant", b"command", current.canonical_bytes(), fence.canonical_bytes())
    )
    return H1PrepareScopedAuthorizationV1(
        tenant_id="tenant",
        command_id="command",
        command_fingerprint=current.command_fingerprint,
        current=current,
        claim_fence=fence,
        request_fingerprint=_sha(_REQUEST + pre + _sha(_FENCE + fence.canonical_bytes()).encode()),
    )


def test_roundtrip_real_g1_and_v3_rejection() -> None:
    raw = _request().canonical_bytes()
    assert decode_h1_prepare_scoped_authorization_v1(raw).canonical_bytes() == raw
    with pytest.raises(ValueError):
        decode_scoped_authorization_issuance_v3(raw)


@pytest.mark.parametrize(
    "field",
    ("selected_g1_canonical_bytes", "selected_g1_record_fingerprint", "lease_expires_at_ns"),
)
def test_g1_mutations_reject(field: str) -> None:
    value = _request()
    update = {
        field: b"forged"
        if field == "selected_g1_canonical_bytes"
        else "0" * 64
        if field.endswith("fingerprint")
        else 91
    }
    with pytest.raises(ValueError):
        value.claim_fence.model_copy(update=update).exact_claim_shape()


def _reseal(
    value: H1PrepareScopedAuthorizationV1, current: H1ScopedAuthorizationCurrentV1
) -> H1PrepareScopedAuthorizationV1:
    fence = value.claim_fence.model_copy(
        update={"current_fingerprint": _sha(_CURRENT + current.canonical_bytes())}
    )
    pre = b"".join(
        len(x).to_bytes(8, "big") + x
        for x in (b"tenant", b"command", current.canonical_bytes(), fence.canonical_bytes())
    )
    return value.model_copy(
        update={
            "current": current,
            "claim_fence": fence,
            "request_fingerprint": _sha(
                _REQUEST + pre + _sha(_FENCE + fence.canonical_bytes()).encode()
            ),
        }
    )


@pytest.mark.parametrize(("observed_at_ns", "lease_expires_at_ns"), ((9, 80), (11, 91)))
def test_resealed_request_rejects_authorization_outside_g1_claim_lease(
    observed_at_ns: int, lease_expires_at_ns: int
) -> None:
    value = _request()
    sealed = _reseal(
        value,
        value.current.model_copy(
            update={"observed_at_ns": observed_at_ns, "lease_expires_at_ns": lease_expires_at_ns}
        ),
    )
    with pytest.raises(ValueError):
        decode_h1_prepare_scoped_authorization_v1(sealed.canonical_bytes())


def test_worker_fence_and_cross_pair_reject() -> None:
    legacy = NonSchedulerFence(
        lineage=NotApplicable(),
        physical_root=NotApplicable(),
        lease=NotApplicable(),
        clock_proof=NotApplicable(),
        run_id="r",
        run_head="h",
        worker_session_id="s",
        runtime_generation="g",
    )
    assert TypeAdapter(WorkerFence).validate_json(legacy.canonical_bytes()) == legacy
    with pytest.raises(ValueError):
        H1DeliveryWorkClaimFenceV1.model_validate_json(legacy.canonical_bytes())
    one, two = _request(), _request()
    with pytest.raises(ValueError):
        one.model_copy(
            update={"claim_fence": two.claim_fence.model_copy(update={"command_id": "other"})}
        ).exact_cross_binding()
