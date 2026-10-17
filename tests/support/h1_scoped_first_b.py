"""Self-contained installed-v20 fixture for an H1 scoped first-B authority."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.deployment_trust.operator_grant_authorization_contracts import (
    OperatorGrantAuthorizationPayloadV2,
    SignedOperatorGrantAuthorizationV2,
)
from chiplog.capabilities.deployment_trust.operator_policy_authorization_contracts import (
    OperatorPolicyAuthorizationPayloadV1,
    SignedOperatorPolicyAuthorizationV1,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_contracts import (
    BoundedExternalSelfSendMandateV1,
    ExternalDeliveryResourcesV1,
    IssuePreparedExternalDeliveryGrantRequestV2,
    PreparedExternalDeliveryGrantScopeV2,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_grant_owner_contracts import (
    prepared_delivery_basis_head_v3,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_policy_contracts import (
    IssuePreparedExternalSelfDeliveryPolicyRequestV1,
    PreparedExternalSelfDeliveryPolicyTermsV1,
)
from chiplog.composition.h1_scoped_delivery_authority import H1ScopedDeliveryAuthorityReader
from chiplog.composition.h1_selected_output_sources import H1SelectedOutputSources
from chiplog.composition.h1_selected_prepare import select_h1_v3_prepare_for_candidate
from chiplog.composition.r14_execution_complete_seal_records import RetainedExecutionCompleteSealV3
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.operator_grant_key_pin import OperatorGrantKeyPinFileV2
from chiplog.platform.operator_policy_key_pin import OperatorPolicyKeyPinFileV1
from tests.support.h1_cli_execution import _admit
from tests.support.h1_installed_launch import DATABASE, TENANT

_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))


def scoped_resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


def write_scoped_pins(database: Path) -> None:
    policy = OperatorPolicyKeyPinFileV1(
        tenant_id=TENANT,
        database_id=DATABASE,
        operator_key_id="operator-key",
        policy_id="policy",
        allowed_operations=("ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",),
        status="ACTIVE",
        public_key=_KEY.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        ),
    )
    policy_path = database.with_suffix(database.suffix + ".operator-policy-key.json")
    policy_path.write_bytes(policy.canonical_bytes())
    policy_path.chmod(0o600)
    grant = OperatorGrantKeyPinFileV2(
        tenant_id=TENANT,
        database_id=DATABASE,
        operator_key_id="operator-key",
        policy_id="policy",
        allowed_operations=("ISSUE_PREPARED_EXTERNAL_DELIVERY_GRANT",),
        status="ACTIVE",
        public_key=_KEY.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        ),
    )
    grant_path = database.with_suffix(database.suffix + ".operator-grant-key.json")
    grant_path.write_bytes(grant.canonical_bytes())
    grant_path.chmod(0o600)


def _trust_observation(runtime: Any) -> Any:
    from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
    from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
        HermeticTrustObservationV1,
    )

    decision_id, _, raw = runtime._trust._journal.entries()[-1]
    return HermeticTrustObservationV1(
        physical_journal_head=ExactHead(
            identity="deployment-trust/journal",
            head=decision_id,
            fingerprint=hashlib.sha256(raw).hexdigest(),
        ),
        logical_snapshot_head=runtime._trust.owner_snapshot_entries()[-1][0],
    )


def _signed_policy(request: IssuePreparedExternalSelfDeliveryPolicyRequestV1) -> bytes:
    raw = request.canonical_bytes()
    payload = OperatorPolicyAuthorizationPayloadV1(
        algorithm="Ed25519",
        source_id="source:" + request.command_id,
        operator_key_id="operator-key",
        tenant_id=request.tenant_id,
        database_id=request.database_id,
        policy_id=request.policy_id,
        command_id=request.command_id,
        operation=request.operation,
        request_sha256=hashlib.sha256(raw).hexdigest(),
        canonical_request_bytes=raw,
    )

    return SignedOperatorPolicyAuthorizationV1(
        payload=payload, signature=_KEY.sign(payload.canonical_bytes())
    ).canonical_bytes()


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


async def _completed_session(runtime: Any) -> tuple[Any, Any]:
    request = await _admit(runtime)
    initial = await runtime.drive_input(request)
    completion = DeliveryCompletion(
        tenant=TENANT,
        run_id=initial.stable_run_lineage_id,
        turn_id=initial.stable_run_lineage_id + "/turn/1",
        deliveries=(ProposedDelivery(payload=(Commentary(text="scoped first-B"),)),),
    )
    runtime._execution_model._responses = (completion.canonical_bytes(),)
    started = await runtime.begin_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
    )
    captured = await runtime.capture_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, started.head
    )
    selected = select_h1_v3_prepare_for_candidate(runtime, captured, expected_head=captured.head)
    port = runtime._h1_preissuance_registration_source_port
    assert port is not None
    await port.verify_selected_original(selected)
    sealed = await runtime.seal_execution_complete(
        "hermetic-ingress", initial.stable_run_lineage_id, captured.head, profile="H1_V3"
    )
    decision = next(
        json.loads(raw)
        for _, _, raw in runtime._loop_decisions().entries()
        if json.loads(raw).get("operation_id") == sealed.head
    )
    response_seal = RetainedExecutionCompleteSealV3.model_validate_json(
        decision["execution_complete_seal"]
    ).exchange.proposal.fan_out.response_seal
    seal = CallSubjectHead(
        subject_id=response_seal.response_seal_id,
        revision=Present(
            head="record:" + response_seal.digest(), fingerprint=response_seal.digest()
        ),
    )
    enrollment = runtime._h1_live_completion_enrollment
    assert enrollment is not None
    session = enrollment._open_session()
    session.capture_first_path(
        original_identity=request.identity,
        original_fingerprint=request.original_driver_command_fingerprint(),
        selected_seal=seal,
    )
    await session.prepare_first_path_completion()
    return request, seal


async def issue_real_h1_scoped_authority(
    runtime: Any,
) -> tuple[H1ScopedDeliveryAuthorityReader, Any]:
    """Issue a real prepared policy/grant and return an authority-reader capture."""
    request, seal = await _completed_session(runtime)
    registry = runtime._h1_completion_exchange_registry
    assert registry is not None
    historical = registry.capture_prepared_delivery_historical(
        original_identity=request.identity,
        original_fingerprint=request.original_driver_command_fingerprint(),
        selected_seal=seal,
    )
    current = registry.capture_prepared_delivery_current(
        original_identity=request.identity,
        original_fingerprint=request.original_driver_command_fingerprint(),
        selected_seal=seal,
    )
    projection = registry.replay_prepared_delivery_current(current)
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
    policy = await runtime.authorize_prepared_self_delivery_policy(
        _signed_policy(
            IssuePreparedExternalSelfDeliveryPolicyRequestV1(
                operation="ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
                command_id="policy-for-grant",
                tenant_id=TENANT,
                database_id=DATABASE,
                policy_id="policy",
                expected_trust_observation=_trust_observation(runtime),
                expected_policy=None,
                terms=terms,
            )
        ),
        request_id="grant-policy",
    )
    scope = PreparedExternalDeliveryGrantScopeV2(
        principal_id=projection.principal_id,
        worker_session_id=projection.worker_session_id,
        contour_head=projection.selected_scope.scope.contour_head,
        authenticated_credential_head=projection.selected_scope.scope.authenticated_cli_state.credential_head,
        authenticated_session_head=projection.selected_scope.scope.authenticated_cli_state.session_head,
        selected_source=selected.selected_source,
        resources=ExternalDeliveryResourcesV1(
            signature_domain="dispatch-resources.v1",
            signed_observation_fingerprint=projection.selected_scope.scope.selected_resource_observation_ref.signed_observation_fingerprint,
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
    await runtime.authorize_prepared_external_delivery_grant(
        _signed_grant(issue),
        request_id="grant-issue",
        original_identity=request.identity,
        original_fingerprint=request.original_driver_command_fingerprint(),
        selected_seal=seal,
    )
    reader = runtime._h1_scoped_delivery_authority_reader
    assert type(reader) is H1ScopedDeliveryAuthorityReader
    return reader, await reader.read(historical_b_capture=historical, grant_id="grant")


__all__ = ["issue_real_h1_scoped_authority", "scoped_resources", "write_scoped_pins"]
