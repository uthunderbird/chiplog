"""Mounted V22 selection retains a V4 issuance across historical replay."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from chiplog.capabilities.agent_loop.delivery_work_contracts import (
    DELIVERY_WORK_CLAIM_COMMAND_SCHEMA,
    DELIVERY_WORK_GENESIS_COMMAND_SCHEMA,
    DELIVERY_WORK_OWNER,
    DELIVERY_WORK_SCHEMA,
    PrepareDeliveryWorkClaimV1,
    PrepareDeliveryWorkGenesisV1,
    prepare_delivery_work_claim,
    prepare_delivery_work_claim_projection,
    prepare_delivery_work_genesis,
)
from chiplog.composition.common_cli_execution_runtime import (
    open_installed_h1_scoped_v22_runtime,
)
from chiplog.composition.h1_completion_issuance import V4_SCHEMA, H1CompletionIssuanceV4
from chiplog.composition.h1_delivery_work_current_claim_reader import (
    H1DeliveryWorkCurrentClaimHold,
    H1DeliveryWorkCurrentClaimReader,
)
from chiplog.composition.h1_delivery_work_sources import (
    DeliveryWorkLocatorV1,
    H1DeliveryWorkSources,
)
from chiplog.composition.h1_historical_selected_sources import bind_selected_h1_completion
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_scoped_authorization_clock_reader import (
    H1ScopedAuthorizationClockReaderV1,
)
from chiplog.composition.h1_scoped_authorization_delivery_work_reader import (
    H1ScopedAuthorizationDeliveryWorkReaderV1,
)
from chiplog.composition.h1_scoped_authorization_normative_conflict_generation import (
    H1ScopedAuthorizationNormativeConflictGenerationReaderV1,
)
from chiplog.composition.h1_scoped_authorization_resource_sources_v4 import (
    H1ScopedAuthorizationResourceReaderV4,
)
from chiplog.composition.h1_scoped_authorization_runtime_and_fence import (
    H1ScopedAuthorizationRuntimeReaderV1,
)
from chiplog.composition.h1_scoped_authorization_sources_v3 import (
    H1ScopedAuthorizationLocatorV1,
)
from chiplog.composition.h1_scoped_delivery_authority import H1ScopedDeliveryAuthorityReader
from chiplog.platform._owner_publication_contracts import (
    CompleteDeliveryBatchV2,
    DeliveryWorkClaimBatchV1,
    DeliveryWorkGenesisBatchV1,
    ExactRecordHead,
    ObservedPresence,
    OwnerCommandBytes,
    OwnerRecordBytes,
    PublicationIdentity,
)
from chiplog.platform.authority_reads import capture_authority_storage_state
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot
from tests.support.h1_scoped_first_b import scoped_resources, write_scoped_pins
from tests.support.h1_v3_recovery import (
    _issue_reconstructed_grant,
    _recover_b_prefix,
    _selected_scoped_root,
)


def _selected_batch(runtime: Any) -> tuple[Any, CompleteDeliveryBatchV2]:
    selected = tuple(
        decision
        for decision in runtime._owner_decisions().snapshot().decisions
        if type(decision.prepared.request) is CompleteDeliveryBatchV2
    )
    assert len(selected) == 1
    return selected[0], selected[0].prepared.request


@pytest.mark.asyncio
async def test_v22_selected_v4_publication_reopens_through_historical_binder_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A V22-selected SCOPED_V3 chain retains its exact V2 Effects exchange."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    write_scoped_pins(slot.database_path)

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_v22_runtime(
            launch, resources=scoped_resources(tmp_path)
        ) as runtime:
            assert runtime._supervisor._j7_operator_policy_key_pin is not None
            assert runtime._supervisor._j7_operator_grant_key_pin is not None
            broker = runtime._supervisor.runtime()
            assert broker._j7_operator_policy_key_pin is not None
            assert broker._j7_operator_grant_key_pin is not None
            request, locator, state = await _selected_scoped_root(runtime)
            assert state.selected_producer == "SCOPED_V3"
            state = await _recover_b_prefix(runtime, request, locator, state)
            await _issue_reconstructed_grant(
                runtime,
                request,
                locator,
                authorization_horizon_ns=3_600_000_000_000,
            )
            coordinator = runtime._h1_postseal_recovery_coordinator
            assert coordinator is not None
            completed = await coordinator.resume_selected(
                request.identity, request.original_driver_command_fingerprint(), locator
            )
            assert len(completed.inputs) == len(completed.results) == 4

            calls: list[str] = []
            broker = runtime._supervisor.runtime()
            original = broker._call_with_admission_guard

            async def counted(sent: Any, **kwargs: Any) -> Any:
                calls.append(sent.operation_id)
                return await original(sent, **kwargs)

            monkeypatch.setattr(broker, "_call_with_admission_guard", counted)
            receipt = await runtime.finalize_execution(
                request.identity, request.original_driver_command_fingerprint()
            )
            assert getattr(receipt, "disposition", None) == "COMMITTED", receipt
            assert calls.count("effects.prepare_h1_scoped_delivery_v2") == 1

            decision, batch = _selected_batch(runtime)
            assert batch.authentication.applicability_schema == V4_SCHEMA
            predecessor = runtime._owner_decisions().predecessor_snapshot_for_selected(decision)
            assert predecessor.tenant_id == batch.identity.tenant_id
            assert predecessor.head != decision.decision_id
            enrollment = runtime._h1_live_completion_enrollment
            assert enrollment is not None
            retained = tuple(enrollment._issuances.values())
            assert len(retained) == 1
            assert type(retained[0].issuance) is H1CompletionIssuanceV4
            issuance = retained[0].issuance
            assert issuance.canonical_bytes() == batch.authentication.applicability_bytes
            assert (
                issuance.effects_call.canonical_bytes()
                == batch.prepared_effects_commands[0].canonical_bytes
            )
            assert (
                issuance.effects_result.intent_result.intent
                == issuance.assembly.ordered_effects[0].intent_result.intent
            )
            assert retained[0].batch == batch and retained[0].state == "CONSUMED"
            assert bind_selected_h1_completion(batch, runtime).decision == decision
            delivery_work = H1DeliveryWorkSources(runtime).read_absent(
                DeliveryWorkLocatorV1(issuance.effects_result.intent_result.intent.intent_id)
            )
            assert delivery_work.decision_id == decision.decision_id
            assert (
                delivery_work.intent.record_id
                == issuance.effects_result.intent_result.intent.intent_id
            )
            # The selected V4 envelope preserves a V2 scoped Effects exchange,
            # but its mutable authority must be reread from the independently
            # authenticated recovery prefix.  This is the only admissible
            # source for a later delivery-work claim; the retained V4 evidence
            # itself is historical and cannot stand in for current authority.
            registry = runtime._h1_completion_exchange_registry
            authority_reader = runtime._h1_scoped_delivery_authority_reader
            assert registry is not None
            assert type(authority_reader) is H1ScopedDeliveryAuthorityReader
            historical = registry.capture_recovered_prepared_delivery_historical(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=locator,
            )
            authority = await authority_reader.read_reconstructed(historical_b_capture=historical)
            assert authority.clock[1] > 0
            assert (
                authority.historical_projection.delivery.delivery_id
                == issuance.effects_result.delivery_id
            )
            selected_recipient = authority.historical_projection.delivery.selection.recipient
            effect_recipient = issuance.effects_result.intent_result.intent.mandate.recipient
            assert (
                selected_recipient.provider_id,
                selected_recipient.account_id,
                selected_recipient.recipient_id,
                selected_recipient.canonical_address,
            ) == (
                effect_recipient.provider,
                effect_recipient.account,
                effect_recipient.recipient,
                effect_recipient.canonical_address,
            )
            assert (
                selected_recipient.endpoint.identity,
                selected_recipient.endpoint.head,
                selected_recipient.endpoint.fingerprint,
            ) == (
                effect_recipient.endpoint.subject_id,
                effect_recipient.endpoint.head,
                effect_recipient.endpoint.fingerprint,
            )
            assert (
                selected_recipient.credential_binding.identity,
                selected_recipient.credential_binding.head,
                selected_recipient.credential_binding.fingerprint,
            ) == (
                effect_recipient.credential_binding.subject_id,
                effect_recipient.credential_binding.head,
                effect_recipient.credential_binding.fingerprint,
            )

            replay = await runtime.finalize_execution(
                request.identity, request.original_driver_command_fingerprint()
            )
            assert replay.disposition == "EXACT_REPLAY"
            assert calls.count("effects.prepare_h1_scoped_delivery_v2") == 1

            source = delivery_work.owner_source()
            genesis_state = prepare_delivery_work_genesis(source)
            genesis_command = PrepareDeliveryWorkGenesisV1(
                selected_decision_id=decision.decision_id,
                selected_decision_fingerprint=decision.decision_fingerprint,
                selected_source=source,
            )
            genesis_record = OwnerRecordBytes(
                owner=DELIVERY_WORK_OWNER,
                record_kind="DELIVERY_WORK_STATE",
                record_id=genesis_state.record_id,
                schema_id=DELIVERY_WORK_SCHEMA,
                canonical_bytes=genesis_state.canonical_bytes(),
                fingerprint=hashlib.sha256(genesis_state.canonical_bytes()).hexdigest(),
            )
            genesis = DeliveryWorkGenesisBatchV1(
                identity=PublicationIdentity(
                    tenant_id=batch.identity.tenant_id,
                    command_id="delivery-work-genesis:" + source.intent_id,
                    command_fingerprint=hashlib.sha256(
                        ("delivery-work-genesis:" + source.intent_id).encode()
                    ).hexdigest(),
                    canonicalization_version="chiplog.owner-publication.v1",
                ),
                authentication=batch.authentication,
                expected=batch.expected.model_copy(
                    update={
                        "tenant_frontier": decision.tenant_commit_sequence,
                        "expected_materialization_commitment": decision.resulting_commitment,
                    }
                ),
                selected_decision_id=decision.decision_id,
                selected_decision_fingerprint=decision.decision_fingerprint,
                selected_source=source,
                genesis_command=OwnerCommandBytes(
                    owner=DELIVERY_WORK_OWNER,
                    schema_id=DELIVERY_WORK_GENESIS_COMMAND_SCHEMA,
                    canonical_bytes=genesis_command.canonical_bytes(),
                    fingerprint=hashlib.sha256(genesis_command.canonical_bytes()).hexdigest(),
                ),
                complete_records=(genesis_record,),
                complete_batch_fingerprint=hashlib.sha256(b"delivery-work-genesis").hexdigest(),
            )
            genesis_coordinator = runtime._h1_delivery_work_publication_coordinator
            committed_genesis = await genesis_coordinator.commit(genesis)
            assert committed_genesis.kind == "COMMITTED"
            assert (await genesis_coordinator.commit(genesis)).kind == "EXACT_REPLAY"
            resources = runtime._require_dispatch_resources()
            clock_epoch, clock_start = resources.clock()
            test_clock_ns = [clock_start]
            monkeypatch.setattr(resources, "clock", lambda: (clock_epoch, test_clock_ns[0]))
            capture = runtime._h1_delivery_work_claim_sources.capture(source.intent_id)
            claim_state = prepare_delivery_work_claim(
                source,
                genesis_state,
                holder_id=capture.session.owner_id,
                session_id=capture.session.session_id,
                lease_expires_at_ns=capture.expires_at_ns,
                trusted_clock_ns=capture.issued_at_ns,
            )
            claim_command = PrepareDeliveryWorkClaimV1(
                projection=prepare_delivery_work_claim_projection(source, genesis_state)
            )
            claim_command_bytes = claim_command.canonical_bytes()
            claim_record_bytes = claim_state.canonical_bytes()
            claim_batch = DeliveryWorkClaimBatchV1(
                identity=PublicationIdentity(
                    tenant_id=batch.identity.tenant_id,
                    command_id="delivery-work-claim:" + source.intent_id,
                    command_fingerprint=hashlib.sha256(claim_command_bytes).hexdigest(),
                    canonicalization_version="chiplog.owner-publication.v1",
                ),
                authentication=batch.authentication,
                expected=batch.expected.model_copy(
                    update={
                        "tenant_frontier": committed_genesis.tenant_commit_sequence,
                        "expected_materialization_commitment": (
                            committed_genesis.resulting_commitment
                        ),
                        "ordered_heads": (
                            ObservedPresence(
                                head=ExactRecordHead(
                                    owner=DELIVERY_WORK_OWNER,
                                    record_kind="DELIVERY_WORK_STATE",
                                    subject_id=genesis_state.subject_id,
                                    record_id=genesis_state.record_id,
                                    fingerprint=hashlib.sha256(
                                        genesis_state.canonical_bytes()
                                    ).hexdigest(),
                                )
                            ),
                        ),
                    }
                ),
                claim_command=OwnerCommandBytes(
                    owner=DELIVERY_WORK_OWNER,
                    schema_id=DELIVERY_WORK_CLAIM_COMMAND_SCHEMA,
                    canonical_bytes=claim_command_bytes,
                    fingerprint=hashlib.sha256(claim_command_bytes).hexdigest(),
                ),
                complete_records=(
                    OwnerRecordBytes(
                        owner=DELIVERY_WORK_OWNER,
                        record_kind="DELIVERY_WORK_STATE",
                        record_id=claim_state.record_id,
                        schema_id=DELIVERY_WORK_SCHEMA,
                        canonical_bytes=claim_record_bytes,
                        fingerprint=hashlib.sha256(claim_record_bytes).hexdigest(),
                    ),
                ),
                complete_batch_fingerprint=hashlib.sha256(b"delivery-work-claim").hexdigest(),
            )
            claim_authority = runtime._h1_delivery_work_claim_authority
            claim_authority.register(claim_batch, capture)
            claim_coordinator = runtime._h1_delivery_work_claim_publication_coordinator
            committed_claim = await claim_coordinator.commit(claim_batch)
            assert committed_claim.kind == "COMMITTED", committed_claim
            assert (await claim_coordinator.commit(claim_batch)).kind == "EXACT_REPLAY"
            current_reader = runtime._h1_delivery_work_current_claim_reader
            assert type(current_reader) is H1DeliveryWorkCurrentClaimReader
            current_claim = current_reader.read(source.intent_id)
            assert current_claim.source == source
            assert current_claim.g0 == genesis_state
            assert current_claim.g1 == claim_state
            assert current_claim.binding.claim_projection.predecessor == genesis_state
            effects_reader = runtime._h1_scoped_authorization_effects_history_reader_v3
            effects_capture = effects_reader.capture(
                H1ScopedAuthorizationLocatorV1(source.intent_id)
            )
            delivery_work_reader = runtime._h1_scoped_authorization_delivery_work_reader_v1
            assert type(delivery_work_reader) is H1ScopedAuthorizationDeliveryWorkReaderV1
            delivery_work_capture = delivery_work_reader.read(effects_capture)
            with runtime._authority_gate().hold():
                joined = delivery_work_reader.evidence_held(delivery_work_capture)
            assert joined.source == source
            assert joined.current_claim == current_claim
            resource_reader = runtime._h1_scoped_authorization_resource_reader_v4
            assert type(resource_reader) is H1ScopedAuthorizationResourceReaderV4
            clock_reader = runtime._h1_scoped_authorization_clock_reader_v1
            assert type(clock_reader) is H1ScopedAuthorizationClockReaderV1
            resource_capture = resource_reader.read(delivery_work_capture)
            with runtime._authority_gate().hold():
                roles = resource_reader.roles_held(resource_capture)
            observation = runtime._require_dispatch_resources().observe()
            assert roles.endpoint.canonical_value == observation.endpoint_bytes
            assert roles.credential_lifecycle.canonical_value == observation.credential_bytes
            assert roles.deployment_entitlement.canonical_value == observation.grant_bytes
            assert roles.clock_epoch == observation.clock_epoch
            clock_capture = clock_reader.read(delivery_work_capture)
            with runtime._authority_gate().hold():
                clock_role = clock_reader.role_held(clock_capture)
            assert clock_role.clock_epoch == observation.clock_epoch
            assert clock_role.valid_until_ns == min(
                issuance.effects_result.intent_result.intent.mandate.horizon.expires_at_ns,
                current_claim.binding.claimant_clock.lease_expires_at_ns,
            )
            runtime_reader = runtime._h1_scoped_authorization_runtime_reader_v1
            assert type(runtime_reader) is H1ScopedAuthorizationRuntimeReaderV1
            normative_reader = (
                runtime._h1_scoped_authorization_normative_conflict_generation_reader_v1
            )
            assert (
                type(normative_reader)
                is H1ScopedAuthorizationNormativeConflictGenerationReaderV1
            )
            normative_capture = normative_reader.read(delivery_work_capture)
            with runtime._authority_gate().hold():
                normative_source = normative_reader.source_held(normative_capture)
            assert normative_source.clock_epoch == clock_role.clock_epoch
            assert normative_source.valid_until_ns == clock_role.valid_until_ns
            mandate = issuance.effects_result.intent_result.intent.mandate
            assert (
                normative_source.invalidation_manifest
                == mandate.normative_conflict_generation
            )
            normative_reader.release(normative_capture)
            test_clock_ns[0] = capture.expires_at_ns
            with pytest.raises(H1DeliveryWorkCurrentClaimHold, match="session or clock"):
                current_reader.read(source.intent_id)
            clock_reader.release(clock_capture)
            resource_reader.release(resource_capture)
            delivery_work_reader.release(delivery_work_capture)
            effects_reader.release(effects_capture)
            assert capture_authority_storage_state(slot.database_path)[0]
    with _open_installed_h1_launch(slot) as reopened_launch:
        async with open_installed_h1_scoped_v22_runtime(
            reopened_launch, resources=scoped_resources(tmp_path)
        ) as reopened:
            calls = []
            broker = reopened._supervisor.runtime()
            original = broker._call_with_admission_guard

            async def counted_reopened(sent: Any, **kwargs: Any) -> Any:
                calls.append(sent.operation_id)
                return await original(sent, **kwargs)

            monkeypatch.setattr(broker, "_call_with_admission_guard", counted_reopened)
            replay = await reopened.finalize_execution(
                request.identity, request.original_driver_command_fingerprint()
            )
            assert replay.disposition == "EXACT_REPLAY"
            assert calls.count("effects.prepare_h1_scoped_delivery_v2") == 0
            decision, batch = _selected_batch(reopened)
            historical = bind_selected_h1_completion(batch, reopened)
            assert historical.decision == decision
            assert type(historical.issuance) is H1CompletionIssuanceV4
            assert historical.issuance.canonical_bytes() == batch.authentication.applicability_bytes
