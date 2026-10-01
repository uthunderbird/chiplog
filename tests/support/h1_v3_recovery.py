"""Shared mounted recovery steps for selected SCOPED_V3 tests."""

from __future__ import annotations

import hashlib
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    HermeticTrustObservationV1,
)
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
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_completion_exchange_registry import H1CompletionExchangeRegistry
from chiplog.composition.h1_postseal_recovery import (
    H1PostSealRecoveryState,
    H1PostSealRecoveryTransition,
)
from chiplog.composition.h1_postseal_recovery_source import H1PostSealRecoveryRootSource
from chiplog.composition.h1_selected_output_sources import H1SelectedOutputSources
from chiplog.composition.h1_selected_prepare import select_h1_v3_prepare_for_candidate
from chiplog.composition.h1_v2_recovery_native_source import H1V2RecoveryNativeSource
from tests.support.h1_cli_execution import _admit
from tests.support.h1_installed_launch import DATABASE, TENANT

_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))


def _trust(runtime: Any) -> HermeticTrustObservationV1:
    runtime._trust.capture_verified_observation()
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
        source_id="policy-source:" + request.command_id,
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


async def _selected_scoped_root(runtime: Any) -> tuple[Any, Any, Any]:
    request = await _admit(runtime)
    initial = await runtime.drive_input(request)
    runtime._execution_model._responses = (
        DeliveryCompletion(
            tenant=request.identity.tenant_id,
            run_id=initial.stable_run_lineage_id,
            turn_id=initial.stable_run_lineage_id + "/turn/1",
            deliveries=(ProposedDelivery(payload=(Commentary(text="selected V3 publication"),)),),
        ).canonical_bytes(),
    )
    started = await runtime.begin_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
    )
    captured = await runtime.capture_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, started.head
    )
    assert select_h1_v3_prepare_for_candidate(
        runtime, captured, expected_head=captured.head
    ).decision_id
    sealed = await runtime.seal_execution_complete(
        "hermetic-ingress", initial.stable_run_lineage_id, captured.head, profile="H1_V2"
    )
    assert sealed.state == "ACTIVE"
    locator = H1V2RecoveryNativeSource(runtime).locate_selected_seal(
        original_identity=request.identity,
        original_fingerprint=request.original_driver_command_fingerprint(),
    )
    root = H1PostSealRecoveryRootSource(runtime).derive_on_restart(
        request.identity, request.original_driver_command_fingerprint(), locator
    )
    receipt = runtime._h1_postseal_recovery_journal.append_transition(
        H1PostSealRecoveryTransition.begin_selected(
            H1PostSealRecoveryState.empty(root), producer_choice="SCOPED_V3"
        ),
        expected_global_tip=runtime._h1_postseal_recovery_journal.scan().tip,
    )
    return request, locator, receipt.scan.state_for_root(root.root_id())


async def _recover_b_prefix(
    runtime: CommonCliExecutionRuntime, request: Any, locator: Any, state: Any
) -> Any:
    coordinator = runtime._h1_postseal_recovery_coordinator
    assert coordinator is not None
    source = coordinator._source
    results: dict[str, bytes] = {}
    context: object | None = None
    session: Any | None = None
    async with await coordinator._fence.acquire() as lease:
        try:
            context = source._capture_recovery(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=locator,
                root=state.root,
            )
            session = coordinator._open_recovery_session(
                source=source, context=context, lease=lease
            )
            session._bind_recovery(source=source, context=context, lease=lease)
            for stage, method in (
                ("COMPLETION", "prepare_first_path_completion"),
                ("CONVERSATION", "prepare_conversation_completion"),
            ):
                semantic = source._reconstruct_input(
                    context, stage, results, None, predecessor_effects_input=None
                )
                state = coordinator._append_stage_held(
                    lease,
                    state,
                    H1PostSealRecoveryTransition.pin_input(
                        state, stage=stage, semantic_input=semantic
                    ),
                )
                session._bind_recovery_stage(
                    stage=stage, semantic_input=semantic, predecessor_effects_input=None
                )
                result = (await getattr(session, method)()).returned.canonical_payload
                source._validate_result(context, stage, semantic, result)
                state = coordinator._append_stage_held(
                    lease,
                    state,
                    H1PostSealRecoveryTransition.commit_result(
                        state, stage=stage, result_bytes=result
                    ),
                )
                results[stage] = result
            return state
        finally:
            if session is not None:
                await coordinator._drain_session_held(lease, session)
            if context is not None:
                source._retire_recovery_context(context)


async def _issue_reconstructed_grant(
    runtime: CommonCliExecutionRuntime,
    request: Any,
    locator: Any,
    *,
    authorization_horizon_ns: int = 1_200_000_000_000,
) -> None:
    if type(authorization_horizon_ns) is not int or authorization_horizon_ns <= 0:
        raise ValueError("reconstructed grant authorization horizon must be positive")
    registry = runtime._h1_completion_exchange_registry
    assert type(registry) is H1CompletionExchangeRegistry
    historical = registry.capture_recovered_prepared_delivery_historical(
        original_identity=request.identity,
        original_fingerprint=request.original_driver_command_fingerprint(),
        selected_seal=locator,
    )
    projection = registry.replay_prepared_delivery_historical(historical)
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
        expires_at_ns=now_ns + authorization_horizon_ns,
    )
    policy = await runtime.authorize_prepared_self_delivery_policy(
        _signed_policy(
            IssuePreparedExternalSelfDeliveryPolicyRequestV1(
                operation="ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
                command_id="recovered-policy",
                tenant_id=TENANT,
                database_id=DATABASE,
                policy_id="policy",
                expected_trust_observation=_trust(runtime),
                expected_policy=None,
                terms=terms,
            )
        ),
        request_id="recovered-policy",
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
    issued = await runtime.authorize_prepared_external_delivery_grant(
        _signed_grant(
            IssuePreparedExternalDeliveryGrantRequestV2(
                operation="ISSUE_PREPARED_EXTERNAL_DELIVERY_GRANT",
                command_id="recovered-grant",
                tenant_id=TENANT,
                database_id=DATABASE,
                grant_id="recovered-grant",
                policy_id="policy",
                expected_trust_observation=_trust(runtime),
                expected_grant=None,
                selected_policy_anchor=policy.anchor,
                canonical_selected_policy_bytes=policy.policy.canonical_bytes(),
                proposed_scope=scope,
            )
        ),
        request_id="recovered-grant",
        original_identity=request.identity,
        original_fingerprint=request.original_driver_command_fingerprint(),
        selected_seal=locator,
    )
    assert issued.grant.status == "ACTIVE"
