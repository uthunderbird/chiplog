"""Red durability acceptance tests for prepared self-delivery policy lineage.

The lineage scanner treats a retained signed source only as an exact immutable
preimage.  Pin resolution and signature verification belong at the append
boundary, before any physical decision is written.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from chiplog.adapters.driven.deployment_trust import (
    IndependentTenantDecisionJournal,
    SQLiteTrustMaterializer,
)
from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead, ProviderRecipient
from chiplog.capabilities.deployment_trust._r7_process import _evaluate, _TrustOwnerCall
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    HermeticTrustObservationV1,
)
from chiplog.capabilities.deployment_trust.operator_policy_authorization_contracts import (
    OperatorPolicyAuthorizationPayloadV1,
    RetainedOperatorPolicyAuthorizationSourceV1,
    SignedOperatorPolicyAuthorizationV1,
    operator_policy_source_content_head,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_policy_contracts import (
    IssuePreparedExternalSelfDeliveryPolicyRequestV1,
    PreparedExternalSelfDeliveryPolicyTermsV1,
    PreparedExternalSelfDeliveryPolicyV1,
    RevokePreparedExternalSelfDeliveryPolicyRequestV1,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_policy_owner_contracts import (  # noqa: E501
    AuthorizePreparedSelfDeliveryPolicyCallV1,
    PreparedSelfDeliveryPolicyProposalV1,
    prepared_self_delivery_policy_request_content_head,
)
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.operator_policy_key_pin import OperatorPolicyKeyPinFileV1
from chiplog.platform.prepared_self_delivery_policy_lineage import (
    AuthenticatedPreparedSelfDeliveryPolicy,
    authenticated_prepared_self_delivery_policy_lineage,
)
from chiplog.platform.r7_trust_durability import BrokerTrustDurability

_OPERATOR_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))


def _head(identity: str, body: bytes = b"fixture") -> ExactHead:
    return ExactHead(
        identity=identity,
        head=f"{identity}/{hashlib.sha256(body).hexdigest()}",
        fingerprint=hashlib.sha256(body).hexdigest(),
    )


def _terms() -> PreparedExternalSelfDeliveryPolicyTermsV1:
    payload = b"prepared self delivery"
    return PreparedExternalSelfDeliveryPolicyTermsV1(
        principal_id="principal",
        channel_id="channel",
        recipient=ProviderRecipient(
            provider_id="telegram",
            account_id="account",
            recipient_id="recipient",
            endpoint=_head("endpoint"),
            canonical_address=b"telegram://account/recipient",
            credential_binding=_head("credential"),
        ),
        communication_permission="PREPARED_EXTERNAL_SEND",
        disclosure_permission="EXACT_RENDERED_PAYLOAD",
        self_recipient_semantics="OPERATOR_ATTESTED_SELF",
        payload_class="NonAuthoritativeText",
        payload_digest=hashlib.sha256(payload).hexdigest(),
        payload_byte_length=len(payload),
        source_classes=("CLI",),
        selection_modes=("ORIGIN_EXACT",),
        external_delivery=True,
        max_calls=1,
        clock_contract="clock",
        clock_epoch="epoch",
        not_before_ns=10,
        expires_at_ns=20,
    )


@dataclass(frozen=True)
class _Bundle:
    gate: AuthorityGate
    durability: BrokerTrustDurability
    materializer: SQLiteTrustMaterializer


def _provision_operator_key_pin(
    gate: AuthorityGate, *, status: Literal["ACTIVE", "REVOKED"] = "ACTIVE"
) -> None:
    from cryptography.hazmat.primitives import serialization

    pin = OperatorPolicyKeyPinFileV1(
        schema_id="chiplog.operator-policy-key-pin.v1",
        tenant_id="tenant",
        database_id="database",
        operator_key_id="operator-key",
        policy_id="policy",
        allowed_operations=(
            "ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
            "REVOKE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
        ),
        status=status,
        public_key=_OPERATOR_KEY.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        ),
    )
    path = gate.database.with_suffix(gate.database.suffix + ".operator-policy-key.json")
    path.write_bytes(pin.canonical_bytes())
    path.chmod(0o600)


@contextmanager
def _bundle(tmp_path: Path) -> Iterator[_Bundle]:
    gate = AuthorityGate.for_database(tmp_path / "authority.sqlite")
    journal = IndependentTenantDecisionJournal.for_authority_bundle(
        tmp_path / "trust.journal", authority_gate=gate
    )
    materializer = SQLiteTrustMaterializer.for_authority_bundle(
        tmp_path / "trust.sqlite", authority_gate=gate
    )
    pin_path = gate.database.with_suffix(gate.database.suffix + ".operator-policy-key.json")
    if not pin_path.exists():
        _provision_operator_key_pin(gate)
    try:
        yield _Bundle(
            gate,
            BrokerTrustDurability(journal, materializer, b"operator-secret"),
            materializer,
        )
    finally:
        materializer.close()


def _bootstrap(bundle: _Bundle) -> None:
    observation = bundle.durability.capture_verified_observation()
    response = _evaluate(
        _TrustOwnerCall(
            mode="BOOTSTRAP",
            snapshot_bytes=observation.snapshot_bytes,
            request_bytes=json.dumps(
                {
                    "credential_id": "credential-1",
                    "database_instance_id": "database",
                    "expected_peer": "uid:test",
                    "peer": "uid:test",
                    "principal_id": "principal-1",
                    "session_id": "session-1",
                    "tenant_id": "tenant",
                    "token_fingerprint": "bootstrap-token",
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
        )
    )
    assert response.reference_bytes is not None
    bundle.durability.apply_authorized(response.reference_bytes)


def _observation(bundle: _Bundle) -> tuple[HermeticTrustObservationV1, bytes]:
    frozen = bundle.durability.capture_verified_observation()
    assert frozen.journal_head is not None
    decision_id, _, raw = bundle.durability._journal.entries()[-1]
    assert decision_id == frozen.journal_head
    logical_head = bundle.durability.owner_snapshot_entries()[-1][0]
    return (
        HermeticTrustObservationV1(
            physical_journal_head=ExactHead(
                identity="deployment-trust/journal",
                head=decision_id,
                fingerprint=hashlib.sha256(raw).hexdigest(),
            ),
            logical_snapshot_head=logical_head,
        ),
        frozen.snapshot_bytes,
    )


def _source(
    request: IssuePreparedExternalSelfDeliveryPolicyRequestV1
    | RevokePreparedExternalSelfDeliveryPolicyRequestV1,
    operator_key: Ed25519PrivateKey,
) -> RetainedOperatorPolicyAuthorizationSourceV1:
    request_bytes = request.canonical_bytes()
    payload = OperatorPolicyAuthorizationPayloadV1(
        algorithm="Ed25519",
        source_id=f"source-{request.command_id}",
        operator_key_id="operator-key",
        tenant_id=request.tenant_id,
        database_id=request.database_id,
        policy_id=request.policy_id,
        command_id=request.command_id,
        operation=request.operation,
        request_sha256=hashlib.sha256(request_bytes).hexdigest(),
        canonical_request_bytes=request_bytes,
    )
    signed = SignedOperatorPolicyAuthorizationV1(
        payload=payload,
        signature=operator_key.sign(payload.canonical_bytes()),
    )
    source_bytes = signed.canonical_bytes()
    return RetainedOperatorPolicyAuthorizationSourceV1(
        ref=ExactHead(
            identity=payload.source_id,
            head=operator_policy_source_content_head(source_bytes),
            fingerprint=hashlib.sha256(source_bytes).hexdigest(),
        ),
        canonical_source_bytes=source_bytes,
    )


def _proposal(
    call: AuthorizePreparedSelfDeliveryPolicyCallV1,
    request: IssuePreparedExternalSelfDeliveryPolicyRequestV1
    | RevokePreparedExternalSelfDeliveryPolicyRequestV1,
    source: RetainedOperatorPolicyAuthorizationSourceV1,
    previous: PreparedExternalSelfDeliveryPolicyV1 | None = None,
) -> PreparedSelfDeliveryPolicyProposalV1:
    request_bytes = request.canonical_bytes()
    if isinstance(request, IssuePreparedExternalSelfDeliveryPolicyRequestV1):
        terms = request.terms
        status: Literal["ACTIVE", "REVOKED"] = "ACTIVE"
    else:
        assert previous is not None
        terms = previous.terms
        status = "REVOKED"
    policy = PreparedExternalSelfDeliveryPolicyV1(
        issuer="deployment_trust",
        tenant_id=request.tenant_id,
        database_id=request.database_id,
        policy_id=request.policy_id,
        revision=0 if previous is None else previous.revision + 1,
        predecessor=None if request.expected_policy is None else request.expected_policy.policy,
        status=status,
        terms=terms,
        authorization_command=ExactHead(
            identity=request.command_id,
            head=prepared_self_delivery_policy_request_content_head(request_bytes),
            fingerprint=hashlib.sha256(request_bytes).hexdigest(),
        ),
        authorization_source=source.ref,
    )
    return PreparedSelfDeliveryPolicyProposalV1(
        call_sha256=hashlib.sha256(call.canonical_bytes()).hexdigest(),
        operator_source=source,
        policy=policy,
    )


def _issue(
    bundle: _Bundle,
    *,
    command_id: str = "issue-command",
    signing_key: Ed25519PrivateKey = _OPERATOR_KEY,
) -> tuple[AuthorizePreparedSelfDeliveryPolicyCallV1, PreparedSelfDeliveryPolicyProposalV1]:
    observation, snapshot = _observation(bundle)
    request = IssuePreparedExternalSelfDeliveryPolicyRequestV1(
        operation="ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
        command_id=command_id,
        tenant_id="tenant",
        database_id="database",
        policy_id="policy",
        expected_trust_observation=observation,
        expected_policy=None,
        terms=_terms(),
    )
    source = _source(request, signing_key)
    call = AuthorizePreparedSelfDeliveryPolicyCallV1(
        canonical_signed_source_bytes=source.canonical_source_bytes,
        snapshot_bytes=snapshot,
        expected_trust_observation=observation,
        latest_policy_anchor=None,
        latest_policy_bytes=None,
    )
    return call, _proposal(call, request, source)


def _revoke(
    bundle: _Bundle, prior: AuthenticatedPreparedSelfDeliveryPolicy
) -> tuple[AuthorizePreparedSelfDeliveryPolicyCallV1, PreparedSelfDeliveryPolicyProposalV1]:
    observation, snapshot = _observation(bundle)
    request = RevokePreparedExternalSelfDeliveryPolicyRequestV1(
        operation="REVOKE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
        command_id="revoke-command",
        tenant_id="tenant",
        database_id="database",
        policy_id="policy",
        expected_trust_observation=observation,
        expected_policy=prior.anchor,
    )
    source = _source(request, _OPERATOR_KEY)
    call = AuthorizePreparedSelfDeliveryPolicyCallV1(
        canonical_signed_source_bytes=source.canonical_source_bytes,
        snapshot_bytes=snapshot,
        expected_trust_observation=observation,
        latest_policy_anchor=prior.anchor,
        latest_policy_bytes=prior.policy.canonical_bytes(),
    )
    return call, _proposal(call, request, source, prior.policy)


def test_issued_policy_is_physically_anchored_current_and_survives_reopen(
    tmp_path: Path,
) -> None:
    with _bundle(tmp_path) as bundle:
        _bootstrap(bundle)
        call, proposal = _issue(bundle)
        bundle.durability.append_prepared_self_delivery_policy(call, proposal)
        issued = bundle.durability.latest_prepared_self_delivery_policy(
            "tenant", "database", "policy"
        )
        assert issued is not None
        assert issued.policy == proposal.policy
        assert issued.anchor.record_ordinal == 1
        assert issued.anchor.decision.head == issued.decision_id
        assert issued.anchor.record.fingerprint == hashlib.sha256(issued.record_bytes).hexdigest()
        assert bundle.durability.current_prepared_self_delivery_policy(issued.anchor) == issued

    with _bundle(tmp_path) as reopened:
        restored = reopened.durability.latest_prepared_self_delivery_policy(
            "tenant", "database", "policy"
        )
        assert restored is not None
        assert restored.policy.status == "ACTIVE"
        assert (
            reopened.durability.current_prepared_self_delivery_policy(restored.anchor) == restored
        )


def test_revoke_preserves_terms_advances_physical_policy_head_and_denies_current(
    tmp_path: Path,
) -> None:
    with _bundle(tmp_path) as bundle:
        _bootstrap(bundle)
        issue_call, issue_proposal = _issue(bundle)
        bundle.durability.append_prepared_self_delivery_policy(issue_call, issue_proposal)
        issued = bundle.durability.latest_prepared_self_delivery_policy(
            "tenant", "database", "policy"
        )
        assert issued is not None
        revoke_call, revoke_proposal = _revoke(bundle, issued)
        bundle.durability.append_prepared_self_delivery_policy(revoke_call, revoke_proposal)

        revoked = bundle.durability.latest_prepared_self_delivery_policy(
            "tenant", "database", "policy"
        )
        assert revoked is not None
        assert revoked.policy.status == "REVOKED"
        assert revoked.policy.predecessor == issued.anchor.policy
        assert revoked.policy.terms == issued.policy.terms
        with pytest.raises(RuntimeError, match="revoked"):
            bundle.durability.current_prepared_self_delivery_policy(revoked.anchor)


@pytest.mark.parametrize("failure", ("stale", "duplicate-command", "source"))
def test_append_denials_leave_journal_and_materialization_unchanged(
    tmp_path: Path, failure: str
) -> None:
    with _bundle(tmp_path) as bundle:
        _bootstrap(bundle)
        call, proposal = _issue(bundle)
        bundle.durability.append_prepared_self_delivery_policy(call, proposal)
        before_entries = bundle.durability._journal.entries()
        before_records = bundle.materializer.records()
        if failure == "source":
            other_call, other_proposal = _issue(bundle, command_id="other-command")
            proposal = other_proposal.model_copy(
                update={"operator_source": proposal.operator_source}
            )
            call = other_call
        with pytest.raises((RuntimeError, ValueError)):
            bundle.durability.append_prepared_self_delivery_policy(call, proposal)
        assert bundle.durability._journal.entries() == before_entries
        assert bundle.materializer.records() == before_records


def test_append_rejects_forged_snapshot_before_writing(tmp_path: Path) -> None:
    with _bundle(tmp_path) as bundle:
        _bootstrap(bundle)
        call, proposal = _issue(bundle)
        forged = call.model_copy(update={"snapshot_bytes": b"forged snapshot"})
        forged_proposal = proposal.model_copy(
            update={"call_sha256": hashlib.sha256(forged.canonical_bytes()).hexdigest()}
        )
        before_entries = bundle.durability._journal.entries()
        before_records = bundle.materializer.records()

        with pytest.raises((RuntimeError, ValueError), match="snapshot"):
            bundle.durability.append_prepared_self_delivery_policy(forged, forged_proposal)

        assert bundle.durability._journal.entries() == before_entries
        assert bundle.materializer.records() == before_records


def test_append_rejects_changed_full_physical_policy_anchor_before_writing(
    tmp_path: Path,
) -> None:
    with _bundle(tmp_path) as bundle:
        _bootstrap(bundle)
        issue_call, issue_proposal = _issue(bundle)
        bundle.durability.append_prepared_self_delivery_policy(issue_call, issue_proposal)
        issued = bundle.durability.latest_prepared_self_delivery_policy(
            "tenant", "database", "policy"
        )
        assert issued is not None
        call, proposal = _revoke(bundle, issued)
        changed_anchor = issued.anchor.model_copy(
            update={"decision": _head("different-physical-decision")}
        )
        changed_call = call.model_copy(update={"latest_policy_anchor": changed_anchor})
        changed_proposal = proposal.model_copy(
            update={"call_sha256": hashlib.sha256(changed_call.canonical_bytes()).hexdigest()}
        )
        before_entries = bundle.durability._journal.entries()
        before_records = bundle.materializer.records()

        with pytest.raises((RuntimeError, ValueError), match=r"physical|policy|CAS"):
            bundle.durability.append_prepared_self_delivery_policy(changed_call, changed_proposal)

        assert bundle.durability._journal.entries() == before_entries
        assert bundle.materializer.records() == before_records


@pytest.mark.parametrize("failure", ("invalid-signature", "revoked-pin"))
def test_append_rejects_invalid_or_revoked_protected_key_before_writing(
    tmp_path: Path, failure: str
) -> None:
    with _bundle(tmp_path) as bundle:
        _bootstrap(bundle)
        signing_key = (
            Ed25519PrivateKey.generate() if failure == "invalid-signature" else _OPERATOR_KEY
        )
        call, proposal = _issue(bundle, signing_key=signing_key)
        if failure == "revoked-pin":
            _provision_operator_key_pin(bundle.gate, status="REVOKED")
        before_entries = bundle.durability._journal.entries()
        before_records = bundle.materializer.records()

        with pytest.raises((RuntimeError, ValueError)):
            bundle.durability.append_prepared_self_delivery_policy(call, proposal)

        assert bundle.durability._journal.entries() == before_entries
        assert bundle.materializer.records() == before_records


def test_lineage_rejects_altered_materialized_record_source_or_policy(tmp_path: Path) -> None:
    with _bundle(tmp_path) as bundle:
        _bootstrap(bundle)
        call, proposal = _issue(bundle)
        bundle.durability.append_prepared_self_delivery_policy(call, proposal)
        entries = bundle.durability._journal.entries()

        with pytest.raises(RuntimeError):
            authenticated_prepared_self_delivery_policy_lineage(entries, lambda *_: b"altered")

        decision_id, predecessor, raw = entries[-1]
        envelope = json.loads(raw)
        for field in ("operator_source", "policy"):
            altered = json.loads(json.dumps(envelope))
            altered["payload"][field] = {} if field == "operator_source" else {"revision": 99}
            altered_entries = (
                *entries[:-1],
                (
                    decision_id,
                    predecessor,
                    json.dumps(altered, sort_keys=True, separators=(",", ":")).encode(),
                ),
            )
            with pytest.raises(RuntimeError):
                authenticated_prepared_self_delivery_policy_lineage(
                    altered_entries, bundle.materializer.record
                )
