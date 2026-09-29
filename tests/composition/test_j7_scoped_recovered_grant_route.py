"""Mounted J7 routing for a selected, but not yet reconstructed, SCOPED_V3 root."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, cast

import pytest
from cryptography.hazmat.primitives import serialization
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
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
    open_installed_h1_runtime,
)
from chiplog.composition.h1_completion_exchange_registry import (
    H1CompletionExchangeRegistry,
    H1CompletionExchangeRegistryViolation,
)
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_postseal_recovery import (
    H1PostSealRecoveryState,
    H1PostSealRecoveryTransition,
)
from chiplog.composition.h1_postseal_recovery_source import H1PostSealRecoveryRootSource
from chiplog.composition.h1_scoped_delivery_authority import (
    H1ScopedDeliveryAuthorityReader,
    H1ScopedDeliveryAuthorityViolation,
)
from chiplog.composition.h1_selected_output_sources import H1SelectedOutputSources
from chiplog.composition.h1_selected_prepare import select_h1_v3_prepare_for_candidate
from chiplog.composition.h1_v2_recovery_native_source import H1V2RecoveryNativeSource
from chiplog.composition.h1_v3_recovery_historical_source import H1V3RecoveryHistoricalSource
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.operator_grant_key_pin import OperatorGrantKeyPinFileV2
from chiplog.platform.operator_policy_key_pin import OperatorPolicyKeyPinFileV1
from tests.support.h1_cli_execution import _admit
from tests.support.h1_installed_launch import (
    DATABASE,
    TENANT,
    installed_slot,
    prepare_installed_slot,
)

_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))


def _write_policy_pin(database: Path) -> None:
    pin = OperatorPolicyKeyPinFileV1(
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
    path = database.with_suffix(database.suffix + ".operator-policy-key.json")
    path.write_bytes(pin.canonical_bytes())
    path.chmod(0o600)


def _write_grant_pin(database: Path) -> None:
    pin = OperatorGrantKeyPinFileV2(
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
    path = database.with_suffix(database.suffix + ".operator-grant-key.json")
    path.write_bytes(pin.canonical_bytes())
    path.chmod(0o600)


def _trust_observation(runtime: CommonCliExecutionRuntime) -> HermeticTrustObservationV1:
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


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


async def _seal_v3_then_v2_without_recovery(runtime: CommonCliExecutionRuntime) -> tuple[Any, Any]:
    """Commit the genuine selected V3 prepare and its V2 seal, without a ROOT."""
    private = cast(Any, runtime)
    request = await _admit(runtime)
    initial = cast(Any, await runtime.drive_input(request))
    private._execution_model._responses = (
        DeliveryCompletion(
            tenant=TENANT,
            run_id=initial.stable_run_lineage_id,
            turn_id=initial.stable_run_lineage_id + "/turn/1",
            deliveries=(ProposedDelivery(payload=(Commentary(text="scoped J7 recovery"),)),),
        ).canonical_bytes(),
    )
    started = await runtime.begin_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
    )
    captured = await runtime.capture_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, started.head
    )
    selected_prepare = select_h1_v3_prepare_for_candidate(
        runtime, captured, expected_head=captured.head
    )
    assert selected_prepare.decision_id
    sealed = await runtime.seal_execution_complete(
        "hermetic-ingress", initial.stable_run_lineage_id, captured.head, profile="H1_V2"
    )
    assert sealed.state == "ACTIVE"
    assert private._h1_postseal_recovery_journal.scan().states_by_root == ()
    return request, initial


async def _install_scoped_root(runtime: CommonCliExecutionRuntime) -> tuple[Any, Any, Any]:
    request, _initial = await _seal_v3_then_v2_without_recovery(runtime)
    locator = H1V2RecoveryNativeSource(runtime).locate_selected_seal(
        original_identity=request.identity,
        original_fingerprint=request.original_driver_command_fingerprint(),
    )
    root = H1PostSealRecoveryRootSource(runtime).derive_on_restart(
        request.identity, request.original_driver_command_fingerprint(), locator
    )
    journal = cast(Any, runtime)._h1_postseal_recovery_journal
    receipt = journal.append_transition(
        H1PostSealRecoveryTransition.begin_selected(
            H1PostSealRecoveryState.empty(root), producer_choice="SCOPED_V3"
        ),
        expected_global_tip=journal.scan().tip,
    )
    state = receipt.scan.state_for_root(root.root_id())
    assert state.selected_producer == "SCOPED_V3"
    assert state.root_evidence is not None
    return request, locator, state


async def _issue_reconstructed_grant(
    runtime: CommonCliExecutionRuntime, *, projection: Any, request: Any, locator: Any
) -> Any:
    """Issue the only policy and grant from one recovered B projection."""
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
        command_id="recovered-policy",
        tenant_id=TENANT,
        database_id=DATABASE,
        policy_id="policy",
        expected_trust_observation=_trust_observation(runtime),
        expected_policy=None,
        terms=terms,
    )
    policy = await runtime.authorize_prepared_self_delivery_policy(
        _signed_policy(policy_request), request_id="recovered-policy"
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
        command_id="recovered-grant",
        tenant_id=TENANT,
        database_id=DATABASE,
        grant_id="recovered-grant",
        policy_id="policy",
        expected_trust_observation=_trust_observation(runtime),
        expected_grant=None,
        selected_policy_anchor=policy.anchor,
        canonical_selected_policy_bytes=policy.policy.canonical_bytes(),
        proposed_scope=scope,
    )
    issued = await runtime.authorize_prepared_external_delivery_grant(
        _signed_grant(issue),
        request_id="recovered-grant",
        original_identity=request.identity,
        original_fingerprint=request.original_driver_command_fingerprint(),
        selected_seal=locator,
    )
    assert issued.grant.status == "ACTIVE"
    return issued


async def _recover_first_two_scoped_stages(
    runtime: CommonCliExecutionRuntime, *, request: Any, locator: Any, state: Any
) -> Any:
    """Mirror the coordinator's durable B choreography for COMPLETION/CONVERSATION."""
    coordinator = cast(Any, runtime)._h1_postseal_recovery_coordinator
    assert coordinator is not None
    source = coordinator._source
    context: object | None = None
    session: object | None = None
    durable_results: dict[str, bytes] = {}
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
            for stage, prepare in (
                ("COMPLETION", "prepare_first_path_completion"),
                ("CONVERSATION", "prepare_conversation_completion"),
            ):
                lease.require_owned()
                source._require_current(context)
                semantic_input = source._reconstruct_input(
                    context, stage, durable_results, None, predecessor_effects_input=None
                )
                state = coordinator._append_stage_held(
                    lease,
                    state,
                    H1PostSealRecoveryTransition.pin_input(
                        state, stage=stage, semantic_input=semantic_input
                    ),
                )
                assert state.stage_input(stage) == (semantic_input, None)
                session._bind_recovery_stage(
                    stage=stage,
                    semantic_input=semantic_input,
                    predecessor_effects_input=None,
                )
                exchange = await getattr(session, prepare)()
                result_bytes = exchange.returned.canonical_payload
                assert isinstance(result_bytes, bytes)
                source._validate_result(context, stage, semantic_input, result_bytes)
                state = coordinator._append_stage_held(
                    lease,
                    state,
                    H1PostSealRecoveryTransition.commit_result(
                        state, stage=stage, result_bytes=result_bytes
                    ),
                )
                assert dict(state.results)[stage] == result_bytes
                durable_results[stage] = result_bytes
            return state
        finally:
            if session is not None:
                await coordinator._drain_session_held(lease, session)
            if context is not None:
                source._retire_recovery_context(context)


