"""Mounted ISSUE witness for the first J7 V2 prepared-delivery grant."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from chiplog.capabilities.deployment_trust.operator_grant_authorization_contracts import (
    OperatorGrantAuthorizationPayloadV2,
    SignedOperatorGrantAuthorizationV2,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_contracts import (
    BoundedExternalSelfSendMandateV1,
    ExternalDeliveryResourcesV1,
    IssuePreparedExternalDeliveryGrantRequestV2,
    ObservedPreparedExternalDeliveryGrantLifecycleV2,
    PreparedExternalDeliveryGrantScopeV2,
    ReadPreparedExternalDeliveryGrantLifecycleV2,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_grant_owner_contracts import (
    prepared_delivery_basis_head_v3,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_policy_contracts import (
    IssuePreparedExternalSelfDeliveryPolicyRequestV1,
    PreparedExternalSelfDeliveryPolicyTermsV1,
)
from chiplog.composition.common_cli_execution_runtime import open_installed_h1_runtime
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_selected_output_sources import H1SelectedOutputSources
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.operator_grant_key_pin import OperatorGrantKeyPinFileV2
from tests.composition.test_h1_prepared_delivery_basis_registry import _completed_session
from tests.platform.test_prepared_policy_owner_route import _authorize, _signed_source, _write_pin
from tests.support.h1_installed_launch import (
    DATABASE,
    TENANT,
    installed_slot,
    prepare_installed_slot,
)

_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
_CHANGED_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(33, 65)))


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


def _write_grant_pin(database: Path, key: Ed25519PrivateKey = _KEY) -> None:
    pin = OperatorGrantKeyPinFileV2(
        tenant_id=TENANT,
        database_id=DATABASE,
        operator_key_id="operator-key",
        policy_id="policy",
        allowed_operations=("ISSUE_PREPARED_EXTERNAL_DELIVERY_GRANT",),
        status="ACTIVE",
        public_key=key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        ),
    )
    path = database.with_suffix(database.suffix + ".operator-grant-key.json")
    path.write_bytes(pin.canonical_bytes())
    path.chmod(0o600)


def _trust_observation(runtime: object) -> object:
    from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
    from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
        HermeticTrustObservationV1,
    )

    trust = runtime._trust  # type: ignore[attr-defined]
    decision_id, _, raw = trust._journal.entries()[-1]
    return HermeticTrustObservationV1(
        physical_journal_head=ExactHead(
            identity="deployment-trust/journal",
            head=decision_id,
            fingerprint=hashlib.sha256(raw).hexdigest(),
        ),
        logical_snapshot_head=trust.owner_snapshot_entries()[-1][0],
    )


def _signed_grant(request: IssuePreparedExternalDeliveryGrantRequestV2) -> bytes:
    raw = request.canonical_bytes()
    payload = OperatorGrantAuthorizationPayloadV2(
        algorithm="Ed25519",
        source_id="grant-source:" + request.command_id,
        operator_key_id="operator-key",
        tenant_id=request.tenant_id,
        database_id=request.database_id,
        grant_id=request.grant_id,
        policy_id=request.policy_id,
        command_id=request.command_id,
        operation=request.operation,
        request_sha256=hashlib.sha256(raw).hexdigest(),
        canonical_request_bytes=raw,
    )
    return SignedOperatorGrantAuthorizationV2(
        payload=payload, signature=_KEY.sign(payload.canonical_bytes())
    ).canonical_bytes()


@pytest.mark.asyncio
async def test_mounted_j7_issue_appends_and_reopens_historical_lifecycle(tmp_path: Path) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    _write_pin(slot.database_path)
    _write_grant_pin(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request, seal, _session, _exchange = await _completed_session(runtime)
            registry = runtime._h1_completion_exchange_registry
            assert registry is not None
            capture = registry.capture_prepared_delivery_current(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=seal,
            )
            projection = registry.replay_prepared_delivery_current(capture)
            selected = H1SelectedOutputSources(runtime).capture_scope_selected_current(
                projection.selected_scope.scope
            )
            assert selected is not None
            epoch, now_ns = runtime._require_dispatch_resources().clock()
            terms = PreparedExternalSelfDeliveryPolicyTermsV1(
                principal_id=projection.principal_id,
                channel_id="hermetic-local",
                recipient=projection.delivery.selection.recipient,
                communication_permission="PREPARED_EXTERNAL_SEND",
                disclosure_permission="EXACT_RENDERED_PAYLOAD",
                self_recipient_semantics="OPERATOR_ATTESTED_SELF",
                payload_class="NonAuthoritativeText",
                payload_digest=projection.delivery.render_digest,
                payload_byte_length=len(projection.delivery.rendered_bytes),
                source_classes=("CLI",),
                selection_modes=("ORIGIN_EXACT",),
                external_delivery=True,
                max_calls=1,
                clock_contract="chiplog.dispatch.monotonic.v2",
                clock_epoch=epoch,
                not_before_ns=now_ns - 1,
                expires_at_ns=now_ns + 300_000_000_000,
            )
            policy_request = IssuePreparedExternalSelfDeliveryPolicyRequestV1(
                operation="ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
                command_id="policy-for-grant",
                tenant_id=TENANT,
                database_id=DATABASE,
                policy_id="policy",
                expected_trust_observation=_trust_observation(runtime),
                expected_policy=None,
                terms=terms,
            )
            policy = await _authorize(
                runtime, _signed_source(policy_request), request_id="grant-policy"
            )
            scope = PreparedExternalDeliveryGrantScopeV2(
                principal_id=projection.principal_id,
                worker_session_id=projection.worker_session_id,
                contour_head=projection.selected_scope.scope.contour_head,
                authenticated_credential_head=(
                    projection.selected_scope.scope.authenticated_cli_state.credential_head
                ),
                authenticated_session_head=(
                    projection.selected_scope.scope.authenticated_cli_state.session_head
                ),
                selected_source=selected.selected_source,
                resources=ExternalDeliveryResourcesV1(
                    signature_domain="dispatch-resources.v1",
                    signed_observation_fingerprint=(
                        projection.selected_scope.scope.selected_resource_observation_ref.signed_observation_fingerprint
                    ),
                    resource_grant=selected.resource_grant,
                    recipient=projection.delivery.selection.recipient,
                    clock_epoch=epoch,
                ),
                mandate=BoundedExternalSelfSendMandateV1(
                    purpose="PREPARED_EXTERNAL_SELF_SEND",
                    external_delivery=True,
                    selection="ORIGIN_EXACT",
                    payload_class="NonAuthoritativeText",
                    communication_authority=policy.anchor.policy,
                    disclosure_authority=policy.anchor.policy,
                    self_recipient_binding=policy.anchor.policy,
                    original_run=projection.original_run,
                    captured_attempt=projection.captured_attempt,
                    preparation_basis=prepared_delivery_basis_head_v3(projection.basis),
                    delivery_id=projection.delivery.delivery_id,
                    payload_digest=projection.delivery.render_digest,
                    payload_byte_length=len(projection.delivery.rendered_bytes),
                    max_calls=1,
                    clock_contract="chiplog.dispatch.monotonic.v2",
                    clock_epoch=epoch,
                    not_before_ns=terms.not_before_ns,
                    expires_at_ns=terms.expires_at_ns,
                ),
            )
            issue = IssuePreparedExternalDeliveryGrantRequestV2(
                operation="ISSUE_PREPARED_EXTERNAL_DELIVERY_GRANT",
                command_id="grant-issue",
                tenant_id=TENANT,
                database_id=DATABASE,
                grant_id="grant",
                policy_id="policy",
                expected_trust_observation=_trust_observation(runtime),
                expected_grant=None,
                selected_policy_anchor=policy.anchor,
                canonical_selected_policy_bytes=policy.policy.canonical_bytes(),
                proposed_scope=scope,
            )
            before = runtime._trust._journal.entries()
            issued = await runtime.authorize_prepared_external_delivery_grant(
                _signed_grant(issue),
                request_id="grant-issue",
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=seal,
            )
            assert issued.grant.status == "ACTIVE"
            assert runtime._trust._journal.entries()[:-1] == before
            after_issue = runtime._trust._journal.entries()
            forged = issue.model_copy(
                update={
                    "proposed_scope": scope.model_copy(
                        update={"worker_session_id": "forged"}
                    )
                }
            )
            with pytest.raises(PermissionError):
                await runtime.authorize_prepared_external_delivery_grant(
                    _signed_grant(forged),
                    request_id="grant-forged",
                    original_identity=request.identity,
                    original_fingerprint=request.original_driver_command_fingerprint(),
                    selected_seal=seal,
                )
            assert runtime._trust._journal.entries() == after_issue
            _write_grant_pin(slot.database_path, _CHANGED_KEY)
            with pytest.raises(PermissionError, match="operator grant pin is not current"):
                await runtime.authorize_prepared_external_delivery_grant(
                    _signed_grant(issue),
                    request_id="grant-changed-pin",
                    original_identity=request.identity,
                    original_fingerprint=request.original_driver_command_fingerprint(),
                    selected_seal=seal,
                )
            assert runtime._trust._journal.entries() == after_issue

        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as reopened:
            observed = reopened.read_prepared_external_delivery_grant_lifecycle(
                ReadPreparedExternalDeliveryGrantLifecycleV2(
                    expected_trust_observation=_trust_observation(reopened),
                    source_anchor=issued.anchor,
                    expected_grant=issued.grant,
                )
            )
            assert isinstance(observed, ObservedPreparedExternalDeliveryGrantLifecycleV2)
