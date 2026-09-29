"""Mounted witnesses for the J7 prepared-policy owner route.

These tests use the installed H1 runtime, rather than calling trust durability
directly: the public broker method alone may select an owner result and make
its proposal durable.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Literal

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import TypeAdapter

from chiplog.architecture.r7_runtime import R14_R17_H1_LOCAL_EFFECTS_J7_PRODUCTION_MANIFEST
from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead, ProviderRecipient
from chiplog.capabilities.deployment_trust._j7_process import make_dispatch
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    HermeticTrustObservationV1,
)
from chiplog.capabilities.deployment_trust.operator_policy_authorization_contracts import (
    OperatorPolicyAuthorizationPayloadV1,
    SignedOperatorPolicyAuthorizationV1,
)
from chiplog.capabilities.deployment_trust.operator_policy_command_verifier import (
    OperatorPolicyKeyBindingV1,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_policy_contracts import (
    IssuePreparedExternalSelfDeliveryPolicyRequestV1,
    PreparedExternalSelfDeliveryPolicyAnchorV1,
    PreparedExternalSelfDeliveryPolicyTermsV1,
    RevokePreparedExternalSelfDeliveryPolicyRequestV1,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_policy_owner_contracts import (  # noqa: E501
    AuthorizePreparedSelfDeliveryPolicyCallV1,
    PreparedSelfDeliveryPolicyProposalV1,
    PreparedSelfDeliveryPolicyRejectedV1,
    PreparedSelfDeliveryPolicyResultV1,
)
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
    open_installed_h1_runtime,
)
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.operator_policy_key_pin import OperatorPolicyKeyPinFileV1
from tests.support.h1_installed_launch import (
    DATABASE,
    TENANT,
    installed_slot,
    prepare_installed_slot,
)

_OPERATOR_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
_FOREIGN_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(33, 65)))


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


def _head(identity: str, body: bytes = b"j7") -> ExactHead:
    return ExactHead(
        identity=identity,
        head=f"{identity}/{hashlib.sha256(body).hexdigest()}",
        fingerprint=hashlib.sha256(body).hexdigest(),
    )


def _terms() -> PreparedExternalSelfDeliveryPolicyTermsV1:
    payload = b"prepared self delivery"
    return PreparedExternalSelfDeliveryPolicyTermsV1(
        principal_id="hermetic-principal",
        channel_id="hermetic-local",
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


def _pin_path(database: Path) -> Path:
    return database.with_suffix(database.suffix + ".operator-policy-key.json")


def _write_pin(database: Path, *, status: Literal["ACTIVE", "REVOKED"] = "ACTIVE") -> Path:
    pin = OperatorPolicyKeyPinFileV1(
        tenant_id=TENANT,
        database_id=DATABASE,
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
    path = _pin_path(database)
    path.write_bytes(pin.canonical_bytes())
    path.chmod(0o600)
    return path


def _observation(runtime: CommonCliExecutionRuntime) -> HermeticTrustObservationV1:
    frozen = runtime._trust.capture_verified_observation()
    entries = runtime._trust._journal.entries()
    logical = runtime._trust.owner_snapshot_entries()
    assert frozen.snapshot_bytes and entries and logical
    decision_id, _, raw = entries[-1]
    return HermeticTrustObservationV1(
        physical_journal_head=ExactHead(
            identity="deployment-trust/journal",
            head=decision_id,
            fingerprint=hashlib.sha256(raw).hexdigest(),
        ),
        logical_snapshot_head=logical[-1][0],
    )


def _signed_source(
    request: IssuePreparedExternalSelfDeliveryPolicyRequestV1
    | RevokePreparedExternalSelfDeliveryPolicyRequestV1,
    *,
    signing_key: Ed25519PrivateKey = _OPERATOR_KEY,
) -> bytes:
    request_bytes = request.canonical_bytes()
    payload = OperatorPolicyAuthorizationPayloadV1(
        algorithm="Ed25519",
        source_id="source:" + request.command_id,
        operator_key_id="operator-key",
        tenant_id=request.tenant_id,
        database_id=request.database_id,
        policy_id=request.policy_id,
        command_id=request.command_id,
        operation=request.operation,
        request_sha256=hashlib.sha256(request_bytes).hexdigest(),
        canonical_request_bytes=request_bytes,
    )
    return SignedOperatorPolicyAuthorizationV1(
        payload=payload, signature=signing_key.sign(payload.canonical_bytes())
    ).canonical_bytes()


def _issue(
    runtime: CommonCliExecutionRuntime,
    *,
    command_id: str,
    observation: HermeticTrustObservationV1 | None = None,
    signing_key: Ed25519PrivateKey = _OPERATOR_KEY,
) -> bytes:
    request = IssuePreparedExternalSelfDeliveryPolicyRequestV1(
        operation="ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
        command_id=command_id,
        tenant_id=TENANT,
        database_id=DATABASE,
        policy_id="policy",
        expected_trust_observation=observation or _observation(runtime),
        expected_policy=None,
        terms=_terms(),
    )
    return _signed_source(request, signing_key=signing_key)


def _revoke(
    runtime: CommonCliExecutionRuntime, anchor: PreparedExternalSelfDeliveryPolicyAnchorV1
) -> bytes:
    request = RevokePreparedExternalSelfDeliveryPolicyRequestV1(
        operation="REVOKE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
        command_id="revoke-command",
        tenant_id=TENANT,
        database_id=DATABASE,
        policy_id="policy",
        expected_trust_observation=_observation(runtime),
        expected_policy=anchor,
    )
    return _signed_source(request)


async def _authorize(
    runtime: CommonCliExecutionRuntime, source: bytes, *, request_id: str
) -> object:
    return await runtime.authorize_prepared_self_delivery_policy(source, request_id=request_id)


def _durable_state(runtime: CommonCliExecutionRuntime) -> tuple[object, object]:
    return runtime._trust._journal.entries(), runtime._trust._materializer.records()


async def _reject(runtime: CommonCliExecutionRuntime, source: bytes, *, request_id: str) -> None:
    with pytest.raises(PermissionError):
        await runtime.authorize_prepared_self_delivery_policy(source, request_id=request_id)


def _owner_call(
    *,
    request_observation: HermeticTrustObservationV1 | None = None,
    signing_key: Ed25519PrivateKey = _OPERATOR_KEY,
) -> AuthorizePreparedSelfDeliveryPolicyCallV1:
    envelope = {"kind": "J7_TEST", "payload": {}, "predecessor": None}
    envelope_bytes = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    logical_head = hashlib.sha256(b"GENESIS\x00" + envelope_bytes).hexdigest()
    snapshot_bytes = json.dumps(
        [[logical_head, None, base64.b64encode(envelope_bytes).decode()]],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    observation = HermeticTrustObservationV1(
        physical_journal_head=_head("j7-owner-wire", envelope_bytes),
        logical_snapshot_head=logical_head,
    )
    request = IssuePreparedExternalSelfDeliveryPolicyRequestV1(
        operation="ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
        command_id="j7-owner-wire",
        tenant_id=TENANT,
        database_id=DATABASE,
        policy_id="policy",
        expected_trust_observation=request_observation or observation,
        expected_policy=None,
        terms=_terms(),
    )
    return AuthorizePreparedSelfDeliveryPolicyCallV1(
        canonical_signed_source_bytes=_signed_source(request, signing_key=signing_key),
        snapshot_bytes=snapshot_bytes,
        expected_trust_observation=observation,
        latest_policy_anchor=None,
        latest_policy_bytes=None,
    )


def _owner_result(
    call: AuthorizePreparedSelfDeliveryPolicyCallV1, *, binding_bytes: bytes | None = None
) -> PreparedSelfDeliveryPolicyResultV1:
    response = make_dispatch(binding_bytes)(
        "deployment_trust.authorize_prepared_external_self_delivery_policy", call.canonical_bytes()
    )
    assert response["schema_id"] == "chiplog.deployment-trust.self-delivery-policy-result.v1"
    raw = base64.b64decode(str(response["payload"]), validate=True)
    result = TypeAdapter(PreparedSelfDeliveryPolicyResultV1).validate_json(raw)
    assert result.canonical_bytes() == raw
    assert result.schema_id == response["schema_id"]
    assert result.call_sha256 == hashlib.sha256(call.canonical_bytes()).hexdigest()
    return result


def _active_binding_bytes() -> bytes:
    digest = hashlib.sha256(b"j7-test-binding").hexdigest()
    return OperatorPolicyKeyBindingV1(
        ref=ExactHead(identity="operator-key", head=digest, fingerprint=digest),
        tenant_id=TENANT,
        database_id=DATABASE,
        operator_key_id="operator-key",
        policy_id="policy",
        allowed_operations=(
            "ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
            "REVOKE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
        ),
        status="ACTIVE",
        public_key=_OPERATOR_KEY.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        ),
    ).canonical_bytes()


def test_owner_denial_raw_wire_is_canonical_schema_bound_and_call_bound() -> None:
    result = _owner_result(_owner_call(signing_key=_FOREIGN_KEY))
    assert isinstance(result, PreparedSelfDeliveryPolicyRejectedV1)
    assert result.disposition == "DENIED"


def test_owner_uses_canonical_startup_binding_for_a_valid_signed_call() -> None:
    result = _owner_result(_owner_call(), binding_bytes=_active_binding_bytes())
    assert isinstance(result, PreparedSelfDeliveryPolicyProposalV1)
    assert result.policy.status == "ACTIVE"


def test_j7_manifest_admits_the_single_discriminated_result_schema() -> None:
    route = next(
        item
        for item in R14_R17_H1_LOCAL_EFFECTS_J7_PRODUCTION_MANIFEST.routes
        if item.operation_id == "deployment_trust.authorize_prepared_external_self_delivery_policy"
    )
    assert route.result_schema_id == "chiplog.deployment-trust.self-delivery-policy-result.v1"


def test_owner_stale_raw_wire_is_canonical_schema_bound_and_call_bound() -> None:
    stale_observation = HermeticTrustObservationV1(
        physical_journal_head=_head("stale-owner-wire"), logical_snapshot_head="stale-logical"
    )
    result = _owner_result(_owner_call(request_observation=stale_observation))
    assert isinstance(result, PreparedSelfDeliveryPolicyRejectedV1)
    assert result.disposition == "STALE"


@pytest.mark.asyncio
async def test_installed_owner_route_issues_then_revokes_one_physically_anchored_policy(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    _write_pin(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            before_journal, before_records = _durable_state(runtime)
            issued = await _authorize(
                runtime, _issue(runtime, command_id="issue-command"), request_id="j7-issue"
            )
            assert issued.policy.status == "ACTIVE"
            after_issue_journal, after_issue_records = _durable_state(runtime)
            assert after_issue_journal[:-1] == before_journal
            assert after_issue_records[:-2] == before_records
            assert len(after_issue_journal) == len(before_journal) + 1
            assert len(after_issue_records) == len(before_records) + 2
            assert issued.anchor.decision.head == after_issue_journal[-1][0]

            revoked = await _authorize(
                runtime, _revoke(runtime, issued.anchor), request_id="j7-revoke"
            )
            assert revoked.policy.status == "REVOKED"
            assert revoked.policy.predecessor == issued.anchor.policy
            after_revoke_journal, after_revoke_records = _durable_state(runtime)
            assert after_revoke_journal[:-1] == after_issue_journal
            assert after_revoke_records[:-2] == after_issue_records
            assert len(after_revoke_journal) == len(after_issue_journal) + 1
            assert len(after_revoke_records) == len(after_issue_records) + 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ("invalid_signature", "missing_pin", "revoked_pin"))
async def test_installed_owner_route_rejects_bad_authority_without_durable_mutation(
    tmp_path: Path, failure: str
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    pin_path = _write_pin(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            source = _issue(
                runtime,
                command_id="bad-authority",
                signing_key=_FOREIGN_KEY if failure == "invalid_signature" else _OPERATOR_KEY,
            )
            if failure == "missing_pin":
                pin_path.unlink()
            elif failure == "revoked_pin":
                _write_pin(slot.database_path, status="REVOKED")
            before = _durable_state(runtime)
            await _reject(runtime, source, request_id="j7-" + failure)
            assert _durable_state(runtime) == before


@pytest.mark.asyncio
async def test_installed_owner_route_rejects_stale_trust_and_latest_anchor_without_mutation(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    _write_pin(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            initial = _observation(runtime)
            stale_trust = _issue(runtime, command_id="stale-trust", observation=initial)
            issued = await _authorize(
                runtime, _issue(runtime, command_id="issue-command"), request_id="j7-issue"
            )
            after_issue = _durable_state(runtime)

            await _reject(runtime, stale_trust, request_id="j7-stale-trust")
            assert _durable_state(runtime) == after_issue

            stale_latest = _issue(runtime, command_id="stale-latest")
            await _reject(runtime, stale_latest, request_id="j7-stale-latest")
            assert _durable_state(runtime) == after_issue
            assert issued.policy.status == "ACTIVE"