@pytest.mark.asyncio
async def test_root_only_scoped_v3_j7_capture_uses_recovered_reader_and_rejects_absent_stages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """J7 sends a selected SCOPED_V3 root to the recovered reader, never live V2."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request, locator, _state = await _install_scoped_root(runtime)

            registry = runtime._h1_completion_exchange_registry
            assert type(registry) is H1CompletionExchangeRegistry
            recovered_calls = 0
            recovered_capture = (
                H1CompletionExchangeRegistry.capture_recovered_prepared_delivery_historical
            )

            def record_recovered_capture(
                captured_registry: H1CompletionExchangeRegistry, **kwargs: object
            ) -> object:
                nonlocal recovered_calls
                recovered_calls += 1
                return recovered_capture(captured_registry, **kwargs)

            def live_v2_capture_must_not_run(
                _captured_registry: H1CompletionExchangeRegistry, **_kwargs: object
            ) -> object:
                raise AssertionError("SCOPED_V3 J7 capture reached live V2 registry capture")

            monkeypatch.setattr(
                H1CompletionExchangeRegistry,
                "capture_recovered_prepared_delivery_historical",
                record_recovered_capture,
            )
            monkeypatch.setattr(
                H1CompletionExchangeRegistry,
                "capture_prepared_delivery_historical",
                live_v2_capture_must_not_run,
            )

            with (
                runtime._authority_gate().hold(),
                pytest.raises(
                    H1CompletionExchangeRegistryViolation, match=r"recovered.*unavailable"
                ),
            ):
                runtime._capture_selected_prepared_delivery_historical_held(
                    registry=registry,
                    original_identity=request.identity,
                    original_fingerprint=request.original_driver_command_fingerprint(),
                    selected_seal=locator,
                )
            assert recovered_calls == 1


@pytest.mark.asyncio
async def test_scoped_v3_j7_recovers_the_two_b_stage_prefix_before_registry_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two real B calls make the five-record recovered historical prefix readable."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request, locator, state = await _install_scoped_root(runtime)
            recovered = await _recover_first_two_scoped_stages(
                runtime, request=request, locator=locator, state=state
            )
            with runtime._authority_gate().hold():
                records = (
                    cast(Any, runtime)
                    ._h1_postseal_recovery_journal._read_selected_root_entries_held(
                        recovered.root.root_id()
                    )
                    .records
                )
            assert tuple((record.kind, record.stage) for record in records) == (
                ("ROOT", None),
                ("STAGE_INPUT", "COMPLETION"),
                ("STAGE_RESULT", "COMPLETION"),
                ("STAGE_INPUT", "CONVERSATION"),
                ("STAGE_RESULT", "CONVERSATION"),
            )
            registry = runtime._h1_completion_exchange_registry
            assert type(registry) is H1CompletionExchangeRegistry
            capture = registry.capture_recovered_prepared_delivery_historical(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=locator,
            )
            projection = registry.replay_prepared_delivery_historical(capture)
            preimages = registry.replay_prepared_delivery_preimages(capture)
            recovered = cast(Any, capture._record)
            root = H1PostSealRecoveryRootSource(runtime).derive_on_restart(
                request.identity,
                request.original_driver_command_fingerprint(),
                locator,
            )
            records = H1V3RecoveryHistoricalSource._decode_required_records(
                recovered.pinned_records, root=root
            )
            completion_input = H1V3RecoveryHistoricalSource._stage_payload(
                records[1], kind="STAGE_INPUT", stage="COMPLETION"
            )
            completion_result = H1V3RecoveryHistoricalSource._stage_payload(
                records[2], kind="STAGE_RESULT", stage="COMPLETION"
            )
            assert (
                preimages.original_completion_request.canonical_bytes()
                == completion_input
            )
            assert preimages.prepared_completion.canonical_bytes() == completion_result
            assert projection.delivery.rendered_bytes

            retained_input = recovered.pinned_records[1].canonical_bytes
            try:
                object.__setattr__(recovered.pinned_records[1], "canonical_bytes", b"{}")
                with pytest.raises(H1CompletionExchangeRegistryViolation):
                    registry.replay_prepared_delivery_preimages(capture)
            finally:
                object.__setattr__(
                    recovered.pinned_records[1], "canonical_bytes", retained_input
                )

            def live_v2_capture_must_not_run(
                _captured_registry: H1CompletionExchangeRegistry, **_kwargs: object
            ) -> object:
                raise AssertionError("SCOPED_V3 J7 capture reached live V2 registry capture")

            monkeypatch.setattr(
                H1CompletionExchangeRegistry,
                "capture_prepared_delivery_historical",
                live_v2_capture_must_not_run,
            )
            with runtime._authority_gate().hold():
                j7_capture = runtime._capture_selected_prepared_delivery_historical_held(
                    registry=registry,
                    original_identity=request.identity,
                    original_fingerprint=request.original_driver_command_fingerprint(),
                    selected_seal=locator,
                )
            assert registry.replay_prepared_delivery_historical(j7_capture) == projection


@pytest.mark.asyncio
async def test_scoped_v3_recovered_b_issues_and_recaptures_reconstructed_authority(
    tmp_path: Path,
) -> None:
    """A genuine recovered B ISSUE is selected again by its immutable B evidence."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    _write_policy_pin(slot.database_path)
    _write_grant_pin(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request, locator, state = await _install_scoped_root(runtime)
            await _recover_first_two_scoped_stages(
                runtime, request=request, locator=locator, state=state
            )
            registry = runtime._h1_completion_exchange_registry
            assert type(registry) is H1CompletionExchangeRegistry
            capture = registry.capture_recovered_prepared_delivery_historical(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=locator,
            )
            projection = registry.replay_prepared_delivery_historical(capture)
            issued = await _issue_reconstructed_grant(
                runtime, projection=projection, request=request, locator=locator
            )
            reader = H1ScopedDeliveryAuthorityReader(runtime)
            authority = await reader.read_reconstructed(historical_b_capture=capture)
            assert authority.evidence.grant_anchor == issued.anchor
            assert authority.evidence.canonical_grant_bytes == issued.grant.canonical_bytes()
            assert authority.historical_projection == projection
            assert reader.recheck(authority) is authority

        # Durable B history survives reopen, while a fresh R16 resource epoch
        # cannot inherit current authority from the earlier process lifetime.
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as reopened:
            registry = reopened._h1_completion_exchange_registry
            assert type(registry) is H1CompletionExchangeRegistry
            recaptured = registry.capture_recovered_prepared_delivery_historical(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=locator,
            )
            assert registry.replay_prepared_delivery_historical(recaptured) == projection
            reader = H1ScopedDeliveryAuthorityReader(reopened)
            with pytest.raises(
                H1ScopedDeliveryAuthorityViolation,
                match="fresh H1 scope is not current",
            ):
                await reader.read_reconstructed(historical_b_capture=recaptured)
