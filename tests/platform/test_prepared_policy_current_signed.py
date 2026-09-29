"""Read fences for current J7 policies with retained signed ISSUE authority."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest
from cryptography.hazmat.primitives import serialization
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
from chiplog.capabilities.deployment_trust.operator_policy_command_verifier import (
    OperatorPolicyOperation,
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
)
from chiplog.platform.r7_trust_durability import BrokerTrustDurability

_OPERATOR_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
_FOREIGN_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(33, 65)))


@dataclass(frozen=True)
class _Bundle:
    gate: AuthorityGate
    durability: BrokerTrustDurability
    materializer: SQLiteTrustMaterializer
    journal_path: Path


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


def _provision_pin(
    gate: AuthorityGate,
    *,
    status: Literal["ACTIVE", "REVOKED"] = "ACTIVE",
    allowed_operations: tuple[OperatorPolicyOperation, ...] = (
        "ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
        "REVOKE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
    ),
) -> None:
    pin = OperatorPolicyKeyPinFileV1(
        tenant_id="tenant",
        database_id="database",
        operator_key_id="operator-key",
        policy_id="policy",
        allowed_operations=allowed_operations,
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
    journal_path = tmp_path / "trust.journal"
    journal = IndependentTenantDecisionJournal.for_authority_bundle(
        journal_path, authority_gate=gate
    )
    materializer = SQLiteTrustMaterializer.for_authority_bundle(
        tmp_path / "trust.sqlite", authority_gate=gate
    )
    _provision_pin(gate)
    try:
        yield _Bundle(
            gate,
            BrokerTrustDurability(journal, materializer, b"operator-secret"),
            materializer,
            journal_path,
        )
    finally:
        materializer.close()


def _bootstrap(bundle: _Bundle) -> None:
    snapshot = bundle.durability.capture_verified_observation()
    response = _evaluate(
        _TrustOwnerCall(
            mode="BOOTSTRAP",
            snapshot_bytes=snapshot.snapshot_bytes,
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
    decision_id, _, raw = bundle.durability._journal.entries()[-1]
    return (
        HermeticTrustObservationV1(
            physical_journal_head=ExactHead(
                identity="deployment-trust/journal",
                head=decision_id,
                fingerprint=hashlib.sha256(raw).hexdigest(),
            ),
            logical_snapshot_head=bundle.durability.owner_snapshot_entries()[-1][0],
        ),
        frozen.snapshot_bytes,
    )


def _source(
    request: IssuePreparedExternalSelfDeliveryPolicyRequestV1
    | RevokePreparedExternalSelfDeliveryPolicyRequestV1,
    key: Ed25519PrivateKey = _OPERATOR_KEY,
) -> RetainedOperatorPolicyAuthorizationSourceV1:
    request_bytes = request.canonical_bytes()
    payload = OperatorPolicyAuthorizationPayloadV1(
        algorithm="Ed25519",
        source_id="source-" + request.command_id,
        operator_key_id="operator-key",
        tenant_id=request.tenant_id,
        database_id=request.database_id,
        policy_id=request.policy_id,
        command_id=request.command_id,
        operation=request.operation,
        request_sha256=hashlib.sha256(request_bytes).hexdigest(),
        canonical_request_bytes=request_bytes,
    )
    source_bytes = SignedOperatorPolicyAuthorizationV1(
        payload=payload, signature=key.sign(payload.canonical_bytes())
    ).canonical_bytes()
    return RetainedOperatorPolicyAuthorizationSourceV1(
        ref=ExactHead(
            identity=payload.source_id,
            head=operator_policy_source_content_head(source_bytes),
            fingerprint=hashlib.sha256(source_bytes).hexdigest(),
        ),
        canonical_source_bytes=source_bytes,
    )


def _issue_request(
    bundle: _Bundle, *, command_id: str = "issue-command"
) -> tuple[IssuePreparedExternalSelfDeliveryPolicyRequestV1, bytes]:
    observation, snapshot = _observation(bundle)
    return (
        IssuePreparedExternalSelfDeliveryPolicyRequestV1(
            operation="ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
            command_id=command_id,
            tenant_id="tenant",
            database_id="database",
            policy_id="policy",
            expected_trust_observation=observation,
            expected_policy=None,
            terms=_terms(),
        ),
        snapshot,
    )


def _policy(
    request: IssuePreparedExternalSelfDeliveryPolicyRequestV1,
    source: RetainedOperatorPolicyAuthorizationSourceV1,
) -> PreparedExternalSelfDeliveryPolicyV1:
    request_bytes = request.canonical_bytes()
    return PreparedExternalSelfDeliveryPolicyV1(
        issuer="deployment_trust",
        tenant_id=request.tenant_id,
        database_id=request.database_id,
        policy_id=request.policy_id,
        revision=0,
        predecessor=None,
        status="ACTIVE",
        terms=request.terms,
        authorization_command=ExactHead(
            identity=request.command_id,
            head=prepared_self_delivery_policy_request_content_head(request_bytes),
            fingerprint=hashlib.sha256(request_bytes).hexdigest(),
        ),
        authorization_source=source.ref,
    )


def _append_issue(bundle: _Bundle) -> AuthenticatedPreparedSelfDeliveryPolicy:
    request, snapshot = _issue_request(bundle)
    source = _source(request)
    policy = _policy(request, source)
    call = AuthorizePreparedSelfDeliveryPolicyCallV1(
        canonical_signed_source_bytes=source.canonical_source_bytes,
        snapshot_bytes=snapshot,
        expected_trust_observation=request.expected_trust_observation,
        latest_policy_anchor=None,
        latest_policy_bytes=None,
    )
    proposal = PreparedSelfDeliveryPolicyProposalV1(
        call_sha256=hashlib.sha256(call.canonical_bytes()).hexdigest(),
        operator_source=source,
        policy=policy,
    )
    return bundle.durability.append_prepared_self_delivery_policy(call, proposal)


def test_current_signed_policy_returns_only_the_exact_current_active_anchor(tmp_path: Path) -> None:
    with _bundle(tmp_path) as bundle:
        _bootstrap(bundle)
        issued = _append_issue(bundle)

        assert (
            bundle.durability.current_signed_prepared_self_delivery_policy(issued.anchor)
            == issued
        )


def test_current_signed_policy_denies_superseded_and_revoked_anchors(tmp_path: Path) -> None:
    with _bundle(tmp_path) as bundle:
        _bootstrap(bundle)
        issued = _append_issue(bundle)
        observation, snapshot = _observation(bundle)
        request = RevokePreparedExternalSelfDeliveryPolicyRequestV1(
            operation="REVOKE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
            command_id="revoke-command",
            tenant_id="tenant",
            database_id="database",
            policy_id="policy",
            expected_trust_observation=observation,
            expected_policy=issued.anchor,
        )
        source = _source(request)
        revoked_policy = issued.policy.model_copy(
            update={
                "revision": 1,
                "predecessor": issued.anchor.policy,
                "status": "REVOKED",
                "authorization_command": ExactHead(
                    identity=request.command_id,
                    head=prepared_self_delivery_policy_request_content_head(request.canonical_bytes()),
                    fingerprint=hashlib.sha256(request.canonical_bytes()).hexdigest(),
                ),
                "authorization_source": source.ref,
            }
        )
        call = AuthorizePreparedSelfDeliveryPolicyCallV1(
            canonical_signed_source_bytes=source.canonical_source_bytes,
            snapshot_bytes=snapshot,
            expected_trust_observation=observation,
            latest_policy_anchor=issued.anchor,
            latest_policy_bytes=issued.policy.canonical_bytes(),
        )
        revoked = bundle.durability.append_prepared_self_delivery_policy(
            call,
            PreparedSelfDeliveryPolicyProposalV1(
                call_sha256=hashlib.sha256(call.canonical_bytes()).hexdigest(),
                operator_source=source,
                policy=revoked_policy,
            ),
        )

        with pytest.raises(RuntimeError, match="superseded"):
            bundle.durability.current_signed_prepared_self_delivery_policy(issued.anchor)
        with pytest.raises(RuntimeError, match="revoked"):
            bundle.durability.current_signed_prepared_self_delivery_policy(revoked.anchor)


@pytest.mark.parametrize(
    ("status", "allowed_operations"),
    (
        ("REVOKED", ("ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",)),
        ("ACTIVE", ("REVOKE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",)),
    ),
)
def test_current_signed_policy_rechecks_current_pin_permission(
    tmp_path: Path,
    status: Literal["ACTIVE", "REVOKED"],
    allowed_operations: tuple[OperatorPolicyOperation, ...],
) -> None:
    with _bundle(tmp_path) as bundle:
        _bootstrap(bundle)
        issued = _append_issue(bundle)
        _provision_pin(bundle.gate, status=status, allowed_operations=allowed_operations)

        with pytest.raises(RuntimeError, match="authorization is invalid"):
            bundle.durability.current_signed_prepared_self_delivery_policy(issued.anchor)


def test_current_signed_policy_rejects_forged_signature_despite_valid_trust_mac(
    tmp_path: Path,
) -> None:
    with _bundle(tmp_path) as bundle:
        _bootstrap(bundle)
        request, _ = _issue_request(bundle, command_id="forged-issue")
        source = _source(request, _FOREIGN_KEY)
        policy = _policy(request, source)
        with bundle.gate.hold():
            bundle.durability._locked_append(
                "PREPARED_SELF_DELIVERY_POLICY_V1",
                {
                    "operator_source": source.model_dump(mode="json"),
                    "policy": policy.model_dump(mode="json"),
                },
            )
        latest = bundle.durability.latest_prepared_self_delivery_policy(
            "tenant", "database", "policy"
        )
        assert latest is not None

        with pytest.raises(RuntimeError, match="authorization is invalid"):
            bundle.durability.current_signed_prepared_self_delivery_policy(latest.anchor)


def test_current_signed_policy_rejects_physically_tampered_trust_state(tmp_path: Path) -> None:
    with _bundle(tmp_path) as bundle:
        _bootstrap(bundle)
        issued = _append_issue(bundle)
        bundle.journal_path.write_bytes(b"tampered")
        bundle.journal_path.chmod(0o600)

        with pytest.raises(RuntimeError):
            bundle.durability.current_signed_prepared_self_delivery_policy(issued.anchor)
